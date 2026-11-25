"""Name and version of the plugin, read from package.json.

package.json is the single source of truth for the version. Decky shows it as
is (for example 1.0.0); the interface shows it as a volume ("Vol. 1"), where
the volume number is the major version.
"""
from __future__ import annotations

import json
import os
import re

DISPLAY_NAME = "Awesome Mix"
PACKAGE_NAME = "eternal-mix"
REPOSITORY_URL = "https://github.com/M2ljay/Eternal-Mix"
CREDITS = "Based on Now Playing by Andrea Sgarro (LoZazaMastro)"

_ROOT = os.path.dirname(os.path.abspath(__file__))
_SEMVER = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:[-+].*)?$")


def read_version(root: str = _ROOT) -> str:
    try:
        with open(os.path.join(root, "package.json"), "r", encoding="utf-8") as handle:
            version = str(json.load(handle).get("version") or "").strip()
    except Exception:
        return "0.0.0"
    return version if _SEMVER.match(version) else "0.0.0"


def volume_label(version: str) -> str:
    """Return "Vol. N" for version N.x.y (the label is the major version)."""
    match = _SEMVER.match(str(version or "").strip())
    return f"Vol. {int(match.group(1))}" if match else "Vol. ?"


VERSION = read_version()
VOLUME = volume_label(VERSION)
USER_AGENT = f"EternalMix/{VERSION} (+{REPOSITORY_URL})"
