"""Install check: is the plugin folder exactly what the installer zip contained?

Decky Loader replaces a plugin by deleting its folder and extracting the new
zip over it. On Windows either step can stop half-way when a file is in use,
which leaves a mix of new, old and missing files. Every release zip therefore
carries ``install-manifest.json`` (relative path, size and SHA-256 of every
other file in the zip), written first in the zip so it always describes the
version being installed.

``InstallCheck.run_quick`` compares sizes (fast, at startup); ``start_hashing``
compares SHA-256 hashes on a background thread. Extra files (``__pycache__``,
anything not in the manifest) are ignored.

main.py imports this module before anything else, so it must keep working when
any other file of the plugin is missing or broken: it imports only stdlib
modules that Decky Loader's frozen Windows interpreter bundles (see
tests/decky_frozen_stdlib.txt) and nothing from the plugin.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import threading
import time

MANIFEST_NAME = "install-manifest.json"
FORMAT = 1
LOG_NAME = "NowPlaying.log"
_CHUNK = 1024 * 1024
_LOGGED_PROBLEMS = 25

# status
OK = "ok"                  # every listed file has the right size (and hash, once hashed)
BROKEN = "broken"          # something is missing, the wrong size or the wrong hash
UNVERIFIED = "unverified"  # no manifest: a source checkout or a build older than the check

# phase
PHASE_SIZES = "sizes"
PHASE_HASHING = "hashing"
PHASE_DONE = "done"
PHASE_STOPPED = "stopped"


class ManifestError(ValueError):
    pass


def _sha256_file(path: str, cancel: "threading.Event | None" = None) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while True:
            if cancel is not None and cancel.is_set():
                raise InterruptedError("cancelled")
            chunk = handle.read(_CHUNK)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def safe_relative_path(path: object) -> str:
    """Return ``path`` if it is a plain relative POSIX path inside the folder, else raise."""
    if not isinstance(path, str) or not path or "\\" in path or ":" in path or path.startswith("/"):
        raise ManifestError(f"unsafe path {path!r}")
    parts = path.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise ManifestError(f"unsafe path {path!r}")
    return path


def build_manifest(name: str, version: str, files) -> dict:
    """``files``: iterable of ``(relative_posix_path, bytes)``. The manifest never lists itself."""
    entries = []
    for rel, data in files:
        rel = safe_relative_path(rel)
        if rel == MANIFEST_NAME:
            continue
        entries.append({"path": rel, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()})
    entries.sort(key=lambda entry: entry["path"])
    if len({entry["path"] for entry in entries}) != len(entries):
        raise ManifestError("duplicate paths")
    return {"format": FORMAT, "name": name, "version": version, "files": entries}


def encode_manifest(manifest: dict) -> bytes:
    return (json.dumps(manifest, indent=0, sort_keys=True, ensure_ascii=True) + "\n").encode("utf-8")


def parse_manifest(payload: object) -> dict:
    if not isinstance(payload, dict) or payload.get("format") != FORMAT:
        raise ManifestError("unknown manifest format")
    version = payload.get("version")
    if not isinstance(version, str) or not version.strip():
        raise ManifestError("no version")
    files = payload.get("files")
    if not isinstance(files, list) or not files:
        raise ManifestError("no files")
    seen = set()
    clean = []
    for entry in files:
        if not isinstance(entry, dict):
            raise ManifestError("bad entry")
        rel = safe_relative_path(entry.get("path"))
        size = entry.get("size")
        sha256 = entry.get("sha256")
        if isinstance(size, bool) or not isinstance(size, int) or size < 0:
            raise ManifestError(f"bad size for {rel}")
        if not isinstance(sha256, str) or len(sha256) != 64 or any(c not in "0123456789abcdef" for c in sha256):
            raise ManifestError(f"bad sha256 for {rel}")
        if rel in seen:
            raise ManifestError(f"duplicate {rel}")
        seen.add(rel)
        clean.append({"path": rel, "size": size, "sha256": sha256})
    return {"format": FORMAT, "name": str(payload.get("name") or ""), "version": version.strip(), "files": clean}


def decode_manifest(body: bytes) -> dict:
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise ManifestError(f"not valid JSON: {exc}") from exc
    return parse_manifest(payload)


def log_path() -> str:
    """The backend's own log (homebrew/logs/<plugin>/NowPlaying.log), or the temp folder."""
    directory = os.environ.get("DECKY_PLUGIN_LOG_DIR", "").strip()
    if not directory:
        decky = sys.modules.get("decky") or sys.modules.get("decky_plugin")
        directory = str(getattr(decky, "DECKY_PLUGIN_LOG_DIR", "") or "").strip()
    if directory:
        return os.path.join(directory, LOG_NAME)
    return os.path.join(tempfile.gettempdir(), LOG_NAME)


