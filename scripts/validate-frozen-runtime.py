#!/usr/bin/env python3
"""Reproduce the missing-http.server crash without requiring Decky/Windows.

The child masks only selected *native import resolutions*. It does not inject
fake HTTP classes: main.py must load the real shipped fallback files, and the
existing backend regression suite then runs against those implementations.
"""
from __future__ import annotations

import importlib
import importlib.util
import os
from pathlib import Path
import runpy
import subprocess
import sys
import types

ROOT = Path(__file__).resolve().parents[1]
ALL_MISSING = {"http.server", "socketserver", "mimetypes", "webbrowser", "html", "html.entities"}


def worker(mode: str) -> None:
    import http
    import http.client
    native_http = http
    native_client = http.client
    missing = ALL_MISSING if mode == "full" else {"http.server"} if mode == "server" else set()
    for name in missing:
        sys.modules.pop(name, None)
        if "." in name:
            parent, _, child = name.rpartition(".")
            module = sys.modules.get(parent)
            if module is not None and hasattr(module, child):
                delattr(module, child)
    original = tuple(sys.meta_path)

    class FrozenImportGate:
        def find_spec(self, fullname, path=None, target=None):
            if fullname in missing:
                return None
            for finder in original:
                spec = finder.find_spec(fullname, path, target)
                if spec is not None:
                    return spec
            return None

    sys.meta_path[:] = [FrozenImportGate()]
    if missing:
        try:
            importlib.import_module("http.server")
        except ModuleNotFoundError as exc:
            assert exc.name == "http.server", str(exc)
        else:
            raise AssertionError("Simulation did not reproduce the missing http.server failure")

    sys.modules.setdefault("decky_plugin", types.ModuleType("decky_plugin"))
    spec = importlib.util.spec_from_file_location("np_frozen_backend", ROOT / "main.py")
    backend = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(backend)
    import runtime_compat
    finder = runtime_compat.install_stdlib_fallbacks(str(ROOT))
    assert finder is runtime_compat.install_stdlib_fallbacks(str(ROOT)), "Finder is not idempotent"
    assert sys.modules["http"] is native_http, "Existing http package was replaced"
    assert sys.modules["http.client"] is native_client, "Existing http.client was replaced"
    loaded = set(runtime_compat.active_stdlib_fallbacks())
    if mode == "native":
        assert not loaded, loaded
    else:
        assert missing.issubset(loaded), (missing, loaded)
    assert finder.find_spec("unrelated_module") is None
    print(f"PASS {mode}: startup; fallbacks={','.join(sorted(loaded)) or 'none'}; native http.client preserved", flush=True)

    # Real backend smoke tests, including HTTP Range requests and vendor imports.
    runpy.run_path(str(ROOT / "scripts" / "validate-runtime.py"), run_name="__main__")
    runpy.run_path(str(ROOT / "scripts" / "validate-loopback.py"), run_name="__main__")
    print(f"PASS {mode}: all existing backend regression tests and loopback lifecycle tests", flush=True)


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--worker":
        worker(sys.argv[2])
    else:
        environment = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
        for mode in ("native", "server", "full"):
            subprocess.run([sys.executable, __file__, "--worker", mode], check=True, timeout=100, env=environment)
        print("Frozen-runtime regression suite OK (native, missing http.server, missing full optional chain).")
