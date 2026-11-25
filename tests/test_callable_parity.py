"""Every backend method the interface calls exists in main.py, and no removed source remains."""
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]


class CallableParityTests(unittest.TestCase):
    def test_frontend_calls_exist_in_backend(self):
        frontend = "\n".join(path.read_text(encoding="utf-8") for path in (ROOT / "src").glob("*.ts*"))
        called = set(re.findall(r'call<[^(]*>\(\s*"([a-z0-9_]+)"', frontend, re.S)) | set(re.findall(r'call\(\s*"([a-z0-9_]+)"', frontend))
        backend = (ROOT / "main.py").read_text(encoding="utf-8")
        defined = set(re.findall(r"^    async def ([a-z0-9_]+)\(", backend, re.M))
        self.assertTrue(called)
        self.assertEqual(sorted(called - defined), [])

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
