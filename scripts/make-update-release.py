#!/usr/bin/env python3
"""Build the self-update files for the ``release`` branch.

Usage:
  python scripts/make-update-release.py --output-dir OUT [--notes TEXT] [--release-clone DIR] [--skip-build]

Runs ``npm run build`` (unless --skip-build) and scripts/package-release.py,
then writes:

  OUT/awesome-mix.zip   the Decky installer zip (top folder "Awesome Mix")
  OUT/update.json       {"version", "sha256", "size", "notes", "url"}

and checks both exactly as the plugin will (mix_update.decode_manifest,
verify_download, verify_installer_layout). Notes default to the body of
CHANGELOG_<version>.md.

With --release-clone (a local clone checked out on the ``release`` branch) the
two files are copied in and ``git add``ed. Committing and pushing that branch is
left to you: the push is the publish.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import mix_update  # noqa: E402


def changelog_notes(version: str) -> str:
    path = ROOT / f"CHANGELOG_{version}.md"
    lines = path.read_text(encoding="utf-8").strip().splitlines()
    return "\n".join(lines[1:]).strip()[: mix_update.MAX_NOTES_CHARS]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--notes", default=None, help="release notes shown in the plugin (default: the changelog)")
    parser.add_argument("--release-clone", type=Path, default=None)
    parser.add_argument("--skip-build", action="store_true", help="reuse the committed dist/index.js")
    args = parser.parse_args()

    version = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))["version"]
    if mix_update.parse_version(version) is None:
        sys.exit(f"package.json version {version!r} is not X.Y.Z; the update channel only offers X.Y.Z versions")

    if not args.skip_build:
        npm = shutil.which("npm")
        if not npm:
            sys.exit("npm not found; run npm run build yourself and pass --skip-build")
        subprocess.run([npm, "run", "build"], cwd=ROOT, check=True)

    output = args.output_dir.expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="eternal-mix-release-") as temporary:
        subprocess.run([sys.executable, str(ROOT / "scripts" / "package-release.py"), "--output-dir", temporary],
                       check=True)
        built = Path(temporary) / f"Eternal-Mix-decky_Installer-{version}.zip"
        zip_path = output / mix_update.ARTIFACT_NAME
        shutil.copyfile(built, zip_path)

    size, sha256 = mix_update.file_digest(str(zip_path))
    if size > mix_update.MAX_ARTIFACT_BYTES:
        sys.exit(f"{zip_path.name} is {size} bytes; GitHub refuses files over {mix_update.MAX_ARTIFACT_BYTES}")
    notes = changelog_notes(version) if args.notes is None else args.notes
    manifest = {"version": version, "sha256": sha256, "size": size, "notes": notes, "url": mix_update.ARTIFACT_URL}
    manifest_path = output / "update.json"
    manifest_path.write_text(json.dumps(manifest, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")

    # The same checks the plugin runs before handing the zip to Decky.
    parsed = mix_update.decode_manifest(manifest_path.read_bytes())
    mix_update.verify_download(*mix_update.file_digest(str(zip_path)), parsed)
    mix_update.verify_installer_layout(str(zip_path), version)

    print(f"{version}  {size} bytes  SHA256 {sha256}")
    print(f"  {zip_path}")
    print(f"  {manifest_path}")

    if args.release_clone:
        clone = args.release_clone.expanduser().resolve()
        branch = subprocess.run(["git", "-C", str(clone), "rev-parse", "--abbrev-ref", "HEAD"],
                                check=True, capture_output=True, text=True).stdout.strip()
        if branch != mix_update.RELEASE_BRANCH:
            sys.exit(f"{clone} is on branch {branch!r}, not {mix_update.RELEASE_BRANCH!r}")
        shutil.copyfile(zip_path, clone / mix_update.ARTIFACT_NAME)
        shutil.copyfile(manifest_path, clone / "update.json")
        subprocess.run(["git", "-C", str(clone), "add", mix_update.ARTIFACT_NAME, "update.json"], check=True)
        print(f"Staged in {clone}. To publish:")
        print(f'  git -C "{clone}" commit -m "Awesome Mix {version}" && git -C "{clone}" push origin release')


if __name__ == "__main__":
    main()
