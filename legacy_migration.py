"""One-time copy of the plugin's own data from the "Now Playing Plus" folders.

Decky keys a plugin's settings by its folder name
(DECKY_HOME/settings/<plugin folder>), so renaming the plugin folder from
"Now Playing Plus" to "Awesome Mix" starts with an empty settings directory.
On first start this module copies an explicit allow-list of entries from the
old settings directory into the new one:

- it only copies: nothing in the old folder is moved, changed or deleted;
- an entry that already exists in the new folder is never overwritten;
- entries are treated as opaque files: nothing is read, parsed or logged;
- a marker file records that the copy ran, so it runs once.

Anything not on the allow-list is not copied: the Spotify settings, tokens and
caches, including Spotify's artist-background cache (Spotify was removed), the
YouTube Music sign-in browser profile (a full browser profile with cookies,
only used while signing in), the YouTube Music response cache (rebuilt on
demand), temporary files, and any file this plugin does not know about.
"""
from __future__ import annotations

import json
import os
import shutil
import time
from typing import Callable, Dict, List, Optional

LEGACY_FOLDER_NAMES = ("Now Playing Plus",)
MARKER_NAME = ".awesome-mix-migration.json"

# Plain files copied as opaque bytes. youtube-music.json holds the YouTube
# Music sign-in (request headers) and artist-background-providers.json holds
# the user's own fanart.tv API key: both are copied so the login and the key
# carry over, and are never opened by the migration.
COPIED_FILES = (
    "source-behavior.json",
    "local-music.json",
    "local-music-library.json",
    "youtube-music.json",
    "youtube-music-library-cache.json",
    "youtube-music-unavailable.json",
    "artist-background-selections.json",
    "artist-background-providers.json",
)

# Folders of images the plugin downloaded or the user picked.
COPIED_DIRECTORIES = (
    "local-music-covers",
    "local-music-artist-profiles",
    "local-music-artist-backgrounds",
    "youtube-music-artist-backgrounds",
)


def _copy_tree_missing(source: str, target: str) -> int:
    """Copy regular files under source that do not exist under target."""
    copied = 0
    for directory, subdirectories, files in os.walk(source, followlinks=False):
        subdirectories[:] = [name for name in subdirectories if not os.path.islink(os.path.join(directory, name))]
        relative = os.path.relpath(directory, source)
        destination_dir = target if relative == "." else os.path.join(target, relative)
        os.makedirs(destination_dir, exist_ok=True)
        for name in files:
            origin = os.path.join(directory, name)
            destination = os.path.join(destination_dir, name)
            if os.path.islink(origin) or not os.path.isfile(origin) or os.path.lexists(destination):
                continue
            shutil.copy2(origin, destination)
            copied += 1
    return copied


def migrate_legacy_settings(
    settings_dir: str,
    log: Optional[Callable[[str], None]] = None,
    legacy_names=LEGACY_FOLDER_NAMES,
) -> Dict[str, object]:
    """Copy the allow-listed entries from a legacy settings folder, once.

    Returns a summary: {"status": ..., "source": folder name, "copied": [...]}.
    Status is "done" (copied now), "already" (marker present), "none" (no
    legacy folder) or "error". Never raises.
    """
    def note(message: str) -> None:
        if log is not None:
            try:
                log(message)
            except Exception:
                pass

    summary: Dict[str, object] = {"status": "none", "source": "", "copied": []}
    try:
        if not settings_dir:
            return summary
        settings_dir = os.path.abspath(settings_dir)
        marker = os.path.join(settings_dir, MARKER_NAME)
        if os.path.exists(marker):
            summary["status"] = "already"
            return summary
        parent = os.path.dirname(settings_dir)
        source = ""
        for name in legacy_names:
            candidate = os.path.join(parent, name)
            if os.path.normcase(candidate) != os.path.normcase(settings_dir) and os.path.isdir(candidate):
                source = candidate
                summary["source"] = name
                break
        if not source:
            return summary
        os.makedirs(settings_dir, exist_ok=True)
        copied: List[str] = []
        for name in COPIED_FILES:
            origin = os.path.join(source, name)
            destination = os.path.join(settings_dir, name)
            if os.path.isfile(origin) and not os.path.islink(origin) and not os.path.lexists(destination):
                shutil.copy2(origin, destination)
                copied.append(name)
        for name in COPIED_DIRECTORIES:
            origin = os.path.join(source, name)
            if os.path.isdir(origin) and not os.path.islink(origin):
                count = _copy_tree_missing(origin, os.path.join(settings_dir, name))
                if count:
                    copied.append(f"{name}/ ({count} files)")
        summary["copied"] = copied
        summary["status"] = "done"
        with open(marker, "w", encoding="utf-8") as handle:
            json.dump({"from": summary["source"], "copied": copied, "at": int(time.time())}, handle, indent=2)
        note(f"Copied settings from '{summary['source']}': {', '.join(copied) or 'nothing to copy'}")
    except Exception as exc:
        summary["status"] = "error"
        note(f"Settings migration from Now Playing Plus failed: {type(exc).__name__}")
    return summary
