"""The one-time copy of Now Playing Plus data into the Awesome Mix settings folder."""
import os
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import legacy_migration  # noqa: E402


class LegacyMigrationTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="awesome-mix-migration-")
        self.settings = Path(self._tmp.name) / "settings"
        self.old = self.settings / "Now Playing Plus"
        self.new = self.settings / "Awesome Mix"
        self.old.mkdir(parents=True)

    def tearDown(self):
        self._tmp.cleanup()

    def write(self, relative, content="x"):
        path = self.old / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def snapshot(self, root):
        return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}

    def test_copies_allow_listed_entries_and_leaves_the_old_folder_untouched(self):
        self.write("youtube-music.json", '{"auth": {"opaque": true}}')
        self.write("local-music.json", "{}")
        self.write("source-behavior.json", "{}")
        self.write("artist-background-providers.json", "{}")
        self.write("local-music-covers/a/b.jpg", "img")
        self.write("youtube-music-artist-backgrounds/c.jpg", "img")
        before = self.snapshot(self.old)
        result = legacy_migration.migrate_legacy_settings(str(self.new))
        self.assertEqual(result["status"], "done")
        self.assertEqual(result["source"], "Now Playing Plus")
        for name in ("youtube-music.json", "local-music.json", "source-behavior.json",
                     "artist-background-providers.json", "local-music-covers/a/b.jpg",
                     "youtube-music-artist-backgrounds/c.jpg"):
            self.assertEqual((self.new / name).read_bytes(), (self.old / name).read_bytes(), name)
        self.assertEqual(self.snapshot(self.old), before)
        self.assertTrue((self.new / legacy_migration.MARKER_NAME).is_file())

    def test_skips_spotify_browser_profile_temporary_and_unknown_entries(self):
        for name in ("spotify-plus.json", "spotify-library-cache.json", "spotify-api-cache/x.json",
                     "spotify-audio-cache/blob", "youtube-music-browser-profile/Default/Cookies",
                     "youtube-music.json.tmp", "youtube-music-response-cache/r.json", "something-else.json"):
            self.write(name)
        legacy_migration.migrate_legacy_settings(str(self.new))
        copied = {p.relative_to(self.new).as_posix() for p in self.new.rglob("*") if p.is_file()}
        self.assertEqual(copied, {legacy_migration.MARKER_NAME})

    def test_never_overwrites_and_runs_once(self):
        self.write("local-music.json", "old")
        self.new.mkdir(parents=True)
        (self.new / "local-music.json").write_text("new", encoding="utf-8")
        first = legacy_migration.migrate_legacy_settings(str(self.new))
        self.assertEqual(first["status"], "done")
        self.assertEqual((self.new / "local-music.json").read_text(encoding="utf-8"), "new")
        self.write("youtube-music.json", "later")
        second = legacy_migration.migrate_legacy_settings(str(self.new))
        self.assertEqual(second["status"], "already")
        self.assertFalse((self.new / "youtube-music.json").exists())

    def test_no_legacy_folder(self):
        result = legacy_migration.migrate_legacy_settings(str(self.settings / "Other" / "Awesome Mix"))
        self.assertEqual(result["status"], "none")

    def test_does_not_migrate_from_itself(self):
        self.write("local-music.json")
        result = legacy_migration.migrate_legacy_settings(str(self.old))
        self.assertEqual(result["status"], "none")

    def test_errors_are_reported_not_raised_and_retried_next_start(self):
        self.write("local-music.json")
        logs = []
        original = legacy_migration.shutil.copy2
        def failing_copy(*_args, **_kwargs):
            raise PermissionError("denied")
        legacy_migration.shutil.copy2 = failing_copy
        try:
            result = legacy_migration.migrate_legacy_settings(str(self.new), logs.append)
        finally:
            legacy_migration.shutil.copy2 = original
        self.assertEqual(result["status"], "error")
        self.assertTrue(logs and "failed" in logs[0])
        self.assertFalse((self.new / legacy_migration.MARKER_NAME).exists())
        self.assertEqual(legacy_migration.migrate_legacy_settings(str(self.new))["status"], "done")
        self.assertTrue((self.new / "local-music.json").is_file())

if __name__ == "__main__":
    unittest.main()
