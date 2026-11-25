"""Decky Loader entry point for Awesome Mix.

Kept small on purpose. It runs the install check (mix_install_check) before
anything else and only then imports the backend (mix_backend). If an update
stopped half-way and left the backend unable to import or start, the plugin
still loads in safe mode: the interface can say what happened and offer to
reinstall the current version, instead of Decky logging "Failed to start".

Only stdlib modules that Decky's frozen Windows interpreter bundles are used
here, and only the two stdlib-only modules mix_install_check and
mix_process_guard are imported unguarded.
"""
import asyncio
import os
import sys
import traceback

_PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
if _PLUGIN_DIR not in sys.path:
    sys.path.insert(0, _PLUGIN_DIR)

import mix_install_check  # noqa: E402  (first: works even when other files are missing)
import mix_process_guard  # noqa: E402

INSTALL_CHECK = mix_install_check.startup(_PLUGIN_DIR)

# How long no helper is restarted after an update is handed to Decky. Decky asks
# the user to confirm first; if they cancel, helpers come back after this.
UPDATE_QUIESCE_SECONDS = 120.0

BACKEND_ERROR = ""


def _backend_failed(stage: str, exc: BaseException) -> str:
    global BACKEND_ERROR
    BACKEND_ERROR = f"{stage}: {type(exc).__name__}: {exc}"
    INSTALL_CHECK.set_backend_error(BACKEND_ERROR)
    mix_install_check.log(f"backend could not {stage}; running in safe mode. {BACKEND_ERROR}\n"
                          + "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))[-4000:])
    return BACKEND_ERROR


try:
    import mix_backend as _backend
except Exception as _exc:  # a missing or mismatched module after a half-finished install
    _backend = None
    _backend_failed("load", _exc)


def _current_version() -> str:
    return mix_install_check.read_package_version(_PLUGIN_DIR) or "0.0.0"


class _InstallSupport:
    """Install check and update hand-off callables, shared by the full and safe-mode plugin."""

    async def get_install_check(self) -> dict:
        return INSTALL_CHECK.snapshot()

    def _install_channel(self):
        channel = getattr(self, "_update_channel", None)
        if channel is None:
            import mix_identity
            import mix_update

            decky = sys.modules.get("decky") or sys.modules.get("decky_plugin")
            runtime = (os.environ.get("DECKY_PLUGIN_RUNTIME_DIR", "").strip()
                       or str(getattr(decky, "DECKY_PLUGIN_RUNTIME_DIR", "") or "").strip())
            if not runtime:
                import tempfile
                runtime = os.path.join(tempfile.gettempdir(), "AwesomeMix-runtime")
            channel = mix_update.UpdateChannel(
                mix_identity.VERSION,
                os.path.join(runtime, "plugin-update"),
                mix_update.HttpTransport(mix_identity.USER_AGENT, mix_update.default_cafile(_PLUGIN_DIR)),
                log=mix_install_check.log,
            )
            self._update_channel = channel
        return channel

    async def prepare_plugin_reinstall(self) -> dict:
        """Download and verify the current (or a newer) version for Decky to install again."""
        try:
            channel = self._install_channel()
        except Exception as exc:
            mix_install_check.log(f"reinstall unavailable: {type(exc).__name__}: {exc}")
            return {"ok": False, "reason": "reinstall_unavailable", "current": _current_version(),
                    "message": f"The updater could not load ({type(exc).__name__}: {exc})."}
        executor = getattr(self, "_update_executor", None)
        return await asyncio.get_running_loop().run_in_executor(executor, channel.prepare, True)

    async def prepare_for_plugin_install(self) -> dict:
        """Stop everything that holds files in the plugin folder, right before Decky replaces it.

        Decky v3.3.1 on Windows kills the backend with TerminateProcess (no _unload),
        deletes the folder with shutil.rmtree and extracts with ZipFile.extractall;
        any file still in use stops either step half-way.
        """
        loop = asyncio.get_running_loop()
        INSTALL_CHECK.cancel(timeout=3.0)
        stop = getattr(self, "_stop_helpers_for_exit", None)
        if stop is not None:
            try:
                await loop.run_in_executor(None, stop, "update", UPDATE_QUIESCE_SECONDS)
            except Exception as exc:
                mix_install_check.log(f"backend helper stop failed: {type(exc).__name__}: {exc}")
        guard = await loop.run_in_executor(
            None, lambda: mix_process_guard.stop_helpers(_PLUGIN_DIR, log=mix_install_check.log))
        locked = await loop.run_in_executor(None, mix_process_guard.locked_files, _PLUGIN_DIR)
        blocking = [t for t in guard["remaining"] if mix_process_guard.is_within(t["path"], _PLUGIN_DIR)]
        names = sorted({t["name"] for t in blocking} | set(locked))
        if names:
            message = "Still in use: " + ", ".join(names)
            mix_install_check.log(f"update not handed to Decky. {message}")
            return {"ok": False, "reason": "helpers_running", "message": message, "current": _current_version()}
        mix_install_check.log("helpers stopped; handing the update to Decky")
        return {"ok": True, "stopped": [{"pid": t["pid"], "name": t["name"]} for t in guard["stopped"]]}

    async def _unload(self) -> None:
        parent = getattr(super(), "_unload", None)
        if parent is not None:
            try:
                await parent()
            except Exception as exc:
                mix_install_check.log(f"backend unload failed: {type(exc).__name__}: {exc}")
        INSTALL_CHECK.cancel(timeout=1.0)
        try:
            await asyncio.wait_for(asyncio.get_running_loop().run_in_executor(
                None, lambda: mix_process_guard.stop_helpers(_PLUGIN_DIR, graceful_timeout=0.5, force_timeout=1.5,
                                                             log=mix_install_check.log)), timeout=3.0)
        except Exception as exc:
            mix_install_check.log(f"unload helper stop failed: {type(exc).__name__}: {exc}")


class _SafeModeBase:
    async def _main(self) -> None:
        mix_install_check.log(f"safe mode: only the install check and reinstall are available ({BACKEND_ERROR})")


class SafeModePlugin(_InstallSupport, _SafeModeBase):
    pass


if _backend is not None:
    class Plugin(_InstallSupport, _backend.Plugin):
        def __init__(self) -> None:
            try:
                super().__init__()
            except Exception as exc:
                _backend_failed("start", exc)
                # Same plain Python object layout; keeps Decky's handle on this instance.
                self.__class__ = SafeModePlugin
else:
    Plugin = SafeModePlugin
