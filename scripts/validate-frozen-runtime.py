#!/usr/bin/env python3
"""Reproduce Decky's frozen Windows runtime without requiring Decky/Windows.

Modes, each in a fresh interpreter:

* native  - nothing hidden.
* server  - only http.server hidden (older Decky builds).
* full    - the optional chain older builds lacked (http.server, socketserver,
            mimetypes, webbrowser, html).
* decky   - every stdlib module not in tests/decky_frozen_stdlib.txt hidden
            (Decky v3.3.1 as Playhub ships it; tests/frozen_gate.py).

The child masks only *native import resolutions*. It does not inject fake
classes: main.py must load the real shipped fallback files, and the existing
backend regression suites then run against those implementations. Every mode
also requires main.py to load the full backend, not its safe mode.
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
MODES = ("native", "server", "full", "decky")
# Imported by the regression scripts themselves (not by the plugin): kept loaded under the decky gate.
HARNESS = {"runpy", "subprocess", "types", "importlib", "pathlib"}


def decky_worker() -> None:
    sys.path.insert(0, str(ROOT / "tests"))
    import frozen_gate
    import tempfile

    work = tempfile.mkdtemp(prefix="awesome-mix-frozen-")
    os.environ["DECKY_PLUGIN_LOG_DIR"] = work
    for name in HARNESS:
        importlib.import_module(name)
    gate = frozen_gate.install(str(ROOT / "tests" / "decky_frozen_stdlib.txt"), keep=HARNESS | {"frozen_gate"})
    sys.modules.setdefault("decky_plugin", types.ModuleType("decky_plugin"))
    spec = importlib.util.spec_from_file_location("np_frozen_entry", ROOT / "main.py")
    entry = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(entry)
    assert entry.BACKEND_ERROR == "", entry.BACKEND_ERROR
    import runtime_compat
    loaded = set(runtime_compat.active_stdlib_fallbacks())
    print(f"PASS decky: startup; fallbacks={','.join(sorted(loaded)) or 'none'}", flush=True)
    runpy.run_path(str(ROOT / "scripts" / "validate-runtime.py"), run_name="__main__")
    runpy.run_path(str(ROOT / "scripts" / "validate-loopback.py"), run_name="__main__")
    hidden = sorted(set(gate.blocked) - {"html.parser", "_elementtree"})
    assert not hidden, f"plugin code asked for modules Decky lacks and found no fallback: {hidden}"
    print("PASS decky: all existing backend regression tests and loopback lifecycle tests", flush=True)


def worker(mode: str) -> None:
    if mode == "decky":
        decky_worker()
        return
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
    spec = importlib.util.spec_from_file_location("np_frozen_entry", ROOT / "main.py")
    entry = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(entry)
    assert entry.BACKEND_ERROR == "", f"main.py fell back to safe mode: {entry.BACKEND_ERROR}"
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
        for mode in MODES:
            subprocess.run([sys.executable, __file__, "--worker", mode], check=True, timeout=300, env=environment)
        print("Frozen-runtime regression suite OK (native, missing http.server, missing full optional chain, "
              "Decky v3.3.1 frozen stdlib).")
