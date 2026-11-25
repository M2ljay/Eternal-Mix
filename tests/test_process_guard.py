"""Helper shutdown before an update: scope rules (pure) and the off-Windows no-op."""
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import mix_process_guard as guard  # noqa: E402


class ScopeTests(unittest.TestCase):
    def setUp(self):
        self.plugin = os.path.join(tempfile.gettempdir(), "plugins", "Awesome Mix")
        self.roots = guard.helper_roots(self.plugin)

    def test_roots_are_the_plugin_folder_and_its_temp_copies(self):
        self.assertEqual(self.roots[0], os.path.abspath(self.plugin))
        self.assertEqual(self.roots[1], os.path.join(tempfile.gettempdir(), "NowPlaying-MediaBridge"))

    def test_within_needs_a_real_path_boundary(self):
        self.assertTrue(guard.is_within(os.path.join(self.plugin, "bin", "MediaBridge.exe"), self.plugin))
        self.assertTrue(guard.is_within(self.plugin, self.plugin))
        self.assertFalse(guard.is_within(self.plugin + " 2" + os.sep + "MediaBridge.exe", self.plugin))
        self.assertFalse(guard.is_within(os.path.join(self.plugin, "..", "Other", "x.exe"), self.plugin))
        self.assertFalse(guard.is_within("", self.plugin))

    def test_only_processes_running_from_the_roots_are_targets(self):
        cache = os.path.join(self.roots[1], "abc123", "MediaBridge.exe")
        entries = [
            {"pid": 10, "name": "MediaBridge.exe", "path": os.path.join(self.plugin, "bin", "MediaBridge.exe")},
            {"pid": 11, "name": "MediaBridge.exe", "path": cache},
            {"pid": 12, "name": "MediaBridge.exe", "path": os.path.join(tempfile.gettempdir(), "elsewhere", "MediaBridge.exe")},
            {"pid": 13, "name": "steam.exe", "path": os.path.join(tempfile.gettempdir(), "Steam", "steam.exe")},
            {"pid": 14, "name": "AppleMusic.exe", "path": ""},
            {"pid": os.getpid(), "name": "python.exe", "path": os.path.join(self.plugin, "python.exe")},
            {"pid": 0, "name": "System Idle", "path": os.path.join(self.plugin, "idle")},
        ]
        targets = guard.select_targets(entries, self.roots, protected_pids={os.getpid()})
        self.assertEqual([t["pid"] for t in targets], [10, 11])

    def test_stop_is_a_no_op_off_windows(self):
        lister = mock.Mock(return_value=[{"pid": 10, "name": "x.exe", "path": os.path.join(self.plugin, "x.exe")}])
        with mock.patch.object(guard.os, "name", "posix"):
            self.assertEqual(guard.stop_helpers(self.plugin, lister=lister), {"stopped": [], "remaining": []})
            self.assertEqual(guard.locked_files(self.plugin), [])
        lister.assert_not_called()


class BackendWiringTests(unittest.TestCase):
    def test_backend_cache_root_matches_the_guard(self):
        backend = (ROOT / "mix_backend.py").read_text(encoding="utf-8")
        self.assertIn(f'os.path.join(tempfile.gettempdir(), "{guard.HELPER_CACHE_DIRNAME}")', backend)

    def test_backend_refuses_to_restart_helpers_while_an_update_is_handed_off(self):
        backend = (ROOT / "mix_backend.py").read_text(encoding="utf-8")
        start = backend.index("def _ensure_helper_unlocked")
        body = backend[start:backend.index("def ", start + 10)]
        self.assertLess(body.index("_update_quiescing()"), body.index("_prepare_helper_runtime()"))


if __name__ == "__main__":
    unittest.main()
