"""Self-update channel for Awesome Mix.

A ``release`` branch on github.com/M2ljay/Eternal-Mix holds two files:

* ``awesome-mix.zip`` - the Decky installer zip (one top folder, ``Awesome Mix``)
* ``update.json``     - ``{"version", "sha256", "size", "notes", "url"}``

``check`` reads update.json (at most every CHECK_INTERVAL_SECONDS unless forced)
and says whether its version is newer than package.json's. ``prepare`` re-reads
it, downloads the zip into the plugin's runtime folder and refuses it unless the
size, the SHA-256 and the zip's layout all match. The frontend then hands the
verified file to Decky Loader's own installer (``utilities/install_plugin``),
which asks the user, checks the SHA-256 again and replaces the plugin folder.

Decky extracts the zip into its plugins folder as is, so the zip's top folder
becomes the plugin folder, and Decky keys the settings folder by that name
(homebrew/settings/<folder>). The layout check therefore insists on the top
folder ``Awesome Mix``: an update can never move the user's settings.

Every release zip also carries ``install-manifest.json`` (mix_install_check):
the layout check refuses a zip whose files do not match their own manifest, and
the installed plugin re-checks its folder against it at startup.

``prepare(reinstall=True)`` also accepts the version that is already installed:
the install check offers it when the last install did not finish copying.

Only stdlib modules that Decky Loader's frozen Windows interpreter bundles for
its own use are imported here (hashlib, json, os, re, ssl, urllib, zipfile).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import ssl
import threading
import time
import urllib.error
import urllib.request
import zipfile
from typing import Any, Callable, Dict, Optional, Tuple

import mix_identity
import mix_install_check

RELEASE_REPO = "M2ljay/Eternal-Mix"
RELEASE_BRANCH = "release"
RELEASE_BASE_URL = f"https://raw.githubusercontent.com/{RELEASE_REPO}/{RELEASE_BRANCH}/"
MANIFEST_URL = RELEASE_BASE_URL + "update.json"
ARTIFACT_NAME = "awesome-mix.zip"
ARTIFACT_URL = RELEASE_BASE_URL + ARTIFACT_NAME
PLUGIN_FOLDER = mix_identity.DISPLAY_NAME

CHECK_INTERVAL_SECONDS = 6 * 60 * 60
MAX_MANIFEST_BYTES = 64 * 1024
# GitHub refuses files over 100 MB in a normal (non-LFS) push.
MAX_ARTIFACT_BYTES = 100 * 1000 * 1000
MAX_NOTES_CHARS = 2000
TIMEOUT_SECONDS = 30
_CHUNK = 64 * 1024

_VERSION_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

# Reasons the frontend translates; the message is the English fallback.
NETWORK = "network"
MANIFEST_INVALID = "manifest_invalid"
UP_TO_DATE = "up_to_date"
SIZE_MISMATCH = "size_mismatch"
HASH_MISMATCH = "hash_mismatch"
LAYOUT_INVALID = "layout_invalid"
DOWNLOAD_FAILED = "download_failed"
BUSY = "busy"
# Sent by main.py: a helper still holds files in the plugin folder, or (safe
# mode) this module itself could not be imported.
HELPERS_RUNNING = "helpers_running"
REINSTALL_UNAVAILABLE = "reinstall_unavailable"


class UpdateError(Exception):
    """A refusal with a machine-readable reason and a plain message."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason
        self.message = message


def parse_version(text: object) -> Optional[Tuple[int, int, int]]:
    """``"1.2.3"`` -> ``(1, 2, 3)``. Any other shape (pre-release, garbage) is None."""
    if not isinstance(text, str):
        return None
    match = _VERSION_RE.match(text.strip())
    return (int(match.group(1)), int(match.group(2)), int(match.group(3))) if match else None


def is_newer(current: str, candidate: str) -> bool:
    """Strictly newer by number (1.0.10 > 1.0.9). Equal, older or unreadable is False."""
    have, want = parse_version(current), parse_version(candidate)
    return have is not None and want is not None and want > have


