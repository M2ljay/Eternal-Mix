"""Install check: manifest format, size and hash comparison, safe mode in main.py."""
import asyncio
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import types
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import mix_install_check as mic  # noqa: E402


def make_plugin(directory: str, files: dict, version="1.0.1", manifest=True) -> None:
    files = dict(files)
    files.setdefault("package.json", json.dumps({"version": version}).encode())
    for rel, body in files.items():
        path = os.path.join(directory, *rel.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as handle:
            handle.write(body)
    if manifest:
        with open(os.path.join(directory, mic.MANIFEST_NAME), "wb") as handle:
            handle.write(mic.encode_manifest(mic.build_manifest("Awesome Mix", version, files.items())))


class ManifestTests(unittest.TestCase):
    def test_round_trip_and_never_lists_itself(self):
        manifest = mic.build_manifest("Awesome Mix", "1.0.1", [("b/c.py", b"x"), ("a.py", b""), (mic.MANIFEST_NAME, b"{}")])
        self.assertEqual([entry["path"] for entry in manifest["files"]], ["a.py", "b/c.py"])
        self.assertEqual(mic.decode_manifest(mic.encode_manifest(manifest)), manifest)

    def test_unsafe_paths_are_refused(self):
        for path in ("../x", "/abs", "a\\b", "C:/x", "a//b", "./a", ""):
            with self.subTest(path), self.assertRaises(mic.ManifestError):
                mic.safe_relative_path(path)

    def test_invalid_manifests(self):
        good = mic.build_manifest("Awesome Mix", "1.0.1", [("a.py", b"x")])
        cases = {
            "not json": b"{",
            "format": mic.encode_manifest(dict(good, format=99)),
            "no files": mic.encode_manifest(dict(good, files=[])),
            "bad hash": mic.encode_manifest(dict(good, files=[dict(good["files"][0], sha256="z" * 64)])),
            "traversal": mic.encode_manifest(dict(good, files=[dict(good["files"][0], path="../a.py")])),
            "duplicate": mic.encode_manifest(dict(good, files=good["files"] * 2)),
        }
        for label, body in cases.items():
            with self.subTest(label), self.assertRaises(mic.ManifestError):
                mic.decode_manifest(body)


class CheckTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="awesome-mix-check-")
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.logged = []
        self.files = {"main.py": b"print(1)\n", "runtime_stdlib/glob.py": b"glob" * 100, "bin/MediaBridge.exe": b"MZ" * 500}

    def check(self):
        return mic.InstallCheck(self.dir, logger=self.logged.append, hash_delay=0)

    def test_complete_install_is_ok(self):
        make_plugin(self.dir, self.files)
        (Path(self.dir) / "__pycache__").mkdir()
        (Path(self.dir) / "__pycache__" / "main.cpython-314.pyc").write_bytes(b"extra files are ignored")
        check = self.check()
        self.assertEqual(check.run_quick()["status"], mic.OK)
        snap = check.hash_now()
        self.assertEqual((snap["status"], snap["phase"], snap["hashedFiles"]), (mic.OK, mic.PHASE_DONE, 4))
        self.assertFalse(snap["safeMode"])

    def test_missing_and_resized_files_are_found_by_the_quick_check(self):
        make_plugin(self.dir, self.files)
        os.remove(os.path.join(self.dir, "runtime_stdlib", "glob.py"))
        Path(self.dir, "bin", "MediaBridge.exe").write_bytes(b"MZ")
        snap = self.check().run_quick()
        self.assertEqual(snap["status"], mic.BROKEN)
        self.assertEqual(snap["problems"], [{"path": "bin/MediaBridge.exe", "kind": "size"},
                                            {"path": "runtime_stdlib/glob.py", "kind": "missing"}])
        self.assertIn("did not finish copying", self.logged[-1])
        self.assertIn("runtime_stdlib/glob.py (missing)", self.logged[-1])

    def test_same_size_old_content_is_found_by_the_hash_check(self):
        make_plugin(self.dir, self.files)
        Path(self.dir, "main.py").write_bytes(b"print(0)\n")
        check = self.check()
        self.assertEqual(check.run_quick()["status"], mic.OK)
        check.start_hashing()
        check._thread.join(10)
        snap = check.snapshot()
        self.assertEqual((snap["status"], snap["problems"]), (mic.BROKEN, [{"path": "main.py", "kind": "hash"}]))

    def test_old_package_json_is_a_version_problem(self):
        make_plugin(self.dir, self.files)
        Path(self.dir, "package.json").write_text(json.dumps({"version": "1.0.0"}))
        snap = self.check().run_quick()
        kinds = {p["path"]: p["kind"] for p in snap["problems"]}
        self.assertEqual(kinds["package.json"], "version")  # same size, older version
        self.assertEqual(snap["status"], mic.BROKEN)

    def test_no_manifest_is_unverified_not_broken(self):
        make_plugin(self.dir, self.files, manifest=False)
        snap = self.check().run_quick()
        self.assertEqual((snap["status"], snap["problemCount"]), (mic.UNVERIFIED, 0))
        self.assertFalse(self.check().start_hashing())

    def test_broken_manifest_is_broken(self):
        make_plugin(self.dir, self.files)
        Path(self.dir, mic.MANIFEST_NAME).write_bytes(b'{"format": 1')
        snap = self.check().run_quick()
        self.assertEqual(snap["status"], mic.BROKEN)
        self.assertTrue(snap["manifestError"])

    def test_cancel_releases_files_promptly(self):
        make_plugin(self.dir, self.files)
        check = mic.InstallCheck(self.dir, logger=self.logged.append, hash_delay=30)
        check.run_quick()
        check.start_hashing()
        check.cancel(timeout=2)
        self.assertFalse(check._thread.is_alive())
        self.assertEqual(check.snapshot()["phase"], mic.PHASE_STOPPED)

    def test_log_goes_to_the_plugin_log_folder(self):
        with mock.patch.dict(os.environ, {"DECKY_PLUGIN_LOG_DIR": self.dir}):
            mic.log("hello")
        self.assertIn("install check: hello", Path(self.dir, mic.LOG_NAME).read_text())

    def test_startup_never_raises(self):
        with mock.patch.object(mic.InstallCheck, "run_quick", side_effect=RuntimeError("boom")), \
                mock.patch.object(mic, "log") as log:
            mic.startup(self.dir)
        self.assertIn("boom", log.call_args[0][0])


