"""Version and volume label: package.json is the single source of truth."""
import json
from pathlib import Path
import re
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import mix_identity  # noqa: E402


class MixIdentityTests(unittest.TestCase):
    def test_volume_label(self):
        self.assertEqual(mix_identity.volume_label("1.0.0"), "Vol. 1")
        self.assertEqual(mix_identity.volume_label("2.3.4"), "Vol. 2")
        self.assertEqual(mix_identity.volume_label("10.0.1-beta.1"), "Vol. 10")
        self.assertEqual(mix_identity.volume_label("garbage"), "Vol. ?")

    def test_version_comes_from_package_json(self):
        package = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
        self.assertEqual(mix_identity.VERSION, package["version"])
        self.assertEqual(mix_identity.VOLUME, mix_identity.volume_label(package["version"]))
        self.assertIn(mix_identity.VERSION, mix_identity.USER_AGENT)

    def test_lockfile_and_changelog_follow_package_json(self):
        version = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))["version"]
        lock = json.loads((ROOT / "package-lock.json").read_text(encoding="utf-8"))
        self.assertEqual(lock["version"], version)
        self.assertEqual(lock["packages"][""]["version"], version)
        self.assertTrue((ROOT / f"CHANGELOG_{version}.md").is_file())

    def test_no_other_hardcoded_plugin_version(self):
        version = mix_identity.VERSION
        for name in ("main.py", "mix_backend.py", "mix_install_check.py", "mix_process_guard.py", "ytmusic_service.py", "mix_update.py", "src/index.tsx", "src/version.ts", "src/updatePanel.tsx"):
            text = (ROOT / name).read_text(encoding="utf-8")
            self.assertNotRegex(text, rf"(?<![\d.]){re.escape(version)}(?![\d.])", name)

    def test_plugin_folder_name(self):
        plugin = json.loads((ROOT / "plugin.json").read_text(encoding="utf-8"))
        self.assertEqual(plugin["name"], mix_identity.DISPLAY_NAME)


if __name__ == "__main__":
    unittest.main()