def parse_manifest(payload: object) -> Dict[str, Any]:
    """Validate a decoded update.json. Raises UpdateError(MANIFEST_INVALID)."""
    def bad(message: str) -> UpdateError:
        return UpdateError(MANIFEST_INVALID, f"update.json {message}")

    if not isinstance(payload, dict):
        raise bad("is not a JSON object")
    version = payload.get("version")
    if parse_version(version) is None:
        raise bad(f"has an unreadable version: {version!r}")
    sha256 = payload.get("sha256")
    if not isinstance(sha256, str) or not _SHA256_RE.match(sha256):
        raise bad("has no valid sha256")
    size = payload.get("size")
    if isinstance(size, bool) or not isinstance(size, int) or not 0 < size <= MAX_ARTIFACT_BYTES:
        raise bad(f"has an invalid size: {size!r}")
    url = payload.get("url")
    if url != ARTIFACT_URL:
        raise bad(f"points somewhere other than {ARTIFACT_URL}")
    notes = payload.get("notes", "")
    notes = notes.strip()[:MAX_NOTES_CHARS] if isinstance(notes, str) else ""
    return {"version": str(version).strip(), "sha256": sha256, "size": size, "notes": notes, "url": url}


def decode_manifest(body: bytes) -> Dict[str, Any]:
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise UpdateError(MANIFEST_INVALID, f"update.json is not valid JSON: {exc}") from exc
    return parse_manifest(payload)


def file_digest(path: str) -> Tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(_CHUNK), b""):
            digest.update(chunk)
            size += len(chunk)
    return size, digest.hexdigest()


def verify_download(size: int, sha256: str, manifest: Dict[str, Any]) -> None:
    """Raise SIZE_MISMATCH / HASH_MISMATCH unless the download is what update.json describes."""
    if size != manifest["size"]:
        raise UpdateError(
            SIZE_MISMATCH,
            f"The download is {size} bytes, update.json says {manifest['size']}. Nothing was installed. "
            "If the update was published moments ago, try again in a few minutes.",
        )
    if sha256 != manifest["sha256"]:
        raise UpdateError(
            HASH_MISMATCH,
            "The download does not match the SHA-256 in update.json. Nothing was installed. "
            "If the update was published moments ago, try again in a few minutes.",
        )


def verify_installer_layout(path: str, version: str) -> None:
    """Raise LAYOUT_INVALID unless the zip installs into ``Awesome Mix`` as ``version``.

    Every entry must sit under the one top folder ``Awesome Mix/`` (no absolute
    or ``..`` paths), and that folder's plugin.json must be named ``Awesome Mix``
    and its package.json must carry ``version``. Its install-manifest.json must
    list exactly the zip's other files, with matching sizes and SHA-256 hashes.
    """
    def bad(message: str) -> UpdateError:
        return UpdateError(LAYOUT_INVALID, f"The update is not a valid {PLUGIN_FOLDER} installer: {message}")

    prefix = PLUGIN_FOLDER + "/"
    try:
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            if not names:
                raise bad("the zip is empty")
            for name in names:
                parts = name.replace("\\", "/").split("/")
                if not name.startswith(prefix) or ".." in parts or ":" in name:
                    raise bad(f"unexpected entry {name!r}")
            for required in ("plugin.json", "package.json", "main.py", "dist/index.js"):
                if prefix + required not in names:
                    raise bad(f"{required} is missing")
            plugin = json.loads(archive.read(prefix + "plugin.json").decode("utf-8"))
            package = json.loads(archive.read(prefix + "package.json").decode("utf-8"))
            _verify_zip_manifest(archive, prefix, version, bad)
    except UpdateError:
        raise
    except (zipfile.BadZipFile, OSError, ValueError, KeyError) as exc:
        raise bad(str(exc)) from exc
    if not isinstance(plugin, dict) or plugin.get("name") != PLUGIN_FOLDER:
        raise bad(f"plugin.json is not named {PLUGIN_FOLDER!r}")
    if not isinstance(package, dict) or package.get("version") != version:
        raise bad(f"package.json is not version {version}")


