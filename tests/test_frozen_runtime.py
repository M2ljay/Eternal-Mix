"""Awesome Mix must import under Decky's frozen Windows Python.

decky_frozen_stdlib.txt lists the standard-library modules Decky v3.3.1's
PluginLoader.exe bundles (as Playhub ships it). Each check runs in a fresh
interpreter where every other stdlib module is hidden (frozen_gate.py), while
the plugin's own fallbacks (runtime_stdlib/ through runtime_compat, vendor/)
still resolve, exactly as on the real runtime.

scripts/validate-frozen-runtime.py runs the backend's behaviour suites under
the same gate.
"""
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
LISTING = os.path.join(HERE, "decky_frozen_stdlib.txt")

PRELUDE = textwrap.dedent(r'''
    import os, sys, types
    root, here, work = sys.argv[1], sys.argv[2], sys.argv[3]
    sys.path.insert(0, here)
    import frozen_gate
    gate = frozen_gate.install(os.path.join(here, "decky_frozen_stdlib.txt"), keep={"frozen_gate"})
    decky = types.ModuleType("decky")
    class _Log:
        def __getattr__(self, _):
            return lambda *a, **k: None
    decky.logger = _Log()
    for attr in ("DECKY_PLUGIN_SETTINGS_DIR", "DECKY_PLUGIN_RUNTIME_DIR", "DECKY_PLUGIN_LOG_DIR", "DECKY_HOME",
                 "DECKY_USER_HOME"):
        value = os.path.join(work, attr)
        os.makedirs(value, exist_ok=True)
        setattr(decky, attr, value)
        os.environ[attr] = value
    decky.DECKY_PLUGIN_DIR = root
    decky.emit = lambda *a, **k: None
    sys.modules["decky"] = decky
    sys.modules["decky_plugin"] = decky
    import importlib.util
    spec = importlib.util.spec_from_file_location("_", os.path.join(root, "main.py"))
    main = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(main)
''')

FULL = PRELUDE + textwrap.dedent(r'''
    assert main.BACKEND_ERROR == "", main.BACKEND_ERROR
    assert main.Plugin is not main.SafeModePlugin
    import mix_backend, ytmusic_service, mix_update, legacy_migration
    # What the backend imports lazily: the YouTube Music stack (vendor/) and tags.
    service = ytmusic_service.YouTubeMusicService(os.path.join(work, "yt"), os.path.join(root, "vendor"),
                                                  lambda *_: None)
    service._ensure_imports()
    import yt_dlp
    from yt_dlp.extractor.youtube import YoutubeIE  # noqa: F401
    from ytmusicapi import YTMusic, setup  # noqa: F401
    from ytmusicapi.helpers import get_authorization  # noqa: F401
    import websocket  # noqa: F401
    from tinytag import TinyTag  # noqa: F401
    import html.parser, xml.etree.ElementTree  # noqa: F401  (vendored copies)
    import runtime_compat
    print("fallbacks:", ",".join(runtime_compat.active_stdlib_fallbacks()))
    print("frozen import OK")
''')

# A half-copied install: the manifest describes the new version, one deep file is gone.
HALF_COPIED = PRELUDE + textwrap.dedent(r'''
    assert main.Plugin is main.SafeModePlugin, main.Plugin
    assert "webbrowser" in main.BACKEND_ERROR, main.BACKEND_ERROR
    snap = main.INSTALL_CHECK.snapshot()
    assert snap["status"] == "broken" and snap["safeMode"], snap
    assert {"path": "runtime_stdlib/webbrowser.py", "kind": "missing"} in snap["problems"], snap
    import asyncio
    plugin = main.Plugin()
    answer = asyncio.run(plugin.get_install_check())
    assert answer["safeMode"] and answer["problemCount"] == 1, answer
    for name in ("prepare_plugin_reinstall", "prepare_for_plugin_install", "_main", "_unload"):
        assert callable(getattr(plugin, name)), name
    asyncio.run(plugin._main())
    asyncio.run(plugin._unload())
    log = open(os.path.join(work, "DECKY_PLUGIN_LOG_DIR", "NowPlaying.log"), encoding="utf-8").read()
    assert "runtime_stdlib/webbrowser.py (missing)" in log, log
    assert "safe mode" in log, log
    print("safe mode OK")
''')


def run_child(code: str, root: str, work: str) -> subprocess.CompletedProcess:
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    for key in list(env):
        if key.startswith("DECKY_"):
            env.pop(key)
    return subprocess.run([sys.executable, "-I", "-c", code, root, HERE, work],
                          capture_output=True, text=True, timeout=300, env=env)


class FrozenRuntimeImport(unittest.TestCase):
    def setUp(self):
        self.work = tempfile.mkdtemp(prefix="awesome-mix-frozen-")
        self.addCleanup(shutil.rmtree, self.work, True)

    def test_backend_and_lazy_imports_with_only_deckys_stdlib(self):
        result = run_child(FULL, ROOT, self.work)
        self.assertEqual(result.returncode, 0, result.stderr[-4000:] + result.stdout[-2000:])
        self.assertIn("frozen import OK", result.stdout)

    def test_half_copied_install_reports_and_falls_back_to_safe_mode(self):
        sys.path.insert(0, ROOT)
        try:
            import mix_install_check
        finally:
            sys.path.remove(ROOT)
        plugin = os.path.join(self.work, "Awesome Mix")
        os.makedirs(plugin)
        names = [n for n in os.listdir(ROOT) if n.endswith(".py") or n in ("package.json", "plugin.json")]
        for name in names:
            shutil.copy2(os.path.join(ROOT, name), plugin)
        shutil.copytree(os.path.join(ROOT, "runtime_stdlib"), os.path.join(plugin, "runtime_stdlib"))
        files = []
        for folder, _dirs, filenames in os.walk(plugin):
            for filename in filenames:
                path = os.path.join(folder, filename)
                with open(path, "rb") as handle:
                    files.append((os.path.relpath(path, plugin).replace(os.sep, "/"), handle.read()))
        manifest = mix_install_check.build_manifest("Awesome Mix", mix_install_check.read_package_version(ROOT), files)
        with open(os.path.join(plugin, mix_install_check.MANIFEST_NAME), "wb") as handle:
            handle.write(mix_install_check.encode_manifest(manifest))
        os.remove(os.path.join(plugin, "runtime_stdlib", "webbrowser.py"))
        result = run_child(HALF_COPIED, plugin, self.work)
        self.assertEqual(result.returncode, 0, result.stderr[-4000:] + result.stdout[-2000:])
        self.assertIn("safe mode OK", result.stdout)


if __name__ == "__main__":
    unittest.main()