def log(message: str) -> None:
    """Write to Decky's plugin log and to NowPlaying.log; never raise, never keep a handle open."""
    text = f"install check: {message}"
    decky = sys.modules.get("decky") or sys.modules.get("decky_plugin")
    logger = getattr(decky, "logger", None)
    try:
        if logger is not None:
            logger.info(text)
    except Exception:
        pass
    try:
        path = log_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        stamp = time.strftime("%Y-%m-%dT%H:%M:%S")
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(f"[{stamp}] [Awesome Mix] {text}\n")
    except Exception:
        pass


def read_package_version(plugin_dir: str) -> str:
    try:
        with open(os.path.join(plugin_dir, "package.json"), "r", encoding="utf-8") as handle:
            return str(json.load(handle).get("version") or "").strip()
    except Exception:
        return ""


class InstallCheck:
    """Compare the plugin folder with its manifest. Thread-safe; never raises."""

    def __init__(self, plugin_dir: str, logger=log, hash_delay: float = 5.0) -> None:
        self.plugin_dir = os.path.abspath(plugin_dir)
        self._log = logger
        self._hash_delay = max(0.0, float(hash_delay))
        self._lock = threading.Lock()
        self._cancel = threading.Event()
        self._thread: "threading.Thread | None" = None
        self._manifest: "dict | None" = None
        self._problems: "dict[str, str]" = {}
        self._status = UNVERIFIED
        self._phase = PHASE_SIZES
        self._hashed = 0
        self._manifest_error = ""
        self._backend_error = ""
        self._logged_hash_problems = False

    # -- results ---------------------------------------------------------

    def _add_problem(self, rel: str, kind: str) -> None:
        with self._lock:
            self._problems.setdefault(rel, kind)
            self._status = BROKEN

    def set_backend_error(self, error: str) -> None:
        with self._lock:
            self._backend_error = str(error or "")[:500]

    def snapshot(self) -> dict:
        with self._lock:
            manifest = self._manifest or {}
            problems = sorted(self._problems.items())
            return {
                "status": self._status,
                "phase": self._phase,
                "version": manifest.get("version") or read_package_version(self.plugin_dir),
                "totalFiles": len(manifest.get("files") or ()),
                "hashedFiles": self._hashed,
                "problemCount": len(problems),
                "problems": [{"path": rel, "kind": kind} for rel, kind in problems[:50]],
                "manifestError": self._manifest_error,
                "backendError": self._backend_error,
                "safeMode": bool(self._backend_error),
            }

    @property
    def broken(self) -> bool:
        with self._lock:
            return self._status == BROKEN

    # -- checks ----------------------------------------------------------

    def run_quick(self) -> dict:
        """Load the manifest and compare sizes. Returns the snapshot."""
        manifest_path = os.path.join(self.plugin_dir, MANIFEST_NAME)
        try:
            with open(manifest_path, "rb") as handle:
                body = handle.read(8 * 1024 * 1024)
        except FileNotFoundError:
            with self._lock:
                self._status = UNVERIFIED
                self._phase = PHASE_DONE
            self._log(f"{MANIFEST_NAME} not found in {self.plugin_dir}; install not verified (source checkout or old build)")
            return self.snapshot()
        except OSError as exc:
            return self._manifest_failed(f"cannot read {MANIFEST_NAME}: {exc}")
        try:
            manifest = decode_manifest(body)
        except ManifestError as exc:
            return self._manifest_failed(f"{MANIFEST_NAME} is invalid: {exc}")
        with self._lock:
            self._manifest = manifest
            self._status = OK
        started = time.monotonic()
        for entry in manifest["files"]:
            path = os.path.join(self.plugin_dir, *entry["path"].split("/"))
            try:
                size = os.stat(path).st_size
            except FileNotFoundError:
                self._add_problem(entry["path"], "missing")
                continue
            except OSError:
                self._add_problem(entry["path"], "unreadable")
                continue
            if size != entry["size"]:
                self._add_problem(entry["path"], "size")
        package_version = read_package_version(self.plugin_dir)
        if package_version and package_version != manifest["version"]:
            self._add_problem("package.json", "version")
        elapsed = time.monotonic() - started
        snap = self.snapshot()
        if snap["status"] == BROKEN:
            self._log_problems(f"{snap['problemCount']} of {snap['totalFiles']} files of version {manifest['version']} "
                               f"are missing or the wrong size ({elapsed:.2f}s). The last install did not finish copying; "
                               "reinstall this version")
        else:
            self._log(f"sizes OK for {snap['totalFiles']} files of version {manifest['version']} ({elapsed:.2f}s)")
        return snap

    def _manifest_failed(self, message: str) -> dict:
        with self._lock:
            self._manifest_error = message
            self._problems.setdefault(MANIFEST_NAME, "invalid")
            self._status = BROKEN
            self._phase = PHASE_DONE
        self._log(message)
        return self.snapshot()

    def _log_problems(self, headline: str) -> None:
        with self._lock:
            problems = sorted(self._problems.items())
        listed = ", ".join(f"{rel} ({kind})" for rel, kind in problems[:_LOGGED_PROBLEMS])
        more = f" and {len(problems) - _LOGGED_PROBLEMS} more" if len(problems) > _LOGGED_PROBLEMS else ""
        self._log(f"{headline}: {listed}{more}")

    def start_hashing(self) -> bool:
        """Hash every listed file on a daemon thread. False if there is nothing to hash."""
        with self._lock:
            if self._manifest is None or self._thread is not None:
                return False
            self._phase = PHASE_HASHING
            self._thread = threading.Thread(target=self._hash_all, name="AwesomeMix-InstallCheck", daemon=True)
            thread = self._thread
        thread.start()
        return True

    def hash_now(self) -> dict:
        """Synchronous hashing (tests, tools)."""
        with self._lock:
            if self._manifest is None:
                return self.snapshot()
            self._phase = PHASE_HASHING
        self._hash_delay = 0.0
        self._hash_all()
        return self.snapshot()

    def _hash_all(self) -> None:
        if self._hash_delay and self._cancel.wait(self._hash_delay):
            self._finish(PHASE_STOPPED)
            return
        manifest = self._manifest or {"files": []}
        started = time.monotonic()
        for entry in manifest["files"]:
            if self._cancel.is_set():
                self._finish(PHASE_STOPPED)
                return
            rel = entry["path"]
            with self._lock:
                known = rel in self._problems
            if not known:
                try:
                    if _sha256_file(os.path.join(self.plugin_dir, *rel.split("/")), self._cancel) != entry["sha256"]:
                        self._add_problem(rel, "hash")
                except InterruptedError:
                    self._finish(PHASE_STOPPED)
                    return
                except FileNotFoundError:
                    self._add_problem(rel, "missing")
                except OSError:
                    self._add_problem(rel, "unreadable")
            with self._lock:
                self._hashed += 1
        self._finish(PHASE_DONE)
        snap = self.snapshot()
        if snap["status"] == BROKEN:
            self._log_problems(f"hash check: {snap['problemCount']} of {snap['totalFiles']} files do not match "
                               f"version {snap['version']} ({time.monotonic() - started:.1f}s)")
        else:
            self._log(f"hashes OK for {snap['totalFiles']} files ({time.monotonic() - started:.1f}s)")

    def _finish(self, phase: str) -> None:
        with self._lock:
            self._phase = phase

    def cancel(self, timeout: float = 2.0) -> None:
        """Stop hashing and release every file handle before Decky replaces the folder."""
        self._cancel.set()
        with self._lock:
            thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(max(0.0, timeout))


def startup(plugin_dir: str, hash_delay: float = 5.0) -> InstallCheck:
    """Run the size check now and the hash check in the background. Never raises."""
    check = InstallCheck(plugin_dir, hash_delay=hash_delay)
    try:
        check.run_quick()
        check.start_hashing()
    except Exception as exc:  # the check must never stop the plugin from starting
        log(f"check failed: {type(exc).__name__}: {exc}")
    return check