def _verify_zip_manifest(archive: zipfile.ZipFile, prefix: str, version: str, bad) -> None:
    manifest_entry = prefix + mix_install_check.MANIFEST_NAME
    if manifest_entry not in archive.namelist():
        raise bad(f"{mix_install_check.MANIFEST_NAME} is missing")
    try:
        manifest = mix_install_check.decode_manifest(archive.read(manifest_entry))
    except mix_install_check.ManifestError as exc:
        raise bad(f"{mix_install_check.MANIFEST_NAME} is invalid: {exc}") from exc
    if manifest["version"] != version:
        raise bad(f"{mix_install_check.MANIFEST_NAME} is not version {version}")
    files = {info.filename[len(prefix):]: info for info in archive.infolist()
             if not info.is_dir() and info.filename != manifest_entry}
    listed = {entry["path"]: entry for entry in manifest["files"]}
    if set(files) != set(listed):
        extra = sorted(set(files) - set(listed))[:3]
        missing = sorted(set(listed) - set(files))[:3]
        raise bad(f"{mix_install_check.MANIFEST_NAME} does not match the zip (unlisted {extra}, absent {missing})")
    for rel, entry in listed.items():
        info = files[rel]
        if info.file_size != entry["size"]:
            raise bad(f"{rel} has the wrong size")
        digest = hashlib.sha256()
        with archive.open(info) as handle:
            for chunk in iter(lambda: handle.read(_CHUNK), b""):
                digest.update(chunk)
        if digest.hexdigest() != entry["sha256"]:
            raise bad(f"{rel} does not match its SHA-256")


def file_artifact(path: str) -> str:
    # Decky's PluginBrowser._install opens ``artifact[7:]`` for a file:// artifact
    # (backend/decky_loader/browser.py), so the raw path follows the scheme as is.
    return "file://" + path


class HttpTransport:
    """HTTPS reads from the release channel, size-capped, with the plugin's User-Agent."""

    def __init__(self, user_agent: str, cafile: str = "") -> None:
        self._user_agent = user_agent
        self._cafile = cafile

    def _open(self, url: str):
        request = urllib.request.Request(url, method="GET")
        request.add_header("User-Agent", self._user_agent)
        request.add_header("Cache-Control", "no-cache")
        try:
            context = ssl.create_default_context(cafile=self._cafile or None)
            return urllib.request.urlopen(request, context=context, timeout=TIMEOUT_SECONDS)
        except urllib.error.HTTPError as exc:
            raise UpdateError(NETWORK, f"GitHub answered HTTP {exc.code}.") from exc
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise UpdateError(NETWORK, f"Could not reach GitHub: {getattr(exc, 'reason', exc)}") from exc

    def read(self, url: str, max_bytes: int) -> bytes:
        with self._open(url) as response:
            try:
                body = response.read(max_bytes + 1)
            except OSError as exc:
                raise UpdateError(NETWORK, f"Reading update.json failed: {exc}") from exc
        if len(body) > max_bytes:
            raise UpdateError(MANIFEST_INVALID, "update.json is too large.")
        return body

    def download(self, url: str, dest: str, max_bytes: int) -> None:
        """Write at most ``max_bytes + 1`` bytes to ``dest`` (one extra shows an oversize)."""
        with self._open(url) as response, open(dest, "wb") as handle:
            written = 0
            while written <= max_bytes:
                try:
                    chunk = response.read(min(_CHUNK, max_bytes + 1 - written))
                except OSError as exc:
                    raise UpdateError(NETWORK, f"Downloading the update failed: {exc}") from exc
                if not chunk:
                    break
                handle.write(chunk)
                written += len(chunk)


def default_cafile(plugin_dir: str) -> str:
    """The bundled certifi CA file, when present (Decky's interpreter may lack system roots)."""
    path = os.path.join(plugin_dir, "vendor", "certifi", "cacert.pem")
    return path if os.path.isfile(path) else ""