class StdlibOnlyTests(unittest.TestCase):
    def test_startup_modules_import_nothing_from_the_plugin(self):
        import ast

        plugin_modules = {p.stem for p in ROOT.glob("*.py")}
        for name in ("mix_install_check.py", "mix_process_guard.py"):
            tree = ast.parse((ROOT / name).read_text(encoding="utf-8"))
            imported = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported |= {alias.name.split(".")[0] for alias in node.names}
                elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                    imported.add(node.module.split(".")[0])
            self.assertFalse(imported & plugin_modules, name)
            self.assertTrue(imported <= set(sys.stdlib_module_names) | {"__future__"}, imported)

    def test_main_imports_the_install_check_before_the_backend(self):
        text = (ROOT / "main.py").read_text(encoding="utf-8")
        self.assertLess(text.index("import mix_install_check"), text.index("import mix_backend"))
        self.assertLess(text.index("mix_install_check.startup("), text.index("import mix_backend"))


def load_main(directory: Path, name: str):
    sys.modules.setdefault("decky_plugin", types.ModuleType("decky_plugin"))
    spec = importlib.util.spec_from_file_location(name, directory / "main.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class SafeModeTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="awesome-mix-main-"))
        self.addCleanup(shutil.rmtree, self.dir, True)
        for name in ("main.py", "mix_install_check.py", "mix_process_guard.py", "package.json"):
            shutil.copy2(ROOT / name, self.dir / name)
        self.env = mock.patch.dict(os.environ, {"DECKY_PLUGIN_LOG_DIR": str(self.dir / "logs")})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.addCleanup(lambda: sys.modules.pop("mix_backend", None))

    def test_broken_backend_import_gives_safe_mode(self):
        (self.dir / "mix_backend.py").write_text("import a_module_the_half_copy_lost\n")
        sys.path.insert(0, str(self.dir))
        self.addCleanup(sys.path.remove, str(self.dir))
        main = load_main(self.dir, "awesome_mix_safe_import")
        self.assertIs(main.Plugin, main.SafeModePlugin)
        self.assertIn("a_module_the_half_copy_lost", main.BACKEND_ERROR)
        snap = asyncio.run(main.Plugin().get_install_check())
        self.assertTrue(snap["safeMode"])
        self.assertIn("safe mode", (self.dir / "logs" / mic.LOG_NAME).read_text())

    def test_backend_that_fails_to_start_becomes_safe_mode(self):
        (self.dir / "mix_backend.py").write_text(
            "class Plugin:\n    def __init__(self):\n        raise OSError('vendor/winrt missing')\n")
        sys.path.insert(0, str(self.dir))
        self.addCleanup(sys.path.remove, str(self.dir))
        main = load_main(self.dir, "awesome_mix_safe_start")
        plugin = main.Plugin()
        self.assertIsInstance(plugin, main.SafeModePlugin)
        self.assertIn("vendor/winrt missing", asyncio.run(plugin.get_install_check())["backendError"])

    def test_unload_chains_to_the_backend_and_stops_helpers(self):
        (self.dir / "mix_backend.py").write_text(
            "calls = []\nclass Plugin:\n    async def _unload(self):\n        calls.append('backend')\n"
            "    def _stop_helpers_for_exit(self, reason, quiesce=0.0):\n        calls.append((reason, quiesce))\n")
        sys.path.insert(0, str(self.dir))
        self.addCleanup(sys.path.remove, str(self.dir))
        main = load_main(self.dir, "awesome_mix_chain")
        backend = sys.modules["mix_backend"]
        with mock.patch.object(main.mix_process_guard, "stop_helpers", return_value={"stopped": [], "remaining": []}) as stop:
            plugin = main.Plugin()
            asyncio.run(plugin._unload())
            self.assertEqual(backend.calls, ["backend"])
            self.assertTrue(stop.called)
            ready = asyncio.run(plugin.prepare_for_plugin_install())
        self.assertEqual(ready, {"ok": True, "stopped": []})
        self.assertEqual(backend.calls[-1], ("update", main.UPDATE_QUIESCE_SECONDS))

    def test_handoff_refused_while_a_helper_runs_from_the_plugin_folder(self):
        (self.dir / "mix_backend.py").write_text("class Plugin:\n    pass\n")
        sys.path.insert(0, str(self.dir))
        self.addCleanup(sys.path.remove, str(self.dir))
        main = load_main(self.dir, "awesome_mix_refuse")
        exe = str(self.dir / "bin" / "MediaBridge.exe")
        remaining = {"stopped": [], "remaining": [{"pid": 7, "name": "MediaBridge.exe", "path": exe}]}
        with mock.patch.object(main.mix_process_guard, "stop_helpers", return_value=remaining):
            answer = asyncio.run(main.Plugin().prepare_for_plugin_install())
        self.assertEqual((answer["ok"], answer["reason"]), (False, "helpers_running"))
        self.assertIn("MediaBridge.exe", answer["message"])


if __name__ == "__main__":
    unittest.main()
