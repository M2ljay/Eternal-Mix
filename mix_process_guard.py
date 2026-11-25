"""Stop the helper processes Awesome Mix started, before Decky replaces the plugin folder.

Decky Loader (v3.3.1, Windows) stops a plugin's backend with
``Process.terminate()`` (TerminateProcess), so ``_unload`` never runs and any
helper the backend started keeps running and keeps its files open. Windows then
refuses to delete or overwrite them and the install stops half-way.

Scope is strict: only processes whose executable lives under one of the given
roots (the plugin folder and the plugin's own temp copies) are touched, the
image path is re-read through the very handle used to terminate (so a recycled
PID can never hit another program), and the current process and its parent are
never touched.

Stdlib only (os, tempfile, time, ctypes), like mix_install_check: main.py uses
it even when the rest of the plugin failed to import.
"""
from __future__ import annotations

import os
import tempfile
import time

# Temp-folder copy of bin/ that the backend runs MediaBridge and ThumbnailBridge
# from (Plugin.helper_cache_root in mix_backend.py).
HELPER_CACHE_DIRNAME = "NowPlaying-MediaBridge"

_PROCESS_TERMINATE = 0x0001
_SYNCHRONIZE = 0x00100000
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_WAIT_OBJECT_0 = 0x00000000
_WM_CLOSE = 0x0010
_TH32CS_SNAPPROCESS = 0x00000002


def helper_roots(plugin_dir: str) -> list:
    """Folders whose executables belong to Awesome Mix."""
    return [os.path.abspath(plugin_dir), os.path.join(tempfile.gettempdir(), HELPER_CACHE_DIRNAME)]


def _norm(path: str) -> str:
    return os.path.normcase(os.path.normpath(os.path.abspath(path)))


def is_within(path: str, root: str) -> bool:
    """True if ``path`` is ``root`` or inside it (case-insensitive on Windows, no prefix tricks)."""
    if not path or not root:
        return False
    try:
        full, base = _norm(path), _norm(root)
    except (TypeError, ValueError):
        return False
    return full == base or full.startswith(base.rstrip(os.sep) + os.sep)


def in_scope(path: str, roots) -> bool:
    return any(is_within(path, root) for root in roots)


def select_targets(entries, roots, protected_pids=()) -> list:
    """Entries ``{pid, name, path}`` whose executable lives under ``roots``."""
    protected = {int(pid) for pid in protected_pids if pid}
    targets = []
    for entry in entries:
        pid = int(entry.get("pid") or 0)
        path = str(entry.get("path") or "")
        if pid > 0 and pid not in protected and in_scope(path, roots):
            targets.append({"pid": pid, "name": str(entry.get("name") or os.path.basename(path)), "path": path})
    return targets


def _protected_pids() -> set:
    pids = {os.getpid()}
    try:
        pids.add(os.getppid())
    except Exception:
        pass
    return pids


# -- Windows -------------------------------------------------------------------

def _kernel32():
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel32.TerminateProcess.restype = wintypes.BOOL
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                                    ctypes.POINTER(wintypes.DWORD)]
    kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    return kernel32


def _image_path(kernel32, handle) -> str:
    import ctypes
    from ctypes import wintypes

    size = wintypes.DWORD(32768)
    buffer = ctypes.create_unicode_buffer(size.value)
    if kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
        return str(buffer.value or "")
    return ""


def list_processes() -> list:
    """``[{pid, name, path}]`` for every process whose image path can be read. Empty off Windows."""
    if os.name != "nt":
        return []
    import ctypes
    from ctypes import wintypes

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD), ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", ctypes.c_long), ("dwFlags", wintypes.DWORD), ("szExeFile", ctypes.c_wchar * 260),
        ]

    kernel32 = _kernel32()
    kernel32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
    kernel32.Process32FirstW.restype = wintypes.BOOL
    kernel32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
    kernel32.Process32NextW.restype = wintypes.BOOL
    snapshot = kernel32.CreateToolhelp32Snapshot(_TH32CS_SNAPPROCESS, 0)
    if not snapshot or snapshot == ctypes.c_void_p(-1).value:
        return []
    entries = []
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        more = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
        while more:
            pid = int(entry.th32ProcessID or 0)
            if pid > 0:
                handle = kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
                if handle:
                    try:
                        path = _image_path(kernel32, handle)
                    finally:
                        kernel32.CloseHandle(handle)
                    if path:
                        entries.append({"pid": pid, "name": str(entry.szExeFile or ""), "path": path})
            entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
            more = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snapshot)
    return entries


