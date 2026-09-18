"""Missing-stdlib support for the frozen Python runtime used by Decky Windows.

The finder runs AFTER the normal importers and only recognises an explicit
allowlist. In particular, it never replaces the existing http package or
http.client, never prepends a second standard library to sys.path, and does
not require importlib.abc (which itself has optional dependencies when frozen).
"""

import importlib.util
import os
import sys


class _MissingStdlibFinder:
    _now_playing_stdlib_finder = True

    def __init__(self, plugin_dir):
        self.plugin_dir = os.path.abspath(plugin_dir)
        base = os.path.join(self.plugin_dir, "runtime_stdlib")
        html = os.path.join(self.plugin_dir, "vendor", "html")
        self.paths = {
            "http.server": os.path.join(base, "http_server.py"),
            "socketserver": os.path.join(base, "socketserver.py"),
            "mimetypes": os.path.join(base, "mimetypes.py"),
            "webbrowser": os.path.join(base, "webbrowser.py"),
            "html": os.path.join(html, "__init__.py"),
            "html.entities": os.path.join(html, "entities.py"),
        }

    def find_spec(self, fullname, path=None, target=None):
        filename = self.paths.get(fullname)
        if filename is None or not os.path.isfile(filename):
            return None
        if fullname == "html":
            return importlib.util.spec_from_file_location(
                fullname, filename, submodule_search_locations=[os.path.dirname(filename)]
            )
        return importlib.util.spec_from_file_location(fullname, filename)


def install_stdlib_fallbacks(plugin_dir):
    """Install once per plugin directory; normal/frozen importers have priority."""
    directory = os.path.abspath(plugin_dir)
    for finder in sys.meta_path:
        if (getattr(finder, "_now_playing_stdlib_finder", False)
                and getattr(finder, "plugin_dir", None) == directory):
            return finder
    finder = _MissingStdlibFinder(directory)
    sys.meta_path.append(finder)
    return finder


def active_stdlib_fallbacks():
    """Return names only, without including user directories in diagnostics."""
    loaded = []
    for finder in sys.meta_path:
        if not getattr(finder, "_now_playing_stdlib_finder", False):
            continue
        for name, filename in finder.paths.items():
            module_file = getattr(sys.modules.get(name), "__file__", "")
            if module_file and os.path.abspath(module_file) == os.path.abspath(filename):
                loaded.append(name)
    return sorted(set(loaded))
