"""Every backend method the interface calls exists in main.py or mix_backend.py, and no removed source remains."""
import json
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]


class CallableParityTests(unittest.TestCase):
    def test_frontend_calls_exist_in_backend(self):
        frontend = "\n".join(path.read_text(encoding="utf-8") for path in (ROOT / "src").glob("*.ts*"))
        called = set(re.findall(r'call<[^(]*>\(\s*"([a-z0-9_]+)"', frontend, re.S)) | set(re.findall(r'call\(\s*"([a-z0-9_]+)"', frontend))
        backend = "\n".join((ROOT / name).read_text(encoding="utf-8") for name in ("main.py", "mix_backend.py"))
        defined = set(re.findall(r"^    async def ([a-z0-9_]+)\(", backend, re.M))
        self.assertTrue(called)
        self.assertEqual(sorted(called - defined), [])

    def test_self_update_callables(self):
        python_ts = (ROOT / "src" / "python.ts").read_text(encoding="utf-8")
        backend = "\n".join((ROOT / name).read_text(encoding="utf-8") for name in ("main.py", "mix_backend.py"))
        for name in ("check_plugin_update", "prepare_plugin_update", "get_install_check",
                     "prepare_plugin_reinstall", "prepare_for_plugin_install"):
            self.assertRegex(python_ts, rf'call<[^(]*>\(\s*"{name}"')
            self.assertRegex(backend, rf"(?m)^    async def {name}\(")

    def test_decky_install_uses_the_plugin_folder_name(self):
        # Decky finds the installed folder by plugin.json's name; any other name
        # installs a second copy and leaves the user's settings behind.
        handoff = (ROOT / "src" / "installHandoff.ts").read_text(encoding="utf-8")
        self.assertRegex(handoff, r'router\.call\("utilities/install_plugin", prepared\.artifact, PLUGIN_DISPLAY_NAME,')
        # Helpers are stopped before Decky deletes the folder (a running helper stops the copy half-way).
        self.assertLess(handoff.index("prepareForPluginInstall()"), handoff.index('router.call("utilities/install_plugin"'))
        for name in ("updatePanel.tsx", "installCheck.tsx"):
            text = (ROOT / "src" / name).read_text(encoding="utf-8")
            self.assertNotIn("utilities/install_plugin", text, name)
            self.assertIn("handOffToDecky(", text, name)
        version_ts = (ROOT / "src" / "version.ts").read_text(encoding="utf-8")
        plugin = json.loads((ROOT / "plugin.json").read_text(encoding="utf-8"))
        self.assertIn(f'PLUGIN_DISPLAY_NAME = "{plugin["name"]}"', version_ts)

    def test_removed_sources_are_gone_from_the_interface(self):
        for path in (ROOT / "src").glob("*.ts*"):
            text = path.read_text(encoding="utf-8")
            for word in ("tidal", "deezer", "soundcloud", "amazonmusic", "spotifyapi", "spotifyplayer"):
                self.assertNotIn(word, text.lower().replace(" ", ""), f"{path.name}: {word}")
        self.assertFalse((ROOT / "src" / "spotify.tsx").exists())
        self.assertFalse((ROOT / "bin" / "SpotifyPlaybackBridge.exe").exists())
        self.assertFalse((ROOT / "vendor" / "spotify_scraper").exists())


if __name__ == "__main__":
    unittest.main()