def _post_close(pids) -> None:
    """Ask windows owned by ``pids`` to close (WM_CLOSE). Helpers without windows just ignore it."""
    import ctypes
    from ctypes import wintypes

    wanted = {int(pid) for pid in pids}
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]

    def visit(hwnd, _lparam):
        owner = wintypes.DWORD(0)
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if int(owner.value) in wanted:
            user32.PostMessageW(hwnd, _WM_CLOSE, 0, 0)
        return True

    user32.EnumWindows(callback_type(visit), 0)


def _wait_exit(kernel32, pid: int, timeout: float) -> bool:
    handle = kernel32.OpenProcess(_SYNCHRONIZE, False, int(pid))
    if not handle:
        return True  # gone (or not ours to wait on); the final listing decides
    try:
        return kernel32.WaitForSingleObject(handle, max(0, int(timeout * 1000))) == _WAIT_OBJECT_0
    finally:
        kernel32.CloseHandle(handle)


def _terminate_in_scope(kernel32, pid: int, roots, timeout: float) -> bool:
    access = _PROCESS_TERMINATE | _SYNCHRONIZE | _PROCESS_QUERY_LIMITED_INFORMATION
    handle = kernel32.OpenProcess(access, False, int(pid))
    if not handle:
        return False
    try:
        # Re-read the image through this handle: the PID may have been recycled.
        if not in_scope(_image_path(kernel32, handle), roots):
            return False
        kernel32.TerminateProcess(handle, 1)
        return kernel32.WaitForSingleObject(handle, max(1, int(timeout * 1000))) == _WAIT_OBJECT_0
    finally:
        kernel32.CloseHandle(handle)


def stop_helpers(plugin_dir: str, graceful_timeout: float = 2.5, force_timeout: float = 3.0,
                 log=lambda _message: None, lister=list_processes) -> dict:
    """Close, then terminate, every process running from the plugin folder or its temp copies.

    Returns ``{"stopped": [...], "remaining": [...]}`` (entries ``{pid, name, path}``).
    Never raises; off Windows there is nothing to stop.
    """
    roots = helper_roots(plugin_dir)
    result = {"stopped": [], "remaining": []}
    if os.name != "nt":
        return result
    try:
        targets = select_targets(lister(), roots, _protected_pids())
        if not targets:
            return result
        log("stopping helper processes: " + ", ".join(f"{t['name']} pid={t['pid']}" for t in targets))
        kernel32 = _kernel32()
        try:
            _post_close([t["pid"] for t in targets])
        except Exception as exc:
            log(f"WM_CLOSE failed: {exc}")
        deadline = time.monotonic() + max(0.0, graceful_timeout)
        pending = list(targets)
        while pending and time.monotonic() < deadline:
            pending = [t for t in pending if not _wait_exit(kernel32, t["pid"], 0.1)]
        for target in pending:
            if not _terminate_in_scope(kernel32, target["pid"], roots, force_timeout):
                log(f"could not terminate {target['name']} pid={target['pid']}")
        alive = {t["pid"] for t in select_targets(lister(), roots, _protected_pids())}
        for target in targets:
            (result["remaining"] if target["pid"] in alive else result["stopped"]).append(target)
        if result["remaining"]:
            log("still running: " + ", ".join(f"{t['name']} pid={t['pid']} ({t['path']})" for t in result["remaining"]))
    except Exception as exc:
        log(f"helper stop failed: {type(exc).__name__}: {exc}")
    return result


def locked_files(plugin_dir: str, folders=("bin",), suffixes=(".exe", ".dll")) -> list:
    """Relative paths of executables in ``folders`` that another process still has open.

    A running image cannot be opened for writing on Windows, so this is the same
    refusal Decky's ZipFile.extractall would meet. Off Windows: always empty.
    """
    if os.name != "nt":
        return []
    locked = []
    for folder in folders:
        base = os.path.join(plugin_dir, folder)
        try:
            names = sorted(os.listdir(base))
        except OSError:
            continue
        for name in names:
            path = os.path.join(base, name)
            if not name.lower().endswith(tuple(suffixes)) or not os.path.isfile(path):
                continue
            try:
                with open(path, "r+b"):
                    pass
            except PermissionError:
                locked.append(f"{folder}/{name}")
            except OSError:
                continue
    return locked
