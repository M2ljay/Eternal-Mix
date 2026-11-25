"""Make this interpreter import like Decky Loader's frozen Windows Python.

tests/decky_frozen_stdlib.txt lists the standard-library modules bundled in
Decky v3.3.1's PluginLoader.exe as Playhub ships it (PYZ archive,
base_library.zip and top-level extension modules), copied from decky-nexus.
Modules compiled into python3XX.dll on Windows are not files there, so they are
not in that list; WINDOWS_BUILTINS adds them back.

``install(listing)`` hides every other stdlib module. A hidden module is still
importable when the plugin ships its own copy: runtime_compat's finder
(runtime_stdlib/) or a plain path entry outside the stdlib folders (vendor/).
This is stricter than the frozen interpreter only where the host's own stdlib
differs, never looser for anything the plugin imports.
"""
from __future__ import annotations

import importlib.abc
import importlib.machinery
import os
import sys
import sysconfig

# Built into python3XX.dll in CPython's Windows builds (PC/config.c), so absent
# from the file listing yet always importable.
WINDOWS_BUILTINS = frozenset({
    "_abc", "_ast", "_bisect", "_blake2", "_codecs", "_codecs_cn", "_codecs_hk", "_codecs_iso2022",
    "_codecs_jp", "_codecs_kr", "_codecs_tw", "_collections", "_contextvars", "_csv", "_datetime",
    "_functools", "_heapq", "_hmac", "_imp", "_interpchannels", "_interpqueues", "_interpreters", "_io", "_json",
    "_locale", "_lsprof", "_md5", "_multibytecodec", "_opcode", "_operator", "_pickle", "_random",
    "_sha1", "_sha2", "_sha3", "_signal", "_sre", "_stat", "_statistics", "_string", "_struct",
    "_suggestions", "_symtable", "_sysconfig", "_thread", "_tokenize", "_tracemalloc", "_typing",
    "_warnings", "_weakref", "_winapi", "array", "atexit", "binascii", "builtins", "cmath", "errno",
    "faulthandler", "gc", "itertools", "marshal", "math", "mmap", "msvcrt", "nt", "sys", "time",
    "winreg", "xxsubtype", "zlib",
})

# POSIX counterparts of nt/msvcrt/_winapi that the host interpreter itself needs.
# Plugin code reaching them would fail on Windows anyway (no such module there).
HOST_PLATFORM_MODULES = frozenset({
    "posix", "pwd", "grp", "fcntl", "termios", "resource", "syslog", "_posixsubprocess", "_posixshmem",
    "select", "_scproxy", "posixpath",
})

# Extension modules some hosts compile in statically that Decky's build lacks.
NOT_IN_DECKY = frozenset({"pyexpat", "_elementtree", "_zoneinfo", "readline", "_curses", "_curses_panel",
                          "_dbm", "_gdbm", "_tkinter", "_testinternalcapi", "_testcapi", "_xxtestfuzz"})

ALWAYS = frozenset({"collections.abc", "os.path"})  # registered at import time, not files


def _plugin_fallback_spec(name, path, target):
    for finder in list(sys.meta_path):
        if getattr(finder, "_now_playing_stdlib_finder", False):
            spec = finder.find_spec(name, path, target)
            if spec is not None:
                return spec
    return None


class FrozenStdlibGate(importlib.abc.MetaPathFinder):
    _frozen_stdlib_gate = True

    def __init__(self, available):
        self.available = frozenset(available) | WINDOWS_BUILTINS | ALWAYS
        self.stdlib = frozenset(sys.stdlib_module_names)
        self.stdlib_dirs = tuple(
            os.path.realpath(sysconfig.get_path(key)) + os.sep for key in ("stdlib", "platstdlib")
        )
        self.blocked = []

    def allowed(self, name: str) -> bool:
        top = name.split(".")[0]
        if top not in self.stdlib and name not in NOT_IN_DECKY:
            return True
        if name in NOT_IN_DECKY:
            return name in self.available
        return name in self.available or name in HOST_PLATFORM_MODULES or name.startswith("encodings.")

    def _outside_stdlib(self, origin: str) -> bool:
        real = os.path.realpath(origin)
        return not real.startswith(self.stdlib_dirs) or (os.sep + "site-packages" + os.sep) in real

    def find_spec(self, name, path=None, target=None):
        if self.allowed(name):
            return None
        spec = _plugin_fallback_spec(name, path, target)
        if spec is not None:
            return spec
        # A copy the plugin ships on sys.path (vendor/...), not the host's stdlib.
        # (A package __path__ the plugin extended with vendor/ counts; its stdlib part does not.)
        search = [p for p in (sys.path if path is None else path) if p and self._outside_stdlib(p)]
        spec = importlib.machinery.PathFinder.find_spec(name, search)
        if spec is not None and spec.origin and self._outside_stdlib(spec.origin):
            return spec
        self.blocked.append(name)
        raise ModuleNotFoundError(f"No module named {name!r} (not in Decky's frozen runtime)", name=name)


def install(listing: str, keep=()) -> FrozenStdlibGate:
    """Hide every stdlib module Decky's frozen Python lacks. ``keep``: already-imported
    harness modules (unittest, runpy, ...) that stay loaded for the caller only."""
    with open(listing, "r", encoding="utf-8") as handle:
        available = {line.strip() for line in handle if line.strip() and not line.startswith("#")}
    gate = FrozenStdlibGate(available)
    keep = set(keep)
    for name in list(sys.modules):
        if name in keep or name.split(".")[0] in keep:
            continue
        if name in sys.builtin_module_names and name not in NOT_IN_DECKY:
            continue
        if not gate.allowed(name):
            sys.modules.pop(name, None)
            parent, _, child = name.rpartition(".")
            if parent and parent in sys.modules and getattr(sys.modules[parent], child, None) is not None:
                try:
                    delattr(sys.modules[parent], child)
                except AttributeError:
                    pass
    sys.meta_path.insert(0, gate)
    return gate