class UpdateChannel:
    """Check the release channel and stage verified installers for Decky.

    Methods block (network and disk); call them from an executor thread.
    """

    def __init__(
        self,
        current_version: str,
        staging_dir: str,
        transport: Any,
        clock: Callable[[], float] = time.time,
        log: Callable[[str], None] = lambda _message: None,
    ) -> None:
        self.current = current_version
        self.staging_dir = staging_dir
        self._transport = transport
        self._clock = clock
        self._log = log
        self._lock = threading.Lock()
        self._manifest: Optional[Dict[str, Any]] = None
        self._checked_at: Optional[float] = None
        self._state_loaded = False

    @property
    def _state_path(self) -> str:
        return os.path.join(self.staging_dir, "state.json")

    def check(self, force: bool = False) -> Dict[str, Any]:
        """``{ok: True, current, updateAvailable, version?, notes?, size?, checkedAt}`` or a failure."""
        if not self._lock.acquire(blocking=False):
            return self._failure(UpdateError(BUSY, "An update check or download is already running."))
        try:
            self._load_state()
            now = self._clock()
            fresh = self._checked_at is not None and 0 <= now - self._checked_at < CHECK_INTERVAL_SECONDS
            if force or not fresh or self._manifest is None:
                self._refresh(now)
            return self._answer()
        except UpdateError as exc:
            self._log(f"update check failed: {exc.reason}: {exc.message}")
            return self._failure(exc)
        finally:
            self._lock.release()

    def prepare(self, reinstall: bool = False) -> Dict[str, Any]:
        """Download and verify the newest build.

        ``{ok: True, version, artifact, sha256, size}`` where ``artifact`` is the
        ``file://`` path Decky's installer reads, or a failure. Nothing is handed
        on unless size, SHA-256 and layout all match. With ``reinstall`` the
        published version may equal the installed one (never be older).
        """
        if not self._lock.acquire(blocking=False):
            return self._failure(UpdateError(BUSY, "An update check or download is already running."))
        try:
            self._load_state()
            manifest = self._refresh(self._clock())
            current = parse_version(self.current)
            same = current is not None and parse_version(manifest["version"]) == current
            if not (is_newer(self.current, manifest["version"]) or (reinstall and same)):
                raise UpdateError(UP_TO_DATE, f"{self.current} is already the newest version.")
            download_dir = os.path.join(self.staging_dir, "download")
            os.makedirs(download_dir, exist_ok=True)
            self._clear_downloads(download_dir)
            dest = os.path.join(download_dir, f"Eternal-Mix-decky_Installer-{manifest['version']}.zip")
            part = dest + ".part"
            try:
                self._transport.download(f"{manifest['url']}?t={int(self._clock())}", part, manifest["size"])
                size, sha256 = file_digest(part)
                verify_download(size, sha256, manifest)
                verify_installer_layout(part, manifest["version"])
                os.replace(part, dest)
            except UpdateError:
                _remove(part)
                raise
            except OSError as exc:
                _remove(part)
                raise UpdateError(DOWNLOAD_FAILED, f"Could not save the update: {exc}") from exc
            self._log(f"update {manifest['version']} downloaded and verified ({size} bytes)")
            return {"ok": True, "current": self.current, "version": manifest["version"],
                    "artifact": file_artifact(dest), "sha256": sha256, "size": size}
        except UpdateError as exc:
            self._log(f"update prepare refused: {exc.reason}: {exc.message}")
            return self._failure(exc)
        finally:
            self._lock.release()

    def _refresh(self, now: float) -> Dict[str, Any]:
        body = self._transport.read(f"{MANIFEST_URL}?t={int(now)}", MAX_MANIFEST_BYTES)
        manifest = decode_manifest(body)
        self._manifest = manifest
        self._checked_at = now
        self._save_state()
        return manifest

    def _answer(self) -> Dict[str, Any]:
        manifest = self._manifest or {}
        out: Dict[str, Any] = {
            "ok": True,
            "current": self.current,
            "updateAvailable": bool(manifest) and is_newer(self.current, manifest.get("version", "")),
            "checkedAt": self._checked_at,
        }
        if manifest:
            out.update(version=manifest["version"], notes=manifest["notes"], size=manifest["size"])
        return out

    def _failure(self, exc: UpdateError) -> Dict[str, Any]:
        return {"ok": False, "reason": exc.reason, "message": exc.message, "current": self.current}

    def _load_state(self) -> None:
        if self._state_loaded:
            return
        self._state_loaded = True
        try:
            with open(self._state_path, "r", encoding="utf-8") as handle:
                state = json.load(handle)
            manifest = parse_manifest(state.get("manifest"))
            checked_at = state.get("checkedAt")
        except (OSError, ValueError, AttributeError, UpdateError):
            return
        if isinstance(checked_at, (int, float)) and not isinstance(checked_at, bool):
            self._manifest = manifest
            self._checked_at = float(checked_at)

    def _save_state(self) -> None:
        try:
            os.makedirs(self.staging_dir, exist_ok=True)
            temporary = self._state_path + ".tmp"
            with open(temporary, "w", encoding="utf-8") as handle:
                json.dump({"checkedAt": self._checked_at, "manifest": self._manifest}, handle, indent=1)
            os.replace(temporary, self._state_path)
        except OSError as exc:
            self._log(f"update: could not record the last check: {exc}")

    @staticmethod
    def _clear_downloads(download_dir: str) -> None:
        # One installer is kept at a time; older ones are only disk space.
        for name in os.listdir(download_dir):
            if name.endswith((".zip", ".part")):
                _remove(os.path.join(download_dir, name))


def _remove(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass
