#!/usr/bin/env python3
"""Write install-manifest.json into a staged plugin folder (used by package-win.ps1).

Usage: python scripts/write-install-manifest.py STAGE_DIR
"""
from __future__ import annotations

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import mix_install_check  # noqa: E402


def main() -> None:
    stage = Path(sys.argv[1]).resolve()
    package = json.loads((stage / "package.json").read_text(encoding="utf-8"))
    plugin = json.loads((stage / "plugin.json").read_text(encoding="utf-8"))
    files = sorted(p for p in stage.rglob("*") if p.is_file() and p.name != mix_install_check.MANIFEST_NAME)
    manifest = mix_install_check.build_manifest(
        plugin["name"], package["version"], ((p.relative_to(stage).as_posix(), p.read_bytes()) for p in files))
    (stage / mix_install_check.MANIFEST_NAME).write_bytes(mix_install_check.encode_manifest(manifest))
    print(f"{mix_install_check.MANIFEST_NAME}: {len(manifest['files'])} files, version {manifest['version']}")


if __name__ == "__main__":
    main()
