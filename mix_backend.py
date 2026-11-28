"""Awesome Mix backend. Decky loads main.py, which runs the install check first and
then imports this module; see main.py for why the two are separate."""
# Decky Windows can omit optional stdlib modules from its frozen interpreter.
# Install the narrow fallback before importing http.server or its dependencies.
import os
import sys

_PLUGIN_SOURCE_DIR = os.path.dirname(os.path.abspath(__file__))
if _PLUGIN_SOURCE_DIR not in sys.path:
    sys.path.insert(0, _PLUGIN_SOURCE_DIR)
from runtime_compat import active_stdlib_fallbacks, install_stdlib_fallbacks

install_stdlib_fallbacks(_PLUGIN_SOURCE_DIR)

import csv
import ctypes
import hashlib
import io
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import urllib.request
import urllib.error
import unicodedata
import http.server
import http.client
import ssl
import webbrowser
import asyncio
import base64
import random
import queue
import mimetypes
import secrets
import socket
import struct
from typing import Any, Dict, List, Optional, Set, Tuple
from concurrent.futures import Future, ThreadPoolExecutor, wait
from collections import deque
from datetime import datetime
from pathlib import Path

import decky_plugin

from ytmusic_service import YouTubeMusicService
import mix_speed_dial

# The error a request returns when YouTube Music answers in a shape ytmusicapi cannot read
# (the frontend shows its translation, src/i18n.ts, never the parser's text).
YOUTUBE_MUSIC_UNEXPECTED_RESPONSE = "YouTube Music returned an unexpected response"
from legacy_migration import migrate_legacy_settings
import mix_identity
import mix_soundtrack
import mix_update

TOPBAR_CEF_PORT = 8080
# Music sources. Apple Music is followed through its Windows media session (SMTC).
SUPPORTED_SERVICES = ("localMusic", "youtubeMusic", "appleMusic")
TOPBAR_BADGE_ID = "decky-nowplaying-topbar-badge"
TOPBAR_STYLE_ID = "decky-nowplaying-topbar-style"
TOPBAR_WEATHER_BADGE_ID = "decky-weather-topbar-badge"
TOPBAR_CLOCK_SELECTORS = [
    "#header ._1HhLUvHH6BZLIOyOE80TVh", "._1HhLUvHH6BZLIOyOE80TVh",
    '#header [class*="Clock"]', "#header time",
]
TOPBAR_REINJECT_SECONDS = 0.75
TOPBAR_FORCE_REINJECT_SECONDS = 2.0
TOPBAR_IDLE_REINJECT_SECONDS = 30.0
TOPBAR_MAX_CHARS = 28


if os.name == "nt":
    class _PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", ctypes.c_ulong),
            ("cntUsage", ctypes.c_ulong),
            ("th32ProcessID", ctypes.c_ulong),
            ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", ctypes.c_ulong),
            ("cntThreads", ctypes.c_ulong),
            ("th32ParentProcessID", ctypes.c_ulong),
            ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", ctypes.c_ulong),
            ("szExeFile", ctypes.c_wchar * 260),
        ]
else:
    _PROCESSENTRY32W = None


def clamp_value(value: Optional[int], minimum: int, maximum: int) -> int:
    try:
        numeric = int(value if value is not None else minimum)
    except Exception:
        numeric = minimum
    return max(minimum, min(maximum, numeric))


class LocalMusicPlayerWorker:
    """Persistent Windows Media Player COM host for the local library.

    The object lives on one dedicated COM thread. Decky callables communicate with it
    through a small request queue, so no COM proxy crosses Python executor threads.
    """

    PLAYING_STATES = {3}
    PAUSED_STATES = {2}
    ENDED_STATES = {8}

    def __init__(self, logger, vendor_path: str = "") -> None:
        self._logger = logger
        self._vendor_path = str(vendor_path or "")
        self._commands: "queue.Queue[Any]" = queue.Queue()
        self._thread = threading.Thread(target=self._run, name="NowPlaying-LocalMusic", daemon=True)
        self._ready = threading.Event()
        self._running = True
        self._startup_error = ""
        self._thread.start()
        self._ready.wait(timeout=5.0)

    def _log(self, message: str) -> None:
        try:
            self._logger(message)
        except Exception:
            pass

    def call(self, command: str, *args: Any, timeout: float = 3.0) -> Any:
        if not self._running:
            raise RuntimeError("Local music player is not running")
        response: "queue.Queue[Any]" = queue.Queue(maxsize=1)
        self._commands.put((command, args, response))
        try:
            ok, value = response.get(timeout=timeout)
        except queue.Empty:
            raise RuntimeError("Local music player did not respond")
        if ok:
            return value
        raise RuntimeError(str(value or "Local music player error"))

    def close(self) -> None:
        if not self._running:
            return
        try:
            self.call("shutdown", timeout=1.0)
        except Exception:
            self._running = False

    def _run(self) -> None:
        player = None
        comtypes_module = None
        playlist: List[Dict[str, Any]] = []
        index = -1
        shuffle_enabled = False
        repeat_mode = "None"
        volume = 100
        last_state = 0

        def current_track() -> Optional[Dict[str, Any]]:
            if 0 <= index < len(playlist):
                return playlist[index]
            return None

        def set_track(new_index: int, auto_play: bool = True) -> None:
            nonlocal index
            if not playlist:
                index = -1
                return
            index = max(0, min(int(new_index), len(playlist) - 1))
            track = current_track() or {}
            path = str(track.get("path") or "")
            if not path or not os.path.isfile(path):
                raise RuntimeError("Local music file is unavailable")
            loaded_with_media = False
            try:
                media = player.newMedia(path)
                player.currentMedia = media
                loaded_with_media = True
            except Exception as exc:
                self._log(f"local player newMedia fallback for {path}: {exc}")
                player.URL = path
            try:
                player.settings.volume = int(volume)
            except Exception:
                pass
            if auto_play:
                player.controls.play()
                # A few Windows Media Player builds accept currentMedia but do not
                # actually open it until URL is assigned. Retry through URL when the
                # ActiveX state remains undefined/stopped immediately after play().
                if loaded_with_media:
                    try:
                        time.sleep(0.06)
                        state = int(player.playState)
                        if state in {0, 1, 10}:
                            player.URL = path
                            player.controls.play()
                    except Exception:
                        try:
                            player.URL = path
                            player.controls.play()
                        except Exception:
                            pass

        def choose_next(direction: int = 1) -> int:
            if not playlist:
                return -1
            if shuffle_enabled and len(playlist) > 1 and direction > 0:
                choices = [value for value in range(len(playlist)) if value != index]
                return random.choice(choices) if choices else index
            candidate = index + direction
            if candidate >= len(playlist):
                return 0 if repeat_mode == "All" else len(playlist) - 1
            if candidate < 0:
                return len(playlist) - 1 if repeat_mode == "All" else 0
            return candidate

        def state_payload() -> Dict[str, Any]:
            track = current_track()
            play_state = 0
            position_ms = 0
            duration_ms = int((track or {}).get("duration_ms") or 0)
            try:
                play_state = int(player.playState)
            except Exception:
                pass
            try:
                position_ms = max(0, int(float(player.controls.currentPosition or 0.0) * 1000))
            except Exception:
                pass
            try:
                media_duration = float(getattr(player.currentMedia, "duration", 0.0) or 0.0)
                if media_duration > 0:
                    duration_ms = int(media_duration * 1000)
            except Exception:
                pass
            status = "Playing" if play_state in self.PLAYING_STATES else "Paused" if play_state in self.PAUSED_STATES else "Stopped"
            return {
                "ok": True,
                "track": track,
                "index": index,
                "queueLength": len(playlist),
                "status": status,
                "position": position_ms,
                "length": duration_ms,
                "volume": int(volume),
                "shuffleActive": bool(shuffle_enabled),
                "repeatMode": repeat_mode,
                "canPrevious": bool(playlist),
                "canNext": bool(playlist),
            }

        try:
            if self._vendor_path and os.path.isdir(self._vendor_path) and self._vendor_path not in sys.path:
                sys.path.insert(0, self._vendor_path)
            import comtypes as comtypes_module  # type: ignore
            import comtypes.client  # type: ignore
            comtypes_module.CoInitialize()
            player = comtypes.client.CreateObject("WMPlayer.OCX")
            try:
                player.settings.autoStart = True
                player.settings.volume = volume
            except Exception:
                pass
        except Exception as exc:
            self._startup_error = str(exc)
            self._log(f"local music COM startup error: {exc}")
        finally:
            self._ready.set()

        while self._running:
            try:
                command, args, response = self._commands.get(timeout=0.12)
            except queue.Empty:
                command = None
                args = ()
                response = None

            if command is not None:
                try:
                    if self._startup_error or player is None:
                        raise RuntimeError(self._startup_error or "Windows Media Player COM is unavailable")
                    if command == "shutdown":
                        self._running = False
                        try:
                            player.controls.stop()
                        except Exception:
                            pass
                        result = True
                    elif command == "play_items":
                        incoming = args[0] if args else []
                        playlist = [dict(item) for item in incoming if isinstance(item, dict) and item.get("path")]
                        index = max(0, min(int(args[1] if len(args) > 1 else 0), max(0, len(playlist) - 1))) if playlist else -1
                        if playlist:
                            set_track(index, True)
                        result = state_payload()
                    elif command == "play_pause":
                        play_state = int(player.playState)
                        if play_state in self.PLAYING_STATES:
                            player.controls.pause()
                        else:
                            if current_track() is None and playlist:
                                set_track(max(0, index), True)
                            else:
                                player.controls.play()
                        result = state_payload()
                    elif command == "next":
                        if playlist:
                            set_track(choose_next(1), True)
                        result = state_payload()
                    elif command == "previous":
                        if playlist:
                            try:
                                if float(player.controls.currentPosition or 0.0) > 4.0:
                                    player.controls.currentPosition = 0.0
                                else:
                                    set_track(choose_next(-1), True)
                            except Exception:
                                set_track(choose_next(-1), True)
                        result = state_payload()
                    elif command == "set_volume":
                        volume = max(0, min(100, int(args[0] if args else 100)))
                        player.settings.volume = volume
                        result = state_payload()
                    elif command == "set_shuffle":
                        shuffle_enabled = bool(args[0] if args else False)
                        result = state_payload()
                    elif command == "set_repeat":
                        requested = str(args[0] if args else "None")
                        repeat_mode = requested if requested in {"None", "One", "All"} else "None"
                        result = state_payload()
                    elif command == "get_state":
                        result = state_payload()
                    elif command == "stop":
                        player.controls.stop()
                        result = state_payload()
                    else:
                        raise RuntimeError(f"Unknown local music command: {command}")
                    if response is not None:
                        response.put((True, result))
                except Exception as exc:
                    if response is not None:
                        response.put((False, str(exc)))

            if player is not None and playlist:
                try:
                    current_state = int(player.playState)
                    if current_state in self.ENDED_STATES and last_state not in self.ENDED_STATES:
                        if repeat_mode == "One":
                            set_track(index, True)
                        elif index < len(playlist) - 1 or repeat_mode == "All" or shuffle_enabled:
                            set_track(choose_next(1), True)
                    last_state = current_state
                except Exception:
                    pass

        try:
            if comtypes_module is not None:
                comtypes_module.CoUninitialize()
        except Exception:
            pass


class _LoopbackHTTPServer(http.server.ThreadingHTTPServer):
    """Plugin-owned server; do not modify Decky's IPC socket/error handlers."""

    daemon_threads = True
    allow_reuse_address = True
    block_on_close = False

    def get_request(self):
        connection, address = super().get_request()
        # CEF may pre-open sockets without sending a request. Bound idle readers.
        connection.settimeout(15.0)
        return connection, address

    def handle_error(self, request, client_address):
        error = sys.exc_info()[1]
        if isinstance(error, (ConnectionError, TimeoutError)):
            return
        if isinstance(error, OSError) and getattr(error, "winerror", None) in {64, 995, 10053, 10054}:
            return
        super().handle_error(request, client_address)


class _TopbarConnection:
    """An open DevTools WebSocket plus bytes read past the last frame."""

    __slots__ = ("sock", "buffer")

    def __init__(self, sock, buffer: bytes = b"") -> None:
        self.sock = sock
        self.buffer = buffer


# YouTube serves long audio files slowly (or cuts them off) when they are
# requested in one piece; yt-dlp uses the same 10 MB piece size.
REMOTE_CHUNK_BYTES = 10 * 1024 * 1024


class LocalMusicStreamServer:
    """Small localhost HTTP server for CEF/HTMLAudioElement playback.

    Chromium handles the requested MP3/AAC/M4A/FLAC/OGG/Opus formats more
    consistently than the legacy Windows Media Player ActiveX host.  The
    server exposes only library tracks, requires an unguessable session token,
    and supports HTTP Range requests for seeking.
    """

    def __init__(self, track_resolver, asset_resolver, logger, remote_resolver=None) -> None:
        self._track_resolver = track_resolver
        self._asset_resolver = asset_resolver
        self._remote_resolver = remote_resolver
        self._logger = logger
        self._server = None
        self._thread = None
        self.token = secrets.token_urlsafe(24)
        self.port = 0

    def _log(self, message: str) -> None:
        try:
            self._logger(message)
        except Exception:
            pass

    def start(self) -> None:
        if self._server is not None:
            return
        parent = self

        class Handler(http.server.BaseHTTPRequestHandler):
            server_version = "NowPlayingLocalAudio/1.0"
            protocol_version = "HTTP/1.1"

            def log_message(self, format: str, *args: Any) -> None:
                return

            def _cors(self, resource_kind: str = "") -> None:
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Access-Control-Allow-Methods", "GET, HEAD, OPTIONS")
                self.send_header("Access-Control-Allow-Headers", "Range")
                self.send_header("Access-Control-Expose-Headers", "Accept-Ranges, Content-Length, Content-Range")
                # Audio must remain range-addressable.  Avoid letting Steam CEF
                # reuse an incomplete partial response as the full resource.
                self.send_header(
                    "Cache-Control",
                    "no-store, no-cache, must-revalidate" if resource_kind == "track" else "private, max-age=86400",
                )
                self.send_header("Connection", "close")

            def do_OPTIONS(self) -> None:  # noqa: N802
                self.send_response(204)
                self._cors()
                self.end_headers()

            def do_HEAD(self) -> None:  # noqa: N802
                self._serve(False)

            def do_GET(self) -> None:  # noqa: N802
                self._serve(True)

            def _serve(self, include_body: bool) -> None:
                try:
                    parsed = urllib.parse.urlparse(self.path)
                    pieces = [urllib.parse.unquote(piece) for piece in parsed.path.split("/") if piece]
                    if len(pieces) != 3 or pieces[0] != parent.token or pieces[1] not in {"track", "ytmusic-track", "cover", "artist", "background", "youtubemusic-background", "preview"}:
                        self.send_error(404)
                        return
                    resource_kind = pieces[1]
                    resource_id = pieces[2]
                    if resource_kind == "ytmusic-track":
                        self._serve_remote(resource_id, include_body)
                        return
                    path = str(parent._track_resolver(resource_id) if resource_kind == "track" else parent._asset_resolver(resource_kind, resource_id) or "")
                    if not path or not os.path.isfile(path):
                        self.send_error(404)
                        return
                    total = os.path.getsize(path)
                    if total <= 0:
                        self.send_response(200)
                        self._cors(resource_kind)
                        self.send_header("Content-Type", "application/octet-stream")
                        self.send_header("Accept-Ranges", "bytes")
                        self.send_header("Content-Length", "0")
                        self.end_headers()
                        self.close_connection = True
                        return

                    start = 0
                    end = total - 1
                    partial = False
                    range_header = str(self.headers.get("Range") or "").strip()
                    if range_header.startswith("bytes="):
                        # Chromium frequently asks for suffix ranges (bytes=-N)
                        # when reading the moov atom at the end of M4A/MP4 files.
                        # Treating that as bytes=0-N truncates duration detection
                        # and can make healthy tracks stop at a fixed timestamp.
                        match = re.fullmatch(r"bytes=(\d*)-(\d*)", range_header)
                        if not match:
                            self.send_response(416)
                            self._cors(resource_kind)
                            self.send_header("Accept-Ranges", "bytes")
                            self.send_header("Content-Range", f"bytes */{total}")
                            self.send_header("Content-Length", "0")
                            self.end_headers()
                            self.close_connection = True
                            return
                        if match:
                            left, right = match.groups()
                            try:
                                if left:
                                    start = int(left)
                                    if start >= total:
                                        raise ValueError("unsatisfiable range")
                                    end = min(int(right), total - 1) if right else total - 1
                                    if end < start:
                                        raise ValueError("invalid range")
                                elif right:
                                    suffix_length = int(right)
                                    if suffix_length <= 0:
                                        raise ValueError("invalid suffix range")
                                    suffix_length = min(suffix_length, total)
                                    start = total - suffix_length
                                    end = total - 1
                                else:
                                    raise ValueError("empty range")
                                partial = True
                            except (TypeError, ValueError):
                                self.send_response(416)
                                self._cors(resource_kind)
                                self.send_header("Accept-Ranges", "bytes")
                                self.send_header("Content-Range", f"bytes */{total}")
                                self.send_header("Content-Length", "0")
                                self.end_headers()
                                self.close_connection = True
                                return
                    length = max(0, end - start + 1)
                    mime, _ = mimetypes.guess_type(path)
                    extension = Path(path).suffix.lower()
                    mime = mime or {
                        ".jpg": "image/jpeg",
                        ".jpeg": "image/jpeg",
                        ".png": "image/png",
                        ".webp": "image/webp",
                        ".avif": "image/avif",
                        ".gif": "image/gif",
                        ".flac": "audio/flac",
                        ".m4a": "audio/mp4",
                        ".aac": "audio/aac",
                        ".ogg": "audio/ogg",
                        ".opus": "audio/ogg",
                        ".mp3": "audio/mpeg",
                        ".wav": "audio/wav",
                        ".wma": "audio/x-ms-wma",
                    }.get(extension, "application/octet-stream")
                    self.send_response(206 if partial else 200)
                    self._cors(resource_kind)
                    self.send_header("Content-Type", mime)
                    self.send_header("Content-Disposition", "inline")
                    self.send_header("Accept-Ranges", "bytes")
                    self.send_header("Content-Length", str(length))
                    if partial:
                        self.send_header("Content-Range", f"bytes {start}-{end}/{total}")
                    self.end_headers()
                    if include_body and length:
                        with open(path, "rb") as handle:
                            handle.seek(start)
                            remaining = length
                            while remaining > 0:
                                chunk = handle.read(min(256 * 1024, remaining))
                                if not chunk:
                                    break
                                self.wfile.write(chunk)
                                remaining -= len(chunk)
                            try:
                                self.wfile.flush()
                            except Exception:
                                pass
                    self.close_connection = True
                except (BrokenPipeError, ConnectionResetError):
                    return
                except Exception as exc:
                    parent._log(f"local audio stream error: {exc}")
                    try:
                        self.send_error(500)
                    except Exception:
                        pass

            def _serve_remote(self, resource_id: str, include_body: bool) -> None:
                if parent._remote_resolver is None:
                    self.send_error(404)
                    return
                stream = parent._remote_resolver(resource_id)
                if not isinstance(stream, dict) or not str(stream.get("url") or "").startswith("http"):
                    self.send_error(404)
                    return
                url = str(stream.get("url"))
                headers = {
                    str(key): str(value)
                    for key, value in (stream.get("headers") or {}).items()
                    if str(key).lower() not in {"host", "content-length", "connection", "accept-encoding", "range"}
                }
                range_header = str(self.headers.get("Range") or "").strip()
                # YouTube throttles or cuts long single downloads, so audio is
                # fetched from YouTube in REMOTE_CHUNK_BYTES pieces and handed to
                # the player as one continuous response. Suffix ranges
                # ("bytes=-N") and servers that ignore ranges use the old
                # single-request path.
                match = re.fullmatch(r"bytes=(\d+)-(\d*)", range_header) if range_header else None
                if range_header and not match:
                    self._serve_remote_passthrough(url, headers, range_header, include_body)
                    return
                start = int(match.group(1)) if match else 0
                wanted_end = int(match.group(2)) if match and match.group(2) else None
                first_end = start + REMOTE_CHUNK_BYTES - 1 if wanted_end is None else min(wanted_end, start + REMOTE_CHUNK_BYTES - 1)
                remote = None
                headers_sent = False
                try:
                    try:
                        remote = self._open_remote_range(url, headers, start, first_end)
                    except urllib.error.HTTPError as exc:
                        self._send_remote_http_error(exc)
                        return
                    status = int(getattr(remote, "status", 200) or 200)
                    content_range = str(remote.headers.get("Content-Range") or "")
                    total_match = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+)", content_range.strip())
                    if status != 206 or not total_match or int(total_match.group(1)) != start:
                        # The server ignored the range: relay its answer as-is.
                        self._relay_response(remote, status, include_body)
                        return
                    total = int(total_match.group(3))
                    last = total - 1 if wanted_end is None else min(wanted_end, total - 1)
                    self.send_response(206 if range_header else 200)
                    self._cors("track")
                    content_type = remote.headers.get("Content-Type")
                    if content_type:
                        self.send_header("Content-Type", content_type)
                    self.send_header("Accept-Ranges", "bytes")
                    self.send_header("Content-Length", str(max(0, last - start + 1)))
                    if range_header:
                        self.send_header("Content-Range", f"bytes {start}-{last}/{total}")
                    self.send_header("Content-Disposition", "inline")
                    self.end_headers()
                    headers_sent = True
                    if not include_body:
                        return
                    position = start
                    began = time.monotonic()
                    slowest_read = 0.0
                    pieces = 1
                    while True:
                        while True:
                            read_started = time.monotonic()
                            chunk = remote.read(256 * 1024)
                            slowest_read = max(slowest_read, time.monotonic() - read_started)
                            if not chunk:
                                break
                            self.wfile.write(chunk)
                            position += len(chunk)
                        try:
                            remote.close()
                        except Exception:
                            pass
                        remote = None
                        if position > last:
                            break
                        piece_end = min(last, position + REMOTE_CHUNK_BYTES - 1)
                        pieces += 1
                        piece_started = time.monotonic()
                        remote = self._open_remote_range(url, headers, position, piece_end, attempts=3)
                        slowest_read = max(slowest_read, time.monotonic() - piece_started)
                        piece_range = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+)", str(remote.headers.get("Content-Range") or "").strip())
                        if int(getattr(remote, "status", 200) or 200) != 206 or not piece_range or int(piece_range.group(1)) != position:
                            raise RuntimeError(f"YouTube returned an unexpected piece at byte {position}")
                    elapsed = time.monotonic() - began
                    # One line per stream, so stutter can be matched to slow
                    # YouTube downloads (or ruled out).
                    parent._log(
                        f"relay: {resource_id} bytes {start}-{last} sent in {elapsed:.1f}s "
                        f"({(position - start) / 1024 / max(elapsed, 0.001):.0f} KB/s, {pieces} piece(s), longest wait {slowest_read:.1f}s)"
                    )
                except (BrokenPipeError, ConnectionResetError):
                    try:
                        parent._log(f"relay: {resource_id} closed by player at byte {position} after {time.monotonic() - began:.1f}s (longest wait {slowest_read:.1f}s)")
                    except Exception:
                        pass
                    return
                except Exception as exc:
                    parent._log(f"YouTube Music stream proxy error: {exc}")
                    if not headers_sent:
                        # Once audio has started, an error page would corrupt the
                        # stream; closing lets the player re-request from here.
                        try:
                            self.send_error(502)
                        except Exception:
                            pass
                finally:
                    if remote is not None:
                        try:
                            remote.close()
                        except Exception:
                            pass
                    self.close_connection = True

            def _open_remote_range(self, url: str, headers: Dict[str, str], start: int, end: int, attempts: int = 1):
                request_headers = dict(headers)
                request_headers["Range"] = f"bytes={start}-{end}"
                last_error: Optional[BaseException] = None
                for attempt in range(max(1, attempts)):
                    try:
                        return urllib.request.urlopen(urllib.request.Request(url, headers=request_headers, method="GET"), timeout=18)
                    except urllib.error.HTTPError:
                        raise
                    except Exception as exc:
                        last_error = exc
                        if attempt + 1 < attempts:
                            time.sleep(0.4 * (attempt + 1))
                raise last_error if last_error else RuntimeError("remote range unavailable")

            def _send_remote_http_error(self, exc: urllib.error.HTTPError) -> None:
                status = int(getattr(exc, "code", 502) or 502)
                self.send_response(status)
                self._cors("track")
                content_range = str((getattr(exc, "headers", None) or {}).get("Content-Range") or "")
                if content_range:
                    self.send_header("Content-Range", content_range)
                self.send_header("Content-Length", "0")
                self.end_headers()
                self.close_connection = True

            def _relay_response(self, remote, status: int, include_body: bool) -> None:
                self.send_response(status)
                self._cors("track")
                for name in ("Content-Type", "Content-Length", "Content-Range", "Accept-Ranges"):
                    value = remote.headers.get(name)
                    if value:
                        self.send_header(name, value)
                self.send_header("Content-Disposition", "inline")
                self.end_headers()
                if include_body:
                    while True:
                        chunk = remote.read(256 * 1024)
                        if not chunk:
                            break
                        self.wfile.write(chunk)

            def _serve_remote_passthrough(self, url: str, headers: Dict[str, str], range_header: str, include_body: bool) -> None:
                request_headers = dict(headers)
                request_headers["Range"] = range_header
                remote = None
                try:
                    remote = urllib.request.urlopen(urllib.request.Request(url, headers=request_headers, method="GET"), timeout=18)
                    self._relay_response(remote, int(getattr(remote, "status", 200) or 200), include_body)
                except urllib.error.HTTPError as exc:
                    self._send_remote_http_error(exc)
                except (BrokenPipeError, ConnectionResetError):
                    return
                except Exception as exc:
                    parent._log(f"YouTube Music stream proxy error: {exc}")
                    try:
                        self.send_error(502)
                    except Exception:
                        pass
                finally:
                    if remote is not None:
                        try:
                            remote.close()
                        except Exception:
                            pass
                    self.close_connection = True

        self._server = _LoopbackHTTPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        self.port = int(self._server.server_address[1])
        self._thread = threading.Thread(target=self._server.serve_forever, name="NowPlaying-LocalAudioHTTP", daemon=True)
        self._thread.start()
        self._log(f"local audio stream ready on 127.0.0.1:{self.port}")

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/{self.token}" if self.port else ""

    def stop(self) -> None:
        server = self._server
        worker = self._thread
        self._server = None
        self._thread = None
        self.port = 0
        if server is None:
            return
        try:
            server.shutdown()
        except Exception:
            pass
        try:
            server.server_close()
        except Exception:
            pass
        if worker is not None and worker is not threading.current_thread():
            worker.join(timeout=1.0)


class Plugin:
    def __init__(self) -> None:
        self.plugin_dir = os.path.dirname(os.path.abspath(__file__))
        self.runtime_dir = self._resolve_runtime_dir()
        self.bundled_helper_dir = os.path.join(self.plugin_dir, "bin")
        self.bundled_helper_path = os.path.join(self.bundled_helper_dir, "MediaBridge.exe")
        self.bundled_thumbnail_bridge_path = os.path.join(self.bundled_helper_dir, "ThumbnailBridge.exe")
        self.bundled_app_volume_bridge_path = os.path.join(self.bundled_helper_dir, "AppVolumeBridge.exe")
        self.helper_cache_root = os.path.join(tempfile.gettempdir(), "NowPlaying-MediaBridge")
        self.helper_state_path = os.path.join(tempfile.gettempdir(), "NowPlaying-MediaBridge-state.json")
        self.helper_dir = self.bundled_helper_dir
        self.helper_path = self.bundled_helper_path
        self.thumbnail_bridge_path = self.bundled_thumbnail_bridge_path
        self.app_volume_bridge_path = self.bundled_app_volume_bridge_path
        # MediaBridge no longer owns a fixed global port. A registered healthy
        # instance is reused; otherwise a fresh loopback port is allocated. This
        # prevents stale HTTP.sys registrations from permanently blocking startup.
        self.port = 0
        self.base_url = ""
        self.player = ""
        self.log_path = self._runtime_log_path()
        self._log_file_lock = threading.RLock()
        self._diagnostic_lock = threading.RLock()
        self._diagnostic_snapshot_signature = ""
        self._diagnostic_snapshot_at = 0.0
        self._diagnostic_events = deque(maxlen=5000)
        self._diagnostic_counters: Dict[str, int] = {}
        self.cover_cache_path = os.path.join(tempfile.gettempdir(), "NowPlaying-cover-cache.json")
        self.cover_settings_path = os.path.join(tempfile.gettempdir(), "NowPlaying-cover-settings.json")
        self.smtc_cover_dir = os.path.join(tempfile.gettempdir(), "NowPlaying-smtc-covers")
        self._cover_cache: Optional[Dict[str, str]] = None
        self._itunes_match_cache: Dict[str, Tuple[float, Dict[str, Any]]] = {}
        self._itunes_match_jobs: Dict[str, Future] = {}
        self._itunes_match_lock = threading.Lock()
        self.cover_source = self._load_cover_source_setting()
        self.topbar_settings_path = os.path.join(tempfile.gettempdir(), "NowPlaying-topbar.json")
        self.topbar_enabled = False
        self.topbar_left = False
        self._topbar_task = None
        self._topbar_settings_changed = asyncio.Event()
        self._topbar_cached_label = ""
        self._topbar_cached_service = "music"
        self._topbar_cached_at = 0.0
        self._topbar_last_signature = ""
        self._topbar_last_injected_at = 0.0
        self._helper_ready = False
        # Set while an update is handed to Decky: no helper is (re)started and
        # nothing is read from bin/, so Decky can delete and replace the folder.
        self._update_quiesce_until = 0.0
        self._helper_process: Optional[subprocess.Popen] = None
        self._helper_pid = 0
        self._helper_request_lock = threading.RLock()
        self._snapshot_refresh_lock = threading.Lock()
        self._helper_consecutive_failures = 0
        self._helper_last_success_at = 0.0
        self._helper_last_failure_at = 0.0
        self._helper_last_failure = ""
        self._helper_recovery_scheduled = False
        self._helper_recovery_cooldown_until = 0.0
        self._service_restart_in_progress = False
        self._service_restart_request_pending = False
        self._helper_memory_check_at = 0.0
        self._helper_memory_restart_at = 0.0
        self._helper_memory_limit_bytes = 384 * 1024 * 1024
        self._helper_forced_player_id = ""
        self._helper_forced_player_at = 0.0
        self._snapshot_lock = threading.Lock()
        self._snapshot_cache: Dict[str, Any] = {"selectedPlayer": "", "currentPlayer": "", "selected": None, "players": []}
        self._snapshot_cache_at = 0.0
        self._snapshot_last_success_at = 0.0
        # Python/WinRT is bundled as an in-process fallback for machines where
        # the standalone MediaBridge process is healthy but Windows returns an
        # empty session list to it. Keeping this path in-process also gives the
        # diagnostic report enough detail to distinguish "no session" from a
        # broken helper lifecycle.
        self._direct_media_manager: Any = None
        self._direct_media_manager_at = 0.0
        self._direct_media_snapshot: Dict[str, Any] = self._empty_snapshot()
        self._direct_media_snapshot_at = 0.0
        self._direct_media_lock: Optional[asyncio.Lock] = None
        self._direct_media_available: Optional[bool] = None
        self._direct_media_last_error = ""

        # Keep latency-sensitive work isolated from slower cover network
        # requests. A stalled online artwork lookup must never block SMTC polling,
        # playback controls, the top bar, or per-app volume changes.
        self._realtime_executor = ThreadPoolExecutor(max_workers=3, thread_name_prefix="NowPlaying-Realtime")
        # Recovery must never queue behind timed-out snapshot calls. It owns a
        # dedicated worker so the Settings recovery action can always make progress.
        self._recovery_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="NowPlaying-Recovery")
        self._diagnostic_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="NowPlaying-Diagnostics")
        self._topbar_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="NowPlaying-Topbar")
        self._cover_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="NowPlaying-Cover")
        self._artist_background_search_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="NowPlaying-BackgroundSearch")
        self._volume_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="NowPlaying-Volume")
        self._volume_request_lock = threading.Lock()
        self._volume_request_revisions: Dict[str, int] = {}
        self._youtube_music_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="NowPlaying-YouTubeMusic")
        self._youtube_music_prefetch_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="NowPlaying-YTM-Prefetch")
        self._source_lifecycle_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="NowPlaying-SourceLifecycle")

        settings_root = getattr(decky_plugin, "DECKY_PLUGIN_SETTINGS_DIR", None)
        if not isinstance(settings_root, str) or not settings_root.strip():
            settings_root = os.path.join(tempfile.gettempdir(), "NowPlaying-settings")
        # Decky names the settings folder after the plugin folder: bring the
        # user's data over from "Now Playing Plus" once (copy only).
        self._settings_migration = migrate_legacy_settings(settings_root, self._log)
        self.settings_dir = settings_root
        self._youtube_music = YouTubeMusicService(
            settings_root,
            self._volume_vendor_path(),
            self._log,
            self._launch_youtube_music_auth_browser,
            self._terminate_process_native,
        )
        self.source_behavior_settings_path = os.path.join(settings_root, "source-behavior.json")
        self._source_behavior_settings = self._load_source_behavior_settings()
        self._source_transition_lock: Optional[asyncio.Lock] = None
        self._source_transition_revision = 0
        self._source_retry_task: Optional[asyncio.Task[Any]] = None
        self._artist_background_selection_path = os.path.join(self.settings_dir, "artist-background-selections.json")
        self._artist_background_selections: Optional[Dict[str, Any]] = None
        self._artist_background_provider_settings_path = os.path.join(self.settings_dir, "artist-background-providers.json")
        self._artist_background_provider_settings = self._load_artist_background_provider_settings()
        self._theaudiodb_artist_cache: Dict[str, Tuple[float, Dict[str, Any]]] = {}
        self._theaudiodb_request_lock = threading.Lock()
        self._theaudiodb_last_request_at = 0.0
        self._artist_background_candidates: Dict[str, Dict[str, Any]] = {}
        self._artist_background_candidates_lock = threading.Lock()
        self._helper_restart_lock = threading.RLock()
        self._remote_image_download_semaphore = threading.BoundedSemaphore(2)
        self._app_running_cache: Dict[str, Tuple[float, bool]] = {}
        self._app_running_cache_lock = threading.Lock()
        self._app_launch_lock = threading.RLock()
        self._app_launch_attempts: Dict[str, float] = {}
        # Direct Core Audio access avoids spawning AppVolumeBridge.exe for every
        # 1% key/gamepad step. The COM session cache lives on the single volume
        # executor thread; the bundled executable remains as a compatibility fallback.
        self._direct_volume_available: Optional[bool] = None
        self._direct_volume_session: Any = None
        self._direct_volume_session_key = ""
        self._direct_volume_session_at = 0.0
        self._volume_thread_state = threading.local()

        # Local music library / player. The library is persisted inside the Decky
        # settings directory and playback is hosted by a dedicated WMP COM thread.
        self.active_service = self._normalized_service_key(
            self._source_behavior_settings.get("active_service", "localMusic")
        ) or "localMusic"
        self.local_music_settings_path = os.path.join(settings_root, "local-music.json")
        self.local_music_library_path = os.path.join(settings_root, "local-music-library.json")
        self.local_music_cover_dir = os.path.join(settings_root, "local-music-covers")
        self.local_music_artist_profile_dir = os.path.join(settings_root, "local-music-artist-profiles")
        self.local_music_artist_background_dir = os.path.join(settings_root, "local-music-artist-backgrounds")
        self.youtube_music_artist_background_dir = os.path.join(settings_root, "youtube-music-artist-backgrounds")
        self._youtube_music_artist_background_cache: Dict[str, str] = {}
        self._youtube_music_artist_cache_build_lock = threading.Lock()
        self._youtube_music_artist_cache_progress_lock = threading.Lock()
        self._youtube_music_artist_cache_progress: Dict[str, Any] = {
            "active": False, "phase": "idle", "current": "", "completed": 0, "total": 0, "error": "",
        }
        self.artist_background_preview_dir = os.path.join(tempfile.gettempdir(), "NowPlaying-artist-background-previews")
        self._local_music_settings = self._load_local_music_settings()
        self._local_music_library: Optional[Dict[str, Any]] = None
        self._local_music_cover_cache: Dict[str, str] = {}
        self._local_music_artist_background_cache: Dict[str, str] = {}
        self._local_music_scan_lock = threading.RLock()
        self._local_music_cache_build_lock = threading.Lock()
        self._local_music_cache_progress_lock = threading.Lock()
        self._local_music_cache_progress: Dict[str, Any] = {
            "active": False,
            "phase": "idle",
            "current": "",
            "completed": 0,
            "total": 0,
            "error": "",
        }
        self._local_music_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="NowPlaying-LocalLibrary")
        self._local_music_player: Optional[LocalMusicPlayerWorker] = None
        self._local_music_frontend_state: Dict[str, Any] = {}
        self._local_music_stream_server: Optional[LocalMusicStreamServer] = None

        # Self-update from the fork's release branch. Staged under Decky's runtime
        # folder (homebrew/data/<plugin>), which an update does not replace.
        self._update_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="NowPlaying-Update")
        self._update_channel = mix_update.UpdateChannel(
            mix_identity.VERSION,
            os.path.join(self.runtime_dir, "plugin-update"),
            mix_update.HttpTransport(mix_identity.USER_AGENT, mix_update.default_cafile(self.plugin_dir)),
            log=self._log,
        )

    async def _run_in_executor(self, executor: ThreadPoolExecutor, func, *args):
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(executor, lambda: func(*args))


    def _resolve_runtime_dir(self) -> str:
        runtime_dir = getattr(decky_plugin, "DECKY_PLUGIN_RUNTIME_DIR", None)
        if isinstance(runtime_dir, str) and runtime_dir.strip():
            return runtime_dir

        runtime_dir = os.getenv("DECKY_PLUGIN_RUNTIME_DIR", "").strip()
        if runtime_dir:
            return runtime_dir

        return self.plugin_dir

    @staticmethod
    def _runtime_log_path() -> str:
        # Decky's own log folder for this plugin (homebrew\logs\<plugin>). The
        # temp folder is a fallback only: under some Decky hosts it points at a
        # private directory the user cannot find.
        log_dir = getattr(decky_plugin, "DECKY_PLUGIN_LOG_DIR", None) or os.environ.get("DECKY_PLUGIN_LOG_DIR")
        if isinstance(log_dir, str) and log_dir.strip():
            try:
                os.makedirs(log_dir, exist_ok=True)
                probe = os.path.join(log_dir, "NowPlaying.log")
                with open(probe, "a", encoding="utf-8"):
                    pass
                return probe
            except Exception:
                pass
        return os.path.join(tempfile.gettempdir(), "NowPlaying.log")

    def _rotate_runtime_log(self) -> None:
        try:
            if not os.path.isfile(self.log_path) or os.path.getsize(self.log_path) < 4 * 1024 * 1024:
                return
            for index in range(3, 0, -1):
                source = f"{self.log_path}.{index}"
                target = f"{self.log_path}.{index + 1}"
                if index == 3:
                    try:
                        if os.path.isfile(source):
                            os.remove(source)
                    except Exception:
                        pass
                elif os.path.isfile(source):
                    try:
                        os.replace(source, target)
                    except Exception:
                        pass
            try:
                os.replace(self.log_path, f"{self.log_path}.1")
            except Exception:
                pass
        except Exception:
            pass

    def _record_diagnostic_event(self, category: str, event: str, details: Optional[Dict[str, Any]] = None, level: str = "info") -> None:
        try:
            entry = {
                "ts": datetime.now().isoformat(timespec="milliseconds"),
                "monotonic": round(time.monotonic(), 3),
                "level": str(level or "info"),
                "category": str(category or "runtime")[:80],
                "event": str(event or "")[:240],
                "thread": threading.current_thread().name,
                "details": details if isinstance(details, dict) else {},
            }
            with self._diagnostic_lock:
                self._diagnostic_events.append(entry)
                counter_key = f"{entry['category']}:{entry['event']}"
                self._diagnostic_counters[counter_key] = int(self._diagnostic_counters.get(counter_key, 0)) + 1
        except Exception:
            pass

    def _log(self, message: str) -> None:
        timestamp = datetime.now().isoformat(timespec="seconds")
        text = str(message or "")
        line = f"[{timestamp}] [Awesome Mix] {text}"
        self._record_diagnostic_event("runtime", text, level="error" if any(token in text.lower() for token in (" error", " failed", " timeout", "non riuscito", "fallito")) else "info")
        try:
            stdout = getattr(sys, "stdout", None)
            if stdout is not None:
                stdout.write(line + "\n")
                stdout.flush()
        except Exception:
            pass
        try:
            with self._log_file_lock:
                self._rotate_runtime_log()
                with open(self.log_path, "a", encoding="utf-8") as handle:
                    handle.write(line + "\n")
        except Exception:
            pass

    def _is_windows(self) -> bool:
        return os.name == "nt"

    def _task_creationflags(self) -> int:
        flags = 0
        if self._is_windows() and hasattr(subprocess, "CREATE_NO_WINDOW"):
            flags |= subprocess.CREATE_NO_WINDOW
        return flags

    def _same_path(self, left: str, right: str) -> bool:
        try:
            return os.path.normcase(os.path.abspath(left)) == os.path.normcase(os.path.abspath(right))
        except Exception:
            return False

    def _file_digest(self, path: str) -> str:
        digest = hashlib.sha256()
        with open(path, "rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()[:16]

    def _prepare_helper_runtime(self) -> None:
        if not os.path.exists(self.bundled_helper_path):
            raise RuntimeError(f"Helper non trovato: {self.bundled_helper_path}")

        digest = self._file_digest(self.bundled_helper_path)
        runtime_dir = os.path.join(self.helper_cache_root, digest)
        os.makedirs(runtime_dir, exist_ok=True)

        for filename in os.listdir(self.bundled_helper_dir):
            source = os.path.join(self.bundled_helper_dir, filename)
            target = os.path.join(runtime_dir, filename)
            if not os.path.isfile(source):
                continue

            if os.path.exists(target) and os.path.getsize(source) == os.path.getsize(target):
                try:
                    if self._file_digest(source) == self._file_digest(target):
                        continue
                except Exception:
                    pass

            shutil.copy2(source, target)

        self.helper_dir = runtime_dir
        self.helper_path = os.path.join(runtime_dir, "MediaBridge.exe")
        self.thumbnail_bridge_path = os.path.join(runtime_dir, "ThumbnailBridge.exe")
        stale_volume_helper = os.path.join(runtime_dir, "AppVolumeBridge.exe")
        if not os.path.isfile(self.bundled_app_volume_bridge_path) and os.path.isfile(stale_volume_helper):
            try:
                os.remove(stale_volume_helper)
            except Exception as exc:
                self._log(f"stale AppVolumeBridge cleanup error: {exc}")
        self.app_volume_bridge_path = ""

    def _is_process_running(self, image_name: str) -> bool:
        if not self._is_windows():
            return False
        expected = str(image_name or "").strip().lower()
        if not expected:
            return False
        # Prefer the native Toolhelp snapshot. Apart from being faster, it avoids
        # spawning tasklist repeatedly while QAM, Big Picture and diagnostics poll.
        native_names = self._running_process_names_native()
        if native_names:
            return expected in native_names
        try:
            completed = subprocess.run(
                ["tasklist", "/FI", f"IMAGENAME eq {image_name}", "/FO", "CSV", "/NH"],
                capture_output=True,
                text=True,
                timeout=2.5,
                creationflags=self._task_creationflags(),
            )
            output = f"{completed.stdout}\n{completed.stderr}".lower()
            return expected in output
        except Exception as exc:
            self._log(f"process check error for {image_name}: {exc}")
            return False

    def _native_process_entries(self) -> List[Dict[str, Any]]:
        if not self._is_windows() or _PROCESSENTRY32W is None:
            return []
        try:
            TH32CS_SNAPPROCESS = 0x00000002
            INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            kernel32 = ctypes.windll.kernel32
            kernel32.CreateToolhelp32Snapshot.argtypes = [ctypes.c_ulong, ctypes.c_ulong]
            kernel32.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
            pointer_type = ctypes.POINTER(_PROCESSENTRY32W)
            kernel32.Process32FirstW.argtypes = [ctypes.c_void_p, pointer_type]
            kernel32.Process32FirstW.restype = ctypes.c_int
            kernel32.Process32NextW.argtypes = [ctypes.c_void_p, pointer_type]
            kernel32.Process32NextW.restype = ctypes.c_int
            kernel32.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
            kernel32.OpenProcess.restype = ctypes.c_void_p
            kernel32.QueryFullProcessImageNameW.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_ulong)]
            kernel32.QueryFullProcessImageNameW.restype = ctypes.c_int
            kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
            kernel32.CloseHandle.restype = ctypes.c_int

            snapshot = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
            if snapshot == INVALID_HANDLE_VALUE or not snapshot:
                return []
            entries: List[Dict[str, Any]] = []
            entry = _PROCESSENTRY32W()
            entry.dwSize = ctypes.sizeof(_PROCESSENTRY32W)
            try:
                has_entry = bool(kernel32.Process32FirstW(snapshot, ctypes.pointer(entry)))
                while has_entry:
                    process_id = int(entry.th32ProcessID or 0)
                    name = str(entry.szExeFile or '').strip()
                    path = ''
                    if process_id > 0:
                        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, process_id)
                        if handle:
                            try:
                                capacity = ctypes.c_ulong(32768)
                                buffer = ctypes.create_unicode_buffer(capacity.value)
                                if kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(capacity)):
                                    path = str(buffer.value or '').strip()
                            finally:
                                kernel32.CloseHandle(handle)
                    entries.append({
                        "pid": process_id,
                        "parent_pid": int(entry.th32ParentProcessID or 0),
                        "name": name,
                        "path": path,
                    })
                    entry.dwSize = ctypes.sizeof(_PROCESSENTRY32W)
                    has_entry = bool(kernel32.Process32NextW(snapshot, ctypes.pointer(entry)))
            finally:
                kernel32.CloseHandle(snapshot)
            return entries
        except Exception as exc:
            self._log(f"native process enumeration error: {exc}")
            return []

    def _running_process_names_native(self) -> Set[str]:
        return {str(item.get("name") or "").strip().lower() for item in self._native_process_entries() if str(item.get("name") or "").strip()}

    def _process_session_id(self, process_id: int) -> Optional[int]:
        if not self._is_windows() or int(process_id or 0) <= 0:
            return None
        try:
            session_id = ctypes.c_ulong(0)
            kernel32 = ctypes.windll.kernel32
            kernel32.ProcessIdToSessionId.argtypes = [ctypes.c_ulong, ctypes.POINTER(ctypes.c_ulong)]
            kernel32.ProcessIdToSessionId.restype = ctypes.c_int
            if kernel32.ProcessIdToSessionId(int(process_id), ctypes.byref(session_id)):
                return int(session_id.value)
        except Exception:
            pass
        return None

    def _launch_hidden_as_interactive_user(
        self,
        executable: str,
        arguments: List[str],
        cwd: str,
        visible: bool = False,
    ) -> Dict[str, Any]:
        """Launch a helper in the same interactive Windows session as Steam.

        Decky can run its Python backend with a different/elevated token. GSMTC
        sessions are scoped to the signed-in desktop user, so inheriting the
        backend token can produce a healthy MediaBridge with zero players.
        """
        if not self._is_windows():
            return {"ok": False, "error": "Windows only"}
        kernel32 = ctypes.windll.kernel32
        advapi32 = ctypes.windll.advapi32
        userenv = ctypes.windll.userenv
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        TOKEN_ASSIGN_PRIMARY = 0x0001
        TOKEN_DUPLICATE = 0x0002
        TOKEN_QUERY = 0x0008
        TOKEN_ADJUST_DEFAULT = 0x0080
        TOKEN_ADJUST_SESSIONID = 0x0100
        SecurityImpersonation = 2
        TokenPrimary = 1
        LOGON_WITH_PROFILE = 0x00000001
        CREATE_UNICODE_ENVIRONMENT = 0x00000400
        CREATE_NO_WINDOW = 0x08000000
        STARTF_USESHOWWINDOW = 0x00000001
        SW_HIDE = 0
        SW_SHOW = 5

        class STARTUPINFOW(ctypes.Structure):
            _fields_ = [
                ("cb", ctypes.c_ulong), ("lpReserved", ctypes.c_wchar_p),
                ("lpDesktop", ctypes.c_wchar_p), ("lpTitle", ctypes.c_wchar_p),
                ("dwX", ctypes.c_ulong), ("dwY", ctypes.c_ulong),
                ("dwXSize", ctypes.c_ulong), ("dwYSize", ctypes.c_ulong),
                ("dwXCountChars", ctypes.c_ulong), ("dwYCountChars", ctypes.c_ulong),
                ("dwFillAttribute", ctypes.c_ulong), ("dwFlags", ctypes.c_ulong),
                ("wShowWindow", ctypes.c_ushort), ("cbReserved2", ctypes.c_ushort),
                ("lpReserved2", ctypes.POINTER(ctypes.c_ubyte)),
                ("hStdInput", ctypes.c_void_p), ("hStdOutput", ctypes.c_void_p),
                ("hStdError", ctypes.c_void_p),
            ]

        class PROCESS_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("hProcess", ctypes.c_void_p), ("hThread", ctypes.c_void_p),
                ("dwProcessId", ctypes.c_ulong), ("dwThreadId", ctypes.c_ulong),
            ]

        kernel32.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
        kernel32.OpenProcess.restype = ctypes.c_void_p
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        kernel32.CloseHandle.restype = ctypes.c_int
        advapi32.OpenProcessToken.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(ctypes.c_void_p)]
        advapi32.OpenProcessToken.restype = ctypes.c_int
        advapi32.DuplicateTokenEx.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.c_void_p)]
        advapi32.DuplicateTokenEx.restype = ctypes.c_int
        advapi32.CreateProcessWithTokenW.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_ulong, ctypes.c_void_p, ctypes.c_wchar_p, ctypes.POINTER(STARTUPINFOW), ctypes.POINTER(PROCESS_INFORMATION)]
        advapi32.CreateProcessWithTokenW.restype = ctypes.c_int
        userenv.CreateEnvironmentBlock.argtypes = [ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p, ctypes.c_int]
        userenv.CreateEnvironmentBlock.restype = ctypes.c_int
        userenv.DestroyEnvironmentBlock.argtypes = [ctypes.c_void_p]
        userenv.DestroyEnvironmentBlock.restype = ctypes.c_int

        active_session = None
        try:
            value = int(kernel32.WTSGetActiveConsoleSessionId())
            if value != 0xFFFFFFFF:
                active_session = value
        except Exception:
            pass
        entries = self._native_process_entries()
        priorities = {"steam.exe": 0, "explorer.exe": 1}
        candidates = sorted(
            [item for item in entries if str(item.get("name") or "").lower() in priorities],
            key=lambda item: priorities.get(str(item.get("name") or "").lower(), 99),
        )
        errors: List[str] = []
        command_line = subprocess.list2cmdline([executable, *arguments])
        for item in candidates:
            pid = int(item.get("pid") or 0)
            session_id = self._process_session_id(pid)
            if active_session is not None and session_id is not None and session_id != active_session:
                continue
            process_handle = token_handle = primary_token = environment = None
            process_info = PROCESS_INFORMATION()
            try:
                process_handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
                if not process_handle:
                    raise OSError(ctypes.get_last_error(), "OpenProcess failed")
                token_handle = ctypes.c_void_p()
                desired = TOKEN_ASSIGN_PRIMARY | TOKEN_DUPLICATE | TOKEN_QUERY
                if not advapi32.OpenProcessToken(process_handle, desired, ctypes.byref(token_handle)):
                    raise OSError(ctypes.get_last_error(), "OpenProcessToken failed")
                primary_token = ctypes.c_void_p()
                token_access = TOKEN_ASSIGN_PRIMARY | TOKEN_DUPLICATE | TOKEN_QUERY | TOKEN_ADJUST_DEFAULT | TOKEN_ADJUST_SESSIONID
                if not advapi32.DuplicateTokenEx(token_handle, token_access, None, SecurityImpersonation, TokenPrimary, ctypes.byref(primary_token)):
                    raise OSError(ctypes.get_last_error(), "DuplicateTokenEx failed")
                environment = ctypes.c_void_p()
                environment_pointer = None
                if userenv.CreateEnvironmentBlock(ctypes.byref(environment), primary_token, False):
                    environment_pointer = environment
                startup = STARTUPINFOW()
                startup.cb = ctypes.sizeof(STARTUPINFOW)
                startup.lpDesktop = "winsta0\\default"
                startup.dwFlags = STARTF_USESHOWWINDOW
                startup.wShowWindow = SW_SHOW if visible else SW_HIDE
                mutable_command = ctypes.create_unicode_buffer(command_line)
                creation_flags = CREATE_UNICODE_ENVIRONMENT | (0 if visible else CREATE_NO_WINDOW)
                created = advapi32.CreateProcessWithTokenW(
                    primary_token, LOGON_WITH_PROFILE, executable, mutable_command,
                    creation_flags, environment_pointer, cwd,
                    ctypes.byref(startup), ctypes.byref(process_info),
                )
                if not created:
                    raise OSError(ctypes.get_last_error(), "CreateProcessWithTokenW failed")
                launched_pid = int(process_info.dwProcessId or 0)
                self._record_diagnostic_event(
                    "mediabridge", "interactive_launch",
                    {"pid": launched_pid, "tokenPid": pid, "tokenProcess": item.get("name"), "sessionId": session_id},
                )
                return {"ok": True, "pid": launched_pid, "mode": "interactive-token", "sessionId": session_id}
            except Exception as exc:
                errors.append(f"{item.get('name')}:{pid}: {exc}")
            finally:
                for handle in (process_info.hThread, process_info.hProcess, primary_token, token_handle, process_handle):
                    if handle:
                        try:
                            kernel32.CloseHandle(handle)
                        except Exception:
                            pass
                if environment:
                    try:
                        userenv.DestroyEnvironmentBlock(environment)
                    except Exception:
                        pass
        return {"ok": False, "error": " | ".join(errors[-4:]) or "No interactive user token was available"}

    def _terminate_process_native(self, process_id: int, timeout: float = 2.0) -> bool:
        if not self._is_windows() or int(process_id or 0) <= 0:
            return False
        try:
            PROCESS_TERMINATE = 0x0001
            SYNCHRONIZE = 0x00100000
            WAIT_OBJECT_0 = 0x00000000
            kernel32 = ctypes.windll.kernel32
            kernel32.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
            kernel32.OpenProcess.restype = ctypes.c_void_p
            kernel32.TerminateProcess.argtypes = [ctypes.c_void_p, ctypes.c_uint]
            kernel32.TerminateProcess.restype = ctypes.c_int
            kernel32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
            kernel32.WaitForSingleObject.restype = ctypes.c_ulong
            kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
            kernel32.CloseHandle.restype = ctypes.c_int
            handle = kernel32.OpenProcess(PROCESS_TERMINATE | SYNCHRONIZE, False, int(process_id))
            if not handle:
                return False
            try:
                terminated = bool(kernel32.TerminateProcess(handle, 1))
                wait_result = kernel32.WaitForSingleObject(handle, max(1, int(timeout * 1000)))
                return terminated or wait_result == WAIT_OBJECT_0
            finally:
                kernel32.CloseHandle(handle)
        except Exception as exc:
            self._log(f"native process termination error for {process_id}: {exc}")
            return False

    def _is_music_app_running_sync(self, app_key: str, max_age: float = 1.0) -> bool:
        key = str(app_key or "").strip().lower()
        config = self._music_app_launchers().get(key)
        if not config:
            return False
        now = time.monotonic()
        with self._app_running_cache_lock:
            cached = self._app_running_cache.get(key)
            if cached and now - cached[0] <= max_age:
                return bool(cached[1])

        expected = {str(name).strip().lower() for name in config.get("processes", []) if str(name).strip()}
        running_names = self._running_process_names_native()
        if running_names:
            running = bool(expected.intersection(running_names))
        else:
            running = self._is_any_process_running(config.get("processes", []))

        with self._app_running_cache_lock:
            self._app_running_cache[key] = (time.monotonic(), running)
        return running

    async def is_music_app_running(self, app_key: str) -> bool:
        try:
            music_key = self._music_app_key_for_service(app_key) or str(app_key or "").strip().lower()
            return bool(await self._run_in_executor(
                self._realtime_executor, self._is_music_app_running_sync, music_key, 0.8
            ))
        except Exception as exc:
            self._log(f"is_music_app_running error: {exc}")
            return False

    async def close_music_app(self, app_key: str) -> str:
        try:
            music_key = self._music_app_key_for_service(app_key) or str(app_key or "").strip().lower()
            if not music_key:
                return "not_supported"
            return str(await self._run_in_executor(
                self._realtime_executor, self._close_music_app_best_effort, music_key
            ))
        except Exception as exc:
            self._log(f"close_music_app error: {exc}")
            return "false"


    def _source_behavior_defaults(self) -> Dict[str, Any]:
        return {"auto_launch": True, "close_on_switch": True, "active_service": "localMusic"}

    def _load_source_behavior_settings(self) -> Dict[str, Any]:
        settings = self._source_behavior_defaults()
        try:
            with open(self.source_behavior_settings_path, "r", encoding="utf-8") as handle:
                loaded = json.load(handle)
            if isinstance(loaded, dict):
                settings["auto_launch"] = bool(loaded.get("auto_launch", settings["auto_launch"]))
                settings["close_on_switch"] = bool(loaded.get("close_on_switch", settings["close_on_switch"]))
                active_service = self._normalized_service_key(loaded.get("active_service", "localMusic"))
                if active_service in SUPPORTED_SERVICES:
                    settings["active_service"] = active_service
        except Exception:
            pass
        return settings

    def _save_source_behavior_settings(self) -> None:
        try:
            os.makedirs(os.path.dirname(self.source_behavior_settings_path), exist_ok=True)
            temporary = self.source_behavior_settings_path + ".tmp"
            with open(temporary, "w", encoding="utf-8") as handle:
                json.dump(self._source_behavior_settings, handle, ensure_ascii=False, indent=2)
            os.replace(temporary, self.source_behavior_settings_path)
        except Exception as exc:
            self._log(f"source behavior settings save error: {exc}")

    async def get_source_behavior_settings(self) -> Dict[str, bool]:
        return {
            "autoLaunch": bool(self._source_behavior_settings.get("auto_launch", True)),
            "closeOnSwitch": bool(self._source_behavior_settings.get("close_on_switch", True)),
        }

    async def set_source_behavior_settings(self, auto_launch: bool, close_on_switch: bool) -> Dict[str, bool]:
        self._source_behavior_settings["auto_launch"] = bool(auto_launch)
        self._source_behavior_settings["close_on_switch"] = bool(close_on_switch)
        self._save_source_behavior_settings()
        return await self.get_source_behavior_settings()

    def _source_transition_guard(self) -> asyncio.Lock:
        if self._source_transition_lock is None:
            self._source_transition_lock = asyncio.Lock()
        return self._source_transition_lock

    def _music_app_key_for_service(self, service: Any) -> str:
        normalized = self._normalized_service_key(service)
        return {"appleMusic": "apple_music"}.get(normalized, "")

    def _invalidate_app_running_cache(self, app_key: str) -> None:
        key = str(app_key or "").strip().lower()
        with self._app_running_cache_lock:
            self._app_running_cache.pop(key, None)


    def _music_app_launchers(self) -> Dict[str, Dict[str, Any]]:
        return {
            "apple_music": {
                "processes": ["AppleMusic.exe"],
                "paths": [
                    r"%LOCALAPPDATA%\Microsoft\WindowsApps\AppleMusic.exe",
                    r"%PROGRAMFILES%\Apple Music\AppleMusic.exe",
                ],
                "protocols": ["music:", "applemusic:"],
            },
        }

    def _expand_candidate_paths(self, paths):
        expanded = []
        for path in paths:
            candidate = os.path.expandvars(path).strip()
            if candidate and candidate not in expanded:
                expanded.append(candidate)
        return expanded

    def _is_any_process_running(self, image_names) -> bool:
        for image_name in image_names:
            if self._is_process_running(image_name):
                return True
        return False

    def _process_ids_for_images(self, image_names) -> Set[int]:
        expected = {str(value or "").strip().lower() for value in image_names if str(value or "").strip()}
        if not expected or not self._is_windows():
            return set()
        return {
            int(item.get("pid") or 0)
            for item in self._native_process_entries()
            if str(item.get("name") or "").strip().lower() in expected and int(item.get("pid") or 0) > 0
        }

    def _plugin_helper_process_ids(self, image_names) -> Set[int]:
        expected = {str(value or "").strip().lower() for value in image_names if str(value or "").strip()}
        roots = [
            os.path.abspath(self.helper_cache_root),
            os.path.abspath(self.bundled_helper_dir),
        ]
        result: Set[int] = set()
        for item in self._native_process_entries():
            if str(item.get("name") or "").strip().lower() not in expected:
                continue
            path = str(item.get("path") or "").strip()
            if not path:
                continue
            try:
                full_path = os.path.abspath(path)
                if any(os.path.commonpath([full_path, root]) == root for root in roots):
                    process_id = int(item.get("pid") or 0)
                    if process_id > 0:
                        result.add(process_id)
            except Exception:
                continue
        return result

    def _minimize_windows_for_pids(self, process_ids: Set[int]) -> None:
        if not process_ids or not self._is_windows():
            return

        try:
            user32 = ctypes.windll.user32
            enum_windows_proc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
            sw_minimize = 6

            @enum_windows_proc
            def enum_window(window_handle, _lparam):
                process_id = ctypes.c_ulong()
                user32.GetWindowThreadProcessId(window_handle, ctypes.byref(process_id))

                if process_id.value in process_ids and user32.IsWindowVisible(window_handle):
                    user32.ShowWindow(window_handle, sw_minimize)

                return True

            user32.EnumWindows(enum_window, 0)
        except Exception as exc:
            self._log(f"window minimize error: {exc}")

    def _schedule_minimize_process_windows(self, image_names, attempts: int = 16, delay: float = 0.18) -> None:
        if not self._is_windows():
            return

        def worker() -> None:
            for _ in range(attempts):
                process_ids = self._process_ids_for_images(image_names)
                if process_ids:
                    self._minimize_windows_for_pids(process_ids)
                time.sleep(delay)

        try:
            threading.Thread(target=worker, daemon=True).start()
        except Exception as exc:
            self._log(f"minimize scheduler error: {exc}")

    def _launch_process_minimized(self, executable: str, args=None, minimize_processes=None) -> None:
        startupinfo = None
        if self._is_windows() and hasattr(subprocess, "STARTUPINFO"):
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = 7

        subprocess.Popen(
            [executable, *(args or [])],
            cwd=os.path.dirname(executable) or None,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            startupinfo=startupinfo,
            creationflags=self._task_creationflags(),
        )

        if minimize_processes:
            self._schedule_minimize_process_windows(minimize_processes)

    def _open_music_app_best_effort(self, app_key: str) -> str:
        if not self._is_windows():
            return "unsupported"

        key = str(app_key or "").strip().lower()
        config = self._music_app_launchers().get(key)
        if not config:
            return "unsupported"

        with self._app_launch_lock:
            if self._is_music_app_running_sync(key, max_age=0.0):
                return "already_running"
            now = time.monotonic()
            if now - float(self._app_launch_attempts.get(key, 0.0) or 0.0) < 4.0:
                return "launch_pending"
            self._app_launch_attempts[key] = now

        for candidate in self._expand_candidate_paths(config.get("paths", [])):
            if not candidate or not os.path.exists(candidate):
                continue

            try:
                self._launch_process_minimized(
                    candidate,
                    args=config.get("args", []),
                    minimize_processes=config.get("processes", []) if config.get("minimizeAfterLaunch") else None,
                )
                self._invalidate_app_running_cache(key)
                return "launched"
            except Exception as exc:
                self._log(f"{key} launch failed for {candidate}: {exc}")

        for protocol in config.get("protocols", []):
            try:
                os.startfile(protocol)  # type: ignore[attr-defined]
                if config.get("minimizeAfterLaunch"):
                    self._schedule_minimize_process_windows(config.get("processes", []))
                self._invalidate_app_running_cache(key)
                return "launched"
            except Exception as exc:
                self._log(f"{key} protocol launch failed for {protocol}: {exc}")

        with self._app_launch_lock:
            self._app_launch_attempts.pop(key, None)
        return "false"

    def _close_music_app_best_effort(self, app_key: str) -> str:
        if not self._is_windows():
            return "unsupported"

        key = str(app_key or "").strip().lower()
        with self._app_launch_lock:
            self._app_launch_attempts.pop(key, None)
        config = self._music_app_launchers().get(key)
        if not config:
            return "unsupported"
        if not self._is_music_app_running_sync(key, max_age=0.0):
            return "not_running"

        process_names = [str(value).strip() for value in config.get("processes", []) if str(value).strip()]
        attempted = False
        for image_name in process_names:
            try:
                completed = subprocess.run(
                    ["taskkill", "/IM", image_name, "/T"],
                    capture_output=True,
                    text=True,
                    timeout=2.5,
                    creationflags=self._task_creationflags(),
                )
                attempted = attempted or completed.returncode == 0
            except Exception as exc:
                self._log(f"graceful close failed for {app_key}/{image_name}: {exc}")

        self._invalidate_app_running_cache(key)
        deadline = time.monotonic() + 1.5
        while time.monotonic() < deadline:
            if not self._is_music_app_running_sync(key, max_age=0.0):
                return "closed"
            time.sleep(0.12)

        # A few desktop clients ignore a normal close while minimized. Force only
        # the exact app executables; never terminate generic browser processes.
        for image_name in process_names:
            try:
                completed = subprocess.run(
                    ["taskkill", "/IM", image_name, "/T", "/F"],
                    capture_output=True,
                    text=True,
                    timeout=2.5,
                    creationflags=self._task_creationflags(),
                )
                attempted = attempted or completed.returncode == 0
            except Exception as exc:
                self._log(f"forced close failed for {app_key}/{image_name}: {exc}")

        self._invalidate_app_running_cache(key)
        return "closed" if attempted and not self._is_music_app_running_sync(key, max_age=0.0) else "false"

    def _process_working_set_bytes(self, process_id: int) -> int:
        if not self._is_windows() or int(process_id or 0) <= 0:
            return 0
        try:
            PROCESS_QUERY_INFORMATION = 0x0400
            PROCESS_VM_READ = 0x0010

            class PROCESS_MEMORY_COUNTERS_EX(ctypes.Structure):
                _fields_ = [
                    ("cb", ctypes.c_ulong),
                    ("PageFaultCount", ctypes.c_ulong),
                    ("PeakWorkingSetSize", ctypes.c_size_t),
                    ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t),
                    ("PeakPagefileUsage", ctypes.c_size_t),
                    ("PrivateUsage", ctypes.c_size_t),
                ]

            kernel32 = ctypes.windll.kernel32
            psapi = ctypes.windll.psapi
            kernel32.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
            kernel32.OpenProcess.restype = ctypes.c_void_p
            kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
            kernel32.CloseHandle.restype = ctypes.c_int
            psapi.GetProcessMemoryInfo.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong]
            psapi.GetProcessMemoryInfo.restype = ctypes.c_int
            handle = kernel32.OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, int(process_id))
            if not handle:
                return 0
            try:
                counters = PROCESS_MEMORY_COUNTERS_EX()
                counters.cb = ctypes.sizeof(counters)
                if not psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
                    return 0
                return int(counters.WorkingSetSize or 0)
            finally:
                kernel32.CloseHandle(handle)
        except Exception as exc:
            self._log(f"helper memory query error: {exc}")
            return 0

    def _watchdog_helper_memory(self) -> None:
        now = time.monotonic()
        if now - self._helper_memory_check_at < 12.0 or now - self._helper_memory_restart_at < 20.0:
            return
        self._helper_memory_check_at = now
        health = self._helper_health()
        process_ids: List[int] = []
        if isinstance(health, dict):
            try:
                process_id = int(health.get("pid") or 0)
                if process_id > 0:
                    process_ids.append(process_id)
            except Exception:
                pass
        if not process_ids:
            process_ids.extend(sorted(self._process_ids_for_images(["MediaBridge.exe"])))
        used = max((self._process_working_set_bytes(process_id) for process_id in process_ids), default=0)
        if used <= self._helper_memory_limit_bytes:
            return
        self._helper_memory_restart_at = time.monotonic()
        self._log(f"MediaBridge memory watchdog: {used / (1024 * 1024):.0f} MB, restarting helper")
        with self._helper_restart_lock:
            self._shutdown_helper()
            if not self._wait_helper_down(timeout=1.0):
                self._kill_helper_processes()
                self._wait_helper_down(timeout=1.0)
            self._helper_ready = False
            self.player = ""
            with self._snapshot_lock:
                self._snapshot_cache = {"selectedPlayer": "", "currentPlayer": "", "selected": None, "players": []}
                self._snapshot_cache_at = 0.0
            self._ensure_helper_unlocked()

    def _set_helper_endpoint(self, port: int) -> None:
        value = max(0, int(port or 0))
        self.port = value
        self.base_url = f"http://127.0.0.1:{value}" if value else ""

    def _allocate_helper_port(self) -> int:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind(("127.0.0.1", 0))
            return int(listener.getsockname()[1])

    def _load_helper_state(self) -> Dict[str, Any]:
        try:
            with open(self.helper_state_path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
            return payload if isinstance(payload, dict) else {}
        except Exception:
            return {}

    def _save_helper_state(self, health: Optional[Dict[str, Any]] = None) -> None:
        if not self.port or not self.helper_path:
            return
        payload = {
            "port": int(self.port),
            "pid": int((health or {}).get("pid") or self._helper_pid or 0),
            "processPath": str((health or {}).get("processPath") or self.helper_path),
            "runtimePath": self.helper_path,
            "updatedAt": time.time(),
        }
        try:
            temporary = self.helper_state_path + ".tmp"
            with open(temporary, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
            os.replace(temporary, self.helper_state_path)
        except Exception as exc:
            self._log(f"helper state save error: {exc}")

    def _clear_helper_state(self) -> None:
        try:
            if os.path.isfile(self.helper_state_path):
                os.remove(self.helper_state_path)
        except Exception:
            pass

    def _helper_health_at(self, port: int, timeout: float = 0.6) -> Optional[Dict[str, Any]]:
        if int(port or 0) <= 0:
            return None
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{int(port)}/health", timeout=max(0.2, float(timeout))) as response:
                payload = json.loads(response.read().decode("utf-8"))
                return payload if isinstance(payload, dict) and bool(payload.get("ok", False)) else None
        except Exception:
            return None

    def _helper_health(self, timeout: float = 0.6) -> Optional[Dict[str, Any]]:
        return self._helper_health_at(self.port, timeout)

    def _healthcheck(self) -> bool:
        return self._helper_health() is not None

    def _wait_helper_down(self, timeout: float = 2.0, port: Optional[int] = None) -> bool:
        target_port = int(port if port is not None else self.port or 0)
        deadline = time.monotonic() + max(0.1, float(timeout))
        while time.monotonic() < deadline:
            if self._helper_health_at(target_port, timeout=0.25) is None:
                return True
            time.sleep(0.08)
        return self._helper_health_at(target_port, timeout=0.25) is None

    def _kill_named_process(self, image_name: str) -> None:
        if not self._is_windows():
            return
        process_ids = sorted(self._plugin_helper_process_ids([image_name]))
        if not process_ids:
            return
        self._record_diagnostic_event("process", "terminate_named", {"image": image_name, "pids": process_ids}, "warning")
        for process_id in process_ids:
            if not self._terminate_process_native(process_id, timeout=2.0):
                self._log(f"native termination failed for {image_name} pid={process_id}")

    def _kill_helper_processes(self) -> None:
        self._kill_named_process("MediaBridge.exe")

    def _kill_all_plugin_helpers(self) -> None:
        for image_name in ("MediaBridge.exe", "AppVolumeBridge.exe", "ThumbnailBridge.exe"):
            self._kill_named_process(image_name)

    def _adopt_registered_helper(self) -> bool:
        state = self._load_helper_state()
        port = int(state.get("port") or 0)
        if port <= 0:
            return False
        registered_path = str(state.get("runtimePath") or state.get("processPath") or "").strip()
        if registered_path and not self._same_path(registered_path, self.helper_path):
            stale_pid = int(state.get("pid") or 0)
            if stale_pid > 0:
                self._terminate_process_native(stale_pid, timeout=1.5)
            self._clear_helper_state()
            return False
        health = self._helper_health_at(port, timeout=0.75)
        if not isinstance(health, dict):
            stale_pid = int(state.get("pid") or 0)
            if stale_pid > 0:
                self._terminate_process_native(stale_pid, timeout=1.5)
            self._clear_helper_state()
            return False
        running_path = str(health.get("processPath") or registered_path or "").strip()
        if running_path and not self._same_path(running_path, self.helper_path):
            return False
        self._set_helper_endpoint(port)
        self._helper_pid = int(health.get("pid") or state.get("pid") or 0)
        self._helper_ready = True
        self._helper_consecutive_failures = 0
        self._helper_last_success_at = time.monotonic()
        self._save_helper_state(health)
        self._record_diagnostic_event("mediabridge", "adopted", {"port": port, "pid": self._helper_pid, "path": running_path})
        return True

    def _stop_helper_unlocked(self, force: bool = True, reason: str = "") -> Dict[str, Any]:
        target_port = int(self.port or 0)
        health = self._helper_health(timeout=0.4)
        pids: Set[int] = set()
        if isinstance(health, dict):
            try:
                pids.add(int(health.get("pid") or 0))
            except Exception:
                pass
        state = self._load_helper_state()
        try:
            pids.add(int(state.get("pid") or 0))
        except Exception:
            pass
        if self._helper_pid:
            pids.add(int(self._helper_pid))
        pids.discard(0)
        self._record_diagnostic_event("mediabridge", "stop_begin", {"reason": reason, "port": target_port, "pids": sorted(pids), "force": bool(force)}, "warning")
        if target_port > 0:
            try:
                self._request_json_no_helper("/shutdown", "POST", timeout=0.45)
            except Exception as exc:
                self._record_diagnostic_event("mediabridge", "shutdown_request_failed", {"error": str(exc), "port": target_port}, "warning")
        self._wait_helper_down(timeout=0.8, port=target_port)
        if force:
            pids.update(self._plugin_helper_process_ids(["MediaBridge.exe"]))
            for process_id in sorted(pid for pid in pids if pid > 0):
                self._terminate_process_native(process_id, timeout=1.8)
        down = self._wait_helper_down(timeout=1.2, port=target_port)
        self._helper_ready = False
        self._helper_pid = 0
        self._helper_process = None
        self._helper_consecutive_failures = 0
        self._clear_helper_state()
        self._set_helper_endpoint(0)
        self._record_diagnostic_event("mediabridge", "stop_complete", {"reason": reason, "down": down, "terminatedPids": sorted(pids)})
        return {"down": down, "pids": sorted(pids), "oldPort": target_port}

    def _ensure_helper(self) -> None:
        with self._helper_restart_lock:
            self._ensure_helper_unlocked()

    def _ensure_helper_unlocked(self) -> None:
        if not self._is_windows():
            raise RuntimeError("Questo plugin funziona solo su Windows")
        if self._update_quiescing():
            raise RuntimeError("Awesome Mix is being updated; helpers stay stopped")
        self._prepare_helper_runtime()

        health = self._helper_health(timeout=0.55) if self.port else None
        if isinstance(health, dict):
            running_path = str(health.get("processPath") or "").strip()
            if not running_path or self._same_path(running_path, self.helper_path):
                self._helper_ready = True
                self._helper_pid = int(health.get("pid") or 0)
                self._helper_consecutive_failures = 0
                self._helper_last_success_at = time.monotonic()
                self._save_helper_state(health)
                return

        # Another plugin object may already have replaced a stale bridge. Always
        # prefer the registered healthy instance before starting a new process.
        if self._adopt_registered_helper():
            return

        # Remove orphaned copies left behind by a crashed/reloaded Decky backend.
        orphaned = sorted(self._plugin_helper_process_ids(["MediaBridge.exe"]))
        if orphaned:
            self._record_diagnostic_event("mediabridge", "orphan_cleanup", {"pids": orphaned}, "warning")
            for process_id in orphaned:
                self._terminate_process_native(process_id, timeout=1.8)

        creationflags = self._task_creationflags()
        if hasattr(subprocess, "DETACHED_PROCESS"):
            creationflags |= subprocess.DETACHED_PROCESS

        last_error = ""
        for attempt in range(1, 4):
            port = self._allocate_helper_port()
            self._set_helper_endpoint(port)
            self._record_diagnostic_event("mediabridge", "start_attempt", {"attempt": attempt, "port": port, "path": self.helper_path})
            interactive = self._launch_hidden_as_interactive_user(
                self.helper_path, ["--server", "--port", str(port)], self.helper_dir
            )
            process = None
            launch_mode = str(interactive.get("mode") or "")
            if interactive.get("ok"):
                self._helper_pid = int(interactive.get("pid") or 0)
            else:
                self._record_diagnostic_event("mediabridge", "interactive_launch_failed", {"error": interactive.get("error"), "port": port}, "warning")
                process = subprocess.Popen(
                    [self.helper_path, "--server", "--port", str(port)],
                    cwd=self.helper_dir,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    stdin=subprocess.DEVNULL,
                    creationflags=creationflags,
                )
                self._helper_pid = int(process.pid or 0)
                launch_mode = "backend-token"
            self._helper_process = process
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline:
                health = self._helper_health(timeout=0.35)
                if isinstance(health, dict):
                    self._helper_ready = True
                    self._helper_pid = int(health.get("pid") or self._helper_pid or 0)
                    self._helper_consecutive_failures = 0
                    self._helper_last_success_at = time.monotonic()
                    self._save_helper_state(health)
                    self._record_diagnostic_event("mediabridge", "started", {"attempt": attempt, "port": port, "pid": self._helper_pid, "launchMode": launch_mode, "sessionId": self._process_session_id(self._helper_pid), "health": health})
                    return
                if process is not None and process.poll() is not None:
                    last_error = f"MediaBridge exited with code {process.returncode} on port {port}"
                    break
                time.sleep(0.12)
            self._terminate_process_native(self._helper_pid, timeout=1.2)
            self._record_diagnostic_event("mediabridge", "start_failed", {"attempt": attempt, "port": port, "pid": self._helper_pid, "error": last_error or "health timeout"}, "error")
            self._helper_ready = False
            self._helper_pid = 0
            self._helper_process = None
            self._set_helper_endpoint(0)

        raise RuntimeError(last_error or "MediaBridge did not start correctly")

    def _mark_helper_success(self, path: str, elapsed: float) -> None:
        self._helper_ready = True
        self._helper_consecutive_failures = 0
        self._helper_last_success_at = time.monotonic()
        self._record_diagnostic_event("mediabridge", "request_ok", {"path": path, "elapsedMs": round(elapsed * 1000, 1), "port": self.port})

    def _mark_helper_failure(self, path: str, error: Exception, elapsed: float) -> None:
        self._helper_ready = False
        self._helper_consecutive_failures += 1
        self._helper_last_failure_at = time.monotonic()
        self._helper_last_failure = str(error)
        self._record_diagnostic_event(
            "mediabridge",
            "request_failed",
            {"path": path, "elapsedMs": round(elapsed * 1000, 1), "port": self.port, "consecutive": self._helper_consecutive_failures, "error": str(error)},
            "error",
        )
        if self._helper_consecutive_failures >= 2:
            self._schedule_helper_recovery(f"request failure on {path}: {error}")

    def _request_json_once(self, path: str, method: str = "GET", timeout: float = 1.0) -> Dict[str, Any]:
        if not self.base_url:
            raise RuntimeError("MediaBridge endpoint is unavailable")
        with self._helper_request_lock:
            started = time.monotonic()
            req = urllib.request.Request(f"{self.base_url}{path}", method=method)
            try:
                with urllib.request.urlopen(req, timeout=max(0.3, float(timeout))) as response:
                    raw = response.read().decode("utf-8")
                    result = json.loads(raw) if raw else {}
                self._mark_helper_success(path, time.monotonic() - started)
                return result if isinstance(result, dict) else {}
            except Exception as exc:
                self._mark_helper_failure(path, exc, time.monotonic() - started)
                raise

    def _request_json(self, path: str, method: str = "GET") -> Dict[str, Any]:
        if not self._helper_ready:
            self._ensure_helper()
        timeout = 2.2 if str(path).startswith("/snapshot") else 1.25
        return self._request_json_once(path, method, timeout=timeout)

    def _request_json_no_helper(self, path: str, method: str = "GET", timeout: float = 0.8) -> Dict[str, Any]:
        if not self.base_url:
            raise RuntimeError("MediaBridge endpoint is unavailable")
        req = urllib.request.Request(f"{self.base_url}{path}", method=method)
        with urllib.request.urlopen(req, timeout=max(0.2, float(timeout))) as response:
            raw = response.read().decode("utf-8")
            return json.loads(raw) if raw else {}

    def _update_quiescing(self) -> bool:
        return time.monotonic() < self._update_quiesce_until

    def _stop_helpers_for_exit(self, reason: str, quiesce_seconds: float = 0.0) -> Dict[str, Any]:
        """Stop MediaBridge (asked first, then forced) and every other helper this plugin started.

        Used before an update is handed to Decky and from _unload. With
        ``quiesce_seconds`` no helper is restarted for that long, so nothing
        reopens a file in the plugin folder while Decky replaces it.
        """
        if quiesce_seconds > 0:
            self._update_quiesce_until = time.monotonic() + quiesce_seconds
        result: Dict[str, Any] = {"reason": reason}
        try:
            self._youtube_music.cancel_browser_auth()
        except Exception:
            pass
        if not self._is_windows():
            return result
        locked = self._helper_restart_lock.acquire(timeout=4.0)
        try:
            result["mediabridge"] = self._stop_helper_unlocked(force=True, reason=reason)
        except Exception as exc:
            result["mediabridge"] = {"error": str(exc)}
            self._log(f"helper stop ({reason}) error: {exc}")
        finally:
            if locked:
                self._helper_restart_lock.release()
        try:
            self._kill_all_plugin_helpers()
        except Exception as exc:
            self._log(f"auxiliary helper stop ({reason}) error: {exc}")
        return result

    def _shutdown_helper(self) -> None:
        if not self._is_windows() or not self.port:
            return
        try:
            self._request_json_no_helper("/shutdown", "POST", timeout=0.45)
            self._log("helper shutdown requested")
        except Exception as exc:
            self._record_diagnostic_event("mediabridge", "shutdown_request_failed", {"error": str(exc)}, "warning")
        finally:
            self._helper_ready = False

    def _reset_runtime_state_after_recovery(self) -> None:
        with self._snapshot_lock:
            self._snapshot_cache = {"selectedPlayer": "", "currentPlayer": "", "selected": None, "players": []}
            self._snapshot_cache_at = 0.0
            self._snapshot_last_success_at = 0.0
        self.player = ""
        self._helper_forced_player_id = ""
        self._helper_forced_player_at = 0.0
        with self._app_running_cache_lock:
            self._app_running_cache.clear()
        with self._app_launch_lock:
            self._app_launch_attempts.clear()
        self._direct_volume_session = None
        self._direct_volume_session_key = ""
        self._direct_volume_session_at = 0.0
        self._topbar_last_signature = ""
        self._topbar_last_injected_at = 0.0
        self._topbar_cached_label = ""
        self._topbar_cached_at = 0.0
        self._direct_media_manager = None
        self._direct_media_manager_at = 0.0
        self._direct_media_snapshot = self._empty_snapshot()
        self._direct_media_snapshot_at = 0.0
        self._direct_media_lock = None
        self._direct_media_last_error = ""

    def _recover_helper_sync(self, reason: str, full: bool = False) -> Dict[str, Any]:
        if not self._is_windows():
            return {"ok": False, "message": "Windows only", "steps": []}
        if not self._helper_restart_lock.acquire(timeout=5.0):
            return {"ok": False, "message": "Plugin service recovery is already running", "steps": []}
        steps: List[Dict[str, Any]] = []
        request_gate_acquired = False
        self._service_restart_in_progress = True
        try:
            request_gate_acquired = self._helper_request_lock.acquire(timeout=3.0)
            if not request_gate_acquired:
                raise RuntimeError("Timed out while waiting for an active MediaBridge request")
            self._record_diagnostic_event("recovery", "begin", {"reason": reason, "full": bool(full)}, "warning")
            steps.append({"step": "capture", "helperHealth": self._helper_health(timeout=0.45) or {}, "helperState": self._load_helper_state()})
            stopped = self._stop_helper_unlocked(force=True, reason=reason)
            steps.append({"step": "stop_helpers", **stopped})
            if full:
                self._youtube_music.cancel_browser_auth()
                self._kill_named_process("AppVolumeBridge.exe")
                self._kill_named_process("ThumbnailBridge.exe")
                steps.append({"step": "stop_auxiliary_helpers", "ok": True})
            self._reset_runtime_state_after_recovery()
            steps.append({"step": "clear_runtime_state", "ok": True})
            if full:
                try:
                    shutil.rmtree(self.artist_background_preview_dir, ignore_errors=True)
                    with self._artist_background_candidates_lock:
                        self._artist_background_candidates.clear()
                    steps.append({"step": "clear_preview_transport_cache", "ok": True})
                except Exception as exc:
                    steps.append({"step": "clear_preview_transport_cache", "ok": False, "error": str(exc)})
                try:
                    if self._local_music_stream_server is None:
                        self._local_music_stream_server = LocalMusicStreamServer(
                            self._local_music_stream_path,
                            self._local_music_stream_asset_path,
                            self._log,
                            self._youtube_music.resolve_stream,
                        )
                        self._local_music_stream_server.start()
                    steps.append({"step": "verify_local_asset_server", "ok": bool(self._local_music_stream_server and self._local_music_stream_server.base_url), "baseUrl": self._local_music_stream_server.base_url if self._local_music_stream_server else ""})
                except Exception as exc:
                    steps.append({"step": "verify_local_asset_server", "ok": False, "error": str(exc)})
            self._ensure_helper_unlocked()
            health = self._helper_health(timeout=0.75) or {}
            steps.append({"step": "start_mediabridge", "ok": bool(health.get("ok")), "health": health, "port": self.port})
            if not bool(health.get("ok")):
                raise RuntimeError("MediaBridge did not restart correctly")

            snapshot: Dict[str, Any] = self._empty_snapshot()
            snapshot_error = ""
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline:
                try:
                    snapshot = self._canonical_snapshot(self._request_json_once("/snapshot", timeout=1.5))
                    break
                except Exception as exc:
                    snapshot_error = str(exc)
                    time.sleep(0.25)
            with self._snapshot_lock:
                self._snapshot_cache = snapshot
                self._snapshot_cache_at = time.monotonic()
                if snapshot.get("players"):
                    self._snapshot_last_success_at = time.monotonic()
            players = snapshot.get("players") if isinstance(snapshot.get("players"), list) else []
            if players:
                try:
                    normalized = self._normalize_snapshot_for_active_service(snapshot)
                    selected_id = str(normalized.get("selectedPlayer") or normalized.get("currentPlayer") or "")
                    if selected_id:
                        encoded = urllib.parse.quote(selected_id, safe="")
                        self._request_json_once(f"/select?player={encoded}", "POST", timeout=1.5)
                        snapshot = normalized
                        steps.append({"step": "select_active_session", "ok": True, "player": selected_id})
                except Exception as exc:
                    steps.append({"step": "select_active_session", "ok": False, "error": str(exc)})
            steps.append({"step": "probe_sessions", "ok": not bool(snapshot_error), "players": len(players), "error": snapshot_error, "snapshot": self._diagnostic_snapshot_summary(snapshot)})
            self._helper_recovery_cooldown_until = time.monotonic() + 8.0
            self._record_diagnostic_event("recovery", "complete", {"reason": reason, "full": bool(full), "port": self.port, "pid": int(health.get("pid") or 0), "players": len(players)})
            return {
                "ok": True,
                "message": "Plugin services recovered",
                "steps": steps,
                "snapshot": snapshot,
                "mediaBridgePid": int(health.get("pid") or 0),
                "mediaBridgePort": int(self.port),
                "warnings": ["No Windows media sessions were visible immediately after restart"] if not players else [],
            }
        except Exception as exc:
            self._helper_ready = False
            self._record_diagnostic_event("recovery", "failed", {"reason": reason, "full": bool(full), "error": str(exc), "steps": steps}, "error")
            self._log(f"plugin service recovery failed: {exc}")
            return {"ok": False, "message": str(exc), "steps": steps}
        finally:
            if request_gate_acquired:
                self._helper_request_lock.release()
            self._service_restart_in_progress = False
            self._helper_restart_lock.release()

    def _automatic_helper_recovery_worker(self, reason: str) -> None:
        try:
            self._recover_helper_sync(reason, full=False)
        finally:
            self._helper_recovery_scheduled = False

    def _schedule_helper_recovery(self, reason: str) -> None:
        if self._service_restart_in_progress or self._helper_recovery_scheduled:
            return
        if time.monotonic() < self._helper_recovery_cooldown_until:
            return
        self._helper_recovery_scheduled = True
        self._record_diagnostic_event("recovery", "scheduled", {"reason": reason}, "warning")
        try:
            self._recovery_executor.submit(self._automatic_helper_recovery_worker, reason)
        except Exception as exc:
            self._helper_recovery_scheduled = False
            self._log(f"automatic helper recovery scheduling failed: {exc}")

    def _restart_plugin_services_sync(self) -> Dict[str, Any]:
        return self._recover_helper_sync("manual Settings recovery", full=True)

    async def restart_plugin_services(self) -> Dict[str, Any]:
        if self._service_restart_request_pending or self._service_restart_in_progress:
            return {"ok": False, "message": "Plugin service recovery is already running", "steps": []}
        self._service_restart_request_pending = True
        try:
            return await asyncio.wait_for(
                self._run_in_executor(self._recovery_executor, self._restart_plugin_services_sync),
                timeout=30.0,
            )
        except asyncio.TimeoutError:
            self._record_diagnostic_event("recovery", "ui_timeout", {"timeoutSeconds": 30}, "error")
            return {"ok": False, "message": "Plugin service recovery exceeded 30 seconds. Export diagnostics for details.", "steps": []}
        finally:
            self._service_restart_request_pending = False

    def _sanitize_text(self, value: str) -> str:
        value = (value or "").strip().lower()
        value = re.sub(r"\s+", " ", value)
        return value

    def _image_extension_from_content_type(self, content_type: str) -> str:
        value = str(content_type or "").split(";", 1)[0].strip().lower()
        if value == "image/png":
            return "png"
        if value == "image/webp":
            return "webp"
        if value == "image/avif":
            return "avif"
        if value == "image/gif":
            return "gif"
        return "jpg"

    def _cover_key(self, title: str, artist: str, album: str) -> str:
        return " | ".join([
            self._sanitize_text(title),
            self._sanitize_text(artist),
            self._sanitize_text(album),
        ]).strip()

    def _load_cover_cache(self) -> Dict[str, str]:
        if self._cover_cache is not None:
            return self._cover_cache

        try:
            with open(self.cover_cache_path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
                if isinstance(data, dict):
                    self._cover_cache = {str(k): str(v) for k, v in data.items()}
                    return self._cover_cache
        except Exception:
            pass

        self._cover_cache = {}
        return self._cover_cache

    def _save_cover_cache(self) -> None:
        if self._cover_cache is None:
            return

        try:
            with open(self.cover_cache_path, "w", encoding="utf-8") as handle:
                json.dump(self._cover_cache, handle, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def _normalize_cover_source(self, source: Any) -> str:
        normalized = str(source or "").strip().lower()
        return "windows" if normalized == "windows" else "online"

    def _load_cover_source_setting(self) -> str:
        try:
            with open(self.cover_settings_path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
                if isinstance(data, dict):
                    return self._normalize_cover_source(data.get("source"))
        except Exception:
            pass
        return "online"

    def _save_cover_source_setting(self) -> None:
        try:
            with open(self.cover_settings_path, "w", encoding="utf-8") as handle:
                json.dump({"source": self.cover_source}, handle, ensure_ascii=False, indent=2)
        except Exception as exc:
            self._log(f"cover source save error: {exc}")

    async def get_cover_source(self) -> str:
        return self._normalize_cover_source(self.cover_source)

    async def set_cover_source(self, source: str) -> str:
        self.cover_source = self._normalize_cover_source(source)
        self._save_cover_source_setting()
        return self.cover_source

    def _normalized_service_key(self, service: Any) -> str:
        value = re.sub(r"[^a-z0-9]+", "", str(service or "").lower())
        aliases = {
            "localmusic": "localMusic",
            "youtube": "youtubeMusic",
            "youtubemusic": "youtubeMusic",
            "ytmusic": "youtubeMusic",
            "apple": "appleMusic",
            "applemusic": "appleMusic",
        }
        return aliases.get(value, str(service or "music"))

    def _normalized_active_service(self) -> str:
        active_service = getattr(self, "active_service", "localMusic")
        value = re.sub(r"[^a-z0-9]+", "", str(active_service or "").lower())
        aliases = {
            "localmusic": "localMusic",
            "youtube": "youtubeMusic",
            "youtubemusic": "youtubeMusic",
            "ytmusic": "youtubeMusic",
            "apple": "appleMusic",
            "applemusic": "appleMusic",
        }
        return aliases.get(value, str(active_service or "music"))

    def _media_player_match_score(self, player: Dict[str, Any], service: str) -> int:
        if not isinstance(player, dict):
            return 0
        identity_text = " ".join(
            str(player.get(key) or "")
            for key in ("id", "name", "appId", "app_id", "source", "process", "processName")
        ).lower()
        metadata_text = " ".join(
            str(player.get(key) or "") for key in ("title", "artist", "album")
        ).lower()
        identity = re.sub(r"[^a-z0-9]+", "", identity_text)
        metadata = re.sub(r"[^a-z0-9]+", "", metadata_text)
        tokens = {
            "appleMusic": ("applemusic", "appleincapplemusicwin", "musicui"),
        }.get(service, ())
        score = 0
        for token in tokens:
            if token and token in identity:
                score += 24
            elif token and token in metadata:
                score += 3
        status = str(player.get("status") or "").lower()
        if status == "playing":
            score += 4
        elif status == "paused":
            score += 2
        if player.get("title"):
            score += 1
        return score

    def _prepare_direct_media_runtime(self) -> bool:
        if not self._is_windows():
            self._direct_media_available = False
            return False
        if self._direct_media_available is not None:
            return bool(self._direct_media_available)
        try:
            vendor_path = self._volume_vendor_path()
            if vendor_path and os.path.isdir(vendor_path) and vendor_path not in sys.path:
                sys.path.insert(0, vendor_path)
            from winrt.windows.media.control import GlobalSystemMediaTransportControlsSessionManager  # type: ignore  # noqa: F401
            self._direct_media_available = True
            self._record_diagnostic_event("direct_smtc", "runtime_ready", {"vendorPath": vendor_path})
            return True
        except Exception as exc:
            self._direct_media_available = False
            self._direct_media_last_error = str(exc)
            self._record_diagnostic_event("direct_smtc", "runtime_unavailable", {"error": str(exc)}, "error")
            return False

    async def _direct_media_manager_async(self, force: bool = False) -> Any:
        if not self._prepare_direct_media_runtime():
            return None
        now = time.monotonic()
        if self._direct_media_manager is not None and not force and now - self._direct_media_manager_at < 120.0:
            return self._direct_media_manager
        from winrt.windows.media.control import GlobalSystemMediaTransportControlsSessionManager  # type: ignore
        manager = await asyncio.wait_for(GlobalSystemMediaTransportControlsSessionManager.request_async(), timeout=3.5)
        self._direct_media_manager = manager
        self._direct_media_manager_at = time.monotonic()
        return manager

    @staticmethod
    def _direct_media_status_name(value: Any) -> str:
        numeric = int(value or 0)
        return {
            0: "Closed",
            1: "Opened",
            2: "Changing",
            3: "Stopped",
            4: "Playing",
            5: "Paused",
        }.get(numeric, "Unknown")

    async def _direct_media_player_payload(self, session: Any, current_id: str) -> Dict[str, Any]:
        session_id = str(getattr(session, "source_app_user_model_id", "") or "")
        properties: Any = None
        try:
            properties = await asyncio.wait_for(session.try_get_media_properties_async(), timeout=2.2)
        except Exception as exc:
            self._record_diagnostic_event("direct_smtc", "media_properties_failed", {"id": session_id, "error": str(exc)}, "warning")
        playback_info = None
        timeline = None
        try:
            playback_info = session.get_playback_info()
        except Exception:
            pass
        try:
            timeline = session.get_timeline_properties()
        except Exception:
            pass
        controls = getattr(playback_info, "controls", None) if playback_info is not None else None
        repeat_value = getattr(playback_info, "auto_repeat_mode", None) if playback_info is not None else None
        repeat_numeric = int(repeat_value) if repeat_value is not None else 0
        status = self._direct_media_status_name(getattr(playback_info, "playback_status", 0) if playback_info is not None else 0)
        position_ms = 0
        length_ms = 0
        if timeline is not None:
            try:
                position_ms = max(0, int(timeline.position.total_seconds() * 1000))
            except Exception:
                pass
            try:
                length_ms = max(0, int(timeline.end_time.total_seconds() * 1000))
            except Exception:
                pass
        title = str(getattr(properties, "title", "") or "") if properties is not None else ""
        artist = str(getattr(properties, "artist", "") or getattr(properties, "album_artist", "") or "") if properties is not None else ""
        album = str(getattr(properties, "album_title", "") or "") if properties is not None else ""
        return {
            "id": session_id,
            "name": session_id,
            "sourceAppUserModelId": session_id,
            "title": title,
            "artist": artist,
            "album": album,
            "status": status,
            "position": position_ms,
            "length": length_ms,
            "canNext": bool(getattr(controls, "is_next_enabled", False)),
            "canPrevious": bool(getattr(controls, "is_previous_enabled", False)),
            "canPlay": bool(getattr(controls, "is_play_enabled", False)),
            "canPause": bool(getattr(controls, "is_pause_enabled", False)),
            "canTogglePlayPause": bool(getattr(controls, "is_play_pause_toggle_enabled", False)),
            "canShuffle": bool(getattr(controls, "is_shuffle_enabled", False)),
            "canRepeat": bool(getattr(controls, "is_repeat_enabled", False)),
            "shuffleActive": bool(getattr(playback_info, "is_shuffle_active", False)) if playback_info is not None else False,
            "repeatMode": "Track" if repeat_numeric == 1 else "List" if repeat_numeric == 2 else "Off",
            "isSelected": bool(session_id and session_id == current_id),
            "isCurrent": bool(session_id and session_id == current_id),
            "transport": "python-winrt",
        }

    async def _direct_media_snapshot_async(self, force: bool = False) -> Dict[str, Any]:
        if self._direct_media_lock is None:
            self._direct_media_lock = asyncio.Lock()
        now = time.monotonic()
        if not force and now - self._direct_media_snapshot_at < 0.35:
            return self._canonical_snapshot(self._direct_media_snapshot)
        async with self._direct_media_lock:
            now = time.monotonic()
            if not force and now - self._direct_media_snapshot_at < 0.35:
                return self._canonical_snapshot(self._direct_media_snapshot)
            started = time.monotonic()
            try:
                manager = await self._direct_media_manager_async(force=force)
                if manager is None:
                    return self._empty_snapshot()
                sessions = list(manager.get_sessions() or [])
                current = manager.get_current_session()
                current_id = str(getattr(current, "source_app_user_model_id", "") or "") if current is not None else ""
                players = [await self._direct_media_player_payload(session, current_id) for session in sessions]
                selected = next((player for player in players if player.get("isCurrent")), None)
                payload = self._canonical_snapshot({
                    "selectedPlayer": str((selected or {}).get("id") or ""),
                    "currentPlayer": current_id,
                    "selected": selected,
                    "players": players,
                    "transport": "python-winrt",
                })
                payload = self._normalize_snapshot_for_active_service(payload)
                self._direct_media_snapshot = payload
                self._direct_media_snapshot_at = time.monotonic()
                self._direct_media_last_error = ""
                self._record_diagnostic_event(
                    "direct_smtc",
                    "snapshot_ok",
                    {"players": len(players), "elapsedMs": round((time.monotonic() - started) * 1000, 1), "current": current_id},
                )
                return payload
            except Exception as exc:
                self._direct_media_last_error = str(exc)
                self._direct_media_manager = None
                self._direct_media_manager_at = 0.0
                self._record_diagnostic_event(
                    "direct_smtc",
                    "snapshot_failed",
                    {"error": str(exc), "elapsedMs": round((time.monotonic() - started) * 1000, 1)},
                    "error",
                )
                return self._empty_snapshot()

    async def _direct_media_selected_session_async(self) -> Any:
        manager = await self._direct_media_manager_async()
        if manager is None:
            return None
        sessions = list(manager.get_sessions() or [])
        if not sessions:
            return None
        service = self._normalized_active_service()
        ranked: List[Tuple[int, Any]] = []
        for session in sessions:
            session_id = str(getattr(session, "source_app_user_model_id", "") or "")
            payload = {"id": session_id, "name": session_id, "sourceAppUserModelId": session_id}
            ranked.append((self._media_player_match_score(payload, service), session))
        ranked.sort(key=lambda value: value[0], reverse=True)
        if ranked and ranked[0][0] > 0:
            return ranked[0][1]
        current = manager.get_current_session()
        if current is not None:
            return current
        return sessions[0] if len(sessions) == 1 else None

    async def _direct_media_action_async(self, action: str) -> bool:
        if not self._prepare_direct_media_runtime():
            return False
        try:
            session = await self._direct_media_selected_session_async()
            if session is None:
                return False
            command = str(action or "").strip().lower()
            if command == "playpause":
                result = await asyncio.wait_for(session.try_toggle_play_pause_async(), timeout=2.0)
            elif command == "next":
                result = await asyncio.wait_for(session.try_skip_next_async(), timeout=2.0)
            elif command == "previous":
                result = await asyncio.wait_for(session.try_skip_previous_async(), timeout=2.0)
            elif command == "shuffle":
                info = session.get_playback_info()
                result = await asyncio.wait_for(session.try_change_shuffle_active_async(not bool(getattr(info, "is_shuffle_active", False))), timeout=2.0)
            elif command == "repeat":
                from winrt.windows.media import MediaPlaybackAutoRepeatMode  # type: ignore
                info = session.get_playback_info()
                current = getattr(info, "auto_repeat_mode", None)
                numeric = int(current) if current is not None else 0
                target = MediaPlaybackAutoRepeatMode.TRACK if numeric == 0 else MediaPlaybackAutoRepeatMode.LIST if numeric == 1 else MediaPlaybackAutoRepeatMode.NONE
                result = await asyncio.wait_for(session.try_change_auto_repeat_mode_async(target), timeout=2.0)
            else:
                return False
            self._direct_media_snapshot_at = 0.0
            self._record_diagnostic_event("direct_smtc", "action", {"action": command, "ok": bool(result)})
            return bool(result)
        except Exception as exc:
            self._direct_media_last_error = str(exc)
            self._record_diagnostic_event("direct_smtc", "action_failed", {"action": action, "error": str(exc)}, "error")
            return False

    @staticmethod
    def _empty_snapshot() -> Dict[str, Any]:
        return {"selectedPlayer": "", "currentPlayer": "", "selected": None, "players": []}

    def _canonical_snapshot(self, data: Any) -> Dict[str, Any]:
        if not isinstance(data, dict):
            return self._empty_snapshot()
        raw_players = data.get("players")
        players = [dict(value) for value in raw_players if isinstance(value, dict)] if isinstance(raw_players, list) else []
        selected = data.get("selected") if isinstance(data.get("selected"), dict) else None
        if selected is None:
            selected = next((value for value in players if value.get("isSelected") or value.get("isCurrent")), None)
        if selected is None and players:
            selected = players[0]
        selected_player = str(data.get("selectedPlayer") or data.get("currentPlayer") or (selected or {}).get("id") or (selected or {}).get("name") or "")
        current_player = str(data.get("currentPlayer") or selected_player)
        return {
            **data,
            "selectedPlayer": selected_player,
            "currentPlayer": current_player,
            "selected": selected,
            "players": players,
        }

    def _normalize_snapshot_for_active_service(self, data: Dict[str, Any]) -> Dict[str, Any]:
        data = self._canonical_snapshot(data)
        service = self._normalized_active_service()
        if service in {"localMusic", "youtubeMusic"}:
            return data
        players = [dict(value) for value in data.get("players", []) if isinstance(value, dict)]
        if not players:
            return data
        ranked = sorted(
            ((self._media_player_match_score(value, service), index, value) for index, value in enumerate(players)),
            key=lambda item: (item[0], str(item[2].get("status") or "").lower() == "playing"),
            reverse=True,
        )
        best_score, _index, best = ranked[0]
        if best_score <= 0:
            # Some Windows Store/PWA sessions expose generic SMTC identities.
            # When the explicitly selected app is running and there is a single
            # playable session, prefer it instead of showing an empty player.
            app_key = self._music_app_key_for_service(service)
            likely = [value for value in players if str(value.get("title") or "").strip() and str(value.get("status") or "").lower() in {"playing", "paused", "stopped", "unknown"}]
            playing = [value for value in likely if str(value.get("status") or "").lower() == "playing"]
            selected = [value for value in likely if value.get("isSelected") or value.get("isCurrent")]
            app_running = bool(app_key and self._is_music_app_running_sync(app_key, max_age=1.0))
            fallback = playing[0] if len(playing) == 1 else selected[0] if len(selected) == 1 else likely[0] if len(likely) == 1 else None
            if app_running and fallback is not None:
                best = fallback
                best_score = 1
                self._log(f"generic SMTC fallback selected for {service}: {str(best.get('id') or best.get('name') or '')}")
            else:
                self._log_snapshot_diagnostic(data, f"no_match_{service}")
                return data
        best_id = str(best.get("id") or best.get("name") or "")
        normalized_players = []
        for value in players:
            current_id = str(value.get("id") or value.get("name") or "")
            value["isSelected"] = bool(current_id == best_id)
            value["isCurrent"] = bool(current_id == best_id)
            normalized_players.append(value)
        result = dict(data)
        result["players"] = normalized_players
        result["selected"] = next((value for value in normalized_players if value.get("isSelected")), best)
        result["selectedPlayer"] = best_id
        result["currentPlayer"] = best_id
        self.player = best_id
        return result

    def _select_active_media_session_sync(self) -> None:
        service = self._normalized_active_service()
        if service in {"localMusic", "youtubeMusic"}:
            return
        try:
            data = self._request_json("/snapshot")
            normalized = self._normalize_snapshot_for_active_service(data)
            selected = normalized.get("selected") if isinstance(normalized, dict) else None
            selected_id = str((selected or {}).get("id") or (selected or {}).get("name") or "") if isinstance(selected, dict) else ""
            if selected_id:
                encoded = urllib.parse.quote(selected_id, safe="")
                self._request_json(f"/select?player={encoded}", "POST")
                with self._snapshot_lock:
                    self._snapshot_cache = normalized
                    self._snapshot_cache_at = time.monotonic()
        except Exception as exc:
            self._log(f"active media session selection error: {exc}")

    def _snapshot_sync(self, max_age: float = 0.18) -> Dict[str, Any]:
        if self._service_restart_in_progress:
            with self._snapshot_lock:
                return self._canonical_snapshot(self._snapshot_cache)
        self._watchdog_helper_memory()
        now = time.monotonic()
        with self._snapshot_lock:
            if self._snapshot_cache and now - self._snapshot_cache_at <= max_age:
                return self._snapshot_cache

        # QAM, Big Picture and the top bar may poll at the same time. MediaBridge
        # should receive one snapshot request, not a burst of parallel requests.
        acquired = self._snapshot_refresh_lock.acquire(timeout=0.22)
        if not acquired:
            with self._snapshot_lock:
                return self._canonical_snapshot(self._snapshot_cache)
        try:
            now = time.monotonic()
            with self._snapshot_lock:
                if self._snapshot_cache and now - self._snapshot_cache_at <= max_age:
                    return self._snapshot_cache
            data = self._canonical_snapshot(self._request_json("/snapshot"))
            if isinstance(data, dict):
                raw_selected = str(data.get("selectedPlayer") or data.get("currentPlayer") or "")
                data = self._normalize_snapshot_for_active_service(data)
                target_selected = str(data.get("selectedPlayer") or data.get("currentPlayer") or "")
                if target_selected and target_selected != raw_selected:
                    selection_now = time.monotonic()
                    should_select = (
                        target_selected != self._helper_forced_player_id
                        or selection_now - self._helper_forced_player_at >= 1.5
                    )
                    if should_select:
                        try:
                            encoded = urllib.parse.quote(target_selected, safe="")
                            self._request_json(f"/select?player={encoded}", "POST")
                            self._helper_forced_player_id = target_selected
                            self._helper_forced_player_at = selection_now
                        except Exception as exc:
                            self._log(f"active media session sync error: {exc}")
                with self._snapshot_lock:
                    self._snapshot_cache = data
                    self._snapshot_cache_at = time.monotonic()
                    self._snapshot_last_success_at = self._snapshot_cache_at
                if isinstance(data.get("selectedPlayer"), str) and data.get("selectedPlayer"):
                    self.player = data["selectedPlayer"]
                return data
            with self._snapshot_lock:
                return self._canonical_snapshot(self._snapshot_cache)
        finally:
            self._snapshot_refresh_lock.release()

    def _current_track_from_data(self, data: Any) -> Dict[str, Any]:
        if not isinstance(data, dict):
            return {}
        current = data.get("selected")
        if not isinstance(current, dict):
            players = data.get("players")
            current = players[0] if isinstance(players, list) and players else {}
        return current if isinstance(current, dict) else {}

    def _cached_current_track_snapshot(self) -> Dict[str, Any]:
        with self._snapshot_lock:
            return self._current_track_from_data(self._snapshot_cache)

    def _current_track_snapshot(self) -> Dict[str, Any]:
        try:
            data = self._snapshot_sync(max_age=0.75)
        except Exception:
            data = self._snapshot_cache
        return self._current_track_from_data(data)

    def _current_volume_process_names(self) -> List[str]:
        # Prefer the explicitly selected source. Media sessions can temporarily be
        # blank while an app starts, but Core Audio still exposes its process.
        current = self._cached_current_track_snapshot() or self._current_track_snapshot()
        bits = [
            str(current.get("id") or ""),
            str(current.get("name") or ""),
            str(self.player or ""),
        ]
        text = " ".join(bits).lower()
        names: List[str] = []
        service = self._normalized_active_service()
        if service == "localMusic":
            service = self._service_key_from_snapshot(current)
        service_names = {
            "appleMusic": ["applemusic", "apple music", "applemusic.exe", "music.ui"],
        }
        names.extend(service_names.get(service, []))
        candidates = [
            ("apple", service_names["appleMusic"]),
        ]
        for token, mapped in candidates:
            if token in text:
                names.extend(mapped)
        for value in bits:
            clean = re.sub(r"[^a-z0-9]+", "", value.lower())
            if clean and clean not in names:
                names.append(clean)
        return list(dict.fromkeys(name for name in names if name))

    def _service_key_from_snapshot(self, current: Dict[str, Any]) -> str:
        text = " ".join(str(current.get(key) or "") for key in ("id", "name")).lower()
        compact = re.sub(r"[^a-z0-9]+", "", text)
        if "localmusic" in compact:
            return "localMusic"
        if "youtubemusic" in compact or "ytmusic" in compact:
            return "youtubeMusic"
        if "applemusic" in compact or "musicui" in compact:
            return "appleMusic"
        active = self._normalized_active_service()
        return active if active != "localMusic" else "music"


    def _volume_vendor_path(self) -> str:
        return os.path.join(self.plugin_dir, "vendor")

    def _load_direct_volume_api(self):
        if self._direct_volume_available is False:
            return None
        vendor_path = self._volume_vendor_path()
        if vendor_path not in sys.path and os.path.isdir(vendor_path):
            sys.path.insert(0, vendor_path)
        try:
            import comtypes  # type: ignore
            from pycaw.pycaw import AudioUtilities  # type: ignore
            self._direct_volume_available = True
            return comtypes, AudioUtilities
        except Exception as exc:
            if self._direct_volume_available is not False:
                self._log(f"direct Core Audio unavailable: {exc}")
            self._direct_volume_available = False
            return None

    def _direct_session_matches(self, session: Any, names: List[str], process_names: Dict[int, str]) -> Tuple[bool, str]:
        try:
            display_name = str(getattr(session, "DisplayName", "") or "")
        except Exception:
            display_name = ""
        process_name = ""
        try:
            process_id = int(getattr(session, "ProcessId", 0) or 0)
            process_name = str(process_names.get(process_id, "") or "")
        except Exception:
            process_name = ""
        haystack = f"{process_name} {display_name}".strip().lower()
        matched = any(str(name or "").strip().lower() in haystack for name in names)
        return matched, f"{process_name} {display_name}".strip()

    def _find_direct_volume_sessions(self, audio_utilities: Any, names: List[str]) -> List[Tuple[Any, str]]:
        matches: List[Tuple[Any, str]] = []
        process_names = {
            int(item.get("pid") or 0): str(item.get("name") or "")
            for item in self._native_process_entries()
            if int(item.get("pid") or 0) > 0
        }
        for session in audio_utilities.GetAllSessions():
            matched, label = self._direct_session_matches(session, names, process_names)
            if matched:
                matches.append((session, label))
        return matches

    def _run_app_volume_direct(self, volume: Optional[int], names: List[str]) -> Optional[Dict[str, Any]]:
        loaded = self._load_direct_volume_api()
        if loaded is None:
            return None
        comtypes, audio_utilities = loaded
        try:
            # Decky invokes this function on one dedicated worker thread. Initialize
            # COM once there so rapid 1% key/gamepad steps avoid helper process starts.
            if not getattr(self._volume_thread_state, "com_initialized", False):
                comtypes.CoInitialize()
                self._volume_thread_state.com_initialized = True
            sessions = self._find_direct_volume_sessions(audio_utilities, names)
            if not sessions:
                return {"ok": False, "volume": 100, "matched": "", "reason": "no-match"}
            if volume is not None:
                for session, _label in sessions:
                    session.SimpleAudioVolume.SetMasterVolume(clamp_value(volume, 0, 100) / 100.0, None)
            current = float(sessions[0][0].SimpleAudioVolume.GetMasterVolume())
            self._direct_volume_session_at = time.monotonic()
            return {
                "ok": True,
                "volume": int(round(max(0.0, min(1.0, current)) * 100)),
                "matched": sessions[0][1],
                "matchedCount": len(sessions),
                "direct": True,
            }
        except Exception as exc:
            # Clear a stale COM pointer. A later recovery or app change can retry.
            self._direct_volume_session = None
            self._direct_volume_session_key = ""
            self._direct_volume_session_at = 0.0
            self._log(f"direct Core Audio error: {exc}")
            return None


    def _run_app_volume_helper(self, volume: Optional[int], names: List[str]) -> Dict[str, Any]:
        if not os.path.exists(self.app_volume_bridge_path):
            return {"ok": False, "volume": 100, "matched": ""}
        try:
            command = [self.app_volume_bridge_path]
            if volume is None:
                command.append("get")
            else:
                command.extend(["set", str(clamp_value(volume, 0, 100))])
            command.extend(names)
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=4,
                creationflags=self._task_creationflags(),
            )
            output = (completed.stdout or "").strip().splitlines()
            data = json.loads(output[-1]) if output else {}
            return data if isinstance(data, dict) else {"ok": False, "volume": 100, "matched": ""}
        except Exception as exc:
            self._log(f"app volume helper error: {exc}")
            return {"ok": False, "volume": 100, "matched": ""}

    def _run_app_volume(self, volume: Optional[int] = None) -> Dict[str, Any]:
        if not self._is_windows():
            return {"ok": False, "volume": 100, "matched": ""}
        names = self._current_volume_process_names()
        if not names:
            return {"ok": False, "volume": 100, "matched": ""}
        direct = self._run_app_volume_direct(volume, names)
        if isinstance(direct, dict) and direct.get("ok"):
            return direct
        return direct if isinstance(direct, dict) else {"ok": False, "volume": 100, "matched": "", "reason": "direct-core-audio-unavailable"}


    def _http_json(self, url: str, timeout: float = 4.0) -> Dict[str, Any]:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Now-playing/0.2",
                "Accept": "application/json"
            }
        )
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))

    def _cover_search_terms(self, title: str, artist: str, album: str) -> list:
        def clean_variant(value: str) -> str:
            value = (value or "").strip()
            value = re.sub(r"\s*[\(\[].*?[\)\]]", " ", value)
            value = re.sub(
                r"\s+-\s+.*\b(remaster|remastered|radio edit|single version|album version|deluxe|explicit|clean)\b.*$",
                " ",
                value,
                flags=re.IGNORECASE,
            )
            value = re.sub(r"\b(feat\.?|ft\.?)\b.*$", " ", value, flags=re.IGNORECASE)
            value = re.sub(r"\s+", " ", value)
            return value.strip()

        def push(parts) -> None:
            term = " ".join(part for part in parts if part and part.strip()).strip()
            if term and term not in search_terms:
                search_terms.append(term)

        search_terms = []
        clean_title = clean_variant(title)
        clean_album = clean_variant(album)

        push([artist, title, album])
        push([artist, title])
        push([title, artist])

        if clean_title != title or clean_album != album:
            push([artist, clean_title, clean_album])
            push([artist, clean_title])
            push([clean_title, artist])

        return search_terms[:6]

    def _itunes_identity(self, value: str) -> str:
        normalized = unicodedata.normalize("NFKD", str(value or ""))
        normalized = "".join(character for character in normalized if not unicodedata.combining(character)).lower()
        normalized = re.sub(r"\b(feat(?:uring)?|ft)\b.*$", " ", normalized)
        normalized = re.sub(r"[^a-z0-9]+", " ", normalized)
        return re.sub(r"\s+", " ", normalized).strip()

    def _best_itunes_match(self, title: str, artist: str, album: str) -> Dict[str, Any]:
        normalized_title = self._itunes_identity(title)
        normalized_artist = self._itunes_identity(artist)
        normalized_album = self._itunes_identity(album)
        cache_key = self._cover_key(title, artist, album)
        now = time.time()
        with self._itunes_match_lock:
            cached = self._itunes_match_cache.get(cache_key)
            if cached and now - float(cached[0]) < 24 * 3600:
                return dict(cached[1])
            pending = self._itunes_match_jobs.get(cache_key)
            owner = pending is None
            if owner:
                pending = Future()
                self._itunes_match_jobs[cache_key] = pending
        if not owner and pending is not None:
            try:
                return dict(pending.result(timeout=4.0))
            except Exception:
                return {}

        result: Dict[str, Any] = {}
        try:
            expected_artist_tokens = set(normalized_artist.split())
            for term in self._cover_search_terms(title, artist, album):
                url = "https://itunes.apple.com/search?media=music&entity=song&limit=8&term=" + urllib.parse.quote(term)
                payload = self._http_json(url, timeout=3.0)
                candidates = []
                for item in payload.get("results", []):
                    if not isinstance(item, dict):
                        continue
                    item_title = self._itunes_identity(item.get("trackName", ""))
                    item_artist = self._itunes_identity(item.get("artistName", ""))
                    item_artist_tokens = set(item_artist.split())
                    if not normalized_title or item_title != normalized_title:
                        continue
                    if expected_artist_tokens and not (
                        expected_artist_tokens.issubset(item_artist_tokens)
                        or item_artist_tokens.issubset(expected_artist_tokens)
                    ):
                        continue
                    score = 4 if item_artist == normalized_artist else 2
                    if normalized_album and self._itunes_identity(item.get("collectionName", "")) == normalized_album:
                        score += 3
                    if item.get("artworkUrl100"):
                        score += 1
                    candidates.append((score, item))
                if candidates:
                    result = dict(max(candidates, key=lambda entry: entry[0])[1])
                    break
        finally:
            with self._itunes_match_lock:
                self._itunes_match_cache[cache_key] = (time.time(), dict(result))
                job = self._itunes_match_jobs.pop(cache_key, None)
                if job is not None and not job.done():
                    job.set_result(dict(result))
        return result

    def _best_itunes_cover(self, title: str, artist: str, album: str) -> str:

        def upgrade_artwork_url(url: str) -> str:
            url = (url or "").strip()
            if not url:
                return ""
            return re.sub(r"/\d+x\d+bb\.", "/1200x1200bb.", url)

        match = self._best_itunes_match(title, artist, album)
        return upgrade_artwork_url(str(match.get("artworkUrl100") or "")) if match else ""

    def _best_cover(self, title: str, artist: str, album: str) -> str:
        for provider in (self._best_itunes_cover,):
            try:
                cover_url = provider(title, artist, album)
                if cover_url:
                    return cover_url
            except Exception as exc:
                self._log(f"{provider.__name__} error: {exc}")

        return ""

    def _best_smtc_cover(self, title: str, artist: str, album: str) -> str:
        if not self._is_windows() or self._update_quiescing() or not os.path.exists(self.thumbnail_bridge_path):
            return ""
        try:
            completed = subprocess.run(
                [self.thumbnail_bridge_path, self.smtc_cover_dir],
                capture_output=True,
                text=True,
                timeout=3,
                creationflags=self._task_creationflags(),
            )
            lines = (completed.stdout or "").strip().splitlines()
            data = json.loads(lines[-1]) if lines else {}
            if not isinstance(data, dict) or not data.get("ok"):
                return ""
            current_title = str(data.get("title") or "")
            current_artist = str(data.get("artist") or "")
            if title and current_title and self._sanitize_text(title) != self._sanitize_text(current_title):
                return ""
            if artist and current_artist and self._sanitize_text(artist) not in self._sanitize_text(current_artist):
                return ""
            path = str(data.get("path") or "").strip()
            content_type = str(data.get("contentType") or "image/png").strip() or "image/png"
            if path and os.path.exists(path):
                with open(path, "rb") as handle:
                    encoded = base64.b64encode(handle.read()).decode("ascii")
                return f"data:{content_type};base64,{encoded}"
            url = str(data.get("url") or "").strip()
            return url if url.startswith("data:image/") else ""
        except Exception as exc:
            self._log(f"smtc cover error: {exc}")
            return ""


    def _playback_activity_sync(self) -> Dict[str, Any]:
        service = self._normalized_active_service()
        current: Dict[str, Any] = {}

        if service in {"localMusic", "youtubeMusic"}:
            snapshot = self._local_music_snapshot_sync()
            selected = snapshot.get("selected") if isinstance(snapshot, dict) else None
            current = selected if isinstance(selected, dict) else {}
        else:
            candidate = self._current_track_snapshot()
            current = candidate if isinstance(candidate, dict) else {}

        status = str(current.get("status") or "Stopped").strip()
        return {
            "active": status.lower() == "playing",
            "status": status,
            "source": service,
            "player": str(current.get("name") or current.get("id") or ""),
            "trackId": str(current.get("id") or current.get("title") or ""),
            "updatedAt": int(time.time() * 1000),
        }

    async def get_playback_activity(self) -> Dict[str, Any]:
        # ThemeDeck only needs the authoritative Playing/Paused/Stopped state.
        return await self._run_in_executor(self._realtime_executor, self._playback_activity_sync)

    async def get_snapshot(self) -> Dict[str, Any]:
        active = self._normalized_active_service()
        if active in {"localMusic", "youtubeMusic"}:
            return await self._run_in_executor(self._realtime_executor, self._local_music_snapshot_sync)
        try:
            result = self._canonical_snapshot(await self._run_in_executor(self._realtime_executor, self._snapshot_sync, 0.08))
            if not result.get("players"):
                direct = self._canonical_snapshot(await self._direct_media_snapshot_async())
                if direct.get("players"):
                    result = direct
                    with self._snapshot_lock:
                        self._snapshot_cache = direct
                        self._snapshot_cache_at = time.monotonic()
                        self._snapshot_last_success_at = self._snapshot_cache_at
            self._log_snapshot_diagnostic(result, "get_snapshot")
            return result
        except Exception as exc:
            self._log(f"get_snapshot error: {exc}")
            direct = self._canonical_snapshot(await self._direct_media_snapshot_async(force=True))
            if direct.get("players"):
                with self._snapshot_lock:
                    self._snapshot_cache = direct
                    self._snapshot_cache_at = time.monotonic()
                    self._snapshot_last_success_at = self._snapshot_cache_at
                self._log_snapshot_diagnostic(direct, "get_snapshot_direct_fallback")
                return direct
            # Preserve the last valid state on a transient helper hiccup instead of
            # flashing an empty/fallback player and cover.
            result = self._canonical_snapshot(self._snapshot_cache)
            self._log_snapshot_diagnostic(result, "get_snapshot_fallback")
            return result

    async def set_media_player(self, player: str) -> str:
        try:
            self.player = player or ""
            encoded = urllib.parse.quote(self.player, safe="")
            await self._run_in_executor(self._realtime_executor, self._request_json, f"/select?player={encoded}", "POST")
            return self.player
        except Exception as exc:
            self._log(f"set_media_player error: {exc}")
            return ""

    def _online_cover(self, title: str, artist: str, album: str) -> str:
        key = self._cover_key(title, artist, album)
        if not key:
            return ""

        cache = self._load_cover_cache()
        cached = cache.get(key, "")
        if cached:
            upgraded = re.sub(r"/\d+x\d+bb\.", "/1200x1200bb.", cached)
            if upgraded != cached:
                cache[key] = upgraded
                self._save_cover_cache()
            return upgraded

        cover_url = self._best_cover(title, artist, album)
        if cover_url:
            cache[key] = cover_url
            self._save_cover_cache()
            return cover_url

        return ""


    def _get_cover_sync(self, title: str, artist: str, album: str) -> str:
        try:
            title = (title or "").strip()
            artist = (artist or "").strip()
            album = (album or "").strip()

            if not title or title == "Non in riproduzione":
                return ""

            source = self._normalize_cover_source(self.cover_source)
            if source == "windows":
                return self._best_smtc_cover(title, artist, album) or self._online_cover(title, artist, album)

            return self._online_cover(title, artist, album) or self._best_smtc_cover(title, artist, album)
        except Exception as exc:
            self._log(f"get_cover error: {exc}")
            return ""

    async def get_cover_for_service(self, service: str, title: str, artist: str, album: str) -> str:
        requested_service = self._normalized_service_key(service)
        if requested_service == "localMusic":
            try:
                state = dict(self._local_music_frontend_state) if isinstance(self._local_music_frontend_state, dict) else {}
                if not state:
                    state = await self._run_in_executor(self._realtime_executor, lambda: self._local_music_get_player().call("get_state", timeout=1.0))
                track = state.get("track") if isinstance(state, dict) else None
                if isinstance(track, dict):
                    local_cover = await self.get_local_music_cover(str(track.get("coverId") or ""))
                    if local_cover:
                        return local_cover
            except Exception:
                pass
        return await self._run_in_executor(self._cover_executor, self._get_cover_sync, title, artist, album)

    async def get_cover(self, title: str, artist: str, album: str) -> str:
        return await self.get_cover_for_service(self.active_service, title, artist, album)

    async def _helper_action(self, path: str) -> str:
        if self._service_restart_in_progress:
            return "false"
        direct_action = str(path or "").strip("/").lower()
        if direct_action in {"playpause", "next", "previous", "shuffle", "repeat"}:
            direct_snapshot = self._canonical_snapshot(await self._direct_media_snapshot_async())
            if direct_snapshot.get("players"):
                if await self._direct_media_action_async(direct_action):
                    return "true"

        def work() -> Dict[str, Any]:
            # Re-assert the source-specific SMTC session immediately before a
            # transport command. This is especially important for packaged apps
            # such as Apple Music, whose session can
            # register after the application window has already opened.
            self._select_active_media_session_sync()
            return self._request_json(path, "POST")

        try:
            result = await self._run_in_executor(self._realtime_executor, work)
            return "true" if result.get("ok", False) else "false"
        except Exception as exc:
            self._log(f"helper action {path} error: {exc}")
            return "false"

    async def pause_external_playback(self) -> str:
        try:
            snapshot = await self._run_in_executor(self._realtime_executor, self._snapshot_sync)
            selected = snapshot.get("selected") if isinstance(snapshot, dict) else None
            if not isinstance(selected, dict):
                players = snapshot.get("players", []) if isinstance(snapshot, dict) else []
                selected = players[0] if isinstance(players, list) and players else None
            status = str((selected or {}).get("status") or "").lower() if isinstance(selected, dict) else ""
            if status == "playing":
                return await self._helper_action("/playpause")
            return "true"
        except Exception as exc:
            self._log(f"pause external playback error: {exc}")
            return "false"

    async def play_pause(self) -> str:
        active = self._normalized_active_service()
        if active in {"localMusic", "youtubeMusic"}:
            result = await self.local_music_command("play_pause")
            return "true" if result.get("ok") else "false"
        return await self._helper_action("/playpause")

    async def next(self) -> str:
        active = self._normalized_active_service()
        if active in {"localMusic", "youtubeMusic"}:
            result = await self.local_music_command("next")
            return "true" if result.get("ok") else "false"
        return await self._helper_action("/next")

    async def previous(self) -> str:
        active = self._normalized_active_service()
        if active in {"localMusic", "youtubeMusic"}:
            result = await self.local_music_command("previous")
            return "true" if result.get("ok") else "false"
        return await self._helper_action("/previous")

    async def _open_music_app(self, app_key: str) -> str:
        try:
            return await self._run_in_executor(self._realtime_executor, self._open_music_app_best_effort, app_key)
        except Exception as exc:
            self._log(f"open_{app_key} error: {exc}")
            return "false"


    async def open_youtube_music(self) -> str:
        return "true"


    async def open_apple_music(self) -> str:
        return await self._open_music_app("apple_music")


    async def shuffle(self) -> str:
        active = self._normalized_active_service()
        if active in {"localMusic", "youtubeMusic"}:
            result = await self.local_music_command("shuffle")
            return "true" if result.get("ok") else "false"
        return await self._helper_action("/shuffle")

    async def repeat(self) -> str:
        active = self._normalized_active_service()
        if active in {"localMusic", "youtubeMusic"}:
            result = await self.local_music_command("repeat")
            return "true" if result.get("ok") else "false"
        return await self._helper_action("/repeat")

    async def get_app_volume(self, service: str = "") -> Dict[str, Any]:
        active = self._normalized_service_key(service) if service else self._normalized_active_service()
        if active in {"localMusic", "youtubeMusic"}:
            state = self._local_music_frontend_state if isinstance(self._local_music_frontend_state, dict) else {}
            return {"ok": True, "volume": clamp_value(state.get("volume", 100), 0, 100), "matched": active}
        return await self._run_in_executor(self._volume_executor, self._run_app_volume, None)

    async def set_app_volume(self, volume: int, service: str = "", revision: int = 0) -> Dict[str, Any]:
        active = self._normalized_service_key(service) if service else self._normalized_active_service()
        requested = clamp_value(volume, 0, 100)
        request_key = active or "active"
        request_revision = max(0, int(revision or 0))
        with self._volume_request_lock:
            self._volume_request_revisions[request_key] = max(
                request_revision,
                int(self._volume_request_revisions.get(request_key, 0) or 0),
            )

        def request_is_current() -> bool:
            if request_revision <= 0:
                return True
            with self._volume_request_lock:
                return request_revision >= int(self._volume_request_revisions.get(request_key, 0) or 0)

        def stale_result() -> Dict[str, Any]:
            return {"ok": True, "stale": True, "volume": requested, "matched": request_key}

        if active in {"localMusic", "youtubeMusic"}:
            if not request_is_current():
                return stale_result()
            return await self.set_local_music_volume(requested)
        def set_external_volume() -> Dict[str, Any]:
            if not request_is_current():
                return stale_result()
            return self._run_app_volume(requested)

        return await self._run_in_executor(self._volume_executor, set_external_volume)

    # ------------------------------------------------------------------
    # Local music library / player
    # ------------------------------------------------------------------
    def _local_music_default_settings(self) -> Dict[str, Any]:
        return {"folders": [], "files": [], "last_scan": 0.0}

    def _load_local_music_settings(self) -> Dict[str, Any]:
        defaults = self._local_music_default_settings()
        try:
            with open(self.local_music_settings_path, "r", encoding="utf-8") as handle:
                loaded = json.load(handle)
            if isinstance(loaded, dict):
                folders = []
                for value in loaded.get("folders", []) if isinstance(loaded.get("folders"), list) else []:
                    folder = os.path.abspath(os.path.expandvars(os.path.expanduser(str(value or "").strip())))
                    if folder and folder not in folders:
                        folders.append(folder)
                defaults["folders"] = folders
                files = []
                for value in loaded.get("files", []) if isinstance(loaded.get("files"), list) else []:
                    path = os.path.abspath(os.path.expandvars(os.path.expanduser(str(value or "").strip())))
                    if path and path not in files:
                        files.append(path)
                defaults["files"] = files
                defaults["last_scan"] = float(loaded.get("last_scan", 0.0) or 0.0)
        except Exception:
            pass
        return defaults

    def _save_local_music_settings(self) -> None:
        try:
            os.makedirs(os.path.dirname(self.local_music_settings_path), exist_ok=True)
            temp_path = self.local_music_settings_path + ".tmp"
            with open(temp_path, "w", encoding="utf-8") as handle:
                json.dump(self._local_music_settings, handle, ensure_ascii=False, indent=2)
            os.replace(temp_path, self.local_music_settings_path)
        except Exception as exc:
            self._log(f"local music settings save error: {exc}")

    def _load_local_music_library(self) -> Dict[str, Any]:
        if isinstance(self._local_music_library, dict):
            return self._local_music_library
        library: Dict[str, Any] = {"tracks": [], "albums": [], "artists": [], "playlists": [], "scannedAt": 0.0}
        try:
            with open(self.local_music_library_path, "r", encoding="utf-8") as handle:
                loaded = json.load(handle)
            if isinstance(loaded, dict):
                library.update(loaded)
        except Exception:
            pass
        self._local_music_library = library
        return library

    def _save_local_music_library(self, library: Dict[str, Any]) -> None:
        self._local_music_library = library
        try:
            os.makedirs(os.path.dirname(self.local_music_library_path), exist_ok=True)
            temp_path = self.local_music_library_path + ".tmp"
            with open(temp_path, "w", encoding="utf-8") as handle:
                json.dump(library, handle, ensure_ascii=False, separators=(",", ":"))
            os.replace(temp_path, self.local_music_library_path)
        except Exception as exc:
            self._log(f"local music library save error: {exc}")

    def _prune_local_music_cache(self, library: Optional[Dict[str, Any]] = None) -> None:
        library = library or self._load_local_music_library()
        valid_cover_ids: Set[str] = set()
        for collection in ("tracks", "albums"):
            items = library.get(collection, []) if isinstance(library.get(collection), list) else []
            for item in items:
                if not isinstance(item, dict):
                    continue
                album = item.get("album") if isinstance(item.get("album"), dict) else {}
                cover_id = str(item.get("coverId") or album.get("coverId") or "").strip()
                if cover_id:
                    valid_cover_ids.add(cover_id)
        artist_items = library.get("artists", []) if isinstance(library.get("artists"), list) else []
        valid_artist_ids = {
            str(item.get("id") or "").strip()
            for item in artist_items
            if isinstance(item, dict) and str(item.get("id") or "").strip()
        }

        def prune_folder(folder: str, valid_ids: Set[str]) -> None:
            if not os.path.isdir(folder):
                return
            for filename in os.listdir(folder):
                candidate = os.path.join(folder, filename)
                if not os.path.isfile(candidate):
                    continue
                if os.path.splitext(filename)[0] in valid_ids:
                    continue
                try:
                    os.remove(candidate)
                except Exception:
                    pass

        prune_folder(self.local_music_cover_dir, valid_cover_ids)
        prune_folder(self.local_music_artist_profile_dir, valid_artist_ids)
        valid_background_ids = {
            self._local_music_hash("artist-background", str(item.get("name") or "").casefold())
            for item in artist_items
            if isinstance(item, dict) and str(item.get("name") or "").strip()
        }
        prune_folder(self.local_music_artist_background_dir, valid_background_ids)
        self._local_music_artist_background_cache.clear()
        self._local_music_cover_cache = {
            key: value for key, value in self._local_music_cover_cache.items() if key in valid_cover_ids
        }

    def _set_local_music_cache_progress(self, **changes: Any) -> None:
        with self._local_music_cache_progress_lock:
            self._local_music_cache_progress.update(changes)

    def _local_music_cache_progress_snapshot(self) -> Dict[str, Any]:
        with self._local_music_cache_progress_lock:
            return dict(self._local_music_cache_progress)

    def _build_local_music_cache_sync(self) -> Dict[str, Any]:
        if not self._local_music_cache_build_lock.acquire(blocking=False):
            raise RuntimeError("Image cache is already being created")

        self._set_local_music_cache_progress(
            active=True,
            phase="scanning",
            current="",
            completed=0,
            total=0,
            error="",
        )
        try:
            stats = self._local_music_scan_sync()
            library = self._load_local_music_library()
            cached_profiles = 0
            cached_backgrounds = 0
            artists = [
                artist for artist in (library.get("artists", []) if isinstance(library.get("artists"), list) else [])
                if isinstance(artist, dict)
                and str(artist.get("id") or "").strip()
                and str(artist.get("name") or "").strip()
            ]
            total = len(artists) * 2
            completed = 0
            self._set_local_music_cache_progress(total=total, completed=0)

            for artist in artists:
                artist_id = str(artist.get("id") or "").strip()
                artist_name = str(artist.get("name") or "").strip()

                self._set_local_music_cache_progress(
                    active=True,
                    phase="profile",
                    current=artist_name,
                    completed=completed,
                    total=total,
                )
                try:
                    if self._download_local_artist_profile(artist_id, artist_name):
                        cached_profiles += 1
                except Exception as exc:
                    self._log(f"local artist cache build error for {artist_name}: {exc}")
                completed += 1
                self._set_local_music_cache_progress(completed=completed)

                self._set_local_music_cache_progress(
                    active=True,
                    phase="background",
                    current=artist_name,
                    completed=completed,
                    total=total,
                )
                try:
                    if self._local_music_artist_background_sync(artist_name):
                        cached_backgrounds += 1
                except Exception as exc:
                    self._log(f"local artist background cache build error for {artist_name}: {exc}")
                completed += 1
                self._set_local_music_cache_progress(completed=completed)

            self._prune_local_music_cache(library)
            self._set_local_music_cache_progress(
                active=False,
                phase="complete",
                current="",
                completed=total,
                total=total,
                error="",
            )
            return {**stats, "cachedProfiles": cached_profiles, "cachedBackgrounds": cached_backgrounds}
        except Exception as exc:
            self._set_local_music_cache_progress(
                active=False,
                phase="error",
                current="",
                error=str(exc),
            )
            raise
        finally:
            self._local_music_cache_build_lock.release()

    def _local_music_get_player(self) -> LocalMusicPlayerWorker:
        if self._local_music_player is None:
            self._local_music_player = LocalMusicPlayerWorker(self._log, self._volume_vendor_path())
        return self._local_music_player

    def _local_music_hash(self, *values: str) -> str:
        joined = "\0".join(str(value or "") for value in values)
        return hashlib.sha1(joined.encode("utf-8", errors="ignore")).hexdigest()[:24]

    def _local_music_tag_value(self, tags: Any, keys: List[str], default: str = "") -> str:
        if not tags:
            return default
        for key in keys:
            try:
                value = tags.get(key)
            except Exception:
                value = None
            if isinstance(value, (list, tuple)):
                value = value[0] if value else None
            if value is not None:
                text = str(value).strip()
                if text:
                    return text
        return default

    def _local_music_number(self, value: str, default: int = 0) -> int:
        match = re.search(r"\d+", str(value or ""))
        return int(match.group(0)) if match else default

    def _local_music_extract_artwork(self, path: str, cover_id: str) -> str:
        if not cover_id:
            return ""
        os.makedirs(self.local_music_cover_dir, exist_ok=True)
        for extension in ("jpg", "jpeg", "png", "webp", "avif", "gif"):
            existing = os.path.join(self.local_music_cover_dir, f"{cover_id}.{extension}")
            if os.path.isfile(existing):
                return existing
        try:
            vendor_path = self._volume_vendor_path()
            if os.path.isdir(vendor_path) and vendor_path not in sys.path:
                sys.path.insert(0, vendor_path)
            from tinytag import TinyTag  # type: ignore
            tag = TinyTag.get(path, tags=True, duration=False, image=True, ignore_errors=True)
            image = getattr(getattr(tag, "images", None), "any", None)
            data = bytes(getattr(image, "data", b"") or b"") if image is not None else b""
            mime = str(getattr(image, "mime_type", "image/jpeg") or "image/jpeg") if image is not None else "image/jpeg"
            if not data or len(data) > 12 * 1024 * 1024:
                return ""
            extension = "png" if "png" in mime.lower() else "webp" if "webp" in mime.lower() else "jpg"
            output = os.path.join(self.local_music_cover_dir, f"{cover_id}.{extension}")
            with open(output, "wb") as handle:
                handle.write(data)
            return output
        except Exception as exc:
            self._log(f"local artwork read error for {path}: {exc}")
            return ""

    def _local_music_read_track(self, path: str, root: str) -> Optional[Dict[str, Any]]:
        try:
            vendor_path = self._volume_vendor_path()
            if os.path.isdir(vendor_path) and vendor_path not in sys.path:
                sys.path.insert(0, vendor_path)
            from tinytag import TinyTag  # type: ignore
            tag = TinyTag.get(path, tags=True, duration=True, image=False, ignore_errors=True)
            stem = os.path.splitext(os.path.basename(path))[0]
            title = str(getattr(tag, "title", "") or stem).strip()
            artist = str(getattr(tag, "artist", "") or getattr(tag, "albumartist", "") or "Unknown artist").strip()
            album_artist = str(getattr(tag, "albumartist", "") or artist).strip()
            album = str(getattr(tag, "album", "") or os.path.basename(os.path.dirname(path)) or "Unknown album").strip()
            date = str(getattr(tag, "year", "") or "").strip()
            year_match = re.search(r"(?:19|20)\d{2}", date)
            year = year_match.group(0) if year_match else ""
            genre = str(getattr(tag, "genre", "") or "").strip()
            track_number = int(getattr(tag, "track", 0) or 0)
            disc_number = int(getattr(tag, "disc", 0) or 0)
            duration_ms = max(0, int(float(getattr(tag, "duration", 0.0) or 0.0) * 1000))
            absolute = os.path.abspath(path)
            track_id = self._local_music_hash(absolute.lower())
            artist_id = self._local_music_hash("artist", album_artist.casefold())
            album_id = self._local_music_hash("album", album_artist.casefold(), album.casefold(), year)
            cover_id = album_id
            folder = os.path.dirname(absolute)
            playlist_id = self._local_music_hash("folder", folder.lower())
            relative = os.path.relpath(absolute, root)
            return {
                "id": track_id,
                "type": "track",
                "uri": f"local:track:{track_id}",
                "name": title,
                "path": absolute,
                "relativePath": relative,
                "duration_ms": duration_ms,
                "track_number": track_number,
                "disc_number": disc_number,
                "release_date": date,
                "year": year,
                "genre": genre,
                "addedAt": os.path.getmtime(absolute),
                "coverId": cover_id,
                "artists": [{"id": artist_id, "name": artist, "type": "artist", "uri": f"local:artist:{artist_id}"}],
                "album": {
                    "id": album_id,
                    "type": "album",
                    "uri": f"local:album:{album_id}",
                    "name": album,
                    "release_date": date,
                    "year": year,
                    "coverId": cover_id,
                    "artists": [{"id": artist_id, "name": album_artist, "type": "artist", "uri": f"local:artist:{artist_id}"}],
                },
                "folderPlaylistId": playlist_id,
                "folder": folder,
            }
        except Exception as exc:
            self._log(f"local metadata error for {path}: {exc}")
            return None

    def _local_music_scan_sync(self) -> Dict[str, Any]:
        supported = {".mp3", ".flac", ".m4a", ".mp4", ".aac", ".ogg", ".opus", ".wav", ".wma", ".aiff", ".aif", ".ape", ".wv", ".mka"}
        with self._local_music_scan_lock:
            folders = [folder for folder in self._local_music_settings.get("folders", []) if os.path.isdir(folder)]
            individual_files = [path for path in self._local_music_settings.get("files", []) if os.path.isfile(path)]
            tracks: List[Dict[str, Any]] = []
            seen_paths: Set[str] = set()
            album_cover_sources: Dict[str, str] = {}
            playlist_files: List[str] = []
            for root in folders:
                for directory, _, filenames in os.walk(root):
                    for filename in filenames:
                        extension = os.path.splitext(filename)[1].lower()
                        path = os.path.join(directory, filename)
                        if extension in {".m3u", ".m3u8"}:
                            playlist_files.append(path)
                            continue
                        if extension not in supported:
                            continue
                        normalized_path = os.path.normcase(os.path.abspath(path))
                        if normalized_path in seen_paths:
                            continue
                        seen_paths.add(normalized_path)
                        track = self._local_music_read_track(path, root)
                        if track:
                            tracks.append(track)
                            album_cover_sources.setdefault(str(track.get("coverId") or ""), path)
            for path in individual_files:
                extension = os.path.splitext(path)[1].lower()
                normalized_path = os.path.normcase(os.path.abspath(path))
                if extension not in supported or normalized_path in seen_paths:
                    continue
                seen_paths.add(normalized_path)
                track = self._local_music_read_track(path, os.path.dirname(path))
                if track:
                    tracks.append(track)
                    album_cover_sources.setdefault(str(track.get("coverId") or ""), path)
            tracks.sort(key=lambda item: (
                str((item.get("album") or {}).get("artists", [{}])[0].get("name", "")).casefold(),
                str((item.get("album") or {}).get("name", "")).casefold(),
                int(item.get("disc_number") or 0),
                int(item.get("track_number") or 0),
                str(item.get("name") or "").casefold(),
            ))

            albums_by_id: Dict[str, Dict[str, Any]] = {}
            artists_by_id: Dict[str, Dict[str, Any]] = {}
            playlists_by_id: Dict[str, Dict[str, Any]] = {}
            for track in tracks:
                album = dict(track.get("album") or {})
                album_id = str(album.get("id") or "")
                if album_id:
                    entry = albums_by_id.setdefault(album_id, {**album, "trackIds": [], "trackCount": 0, "addedAt": 0.0})
                    entry["trackIds"].append(track["id"])
                    entry["trackCount"] = len(entry["trackIds"])
                    entry["addedAt"] = max(float(entry.get("addedAt", 0.0)), float(track.get("addedAt", 0.0)))
                for artist in track.get("artists", []) if isinstance(track.get("artists"), list) else []:
                    artist_id = str(artist.get("id") or "")
                    if not artist_id:
                        continue
                    entry = artists_by_id.setdefault(artist_id, {**artist, "trackIds": [], "albumIds": [], "coverId": track.get("coverId", ""), "profileImageId": artist_id})
                    if track["id"] not in entry["trackIds"]:
                        entry["trackIds"].append(track["id"])
                    if album_id and album_id not in entry["albumIds"]:
                        entry["albumIds"].append(album_id)
                playlist_id = str(track.get("folderPlaylistId") or "")
                if playlist_id:
                    folder = str(track.get("folder") or "")
                    entry = playlists_by_id.setdefault(playlist_id, {
                        "id": playlist_id,
                        "type": "playlist",
                        "uri": f"local:playlist:{playlist_id}",
                        "name": os.path.basename(folder) or folder,
                        "description": folder,
                        "folder": folder,
                        "coverId": track.get("coverId", ""),
                        "owner": {"display_name": "La tua musica"},
                        "trackIds": [],
                    })
                    entry["trackIds"].append(track["id"])

            # Real M3U/M3U8 playlists are imported in addition to automatic
            # folder playlists. Relative entries are resolved from the playlist file.
            path_to_id = {os.path.normcase(os.path.abspath(str(track.get("path") or ""))): str(track.get("id") or "") for track in tracks}
            for playlist_path in playlist_files:
                try:
                    try:
                        content = Path(playlist_path).read_text(encoding="utf-8-sig", errors="replace")
                    except Exception:
                        content = Path(playlist_path).read_text(encoding="cp1252", errors="replace")
                    track_ids: List[str] = []
                    for raw_line in content.splitlines():
                        line = raw_line.strip()
                        if not line or line.startswith("#") or "://" in line:
                            continue
                        resolved = line if os.path.isabs(line) else os.path.join(os.path.dirname(playlist_path), line)
                        track_id = path_to_id.get(os.path.normcase(os.path.abspath(resolved)))
                        if track_id and track_id not in track_ids:
                            track_ids.append(track_id)
                    if track_ids:
                        playlist_id = self._local_music_hash("m3u", os.path.normcase(os.path.abspath(playlist_path)))
                        first_track = next((track for track in tracks if str(track.get("id")) == track_ids[0]), {})
                        playlists_by_id[playlist_id] = {
                            "id": playlist_id,
                            "type": "playlist",
                            "uri": f"local:playlist:{playlist_id}",
                            "name": os.path.splitext(os.path.basename(playlist_path))[0],
                            "description": playlist_path,
                            "folder": os.path.dirname(playlist_path),
                            "coverId": first_track.get("coverId", ""),
                            "owner": {"display_name": "La tua musica"},
                            "trackIds": track_ids,
                            "source": "m3u",
                        }
                except Exception as exc:
                    self._log(f"local playlist read error for {playlist_path}: {exc}")

            for cover_id, source_path in album_cover_sources.items():
                if cover_id:
                    self._local_music_extract_artwork(source_path, cover_id)

            library = {
                "tracks": tracks,
                "albums": sorted(albums_by_id.values(), key=lambda item: str(item.get("name") or "").casefold()),
                "artists": sorted(artists_by_id.values(), key=lambda item: str(item.get("name") or "").casefold()),
                "playlists": sorted(playlists_by_id.values(), key=lambda item: str(item.get("name") or "").casefold()),
                "scannedAt": time.time(),
            }
            self._local_music_settings["last_scan"] = library["scannedAt"]
            self._save_local_music_settings()
            self._save_local_music_library(library)
            self._prune_local_music_cache(library)
            return self._local_music_stats(library)

    def _local_music_stats(self, library: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        library = library or self._load_local_music_library()
        return {
            "tracks": len(library.get("tracks", []) if isinstance(library.get("tracks"), list) else []),
            "albums": len(library.get("albums", []) if isinstance(library.get("albums"), list) else []),
            "artists": len(library.get("artists", []) if isinstance(library.get("artists"), list) else []),
            "playlists": len(library.get("playlists", []) if isinstance(library.get("playlists"), list) else []),
            "scannedAt": float(library.get("scannedAt", 0.0) or 0.0),
        }

    def _local_music_lookup(self, collection: str, item_id: str) -> Optional[Dict[str, Any]]:
        library = self._load_local_music_library()
        items = library.get(collection, []) if isinstance(library.get(collection), list) else []
        return next((item for item in items if str(item.get("id") or "") == str(item_id or "")), None)

    def _local_music_track_map(self) -> Dict[str, Dict[str, Any]]:
        library = self._load_local_music_library()
        return {str(item.get("id") or ""): item for item in library.get("tracks", []) if isinstance(item, dict) and item.get("id")}

    def _local_music_snapshot_sync(self) -> Dict[str, Any]:
        # Browser audio is the authoritative local playback engine.  Never spin
        # up the legacy Windows Media Player COM worker merely to read state;
        # doing so creates an unnecessary hidden player and extra resources.
        state = dict(self._local_music_frontend_state) if isinstance(self._local_music_frontend_state, dict) else {}
        source_key = self._normalized_service_key(state.get("sourceKey") or self._normalized_active_service())
        if source_key not in {"localMusic", "youtubeMusic"}:
            source_key = "localMusic"
        source_name = "YouTube Music" if source_key == "youtubeMusic" else "Your Music"
        track = state.get("track") if isinstance(state, dict) else None
        if not isinstance(track, dict):
            return {"selectedPlayer": source_key, "currentPlayer": source_key, "selected": None, "players": []}
        images = track.get("images") if isinstance(track.get("images"), list) else []
        album = track.get("album") if isinstance(track.get("album"), dict) else {}
        if not images and isinstance(album.get("images"), list):
            images = album.get("images")
        artwork_url = ""
        for image in reversed(images):
            if isinstance(image, dict) and str(image.get("url") or "").strip():
                artwork_url = str(image.get("url") or "").strip()
                break
        player = {
            "id": source_key,
            "name": source_name,
            "title": str(track.get("name") or ""),
            "artist": ", ".join(str(item.get("name") or "") for item in track.get("artists", []) if isinstance(item, dict)),
            "album": str((track.get("album") or {}).get("name") or ""),
            "status": str(state.get("status") or "Stopped"),
            "length": int(state.get("length") or track.get("duration_ms") or 0),
            "position": int(state.get("position") or 0),
            "canNext": bool(state.get("canNext")),
            "canPrevious": bool(state.get("canPrevious")),
            "canPlay": True,
            "canPause": True,
            "canTogglePlayPause": True,
            "isSelected": True,
            "isCurrent": True,
            "canShuffle": True,
            "canRepeat": True,
            "shuffleActive": bool(state.get("shuffleActive")),
            "repeatMode": str(state.get("repeatMode") or "None"),
            "localTrackId": str(track.get("id") or ""),
            "coverId": str(track.get("coverId") or ""),
            "artworkUrl": artwork_url,
        }
        return {"selectedPlayer": source_key, "currentPlayer": source_key, "selected": player, "players": [player]}

    async def set_active_service(self, service: str) -> str:
        next_service = self._normalized_service_key(service) or "localMusic"
        if next_service not in SUPPORTED_SERVICES:
            next_service = "localMusic"
        self._source_transition_revision += 1
        revision = self._source_transition_revision

        async with self._source_transition_guard():
            if revision != self._source_transition_revision:
                return self.active_service

            previous_service = self._normalized_active_service()
            auto_launch = bool(self._source_behavior_settings.get("auto_launch", True))
            close_on_switch = bool(self._source_behavior_settings.get("close_on_switch", True))
            source_changed = previous_service != next_service

            if source_changed:
                self._topbar_cached_label = ""
                self._topbar_cached_service = "music"
                self._topbar_cached_at = 0.0

            if self._source_retry_task is not None and not self._source_retry_task.done():
                self._source_retry_task.cancel()
            self._source_retry_task = None

            previous_app_key = self._music_app_key_for_service(previous_service)
            next_app_key = self._music_app_key_for_service(next_service)
            same_underlying_app = bool(previous_app_key and previous_app_key == next_app_key)

            if source_changed and previous_service not in {"localMusic", "youtubeMusic"} and not same_underlying_app:
                try:
                    await self.pause_external_playback()
                except Exception as exc:
                    self._log(f"source switch pause error: {exc}")

                if close_on_switch and previous_app_key:
                    try:
                        await self._run_in_executor(
                            self._source_lifecycle_executor,
                            self._close_music_app_best_effort,
                            previous_app_key,
                        )
                    except Exception as exc:
                        self._log(f"source switch close error for {previous_app_key}: {exc}")

            if revision != self._source_transition_revision:
                return self.active_service

            self.active_service = next_service
            self._source_behavior_settings["active_service"] = next_service
            self._save_source_behavior_settings()
            self._log(f"active service changed: {previous_service} -> {next_service}; autoLaunch={auto_launch}; closeOnSwitch={close_on_switch}")
            self.player = ""
            self._helper_forced_player_id = ""
            self._helper_forced_player_at = 0.0
            with self._snapshot_lock:
                self._snapshot_cache = self._empty_snapshot()
                self._snapshot_cache_at = 0.0

            if source_changed and auto_launch and next_app_key and not same_underlying_app:
                try:
                    await self._run_in_executor(
                        self._source_lifecycle_executor,
                        self._open_music_app_best_effort,
                        next_app_key,
                    )
                except Exception as exc:
                    self._log(f"source switch launch error for {next_app_key}: {exc}")

            if self._normalized_active_service() not in {"localMusic", "youtubeMusic"}:
                try:
                    await self._run_in_executor(self._realtime_executor, self._select_active_media_session_sync)
                except Exception:
                    pass

                async def select_later(expected_service: str, expected_revision: int) -> None:
                    try:
                        for delay in (0.35, 0.9, 1.8, 3.2):
                            await asyncio.sleep(delay)
                            if expected_revision != self._source_transition_revision:
                                return
                            if self._normalized_active_service() != expected_service:
                                return
                            await self._run_in_executor(self._realtime_executor, self._select_active_media_session_sync)
                    except asyncio.CancelledError:
                        return
                    except Exception as exc:
                        self._log(f"source session retry error: {exc}")

                try:
                    self._source_retry_task = asyncio.create_task(select_later(next_service, revision))
                except Exception:
                    self._source_retry_task = None

            return self.active_service

    async def get_active_service(self) -> str:
        return self._normalized_active_service()

    async def get_local_music_settings(self) -> Dict[str, Any]:
        cache_stats = self._split_asset_stats([
            self.local_music_cover_dir,
            self.local_music_artist_profile_dir,
            self.local_music_artist_background_dir,
        ], "local")
        return {
            "folders": list(self._local_music_settings.get("folders", [])),
            "files": list(self._local_music_settings.get("files", [])),
            "lastScan": float(self._local_music_settings.get("last_scan", 0.0) or 0.0),
            "stats": self._local_music_stats(),
            "cacheBytes": cache_stats["bytes"],
            "cacheFiles": cache_stats["files"],
            "manualBackgroundBytes": cache_stats["manualBytes"],
            "manualBackgroundFiles": cache_stats["manualFiles"],
        }

    def _local_music_pick_folder_sync(self) -> str:
        if not self._is_windows():
            return ""
        script = (
            "Add-Type -AssemblyName System.Windows.Forms; "
            "$dialog = New-Object System.Windows.Forms.FolderBrowserDialog; "
            "$dialog.Description = 'Select a music folder'; "
            "$dialog.ShowNewFolderButton = $false; "
            "if ($dialog.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) { "
            "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; Write-Output $dialog.SelectedPath }"
        )
        completed = subprocess.run(
            ["powershell.exe", "-NoProfile", "-STA", "-Command", script],
            capture_output=True,
            text=True,
            timeout=180,
            creationflags=self._task_creationflags(),
        )
        return (completed.stdout or "").strip().splitlines()[-1].strip() if (completed.stdout or "").strip() else ""

    async def pick_local_music_folder(self) -> Dict[str, Any]:
        try:
            folder = await self._run_in_executor(self._local_music_executor, self._local_music_pick_folder_sync)
            if folder:
                await self.add_local_music_folder(folder)
            return {"ok": bool(folder), "folder": folder, "settings": await self.get_local_music_settings()}
        except Exception as exc:
            return {"ok": False, "error": str(exc), "folder": ""}

    async def add_local_music_folder(self, folder: str) -> Dict[str, Any]:
        normalized = os.path.abspath(os.path.expandvars(os.path.expanduser(str(folder or "").strip())))
        if not normalized or not os.path.isdir(normalized):
            return {"ok": False, "error": "Folder not found"}
        folders = list(self._local_music_settings.get("folders", []))
        added = normalized not in folders
        if added:
            folders.append(normalized)
            self._local_music_settings["folders"] = folders
            self._save_local_music_settings()
        return {"ok": True, "added": added, "settings": await self.get_local_music_settings()}

    async def list_local_music_directory(self, path: str = "") -> Dict[str, Any]:
        supported = {".mp3", ".flac", ".m4a", ".mp4", ".aac", ".ogg", ".opus", ".wav", ".wma", ".aiff", ".aif", ".ape", ".wv", ".mka"}
        requested = str(path or "").strip() or ("C:\\" if self._is_windows() else os.path.expanduser("~"))
        resolved = os.path.abspath(os.path.expandvars(os.path.expanduser(requested)))
        if not os.path.isdir(resolved):
            return {"ok": False, "path": requested, "dirs": [], "files": [], "error": "Folder not found"}
        directories: List[str] = []
        files: List[str] = []
        try:
            with os.scandir(resolved) as entries:
                for entry in sorted(entries, key=lambda item: item.name.casefold()):
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            directories.append(entry.name)
                        elif entry.is_file(follow_symlinks=False) and os.path.splitext(entry.name)[1].lower() in supported:
                            files.append(entry.name)
                    except OSError:
                        continue
            return {"ok": True, "path": resolved, "dirs": directories, "files": files, "error": ""}
        except Exception as exc:
            return {"ok": False, "path": resolved, "dirs": [], "files": [], "error": str(exc)}

    async def add_local_music_file(self, path: str) -> Dict[str, Any]:
        normalized = os.path.abspath(os.path.expandvars(os.path.expanduser(str(path or "").strip())))
        supported = {".mp3", ".flac", ".m4a", ".mp4", ".aac", ".ogg", ".opus", ".wav", ".wma", ".aiff", ".aif", ".ape", ".wv", ".mka"}
        if not os.path.isfile(normalized) or os.path.splitext(normalized)[1].lower() not in supported:
            return {"ok": False, "error": "Local music file is unavailable"}
        files = list(self._local_music_settings.get("files", []))
        if normalized not in files:
            files.append(normalized)
            self._local_music_settings["files"] = files
            self._save_local_music_settings()
        return {"ok": True, "file": normalized, "settings": await self.get_local_music_settings()}

    async def remove_local_music_file(self, path: str) -> Dict[str, Any]:
        normalized = os.path.normcase(os.path.abspath(str(path or "")))
        self._local_music_settings["files"] = [
            value for value in self._local_music_settings.get("files", [])
            if os.path.normcase(os.path.abspath(str(value))) != normalized
        ]
        self._save_local_music_settings()
        try:
            stats = await self._run_in_executor(self._local_music_executor, self._local_music_scan_sync)
            return {"ok": True, "stats": stats, "settings": await self.get_local_music_settings()}
        except Exception as exc:
            return {"ok": False, "error": str(exc), "settings": await self.get_local_music_settings()}

    async def remove_local_music_folder(self, folder: str) -> Dict[str, Any]:
        normalized = os.path.abspath(str(folder or ""))
        folders = [value for value in self._local_music_settings.get("folders", []) if os.path.abspath(str(value)) != normalized]
        self._local_music_settings["folders"] = folders
        self._save_local_music_settings()
        try:
            stats = await self._run_in_executor(self._local_music_executor, self._local_music_scan_sync)
            return {"ok": True, "stats": stats, "settings": await self.get_local_music_settings()}
        except Exception as exc:
            self._log(f"local music folder removal rescan error: {exc}")
            return {"ok": False, "error": str(exc), "settings": await self.get_local_music_settings()}

    async def scan_local_music(self) -> Dict[str, Any]:
        try:
            stats = await self._run_in_executor(self._local_music_executor, self._local_music_scan_sync)
            return {"ok": True, "stats": stats}
        except Exception as exc:
            self._log(f"local music scan error: {exc}")
            return {"ok": False, "error": str(exc)}

    async def get_local_music_home(self) -> Dict[str, Any]:
        library = self._load_local_music_library()
        albums = sorted(library.get("albums", []), key=lambda item: float(item.get("addedAt", 0.0) or 0.0), reverse=True)[:20]
        artist_items = list(library.get("artists", []) if isinstance(library.get("artists"), list) else [])
        artists = random.sample(artist_items, min(20, len(artist_items))) if artist_items else []
        return {"albums": albums, "artists": artists}

    async def get_local_music_library(self, section: str, offset: int = 0, limit: int = 300) -> Dict[str, Any]:
        key = {"tracks": "tracks", "albums": "albums", "artists": "artists", "playlists": "playlists"}.get(str(section), "tracks")
        library = self._load_local_music_library()
        items = library.get(key, []) if isinstance(library.get(key), list) else []
        start = max(0, int(offset or 0))
        count = max(1, min(1000, int(limit or 300)))
        return {"items": items[start:start + count], "total": len(items), "offset": start, "limit": count}

    async def search_local_music(self, query: str) -> Dict[str, Any]:
        needle = self._sanitize_text(query)
        library = self._load_local_music_library()
        def matches(item: Dict[str, Any]) -> bool:
            parts = [str(item.get("name") or "")]
            parts.extend(str(artist.get("name") or "") for artist in item.get("artists", []) if isinstance(artist, dict))
            if isinstance(item.get("album"), dict):
                parts.append(str(item["album"].get("name") or ""))
            return needle in self._sanitize_text(" ".join(parts))
        return {
            "tracks": [item for item in library.get("tracks", []) if matches(item)][:10],
            "albums": [item for item in library.get("albums", []) if matches(item)][:10],
            "artists": [item for item in library.get("artists", []) if matches(item)][:10],
        }

    def _local_music_artist_profile_path(self, artist_id: str) -> str:
        safe_id = re.sub(r"[^A-Za-z0-9]", "", str(artist_id or ""))
        if not safe_id:
            return ""
        for extension in ("jpg", "jpeg", "png", "webp", "avif", "gif"):
            candidate = os.path.join(self.local_music_artist_profile_dir, f"{safe_id}.{extension}")
            if os.path.isfile(candidate):
                return candidate
        return ""

    def _download_local_artist_profile(self, artist_id: str, artist_name: str) -> str:
        existing = self._local_music_artist_profile_path(artist_id)
        if existing:
            return existing
        name = str(artist_name or "").strip()[:180]
        safe_id = re.sub(r"[^A-Za-z0-9]", "", str(artist_id or ""))
        if not name or not safe_id:
            return ""

        candidates: List[Tuple[str, str]] = []
        seen: Set[str] = set()

        # fanart.tv is the preferred source. Its newer API may return either a
        # full URL or only a filename, so normalize both forms before download.
        try:
            for mbid in self._musicbrainz_artist_mbids(name, 8):
                payload = self._fanart_artist_payload(mbid)
                entries = payload.get("artistthumb") if isinstance(payload, dict) else []
                if not isinstance(entries, list):
                    continue
                for entry in entries:
                    if not isinstance(entry, dict):
                        continue
                    value = self._fanart_asset_url(entry.get("url"), mbid, "artistthumb")
                    if value and value not in seen:
                        seen.add(value)
                        candidates.append((value, "https://fanart.tv/"))
        except Exception as exc:
            self._log(f"local artist profile fanart.tv lookup failed for {name}: {exc}")

        # TheAudioDB is the only automatic fallback.
        try:
            for item in self._theaudiodb_artist_candidates(
                name,
                ("strArtistThumb", "strArtistWideThumb", "strArtistBanner"),
                "TheAudioDB",
            ):
                value = str(item.get("url") or "").strip()
                if value and value not in seen:
                    seen.add(value)
                    candidates.append((value, "https://www.theaudiodb.com/"))
        except Exception as exc:
            self._log(f"local artist profile TheAudioDB lookup failed for {name}: {exc}")

        for candidate, referer in candidates:
            try:
                data, content_type = self._download_remote_image_bytes(candidate, referer, 12 * 1024 * 1024, 18)
                if not data or len(data) > 12 * 1024 * 1024 or "image" not in content_type:
                    continue
                extension = self._image_extension_from_content_type(content_type)
                os.makedirs(self.local_music_artist_profile_dir, exist_ok=True)
                output = os.path.join(self.local_music_artist_profile_dir, f"{safe_id}.{extension}")
                temporary = output + ".tmp"
                with open(temporary, "wb") as handle:
                    handle.write(data)
                os.replace(temporary, output)
                return output
            except Exception as exc:
                self._log(f"local artist profile download failed for {name}: {exc}")
        return ""

    def _local_music_stream_asset_path(self, kind: str, resource_id: str) -> str:
        if kind == "preview":
            safe_id = re.sub(r"[^A-Za-z0-9]", "", str(resource_id or ""))
            if not safe_id:
                return ""
            for extension in ("jpg", "jpeg", "png", "webp", "avif", "gif"):
                cached = os.path.join(self.artist_background_preview_dir, f"{safe_id}.{extension}")
                if os.path.isfile(cached):
                    return cached
            with self._artist_background_candidates_lock:
                candidate = dict(self._artist_background_candidates.get(safe_id) or {})
            image_url = str(candidate.get("previewRemoteUrl") or candidate.get("url") or "")
            if not image_url:
                return ""
            host = str(urllib.parse.urlparse(image_url).hostname or "").lower()
            referer = "https://fanart.tv/" if host.endswith("fanart.tv") else "https://www.theaudiodb.com/" if host.endswith("theaudiodb.com") else ""
            try:
                data, content_type = self._download_remote_image_bytes(image_url, referer, 18 * 1024 * 1024, 24)
                os.makedirs(self.artist_background_preview_dir, exist_ok=True)
                extension = self._image_extension_from_content_type(content_type)
                output = os.path.join(self.artist_background_preview_dir, f"{safe_id}.{extension}")
                temporary = output + ".tmp"
                with open(temporary, "wb") as handle:
                    handle.write(data)
                os.replace(temporary, output)
                return output
            except Exception as exc:
                self._log(f"artist background preview download failed for {image_url}: {exc}")
                return ""
        if kind == "cover":
            return self._local_music_cover_path(resource_id)
        if kind == "artist":
            return self._local_music_artist_profile_path(resource_id)
        if kind in {"background", "youtubemusic-background"}:
            safe_id = re.sub(r"[^A-Za-z0-9]", "", str(resource_id or ""))
            if not safe_id:
                return ""
            folder = (
                self.youtube_music_artist_background_dir if kind == "youtubemusic-background"
                else self.local_music_artist_background_dir
            )
            for extension in ("jpg", "jpeg", "png", "webp", "avif", "gif"):
                candidate = os.path.join(folder, f"{safe_id}.{extension}")
                if os.path.isfile(candidate):
                    return candidate
        return ""

    def _cache_local_artist_background(self, artist_name: str, image_url: str) -> str:
        name = str(artist_name or "").strip()
        url = str(image_url or "").strip()
        if not name or not url.startswith("http"):
            return ""
        background_id = self._local_music_hash("artist-background", name.casefold())
        existing = self._local_music_stream_asset_path("background", background_id)
        if existing:
            return existing
        try:
            host = str(urllib.parse.urlparse(url).hostname or "").lower()
            referer = "https://fanart.tv/" if host.endswith("fanart.tv") else "https://www.theaudiodb.com/" if host.endswith("theaudiodb.com") else ""
            data, content_type = self._download_remote_image_bytes(url, referer, 16 * 1024 * 1024, 20)
            if not data or len(data) > 16 * 1024 * 1024:
                return ""
            extension = self._image_extension_from_content_type(content_type)
            os.makedirs(self.local_music_artist_background_dir, exist_ok=True)
            output = os.path.join(self.local_music_artist_background_dir, f"{background_id}.{extension}")
            with open(output, "wb") as handle:
                handle.write(data)
            return output
        except Exception as exc:
            self._log(f"local artist background download failed for {name}: {exc}")
            return ""

    def _local_music_artist_background_sync(self, artist_name: str) -> str:
        name = str(artist_name or "").strip()
        if not name:
            return ""
        key = name.casefold()
        cached = self._local_music_artist_background_cache.get(key)
        if cached is not None:
            if cached.startswith("http") or os.path.isfile(cached):
                return cached
            self._local_music_artist_background_cache.pop(key, None)

        background_id = self._local_music_hash("artist-background", key)
        existing = self._local_music_stream_asset_path("background", background_id)
        if existing:
            self._local_music_artist_background_cache[key] = existing
            return existing

        candidates: List[str] = []
        try:
            candidates.extend(str(item.get("url") or "") for item in self._fanart_artist_background_candidates(name, 8))
        except Exception as exc:
            self._log(f"local artist fanart.tv background lookup failed for {name}: {exc}")
        try:
            candidates.extend(str(item.get("url") or "") for item in self._theaudiodb_artist_background_candidates(name, 6))
        except Exception as exc:
            self._log(f"local artist TheAudioDB background lookup failed for {name}: {exc}")

        seen: Set[str] = set()
        for candidate in candidates:
            value = str(candidate or "").strip()
            if not value.startswith("http") or value in seen:
                continue
            seen.add(value)
            cached_path = self._cache_local_artist_background(name, value)
            if cached_path:
                self._local_music_artist_background_cache[key] = cached_path
                return cached_path

        if len(self._local_music_artist_background_cache) > 120:
            self._local_music_artist_background_cache.clear()
        self._local_music_artist_background_cache[key] = ""
        return ""

    async def _local_music_artist_visual_fallback(self, artist_name: str) -> str:
        """Return a dependable cached profile/album image when no wide hero is usable."""
        name = str(artist_name or "").strip()[:180]
        if not name:
            return ""
        normalized = self._sanitize_text(name)
        library = self._load_local_music_library()
        artists = library.get("artists", []) if isinstance(library.get("artists"), list) else []
        artist = next(
            (
                item for item in artists
                if isinstance(item, dict) and self._sanitize_text(str(item.get("name") or "")) == normalized
            ),
            None,
        )
        if not isinstance(artist, dict):
            return ""
        artist_id = str(artist.get("id") or "")
        profile = await self.get_local_music_artist_profile(artist_id, name)
        if profile:
            return profile
        cover_id = str(artist.get("coverId") or "")
        if not cover_id:
            album_ids = artist.get("albumIds", []) if isinstance(artist.get("albumIds"), list) else []
            for album_id in album_ids:
                album = self._local_music_lookup("albums", str(album_id or ""))
                candidate = str((album or {}).get("coverId") or "") if isinstance(album, dict) else ""
                if candidate:
                    cover_id = candidate
                    break
        return await self.get_local_music_cover(cover_id) if cover_id else ""

    async def get_artist_background(self, artist_name: str) -> str:
        """Return a cached artist hero, falling back to a locally served smaller image."""
        name = str(artist_name or "").strip()[:180]
        if not name:
            return ""
        try:
            result = await self._run_in_executor(self._cover_executor, self._local_music_artist_background_sync, name)
            if result and os.path.isfile(result):
                base = await self.get_local_music_stream_base()
                background_id = self._local_music_hash("artist-background", name.casefold())
                version = int(os.path.getmtime(result))
                return f"{base}/background/{urllib.parse.quote(background_id, safe='')}?v={version}"
            # Remote provider URLs are intentionally not used as the final fallback:
            # Steam CEF can leave the hero black when a provider rejects hotlinking.
            # A profile or album image served by our localhost cache is predictable.
            fallback = await self._local_music_artist_visual_fallback(name)
            return fallback or result
        except Exception as exc:
            self._log(f"artist background lookup error for {name}: {exc}")
            try:
                return await self._local_music_artist_visual_fallback(name)
            except Exception:
                return ""

    async def get_local_music_detail(self, kind: str, item_id: str) -> Dict[str, Any]:
        kind = str(kind or "")
        track_map = self._local_music_track_map()
        if kind == "album":
            item = self._local_music_lookup("albums", item_id)
            tracks = [track_map[value] for value in (item or {}).get("trackIds", []) if value in track_map]
            return {"kind": kind, "item": item, "tracks": tracks}
        if kind == "artist":
            item = self._local_music_lookup("artists", item_id)
            # Return catalogue content immediately. Artist artwork is fetched in a
            # separate frontend request so a slow image provider can never leave the
            # whole artist page black and apparently empty.
            if not isinstance(item, dict):
                return {"kind": kind, "item": None, "tracks": [], "albums": [], "backgroundImage": ""}
            track_ids = item.get("trackIds", []) if isinstance(item.get("trackIds"), list) else []
            album_ids = item.get("albumIds", []) if isinstance(item.get("albumIds"), list) else []
            tracks = [track_map[value] for value in track_ids if value in track_map]
            albums = [self._local_music_lookup("albums", value) for value in album_ids]
            artist_name = str(item.get("name") or "")
            background = self._local_music_artist_background_cache.get(artist_name.casefold(), "")
            if background and os.path.isfile(background):
                base = await self.get_local_music_stream_base()
                background_id = self._local_music_hash("artist-background", artist_name.casefold())
                version = int(os.path.getmtime(background))
                background = f"{base}/background/{urllib.parse.quote(background_id, safe='')}?v={version}"
            if not background:
                profile_path = self._local_music_artist_profile_path(str(item.get("id") or ""))
                if profile_path:
                    base = await self.get_local_music_stream_base()
                    version = int(os.path.getmtime(profile_path))
                    background = f"{base}/artist/{urllib.parse.quote(str(item.get('id') or ''), safe='')}?v={version}"
                else:
                    cover_id = str(item.get("coverId") or "")
                    if cover_id:
                        background = await self.get_local_music_cover(cover_id)
            return {"kind": kind, "item": item, "tracks": tracks, "albums": [value for value in albums if value], "backgroundImage": background}
        item = self._local_music_lookup("playlists", item_id)
        tracks = [track_map[value] for value in (item or {}).get("trackIds", []) if value in track_map]
        return {"kind": "playlist", "item": item, "tracks": tracks}

    def _local_music_cover_path(self, cover_id: str) -> str:
        for extension in ("jpg", "jpeg", "png", "webp", "avif", "gif"):
            candidate = os.path.join(self.local_music_cover_dir, f"{cover_id}.{extension}")
            if os.path.isfile(candidate):
                return candidate
        return ""

    async def get_local_music_cover(self, cover_id: str) -> str:
        cover_id = re.sub(r"[^A-Za-z0-9]", "", str(cover_id or ""))
        if not cover_id:
            return ""
        cached = self._local_music_cover_cache.get(cover_id)
        if cached:
            return cached
        path = self._local_music_cover_path(cover_id)
        if not path:
            try:
                album = self._local_music_lookup("albums", cover_id)
                if isinstance(album, dict):
                    artists = album.get("artists", []) if isinstance(album.get("artists"), list) else []
                    artist = str((artists[0] if artists else {}).get("name", ""))
                    fallback = await self._run_in_executor(
                        self._cover_executor,
                        self._online_cover,
                        "",
                        artist,
                        str(album.get("name") or ""),
                    )
                    if fallback:
                        self._local_music_cover_cache[cover_id] = fallback
                        return fallback
            except Exception:
                pass
            return ""
        try:
            base = await self.get_local_music_stream_base()
            version = int(os.path.getmtime(path)) if os.path.isfile(path) else 0
            result = f"{base}/cover/{urllib.parse.quote(cover_id, safe='')}?v={version}"
            if len(self._local_music_cover_cache) > 500:
                self._local_music_cover_cache.clear()
            self._local_music_cover_cache[cover_id] = result
            return result
        except Exception:
            return ""

    async def get_local_music_artist_profile(self, artist_id: str, artist_name: str = "") -> str:
        safe_id = re.sub(r"[^A-Za-z0-9]", "", str(artist_id or ""))
        if not safe_id:
            return ""
        path = self._local_music_artist_profile_path(safe_id)
        if not path:
            path = await self._run_in_executor(self._cover_executor, self._download_local_artist_profile, safe_id, str(artist_name or ""))
        if path:
            base = await self.get_local_music_stream_base()
            version = int(os.path.getmtime(path)) if os.path.isfile(path) else 0
            return f"{base}/artist/{urllib.parse.quote(safe_id, safe='')}?v={version}"
        return ""

    def _clear_local_music_cache_sync(self) -> Dict[str, int]:
        if not self._local_music_cache_build_lock.acquire(blocking=False):
            raise RuntimeError("Image cache is busy")
        folders = [self.local_music_cover_dir, self.local_music_artist_profile_dir, self.local_music_artist_background_dir]
        self._set_local_music_cache_progress(active=True, phase="clearing", current="", completed=0, total=0, error="")
        try:
            preserve = self._manual_artist_background_paths("local")
            def update(path: str, index: int, total: int) -> None:
                self._set_local_music_cache_progress(
                    active=True,
                    phase="clearing",
                    current=os.path.basename(path),
                    completed=index,
                    total=total,
                    error="",
                )
            stats = self._clear_asset_folders(folders, preserve, update)
            self._local_music_cover_cache.clear()
            self._local_music_artist_background_cache.clear()
            self._set_local_music_cache_progress(active=False, phase="cleared", current="", completed=stats["files"], total=stats["files"], error="")
            return stats
        except Exception as exc:
            self._set_local_music_cache_progress(active=False, phase="error", current="", error=str(exc))
            raise
        finally:
            self._local_music_cache_build_lock.release()

    async def clear_local_music_cache(self) -> Dict[str, Any]:
        try:
            stats = await self._run_in_executor(self._local_music_executor, self._clear_local_music_cache_sync)
            return {"ok": True, "stats": stats, "settings": await self.get_local_music_settings()}
        except Exception as exc:
            return {"ok": False, "error": str(exc), "settings": await self.get_local_music_settings()}

    async def get_local_music_cache_progress(self) -> Dict[str, Any]:
        return self._local_music_cache_progress_snapshot()

    async def build_local_music_cache(self) -> Dict[str, Any]:
        try:
            stats = await self._run_in_executor(self._local_music_executor, self._build_local_music_cache_sync)
            return {"ok": True, "stats": stats, "settings": await self.get_local_music_settings()}
        except Exception as exc:
            self._log(f"local music cache build error: {exc}")
            return {"ok": False, "error": str(exc), "settings": await self.get_local_music_settings()}

    async def update_local_music_frontend_state(self, state: Dict[str, Any]) -> bool:
        if not isinstance(state, dict):
            return False
        allowed = {
            "track": state.get("track"),
            "index": int(state.get("index", -1) or -1),
            "queueLength": int(state.get("queueLength", 0) or 0),
            "status": str(state.get("status") or "Stopped"),
            "position": int(state.get("position", 0) or 0),
            "length": int(state.get("length", 0) or 0),
            "volume": clamp_value(state.get("volume", 100), 0, 100),
            "shuffleActive": bool(state.get("shuffleActive", False)),
            "repeatMode": str(state.get("repeatMode") or "None"),
            "canPrevious": bool(state.get("canPrevious", False)),
            "canNext": bool(state.get("canNext", False)),
            "sourceKey": self._normalized_service_key(state.get("sourceKey") or self._normalized_active_service()),
        }
        self._local_music_frontend_state = allowed
        # The explicit source selector is authoritative. A delayed browser-audio
        # state sync must never switch the backend back to local music after the
        # user has selected Apple Music or another external source.
        return True

    def _local_music_stream_path(self, track_id: str) -> str:
        track = self._local_music_track_map().get(str(track_id or ""))
        path = str((track or {}).get("path") or "")
        return path if path and os.path.isfile(path) else ""

    async def get_local_music_stream_base(self) -> str:
        if self._local_music_stream_server is None:
            self._local_music_stream_server = LocalMusicStreamServer(
                self._local_music_stream_path,
                self._local_music_stream_asset_path,
                self._log,
                self._youtube_music.resolve_stream,
            )
            self._local_music_stream_server.start()
        return self._local_music_stream_server.base_url

    async def get_youtube_music_settings(self) -> Dict[str, Any]:
        return self._youtube_music.public_settings()

    async def connect_youtube_music(self, headers_raw: str) -> Dict[str, Any]:
        try:
            data = await self._run_in_executor(
                self._youtube_music_executor,
                self._youtube_music.connect,
                str(headers_raw or ""),
            )
            return {"ok": True, "data": data}
        except Exception as exc:
            self._log(f"YouTube Music authentication error: {exc}")
            return {"ok": False, "error": str(exc)}

    async def start_youtube_music_browser_auth(self) -> Dict[str, Any]:
        try:
            data = self._youtube_music.start_browser_auth()
            return {"ok": True, "data": data}
        except Exception as exc:
            self._log(f"YouTube Music browser authentication start error: {exc}")
            return {"ok": False, "error": str(exc)}

    async def get_youtube_music_browser_auth_status(self) -> Dict[str, Any]:
        try:
            return {"ok": True, "data": self._youtube_music.browser_auth_status()}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    async def cancel_youtube_music_browser_auth(self) -> Dict[str, Any]:
        try:
            return {"ok": True, "data": self._youtube_music.cancel_browser_auth()}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    async def disconnect_youtube_music(self) -> Dict[str, Any]:
        try:
            data = await self._run_in_executor(self._youtube_music_executor, self._youtube_music.disconnect)
            return {"ok": True, "data": data}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    async def set_youtube_music_audio_quality(self, quality: str) -> Dict[str, Any]:
        try:
            data = await self._run_in_executor(
                self._youtube_music_executor,
                self._youtube_music.set_audio_quality,
                quality,
            )
            return {"ok": True, "data": data}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    async def set_youtube_music_compact_saved_tracks(self, enabled: bool) -> Dict[str, Any]:
        try:
            data = await self._run_in_executor(
                self._youtube_music_executor,
                self._youtube_music.set_compact_saved_tracks,
                bool(enabled),
            )
            return {"ok": True, "data": data}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    async def refresh_youtube_music_cache(self) -> Dict[str, Any]:
        try:
            data = await self._run_in_executor(self._youtube_music_executor, self._youtube_music.clear_cache)
            return {"ok": True, "data": data}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    async def youtube_music_get_home(self) -> Dict[str, Any]:
        return await self._youtube_music_result(self._youtube_music.get_home)

    async def youtube_music_search(self, query: str) -> Dict[str, Any]:
        return await self._youtube_music_result(self._youtube_music.search, query)

    async def youtube_music_get_speed_dial(self, refresh: bool = False, max_age: float = 0) -> Dict[str, Any]:
        return await self._youtube_music_result(self._youtube_music.get_speed_dial, bool(refresh), max_age)

    async def youtube_music_get_speed_dial_pins(self) -> Dict[str, Any]:
        return await self._youtube_music_result(self._youtube_music.get_speed_dial_pins)

    async def youtube_music_set_speed_dial_pins(self, items: List[Dict[str, Any]]) -> Dict[str, Any]:
        return await self._youtube_music_result(self._youtube_music.set_speed_dial_pins, items if isinstance(items, list) else [])

    async def youtube_music_get_speed_dial_queue(self, item: Dict[str, Any]) -> Dict[str, Any]:
        return await self._youtube_music_result(self._youtube_music.get_speed_dial_queue, item if isinstance(item, dict) else {})

    async def youtube_music_find_soundtrack(self, game: str, platform: str = "") -> Dict[str, Any]:
        return await self._youtube_music_result(self._youtube_music.find_soundtrack, str(game or ""), str(platform or ""))

    async def get_collector_now_playing(self, app_id: int) -> Dict[str, Any]:
        """The Collector's ROM title and console for a running shortcut (its file); empty when absent."""
        try:
            home = getattr(decky_plugin, "DECKY_HOME", None) or os.environ.get("DECKY_HOME", "")
            paths = mix_soundtrack.collector_file_paths(str(home or ""), self._resolve_runtime_dir())
            record = await asyncio.get_running_loop().run_in_executor(
                None, mix_soundtrack.read_collector_record, paths, int(app_id or 0))
            return {"ok": True, "data": record or {}}
        except Exception as exc:
            self._log(f"Collector now playing unavailable: {type(exc).__name__}: {exc}")
            return {"ok": True, "data": {}}

    async def youtube_music_get_library(self, section: str, max_items: int = 100) -> Dict[str, Any]:
        return await self._youtube_music_result(self._youtube_music.get_library, section, max_items)

    async def youtube_music_get_detail(self, kind: str, item_id: str) -> Dict[str, Any]:
        return await self._youtube_music_result(self._youtube_music.get_detail, kind, item_id)

    async def youtube_music_prepare_stream(self, video_id: str) -> Dict[str, Any]:
        try:
            stream = await self._run_in_executor(
                self._youtube_music_executor,
                self._youtube_music.resolve_stream,
                video_id,
            )
            base = await self.get_local_music_stream_base()
            clean_id = re.sub(r"[^A-Za-z0-9_-]", "", str(video_id or ""))[:32]
            return {
                "ok": True,
                "data": {
                    "url": f"{base}/ytmusic-track/{urllib.parse.quote(clean_id, safe='')}",
                    "durationMs": int(stream.get("durationMs") or 0),
                    "title": str(stream.get("title") or ""),
                    "artist": str(stream.get("artist") or ""),
                    "album": str(stream.get("album") or ""),
                    "thumbnail": str(stream.get("thumbnail") or ""),
                },
            }
        except Exception as exc:
            self._log(f"YouTube Music stream prepare error: {exc}")
            return {"ok": False, "error": str(exc)}

    async def youtube_music_prefetch_stream(self, video_id: str) -> Dict[str, Any]:
        # Warm the stream cache on a dedicated single-worker executor so prefetch
        # never competes with on-demand playback resolution (the reason a 6-track
        # prefetch made selecting/advancing slower).
        try:
            await self._run_in_executor(
                self._youtube_music_prefetch_executor,
                self._youtube_music.resolve_stream,
                video_id,
            )
            return {"ok": True}
        except Exception as exc:
            self._log(f"YouTube Music stream prefetch error: {exc}")
            return {"ok": False, "error": str(exc)}

    async def youtube_music_invalidate_stream(self, video_id: str) -> Dict[str, Any]:
        try:
            removed = await self._run_in_executor(
                self._youtube_music_executor,
                self._youtube_music.invalidate_stream,
                video_id,
            )
            return {"ok": True, "data": {"removed": int(removed or 0)}}
        except Exception as exc:
            self._log(f"YouTube Music stream invalidate error: {exc}")
            return {"ok": False, "error": str(exc)}

    async def _youtube_music_result(self, function, *args: Any) -> Dict[str, Any]:
        try:
            data = await self._run_in_executor(self._youtube_music_executor, function, *args)
            return {"ok": True, "data": data}
        except Exception as exc:
            if mix_speed_dial.is_parser_error(exc):
                # A response shape ytmusicapi cannot read: log it briefly, show a short line.
                self._log(f"YouTube Music request error ({getattr(function, '__name__', 'request')}): "
                          f"unexpected response: {mix_speed_dial.short_error(exc)}")
                return {"ok": False, "error": YOUTUBE_MUSIC_UNEXPECTED_RESPONSE}
            self._log(f"YouTube Music request error: {exc}")
            return {"ok": False, "error": str(exc)}

    async def get_local_music_track(self, track_id: str) -> Dict[str, Any]:
        track = self._local_music_track_map().get(str(track_id or ""))
        return {"ok": bool(track), "data": track, "error": "Local music track not found" if not track else ""}

    async def get_local_music_tracks(self, track_ids: List[str]) -> Dict[str, Any]:
        track_map = self._local_music_track_map()
        tracks = [track_map[str(value)] for value in track_ids if str(value) in track_map]
        return {"ok": bool(tracks), "data": tracks, "error": "Local music tracks not found" if not tracks else ""}

    async def play_local_music_items(self, track_ids: List[str], start_index: int = 0) -> Dict[str, Any]:
        track_map = self._local_music_track_map()
        tracks = [track_map[str(value)] for value in track_ids if str(value) in track_map]
        if not tracks:
            return {"ok": False, "error": "No playable local tracks"}
        try:
            state = await self._run_in_executor(self._realtime_executor, lambda: self._local_music_get_player().call("play_items", tracks, int(start_index), timeout=5.0))
            self.active_service = "localMusic"
            self._source_behavior_settings["active_service"] = "localMusic"
            self._save_source_behavior_settings()
            return {"ok": True, "data": state}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def _local_music_command_sync(self, command: str) -> Dict[str, Any]:
        mapping = {"play_pause": "play_pause", "next": "next", "previous": "previous"}
        player = self._local_music_get_player()
        if command == "shuffle":
            state = player.call("get_state", timeout=1.0)
            result = player.call("set_shuffle", not bool(state.get("shuffleActive")), timeout=1.5)
        elif command == "repeat":
            state = player.call("get_state", timeout=1.0)
            current = str(state.get("repeatMode") or "None")
            next_mode = "All" if current == "None" else "One" if current == "All" else "None"
            result = player.call("set_repeat", next_mode, timeout=1.5)
        else:
            result = player.call(mapping.get(command, command), timeout=2.0)
        return {"ok": True, "data": result}

    async def local_music_command(self, command: str) -> Dict[str, Any]:
        try:
            return await self._run_in_executor(self._realtime_executor, self._local_music_command_sync, command)
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    async def get_local_music_state(self) -> Dict[str, Any]:
        try:
            state = await self._run_in_executor(self._realtime_executor, lambda: self._local_music_get_player().call("get_state", timeout=1.5))
            return {"ok": True, "data": state}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    async def set_local_music_volume(self, volume: int) -> Dict[str, Any]:
        requested = clamp_value(volume, 0, 100)
        if not isinstance(self._local_music_frontend_state, dict):
            self._local_music_frontend_state = {}
        self._local_music_frontend_state["volume"] = requested
        # Keep compatibility with an explicitly started legacy worker without
        # creating one solely for a volume request.
        if self._local_music_player is not None:
            try:
                await self._run_in_executor(
                    self._volume_executor,
                    lambda: self._local_music_player.call("set_volume", requested, timeout=2.0),
                )
            except Exception as exc:
                self._log(f"legacy local volume sync error: {exc}")
        return {"ok": True, "volume": requested, "matched": "localMusic"}

    async def open_local_music(self) -> str:
        self.active_service = "localMusic"
        self._source_behavior_settings["active_service"] = "localMusic"
        self._save_source_behavior_settings()
        return "true"

    # ---------------------------------------------------------------- top bar

    # ------------------------------------------------------------------
    # Browser windows and external links
    # ------------------------------------------------------------------

    def _process_tree_ids_native(self, root_process_id: int) -> Set[int]:
        root = int(root_process_id or 0)
        if root <= 0:
            return set()
        process_ids = {root}
        entries = self._native_process_entries()
        changed = True
        while changed:
            changed = False
            for item in entries:
                process_id = int(item.get("pid") or 0)
                parent_id = int(item.get("parent_pid") or 0)
                if process_id > 0 and parent_id in process_ids and process_id not in process_ids:
                    process_ids.add(process_id)
                    changed = True
        return process_ids

    def _browser_window_handles(self, process_ids: Optional[Set[int]] = None) -> List[int]:
        if not self._is_windows():
            return []
        handles: List[int] = []
        try:
            user32 = ctypes.windll.user32
            enum_windows_proc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

            @enum_windows_proc
            def enum_window(window_handle, _lparam):
                if not user32.IsWindowVisible(window_handle):
                    return True
                class_name = ctypes.create_unicode_buffer(256)
                user32.GetClassNameW(window_handle, class_name, len(class_name))
                value = class_name.value
                if value.startswith("Chrome_WidgetWin_") or value in {"MozillaWindowClass", "ApplicationFrameWindow"}:
                    window_process_id = ctypes.c_ulong(0)
                    user32.GetWindowThreadProcessId(window_handle, ctypes.byref(window_process_id))
                    if not process_ids or int(window_process_id.value or 0) in process_ids:
                        handles.append(int(window_handle))
                return True

            user32.EnumWindows(enum_window, 0)
        except Exception as exc:
            self._log(f"browser window enumeration error: {exc}")
        return handles

    def _focus_browser_window(self, previous_handles: Set[int], launched_process_id: int = 0) -> None:
        if not self._is_windows():
            return
        try:
            user32 = ctypes.windll.user32
            kernel32 = ctypes.windll.kernel32
            target = 0
            for _ in range(24):
                process_ids = self._process_tree_ids_native(launched_process_id) if launched_process_id else set()
                handles = self._browser_window_handles(process_ids) if process_ids else []
                if not handles:
                    handles = self._browser_window_handles()
                fresh = [handle for handle in handles if handle not in previous_handles]
                if fresh or handles:
                    target = (fresh or handles)[0]
                    if fresh or process_ids:
                        break
                time.sleep(0.125)
            if not target:
                return
            user32.SetWindowPos.argtypes = [
                ctypes.c_void_p, ctypes.c_void_p,
                ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                ctypes.c_uint,
            ]
            user32.SetWindowPos.restype = ctypes.c_int
            try:
                user32.AllowSetForegroundWindow(0xFFFFFFFF)
            except Exception:
                pass
            foreground = int(user32.GetForegroundWindow() or 0)
            current_thread = int(kernel32.GetCurrentThreadId())
            foreground_thread = int(user32.GetWindowThreadProcessId(foreground, None)) if foreground else 0
            target_thread = int(user32.GetWindowThreadProcessId(target, None))
            attached_foreground = bool(foreground_thread and foreground_thread != current_thread and user32.AttachThreadInput(current_thread, foreground_thread, True))
            attached_target = bool(target_thread and target_thread != current_thread and target_thread != foreground_thread and user32.AttachThreadInput(current_thread, target_thread, True))
            try:
                user32.ShowWindow(target, 9)
                user32.SetWindowPos(target, ctypes.c_void_p(-1), 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0040)
                user32.BringWindowToTop(target)
                user32.keybd_event(0x12, 0, 0, 0)
                user32.keybd_event(0x12, 0, 2, 0)
                user32.SetForegroundWindow(target)
                user32.SetActiveWindow(target)
                if hasattr(user32, "SwitchToThisWindow"):
                    user32.SwitchToThisWindow(target, True)
                time.sleep(0.12)
                user32.SetWindowPos(target, ctypes.c_void_p(-2), 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0040)
            finally:
                if attached_target:
                    user32.AttachThreadInput(current_thread, target_thread, False)
                if attached_foreground:
                    user32.AttachThreadInput(current_thread, foreground_thread, False)
        except Exception as exc:
            self._log(f"browser foreground error: {exc}")

    def _open_external_url_sync(self, url: str) -> bool:
        parsed = urllib.parse.urlparse(str(url or "").strip())
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise RuntimeError("Invalid external URL")
        previous_handles = set(self._browser_window_handles())
        try:
            if self._is_windows() and hasattr(os, "startfile"):
                try:
                    ctypes.windll.user32.AllowSetForegroundWindow(0xFFFFFFFF)
                except Exception:
                    pass
                os.startfile(url)  # type: ignore[attr-defined]
                self._focus_browser_window(previous_handles)
                return True
        except Exception as exc:
            self._log(f"external URL startfile error: {exc}")
        try:
            opened = bool(webbrowser.open(url, new=1, autoraise=True))
            self._focus_browser_window(previous_handles)
            return opened
        except Exception as exc:
            self._log(f"external URL browser open error: {exc}")
            return False

    def _shell_execute_visible(self, executable: str, arguments: List[str], cwd: str) -> Dict[str, Any]:
        if not self._is_windows():
            return {"ok": False, "error": "Windows only"}

        class SHELLEXECUTEINFOW(ctypes.Structure):
            _fields_ = [
                ("cbSize", ctypes.c_ulong),
                ("fMask", ctypes.c_ulong),
                ("hwnd", ctypes.c_void_p),
                ("lpVerb", ctypes.c_wchar_p),
                ("lpFile", ctypes.c_wchar_p),
                ("lpParameters", ctypes.c_wchar_p),
                ("lpDirectory", ctypes.c_wchar_p),
                ("nShow", ctypes.c_int),
                ("hInstApp", ctypes.c_void_p),
                ("lpIDList", ctypes.c_void_p),
                ("lpClass", ctypes.c_wchar_p),
                ("hkeyClass", ctypes.c_void_p),
                ("dwHotKey", ctypes.c_ulong),
                ("hIconOrMonitor", ctypes.c_void_p),
                ("hProcess", ctypes.c_void_p),
            ]

        shell32 = ctypes.WinDLL("shell32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        shell32.ShellExecuteExW.argtypes = [ctypes.POINTER(SHELLEXECUTEINFOW)]
        shell32.ShellExecuteExW.restype = ctypes.c_int
        kernel32.GetProcessId.argtypes = [ctypes.c_void_p]
        kernel32.GetProcessId.restype = ctypes.c_ulong
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        kernel32.CloseHandle.restype = ctypes.c_int

        info = SHELLEXECUTEINFOW()
        info.cbSize = ctypes.sizeof(SHELLEXECUTEINFOW)
        info.fMask = 0x00000040 | 0x00000100  # SEE_MASK_NOCLOSEPROCESS | SEE_MASK_NOASYNC
        info.lpVerb = "open"
        info.lpFile = executable
        info.lpParameters = subprocess.list2cmdline(arguments)
        info.lpDirectory = cwd
        info.nShow = 5  # SW_SHOW
        if not shell32.ShellExecuteExW(ctypes.byref(info)):
            return {"ok": False, "error": f"ShellExecuteExW failed: {ctypes.WinError(ctypes.get_last_error())}"}
        try:
            process_id = int(kernel32.GetProcessId(info.hProcess) or 0) if info.hProcess else 0
        finally:
            if info.hProcess:
                kernel32.CloseHandle(info.hProcess)
        if process_id <= 0:
            return {"ok": False, "error": "ShellExecuteExW did not return a browser process"}
        return {
            "ok": True,
            "pid": process_id,
            "mode": "shell-interactive",
            "sessionId": self._process_session_id(process_id),
        }

    def _launch_youtube_music_auth_browser(self, executable: str, arguments: List[str], cwd: str) -> Dict[str, Any]:
        previous_handles = set(self._browser_window_handles())
        launched = self._shell_execute_visible(executable, arguments, cwd)
        errors: List[str] = []
        if not launched.get("ok"):
            errors.append(str(launched.get("error") or "shell launch failed"))
            try:
                creation_flags = int(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)) | int(getattr(subprocess, "DETACHED_PROCESS", 0))
                process = subprocess.Popen(
                    [executable, *arguments],
                    cwd=cwd,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=creation_flags,
                    close_fds=True,
                )
                launched = {
                    "ok": True,
                    "pid": int(process.pid or 0),
                    "mode": "direct-interactive",
                    "sessionId": self._process_session_id(os.getpid()),
                }
            except Exception as exc:
                errors.append(f"direct launch: {type(exc).__name__}: {exc}")
                launched = self._launch_hidden_as_interactive_user(executable, arguments, cwd, visible=True)
                if not launched.get("ok"):
                    errors.append(str(launched.get("error") or "interactive-token launch failed"))
                    launched["error"] = " | ".join(errors)
        if launched.get("ok"):
            launched_pid = int(launched.get("pid") or 0)
            self._record_diagnostic_event(
                "youtube_music",
                "browser_auth_launch",
                {"pid": launched_pid, "mode": launched.get("mode"), "sessionId": launched.get("sessionId")},
            )
            time.sleep(0.18)
            process_ids = self._process_tree_ids_native(launched_pid)
            foreground_process_id = ctypes.c_ulong(0)
            foreground_window = int(ctypes.windll.user32.GetForegroundWindow() or 0)
            if foreground_window:
                ctypes.windll.user32.GetWindowThreadProcessId(foreground_window, ctypes.byref(foreground_process_id))
            if int(foreground_process_id.value or 0) not in process_ids:
                self._focus_browser_window(previous_handles, launched_pid)
        return launched


    async def open_external_url(self, url: str) -> bool:
        return bool(await self._run_in_executor(self._realtime_executor, self._open_external_url_sync, url))


    def _load_artist_background_selections(self) -> Dict[str, Any]:
        if isinstance(self._artist_background_selections, dict):
            return self._artist_background_selections
        selections: Dict[str, Any] = {}
        try:
            if os.path.exists(self._artist_background_selection_path):
                with open(self._artist_background_selection_path, "r", encoding="utf-8") as handle:
                    loaded = json.load(handle)
                if isinstance(loaded, dict):
                    selections = loaded
        except Exception as exc:
            self._log(f"Artist background selections load error: {exc}")
        self._artist_background_selections = selections
        return selections

    def _save_artist_background_selections(self) -> None:
        try:
            os.makedirs(self.settings_dir, exist_ok=True)
            temporary = self._artist_background_selection_path + ".tmp"
            with open(temporary, "w", encoding="utf-8") as handle:
                json.dump(self._artist_background_selections or {}, handle, ensure_ascii=False, indent=2)
            os.replace(temporary, self._artist_background_selection_path)
        except Exception as exc:
            self._log(f"Artist background selections save error: {exc}")

    def _artist_background_provider_key(self, provider: str) -> str:
        value = str(provider or "").lower()
        if value in ("youtubemusic", "youtube_music", "youtube", "ytmusic"):
            return "youtubeMusic"
        return "local"

    def _artist_background_dir_for(self, provider_key: str) -> str:
        if provider_key == "youtubeMusic":
            return self.youtube_music_artist_background_dir
        return self.local_music_artist_background_dir

    def _artist_background_stream_kind(self, provider_key: str) -> str:
        if provider_key == "youtubeMusic":
            return "youtubemusic-background"
        return "background"

    def _artist_background_resource_id(self, provider_key: str, clean_id: str, artist_name: str) -> str:
        if provider_key == "youtubeMusic":
            return self._local_music_hash("youtubemusic-artist-background", str(artist_name or "").casefold())
        return self._local_music_hash("artist-background", str(artist_name or "").casefold())

    def _artist_background_selection_key(self, provider: str, artist_id: str, artist_name: str) -> str:
        provider_key = self._artist_background_provider_key(provider)
        identity = re.sub(r"[^A-Za-z0-9]", "", str(artist_id or ""))
        if not identity:
            identity = self._sanitize_text(artist_name)
        return f"{provider_key}:{identity}"

    def _load_artist_background_provider_settings(self) -> Dict[str, str]:
        result = {"fanart_api_key": ""}
        try:
            with open(self._artist_background_provider_settings_path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
            if isinstance(payload, dict):
                result["fanart_api_key"] = re.sub(r"[^A-Za-z0-9]", "", str(payload.get("fanart_api_key") or ""))[:160]
        except Exception:
            pass
        return result

    def _save_artist_background_provider_settings(self) -> None:
        try:
            os.makedirs(os.path.dirname(self._artist_background_provider_settings_path), exist_ok=True)
            temporary = self._artist_background_provider_settings_path + ".tmp"
            with open(temporary, "w", encoding="utf-8") as handle:
                json.dump(self._artist_background_provider_settings, handle, ensure_ascii=False, indent=2)
            os.replace(temporary, self._artist_background_provider_settings_path)
        except Exception as exc:
            self._log(f"artist background provider settings save error: {exc}")

    async def get_artist_background_provider_settings(self) -> Dict[str, str]:
        return {"fanartApiKey": str(self._artist_background_provider_settings.get("fanart_api_key") or "")}

    async def set_fanart_api_key(self, api_key: str) -> Dict[str, str]:
        self._artist_background_provider_settings["fanart_api_key"] = re.sub(r"[^A-Za-z0-9]", "", str(api_key or ""))[:160]
        self._save_artist_background_provider_settings()
        # Provider credentials are live settings: invalidate negative lookups and
        # transient search candidates so the next search uses the new key immediately.
        with self._artist_background_candidates_lock:
            self._artist_background_candidates.clear()
        return await self.get_artist_background_provider_settings()

    def _folder_asset_stats(self, folders: List[str]) -> Dict[str, int]:
        total_bytes = 0
        files = 0
        for folder in folders:
            if not os.path.isdir(folder):
                continue
            for root, _, names in os.walk(folder):
                for name in names:
                    path = os.path.join(root, name)
                    try:
                        if os.path.isfile(path):
                            total_bytes += max(0, int(os.path.getsize(path)))
                            files += 1
                    except Exception:
                        pass
        return {"bytes": total_bytes, "files": files}

    def _asset_path_stats(self, paths: Set[str]) -> Dict[str, int]:
        total_bytes = 0
        files = 0
        for raw_path in paths:
            path = os.path.abspath(str(raw_path or ""))
            try:
                if os.path.isfile(path):
                    total_bytes += max(0, int(os.path.getsize(path)))
                    files += 1
            except Exception:
                pass
        return {"bytes": total_bytes, "files": files}

    def _split_asset_stats(self, folders: List[str], provider: str) -> Dict[str, int]:
        total = self._folder_asset_stats(folders)
        manual = self._asset_path_stats(self._manual_artist_background_paths(provider))
        return {
            "bytes": max(0, int(total.get("bytes", 0)) - int(manual.get("bytes", 0))),
            "files": max(0, int(total.get("files", 0)) - int(manual.get("files", 0))),
            "manualBytes": int(manual.get("bytes", 0)),
            "manualFiles": int(manual.get("files", 0)),
            "totalBytes": int(total.get("bytes", 0)),
            "totalFiles": int(total.get("files", 0)),
        }

    def _manual_artist_background_paths(self, provider: str) -> Set[str]:
        provider_key = self._artist_background_provider_key(provider)
        selections = self._load_artist_background_selections()
        result: Set[str] = set()
        library = self._load_local_music_library() if provider_key == "local" else {}
        artist_by_id = {
            str(item.get("id") or ""): str(item.get("name") or "")
            for item in (library.get("artists", []) if isinstance(library, dict) and isinstance(library.get("artists"), list) else [])
            if isinstance(item, dict)
        }
        for key, value in selections.items() if isinstance(selections, dict) else []:
            if not str(key).startswith(provider_key + ":") or not isinstance(value, dict) or not bool(value.get("manual")):
                continue
            identity = str(key).split(":", 1)[1]
            resource_id = str(value.get("resourceId") or "")
            if not resource_id:
                artist_name = artist_by_id.get(identity, "") or str(value.get("artistName") or "")
                if artist_name:
                    resource_id = self._artist_background_resource_id(provider_key, identity, artist_name)
            folder = self._artist_background_dir_for(provider_key)
            if resource_id:
                for extension in ("jpg", "jpeg", "png", "webp", "avif", "gif"):
                    candidate = os.path.abspath(os.path.join(folder, f"{resource_id}.{extension}"))
                    if os.path.isfile(candidate):
                        result.add(candidate)
        return result

    def _clear_asset_folders(self, folders: List[str], preserve: Optional[Set[str]] = None, progress=None) -> Dict[str, int]:
        preserve_paths = {os.path.abspath(path) for path in (preserve or set())}
        candidates: List[str] = []
        for folder in folders:
            if not os.path.isdir(folder):
                continue
            for root, _, names in os.walk(folder):
                for name in names:
                    path = os.path.abspath(os.path.join(root, name))
                    if os.path.isfile(path) and path not in preserve_paths:
                        candidates.append(path)
        total = len(candidates)
        removed = 0
        removed_bytes = 0
        for index, path in enumerate(candidates):
            if progress:
                progress(path, index, total)
            try:
                removed_bytes += max(0, int(os.path.getsize(path)))
                os.remove(path)
                removed += 1
            except Exception:
                pass
        for folder in folders:
            if not os.path.isdir(folder):
                continue
            for root, dirs, _ in os.walk(folder, topdown=False):
                for name in dirs:
                    try:
                        os.rmdir(os.path.join(root, name))
                    except Exception:
                        pass
        return {"files": removed, "bytes": removed_bytes}

    def _image_dimensions_from_bytes(self, data: bytes) -> Tuple[int, int]:
        try:
            if data.startswith(b"\x89PNG\r\n\x1a\n") and len(data) >= 24:
                return struct.unpack(">II", data[16:24])
            if data[:6] in {b"GIF87a", b"GIF89a"} and len(data) >= 10:
                return struct.unpack("<HH", data[6:10])
            if data.startswith(b"RIFF") and data[8:12] == b"WEBP" and len(data) >= 30:
                chunk = data[12:16]
                if chunk == b"VP8X":
                    width = 1 + int.from_bytes(data[24:27], "little")
                    height = 1 + int.from_bytes(data[27:30], "little")
                    return width, height
                if chunk == b"VP8L" and len(data) >= 25:
                    bits = int.from_bytes(data[21:25], "little")
                    return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
                if chunk == b"VP8 " and len(data) >= 30 and data[23:26] == b"\x9d\x01\x2a":
                    return struct.unpack("<HH", data[26:30])
            if data.startswith(b"\xff\xd8"):
                offset = 2
                length = len(data)
                while offset + 9 < length:
                    if data[offset] != 0xFF:
                        offset += 1
                        continue
                    marker = data[offset + 1]
                    offset += 2
                    if marker in {0xD8, 0xD9}:
                        continue
                    if offset + 2 > length:
                        break
                    segment_length = int.from_bytes(data[offset:offset + 2], "big")
                    if segment_length < 2 or offset + segment_length > length:
                        break
                    if marker in {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}:
                        height = int.from_bytes(data[offset + 3:offset + 5], "big")
                        width = int.from_bytes(data[offset + 5:offset + 7], "big")
                        return width, height
                    offset += segment_length
        except Exception:
            pass
        return 0, 0

    def _local_image_dimensions(self, path: str) -> Tuple[int, int]:
        try:
            with open(path, "rb") as handle:
                return self._image_dimensions_from_bytes(handle.read(768 * 1024))
        except Exception:
            return 0, 0

    def _image_content_type_from_bytes(self, data: bytes, fallback: str = "image/jpeg") -> str:
        sample = bytes(data[:32] or b"")
        if sample.startswith(b"\xff\xd8\xff"):
            return "image/jpeg"
        if sample.startswith(b"\x89PNG\r\n\x1a\n"):
            return "image/png"
        if sample.startswith((b"GIF87a", b"GIF89a")):
            return "image/gif"
        if len(sample) >= 12 and sample[:4] == b"RIFF" and sample[8:12] == b"WEBP":
            return "image/webp"
        if len(sample) >= 12 and sample[4:8] == b"ftyp" and sample[8:12] in {b"avif", b"avis"}:
            return "image/avif"
        return fallback if str(fallback or "").startswith("image/") else "image/jpeg"

    def _fanart_preview_url(self, image_url: str) -> str:
        url = str(image_url or "").strip()
        if not url:
            return ""
        try:
            parsed = urllib.parse.urlparse(url)
            host = str(parsed.hostname or "").lower()
            if not host.endswith("assets.fanart.tv"):
                return url
            path = str(parsed.path or "")
            if "/fanart/" in path:
                path = path.replace("/fanart/", "/preview/", 1)
            return urllib.parse.urlunparse(parsed._replace(path=path))
        except Exception:
            return url

    def _remote_image_url_variants(self, image_url: str) -> List[str]:
        url = str(image_url or "").strip()
        if not url:
            return []
        variants: List[str] = []
        def add(value: str) -> None:
            if value and value.startswith(("http://", "https://")) and value not in variants:
                variants.append(value)
        add(url)
        try:
            parsed = urllib.parse.urlparse(url)
            host = str(parsed.hostname or "").lower()
            if host.endswith("assets.fanart.tv"):
                # assets.fanart.tv has shown edge-specific resets on some Windows
                # networks. Try the two public fanart hostnames with the exact same
                # path before giving up; no external downloader is launched.
                for alternate_host in ("static.fanart.tv", "fanart.tv"):
                    netloc = alternate_host
                    if parsed.port:
                        netloc = f"{alternate_host}:{parsed.port}"
                    add(urllib.parse.urlunparse(parsed._replace(scheme="https", netloc=netloc)))
                if "/preview/" in str(parsed.path or ""):
                    full_path = str(parsed.path or "").replace("/preview/", "/fanart/", 1)
                    add(urllib.parse.urlunparse(parsed._replace(scheme="https", path=full_path)))
        except Exception:
            pass
        return variants

    def _download_remote_image_bytes_ipv4(self, image_url: str, referer: str, max_bytes: int, timeout: int, redirects: int = 3) -> Tuple[bytes, str]:
        """Download an image over an explicit IPv4 HTTP/1.1 connection.

        Some Windows networks reset fanart.tv CDN connections before an HTTP
        response is returned. This path bypasses a broken IPv6 route while keeping
        TLS hostname verification and SNI. It never launches an external process.
        """
        parsed = urllib.parse.urlparse(str(image_url or "").strip())
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise RuntimeError("Invalid image URL")
        host = str(parsed.hostname)
        port = int(parsed.port or (443 if parsed.scheme == "https" else 80))
        path = parsed.path or "/"
        if parsed.query:
            path += "?" + parsed.query
        addresses = socket.getaddrinfo(host, port, socket.AF_INET, socket.SOCK_STREAM)
        if not addresses:
            raise RuntimeError(f"No IPv4 address available for {host}")
        last_error: Optional[Exception] = None
        for family, socktype, proto, _, sockaddr in addresses:
            raw_socket = connection = None
            try:
                raw_socket = socket.socket(family, socktype, proto)
                raw_socket.settimeout(max(8, int(timeout)))
                raw_socket.connect(sockaddr)
                if parsed.scheme == "https":
                    context = ssl.create_default_context()
                    raw_socket = context.wrap_socket(raw_socket, server_hostname=host)
                connection = http.client.HTTPConnection(host, port, timeout=max(8, int(timeout)))
                connection.sock = raw_socket
                headers = {
                    "Host": host,
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/138 Safari/537.36",
                    "Accept": "image/avif,image/webp,image/png,image/jpeg,image/*;q=0.8,*/*;q=0.1",
                    "Accept-Encoding": "identity",
                    "Connection": "close",
                    "Cache-Control": "no-cache",
                }
                if referer:
                    headers["Referer"] = referer
                connection.request("GET", path, headers=headers)
                response = connection.getresponse()
                status = int(response.status or 0)
                if status in {301, 302, 303, 307, 308} and redirects > 0:
                    location = str(response.getheader("Location") or "").strip()
                    response.read(4096)
                    if location:
                        target = urllib.parse.urljoin(image_url, location)
                        return self._download_remote_image_bytes_ipv4(target, referer, max_bytes, timeout, redirects - 1)
                if status < 200 or status >= 300:
                    response.read(4096)
                    raise RuntimeError(f"IPv4 image request returned HTTP {status}")
                content_type = str(response.getheader("Content-Type") or "image/jpeg").split(";", 1)[0].strip().lower()
                chunks: List[bytes] = []
                total = 0
                while True:
                    chunk = response.read(min(64 * 1024, max_bytes + 1 - total))
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > int(max_bytes):
                        raise RuntimeError("The selected image is too large")
                    chunks.append(chunk)
                data = b"".join(chunks)
                if len(data) < 256:
                    raise RuntimeError("The selected image could not be downloaded")
                content_type = self._image_content_type_from_bytes(data, content_type)
                if not content_type.startswith("image/"):
                    raise RuntimeError("The selected image could not be downloaded")
                return data, content_type
            except Exception as exc:
                last_error = exc
            finally:
                try:
                    if connection is not None:
                        connection.close()
                    elif raw_socket is not None:
                        raw_socket.close()
                except Exception:
                    pass
        raise RuntimeError(str(last_error or f"IPv4 image connection failed for {host}"))

    def _download_remote_image_bytes_winhttp(self, image_url: str, referer: str, max_bytes: int, timeout: int) -> Tuple[bytes, str]:
        if not self._is_windows():
            raise RuntimeError("WinHTTP is unavailable")
        parsed = urllib.parse.urlparse(str(image_url or ""))
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise RuntimeError("Invalid image URL")
        winhttp = ctypes.WinDLL("winhttp", use_last_error=True)
        HINTERNET = ctypes.c_void_p
        DWORD = ctypes.c_ulong
        DWORD_PTR = ctypes.c_size_t
        INTERNET_PORT = ctypes.c_ushort
        LPVOID = ctypes.c_void_p
        WINHTTP_ACCESS_TYPE_AUTOMATIC_PROXY = 4
        WINHTTP_FLAG_SECURE = 0x00800000
        WINHTTP_QUERY_CONTENT_TYPE = 1
        WINHTTP_QUERY_STATUS_CODE = 19
        WINHTTP_QUERY_FLAG_NUMBER = 0x20000000
        WINHTTP_ADDREQ_FLAG_ADD = 0x20000000
        WINHTTP_ADDREQ_FLAG_REPLACE = 0x80000000

        winhttp.WinHttpOpen.argtypes = [ctypes.c_wchar_p, DWORD, ctypes.c_wchar_p, ctypes.c_wchar_p, DWORD]
        winhttp.WinHttpOpen.restype = HINTERNET
        winhttp.WinHttpConnect.argtypes = [HINTERNET, ctypes.c_wchar_p, INTERNET_PORT, DWORD]
        winhttp.WinHttpConnect.restype = HINTERNET
        winhttp.WinHttpOpenRequest.argtypes = [HINTERNET, ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_void_p, DWORD]
        winhttp.WinHttpOpenRequest.restype = HINTERNET
        winhttp.WinHttpSetTimeouts.argtypes = [HINTERNET, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int]
        winhttp.WinHttpSetTimeouts.restype = ctypes.c_int
        winhttp.WinHttpAddRequestHeaders.argtypes = [HINTERNET, ctypes.c_wchar_p, DWORD, DWORD]
        winhttp.WinHttpAddRequestHeaders.restype = ctypes.c_int
        winhttp.WinHttpSendRequest.argtypes = [HINTERNET, ctypes.c_wchar_p, DWORD, LPVOID, DWORD, DWORD, DWORD_PTR]
        winhttp.WinHttpSendRequest.restype = ctypes.c_int
        winhttp.WinHttpReceiveResponse.argtypes = [HINTERNET, LPVOID]
        winhttp.WinHttpReceiveResponse.restype = ctypes.c_int
        winhttp.WinHttpQueryHeaders.argtypes = [HINTERNET, DWORD, ctypes.c_wchar_p, LPVOID, ctypes.POINTER(DWORD), ctypes.POINTER(DWORD)]
        winhttp.WinHttpQueryHeaders.restype = ctypes.c_int
        winhttp.WinHttpReadData.argtypes = [HINTERNET, LPVOID, DWORD, ctypes.POINTER(DWORD)]
        winhttp.WinHttpReadData.restype = ctypes.c_int
        winhttp.WinHttpCloseHandle.argtypes = [HINTERNET]
        winhttp.WinHttpCloseHandle.restype = ctypes.c_int

        session = connection = request = None
        try:
            session = winhttp.WinHttpOpen(
                f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) EternalMix/{mix_identity.VERSION}",
                WINHTTP_ACCESS_TYPE_AUTOMATIC_PROXY,
                None,
                None,
                0,
            )
            if not session:
                raise OSError(ctypes.get_last_error(), "WinHttpOpen failed")
            port = parsed.port or (443 if parsed.scheme == "https" else 80)
            connection = winhttp.WinHttpConnect(session, parsed.hostname, port, 0)
            if not connection:
                raise OSError(ctypes.get_last_error(), "WinHttpConnect failed")
            path = parsed.path or "/"
            if parsed.query:
                path += "?" + parsed.query
            flags = WINHTTP_FLAG_SECURE if parsed.scheme == "https" else 0
            request = winhttp.WinHttpOpenRequest(connection, "GET", path, None, None, None, flags)
            if not request:
                raise OSError(ctypes.get_last_error(), "WinHttpOpenRequest failed")
            timeout_ms = max(5000, int(timeout) * 1000)
            winhttp.WinHttpSetTimeouts(request, 5000, 7000, timeout_ms, timeout_ms)
            header_lines = [
                "Accept: image/avif,image/webp,image/png,image/jpeg,image/*;q=0.8,*/*;q=0.1",
                "Accept-Encoding: identity",
                "Cache-Control: no-cache",
                "Pragma: no-cache",
            ]
            if referer:
                header_lines.append(f"Referer: {referer}")
            extra_headers = "\r\n".join(header_lines) + "\r\n"
            if not winhttp.WinHttpAddRequestHeaders(request, extra_headers, DWORD(-1).value, WINHTTP_ADDREQ_FLAG_ADD | WINHTTP_ADDREQ_FLAG_REPLACE):
                raise OSError(ctypes.get_last_error(), "WinHttpAddRequestHeaders failed")
            if not winhttp.WinHttpSendRequest(request, None, 0, None, 0, 0, 0):
                raise OSError(ctypes.get_last_error(), "WinHttpSendRequest failed")
            if not winhttp.WinHttpReceiveResponse(request, None):
                raise OSError(ctypes.get_last_error(), "WinHttpReceiveResponse failed")

            status = DWORD(0)
            status_size = DWORD(ctypes.sizeof(status))
            if not winhttp.WinHttpQueryHeaders(request, WINHTTP_QUERY_STATUS_CODE | WINHTTP_QUERY_FLAG_NUMBER, None, ctypes.byref(status), ctypes.byref(status_size), None):
                raise OSError(ctypes.get_last_error(), "WinHttpQueryHeaders status failed")
            if int(status.value) < 200 or int(status.value) >= 300:
                raise RuntimeError(f"WinHTTP image request returned HTTP {int(status.value)}")

            content_type = "image/jpeg"
            content_length = DWORD(0)
            winhttp.WinHttpQueryHeaders(request, WINHTTP_QUERY_CONTENT_TYPE, None, None, ctypes.byref(content_length), None)
            if content_length.value:
                content_buffer = ctypes.create_unicode_buffer(max(2, int(content_length.value // ctypes.sizeof(ctypes.c_wchar)) + 1))
                if winhttp.WinHttpQueryHeaders(request, WINHTTP_QUERY_CONTENT_TYPE, None, content_buffer, ctypes.byref(content_length), None):
                    content_type = str(content_buffer.value or "image/jpeg").split(";", 1)[0].strip().lower()

            chunks: List[bytes] = []
            total = 0
            while True:
                buffer = ctypes.create_string_buffer(64 * 1024)
                read = DWORD(0)
                if not winhttp.WinHttpReadData(request, buffer, len(buffer), ctypes.byref(read)):
                    raise OSError(ctypes.get_last_error(), "WinHttpReadData failed")
                count = int(read.value)
                if count <= 0:
                    break
                total += count
                if total > int(max_bytes):
                    raise RuntimeError("The selected image is too large")
                chunks.append(buffer.raw[:count])
            data = b"".join(chunks)
            if len(data) < 256:
                raise RuntimeError("The selected image could not be downloaded")
            content_type = self._image_content_type_from_bytes(data, content_type)
            if not content_type.startswith("image/"):
                raise RuntimeError("The selected image could not be downloaded")
            return data, content_type
        finally:
            for handle in (request, connection, session):
                if handle:
                    try:
                        winhttp.WinHttpCloseHandle(handle)
                    except Exception:
                        pass

    def _download_remote_image_bytes(self, image_url: str, referer: str = "", max_bytes: int = 28 * 1024 * 1024, timeout: int = 24) -> Tuple[bytes, str]:
        variants = self._remote_image_url_variants(image_url)
        if not variants or not variants[0].startswith("http"):
            raise RuntimeError("The selected image could not be downloaded")
        base_headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/138 Safari/537.36",
            "Accept": "image/avif,image/webp,image/png,image/jpeg,image/*;q=0.8,*/*;q=0.1",
            "Accept-Encoding": "identity",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
            "Connection": "close",
        }
        is_fanart = str(urllib.parse.urlparse(variants[0]).hostname or "").lower().endswith("fanart.tv")
        last_error: Optional[Exception] = None
        attempt_errors: List[str] = []

        def validate(data: bytes, content_type: str) -> Tuple[bytes, str]:
            if len(data) < 256 or len(data) > max_bytes:
                raise RuntimeError("The selected image could not be downloaded")
            normalized_type = self._image_content_type_from_bytes(data, content_type)
            if not normalized_type.startswith("image/"):
                raise RuntimeError("The selected image could not be downloaded")
            return data, normalized_type

        with self._remote_image_download_semaphore:
            # On Windows, Fanart CDN failures observed in diagnostics happen before
            # an HTTP response is produced. WinHTTP first uses the native proxy/TLS
            # configuration; an explicit IPv4 HTTP/1.1 path then bypasses bad IPv6
            # routes. Neither path launches curl, PowerShell or a console window.
            if is_fanart and self._is_windows():
                for candidate_url in variants:
                    started = time.monotonic()
                    try:
                        data, content_type = self._download_remote_image_bytes_winhttp(candidate_url, referer, max_bytes, timeout)
                        data, content_type = validate(data, content_type)
                        self._record_diagnostic_event("artwork", "download_ok", {"provider": "winhttp", "host": urllib.parse.urlparse(candidate_url).hostname, "bytes": len(data), "elapsedMs": round((time.monotonic() - started) * 1000, 1)})
                        return data, content_type
                    except Exception as exc:
                        last_error = exc
                        attempt_errors.append(f"winhttp {candidate_url}: {exc}")
                for candidate_url in variants:
                    started = time.monotonic()
                    try:
                        data, content_type = self._download_remote_image_bytes_ipv4(candidate_url, referer, max_bytes, timeout)
                        data, content_type = validate(data, content_type)
                        self._record_diagnostic_event("artwork", "download_ok", {"provider": "ipv4-http11", "host": urllib.parse.urlparse(candidate_url).hostname, "bytes": len(data), "elapsedMs": round((time.monotonic() - started) * 1000, 1)})
                        return data, content_type
                    except Exception as exc:
                        last_error = exc
                        attempt_errors.append(f"ipv4-http11 {candidate_url}: {exc}")

            # urllib remains the normal path for TheAudioDB and a final in-process
            # compatibility path for Fanart. There are no curl/PowerShell fallbacks.
            for candidate_url in variants:
                headers = dict(base_headers)
                if referer:
                    headers["Referer"] = referer
                started = time.monotonic()
                try:
                    request = urllib.request.Request(candidate_url, headers=headers)
                    with urllib.request.urlopen(request, timeout=max(8, int(timeout))) as response:
                        data = response.read(max_bytes + 1)
                        content_type = str(response.headers.get("Content-Type") or "image/jpeg").split(";", 1)[0].strip().lower()
                    data, content_type = validate(data, content_type)
                    self._record_diagnostic_event("artwork", "download_ok", {"provider": "urllib", "host": urllib.parse.urlparse(candidate_url).hostname, "bytes": len(data), "elapsedMs": round((time.monotonic() - started) * 1000, 1)})
                    return data, content_type
                except Exception as exc:
                    last_error = exc
                    attempt_errors.append(f"urllib {candidate_url}: {exc}")

        if attempt_errors:
            self._record_diagnostic_event("artwork", "download_failed", {"url": variants[0], "attempts": attempt_errors[-8:]}, "error")
            self._log("remote image download failed: " + " | ".join(attempt_errors[-6:]))
        raise RuntimeError(str(last_error or "The selected image could not be downloaded"))

    def _probe_remote_image(self, image_url: str, referer: str = "") -> Tuple[int, int, str]:
        url = str(image_url or "").strip()
        if not url.startswith("http"):
            return 0, 0, ""
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/138 Safari/537.36",
            "Accept": "image/avif,image/webp,image/png,image/jpeg,image/*;q=0.8",
            "Range": "bytes=0-786431",
        }
        if referer:
            headers["Referer"] = referer
        try:
            request = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(request, timeout=10) as response:
                data = response.read(768 * 1024)
                content_type = str(response.headers.get("Content-Type") or "").lower()
            width, height = self._image_dimensions_from_bytes(data)
            return width, height, content_type
        except Exception as exc:
            self._log(f"Artist background probe unavailable for {url}: {exc}")
            try:
                data, content_type = self._download_remote_image_bytes(url, referer, 18 * 1024 * 1024, 18)
                width, height = self._image_dimensions_from_bytes(data[:768 * 1024])
                return width, height, content_type
            except Exception as fallback_exc:
                self._log(f"Artist background full probe unavailable for {url}: {fallback_exc}")
                return 0, 0, ""

    def _musicbrainz_artist_mbids(self, artist_name: str, limit: int = 6) -> List[str]:
        name = re.sub(r"\s+", " ", str(artist_name or "").strip())[:160]
        if not name:
            return []
        params = {"query": f'artist:"{name}"', "fmt": "json", "limit": max(8, min(25, int(limit) * 3))}
        request = urllib.request.Request(
            "https://musicbrainz.org/ws/2/artist/?" + urllib.parse.urlencode(params),
            headers={"User-Agent": mix_identity.USER_AGENT, "Accept": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            payload = json.loads(response.read().decode("utf-8", errors="replace"))
        artists = payload.get("artists") if isinstance(payload, dict) else []
        if not isinstance(artists, list):
            return []
        normalized = self._sanitize_text(name)
        candidates = [item for item in artists if isinstance(item, dict) and item.get("id")]
        candidates.sort(key=lambda item: (
            0 if self._sanitize_text(item.get("name", "")) == normalized else 1,
            0 if any(self._sanitize_text(alias.get("name", "")) == normalized for alias in (item.get("aliases") or []) if isinstance(alias, dict)) else 1,
            -int(item.get("score") or 0),
            0 if str(item.get("type") or "").lower() in {"person", "group", "orchestra", "choir"} else 1,
        ))
        result: List[str] = []
        for item in candidates:
            mbid = str(item.get("id") or "").strip()
            if mbid and mbid not in result:
                result.append(mbid)
            if len(result) >= max(1, int(limit)):
                break
        return result

    def _fanart_asset_url(self, raw_url: Any, mbid: str, category: str) -> str:
        value = str(raw_url or "").strip().replace("\\/", "/")
        if not value:
            return ""
        if value.startswith("//"):
            return "https:" + value
        if value.startswith("http://"):
            return "https://" + value[len("http://"):]
        if value.startswith("https://"):
            return value
        clean_mbid = re.sub(r"[^A-Za-z0-9-]", "", str(mbid or ""))
        clean_category = re.sub(r"[^A-Za-z0-9_-]", "", str(category or ""))
        if not clean_mbid or not clean_category:
            return ""
        if "/" in value:
            return "https://assets.fanart.tv/" + value.lstrip("/")
        filename = urllib.parse.quote(os.path.basename(value), safe="._-()")
        return f"https://assets.fanart.tv/fanart/music/{clean_mbid}/{clean_category}/{filename}"

    def _fanart_artist_payload(self, mbid: str) -> Dict[str, Any]:
        api_key = re.sub(r"[^A-Za-z0-9]", "", str(self._artist_background_provider_settings.get("fanart_api_key") or ""))
        clean_mbid = re.sub(r"[^A-Za-z0-9-]", "", str(mbid or ""))
        if not api_key or not clean_mbid:
            return {}
        last_error: Optional[Exception] = None
        for version in ("v3.2", "v3"):
            try:
                url = f"https://webservice.fanart.tv/{version}/music/{urllib.parse.quote(clean_mbid, safe='')}?" + urllib.parse.urlencode({"api_key": api_key})
                request = urllib.request.Request(
                    url,
                    headers={"User-Agent": mix_identity.USER_AGENT, "Accept": "application/json"},
                )
                with urllib.request.urlopen(request, timeout=5) as response:
                    payload = json.loads(response.read().decode("utf-8", errors="replace"))
                if isinstance(payload, dict):
                    return payload
            except urllib.error.HTTPError as exc:
                last_error = exc
                code = int(getattr(exc, "code", 0) or 0)
                if code not in {404, 405, 410}:
                    self._log(f"fanart.tv {version} lookup failed for {clean_mbid}: HTTP {getattr(exc, 'code', '?')}")
                    # A 429/5xx response is an upstream availability problem, not
                    # an API-version mismatch. Retrying the legacy endpoint only
                    # doubles the wait and is what made Background Settings appear
                    # frozen for minutes.
                    break
            except Exception as exc:
                last_error = exc
                self._log(f"fanart.tv {version} lookup failed for {clean_mbid}: {exc}")
                break
        if last_error:
            return {}
        return {}

    def _theaudiodb_artist_payload(self, artist_name: str) -> Dict[str, Any]:
        name = re.sub(r"\s+", " ", str(artist_name or "").strip())[:180]
        cache_key = self._sanitize_text(name)
        if not cache_key:
            return {}
        if not hasattr(self, "_theaudiodb_artist_cache"):
            self._theaudiodb_artist_cache = {}
        if not hasattr(self, "_theaudiodb_request_lock"):
            self._theaudiodb_request_lock = threading.Lock()
        if not hasattr(self, "_theaudiodb_last_request_at"):
            self._theaudiodb_last_request_at = 0.0
        cached = self._theaudiodb_artist_cache.get(cache_key)
        if cached and time.time() - float(cached[0]) < 24 * 3600:
            return dict(cached[1])
        with self._theaudiodb_request_lock:
            cached = self._theaudiodb_artist_cache.get(cache_key)
            if cached and time.time() - float(cached[0]) < 24 * 3600:
                return dict(cached[1])
            delay = 0.55 - (time.time() - self._theaudiodb_last_request_at)
            if delay > 0:
                time.sleep(delay)
            url = "https://www.theaudiodb.com/api/v1/json/2/search.php?" + urllib.parse.urlencode({"s": name})
            request = urllib.request.Request(
                url,
                headers={"User-Agent": mix_identity.USER_AGENT, "Accept": "application/json"},
            )
            try:
                with urllib.request.urlopen(request, timeout=8) as response:
                    payload = json.loads(response.read().decode("utf-8", errors="replace"))
            except Exception as exc:
                self._log(f"TheAudioDB artist lookup failed for {name}: {exc}")
                payload = {}
            finally:
                self._theaudiodb_last_request_at = time.time()
            result = payload if isinstance(payload, dict) else {}
            if len(self._theaudiodb_artist_cache) > 180:
                self._theaudiodb_artist_cache.clear()
            self._theaudiodb_artist_cache[cache_key] = (time.time(), result)
            return dict(result)

    def _theaudiodb_artist_candidates(
        self,
        artist_name: str,
        fields: Tuple[str, ...],
        source: str = "TheAudioDB",
    ) -> List[Dict[str, Any]]:
        payload = self._theaudiodb_artist_payload(artist_name)
        artists = payload.get("artists") if isinstance(payload, dict) else []
        if not isinstance(artists, list):
            return []
        normalized = self._sanitize_text(artist_name)
        exact = [
            item for item in artists
            if isinstance(item, dict) and self._sanitize_text(item.get("strArtist", "")) == normalized
        ]
        selected = exact or [item for item in artists if isinstance(item, dict)]
        result: List[Dict[str, Any]] = []
        seen: Set[str] = set()
        for item in selected[:3]:
            for field in fields:
                value = str(item.get(field) or "").strip()
                if not value.startswith("http") or value in seen:
                    continue
                seen.add(value)
                result.append({"url": value, "width": 0, "height": 0, "source": source})
        return result

    def _fanart_artist_background_candidates(self, artist_name: str, limit: int = 24) -> List[Dict[str, Any]]:
        if not self._artist_background_provider_settings.get("fanart_api_key"):
            return []
        candidates: List[Dict[str, Any]] = []
        seen: Set[str] = set()
        mbids: List[str] = []
        try:
            payload = self._theaudiodb_artist_payload(artist_name)
            artists = payload.get("artists") if isinstance(payload, dict) else []
            if not isinstance(artists, list):
                artists = []
            normalized = self._sanitize_text(artist_name)
            ordered = sorted(
                (item for item in artists if isinstance(item, dict)),
                key=lambda item: 0 if self._sanitize_text(item.get("strArtist", "")) == normalized else 1,
            )
            for item in ordered:
                mbid = re.sub(r"[^A-Za-z0-9-]", "", str(item.get("strMusicBrainzID") or ""))
                if mbid and mbid not in mbids:
                    mbids.append(mbid)
                if mbids:
                    break
        except Exception as exc:
            self._log(f"TheAudioDB artist identifier unavailable for {artist_name}: {exc}")
        if not mbids:
            try:
                # One exact MBID is sufficient and keeps the fallback request bounded.
                mbids = self._musicbrainz_artist_mbids(artist_name, 1)
            except Exception as exc:
                self._log(f"artist identifier fallback unavailable for {artist_name}: {exc}")
        for mbid in mbids:
            payload = self._fanart_artist_payload(mbid)
            entries = payload.get("artistbackground") if isinstance(payload, dict) else []
            if not isinstance(entries, list):
                continue
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                image_url = self._fanart_asset_url(entry.get("url"), mbid, "artistbackground")
                if not image_url or image_url in seen:
                    continue
                seen.add(image_url)
                width = int(entry.get("width") or 0)
                height = int(entry.get("height") or 0)
                # v3.2 includes dimensions. Legacy responses do not, but
                # artistbackground assets are standard wide fanart; avoid
                # downloading every result merely to inspect its dimensions.
                if width <= 0 or height <= 0:
                    width, height = 1920, 1080
                if width >= 800 and height >= 400 and width / max(1, height) >= 1.25:
                    candidates.append({"url": image_url, "width": width, "height": height, "source": "fanart.tv"})
                if len(candidates) >= max(1, int(limit)):
                    return candidates
        return candidates

    def _theaudiodb_artist_background_candidates(self, artist_name: str, limit: int = 12) -> List[Dict[str, Any]]:
        raw = self._theaudiodb_artist_candidates(
            artist_name,
            ("strArtistFanart", "strArtistFanart2", "strArtistFanart3", "strArtistFanart4", "strArtistWideThumb", "strArtistBanner"),
            "TheAudioDB",
        )
        # Do not synchronously download every AudioDB image merely to discover its
        # dimensions. That turned a single search into up to sixteen serial network
        # probes. The actual preview/download path validates the bytes later.
        result: List[Dict[str, Any]] = []
        for item in raw:
            result.append({**item, "width": 1920, "height": 1080})
            if len(result) >= max(1, int(limit)):
                break
        return result

    def _artist_background_search_sync(self, provider: str, artist_id: str, artist_name: str, source: str = "all") -> Dict[str, Any]:
        provider_key = self._artist_background_provider_key(provider)
        name = re.sub(r"\s+", " ", str(artist_name or "").strip())[:180]
        clean_id = re.sub(r"[^A-Za-z0-9]", "", str(artist_id or ""))
        if not name:
            raise RuntimeError("Artist name is required")

        # fanart.tv is preferred and TheAudioDB is the fallback.
        candidates: List[Dict[str, Any]] = []
        provider_errors: Dict[str, str] = {}
        pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="NowPlaying-BackgroundProvider")
        futures = {
            pool.submit(self._fanart_artist_background_candidates, name, 24): "fanart.tv",
            pool.submit(self._theaudiodb_artist_background_candidates, name, 16): "TheAudioDB",
        }
        try:
            done, pending = wait(list(futures), timeout=9.0)
            for future in done:
                provider_name = futures[future]
                try:
                    values = future.result()
                    if isinstance(values, list):
                        candidates.extend(item for item in values if isinstance(item, dict))
                except Exception as exc:
                    provider_errors[provider_name] = str(exc)
                    self._log(f"{provider_name} background search unavailable for {name}: {exc}")
            for future in pending:
                provider_name = futures[future]
                provider_errors[provider_name] = "timeout"
                future.cancel()
                self._log(f"{provider_name} background search timed out for {name}")
        finally:
            pool.shutdown(wait=False, cancel_futures=True)

        unique: Dict[str, Dict[str, Any]] = {}
        for item in candidates:
            url = str(item.get("url") or "").strip()
            width = int(item.get("width") or 0)
            height = int(item.get("height") or 0)
            if not url.startswith("http") or width <= 0 or height <= 0:
                continue
            if url not in unique:
                unique[url] = item
        selection_key = self._artist_background_selection_key(provider_key, clean_id, name)
        selected_url = str((self._load_artist_background_selections().get(selection_key) or {}).get("url") or "")
        ordered = list(unique.values())
        ordered.sort(key=lambda item: (
            0 if item.get("source") == "fanart.tv" else 1,
            abs((int(item.get("width") or 1) / max(1, int(item.get("height") or 1))) - (16 / 9)),
            -(int(item.get("width") or 0) * int(item.get("height") or 0)),
        ))
        public_items: List[Dict[str, Any]] = []
        now = time.time()
        with self._artist_background_candidates_lock:
            self._artist_background_candidates = {
                key: value for key, value in self._artist_background_candidates.items()
                if now - float(value.get("storedAt", 0.0) or 0.0) < 1800
            }
            for item in ordered[:36]:
                url = str(item.get("url") or "")
                source_name = str(item.get("source") or "Online")
                preview_version = "fanart-preview-v3" if source_name.lower() == "fanart.tv" else "standard-v1"
                candidate_id = hashlib.sha256(f"{selection_key}:{url}:{preview_version}".encode("utf-8")).hexdigest()[:32]
                stored = {
                    "provider": provider_key,
                    "artistId": clean_id,
                    "artistName": name,
                    "url": url,
                    "previewRemoteUrl": self._fanart_preview_url(url) if source_name.lower() == "fanart.tv" else url,
                    "width": int(item.get("width") or 0),
                    "height": int(item.get("height") or 0),
                    "source": source_name,
                    "storedAt": now,
                }
                self._artist_background_candidates[candidate_id] = stored
                public_items.append({
                    "id": candidate_id,
                    "previewUrl": url,
                    "width": stored["width"],
                    "height": stored["height"],
                    "source": stored["source"],
                    "selected": bool(selected_url and selected_url == url),
                })
        return {"items": public_items, "selectedUrl": selected_url, "source": "all", "providerErrors": provider_errors}

    async def search_artist_backgrounds(self, provider: str, artist_id: str, artist_name: str, source: str = "all") -> Dict[str, Any]:
        try:
            data = await asyncio.wait_for(
                self._run_in_executor(
                    self._artist_background_search_executor,
                    self._artist_background_search_sync,
                    provider,
                    artist_id,
                    artist_name,
                    source,
                ),
                timeout=15.0,
            )
            base = await self.get_local_music_stream_base()
            for item in data.get("items", []) if isinstance(data, dict) else []:
                candidate_id = re.sub(r"[^A-Za-z0-9]", "", str(item.get("id") or ""))
                if candidate_id:
                    item["previewUrl"] = f"{base}/preview/{urllib.parse.quote(candidate_id, safe='')}"
            return {"ok": True, "data": data}
        except Exception as exc:
            message = "Background search timed out" if isinstance(exc, asyncio.TimeoutError) else str(exc)
            self._record_diagnostic_event("artwork", "search_failed", {"artist": artist_name, "error": message}, "error")
            return {"ok": False, "error": message}

    def _download_selected_artist_background(self, provider: str, artist_id: str, artist_name: str, candidate_id: str) -> Dict[str, Any]:
        with self._artist_background_candidates_lock:
            candidate = dict(self._artist_background_candidates.get(str(candidate_id or "")) or {})
        if not candidate:
            raise RuntimeError("Background choice expired. Search again")
        provider_key = self._artist_background_provider_key(provider)
        clean_id = re.sub(r"[^A-Za-z0-9]", "", str(artist_id or ""))
        name = re.sub(r"\s+", " ", str(artist_name or "").strip())[:180]
        if candidate.get("provider") != provider_key or candidate.get("artistId") != clean_id or candidate.get("artistName") != name:
            raise RuntimeError("Invalid background choice")
        image_url = str(candidate.get("url") or "")
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/138 Safari/537.36",
            "Accept": "image/avif,image/webp,image/png,image/jpeg,image/*;q=0.8",
        }
        image_host = str(urllib.parse.urlparse(image_url).hostname or "").lower()
        if image_host.endswith("fanart.tv"):
            headers["Referer"] = "https://fanart.tv/"
        elif image_host.endswith("theaudiodb.com"):
            headers["Referer"] = "https://www.theaudiodb.com/"
        referer = str(headers.get("Referer") or "")
        try:
            data, content_type = self._download_remote_image_bytes(image_url, referer, 28 * 1024 * 1024, 24)
        except Exception as primary_error:
            preview_url = self._fanart_preview_url(image_url) if image_host.endswith("fanart.tv") else image_url
            if not preview_url or preview_url == image_url:
                raise
            self._log(f"fanart.tv full asset unavailable, using preview fallback: {primary_error}")
            data, content_type = self._download_remote_image_bytes(preview_url, referer, 12 * 1024 * 1024, 20)
        if len(data) < 1024 or len(data) > 28 * 1024 * 1024 or "image" not in content_type:
            raise RuntimeError("The selected image could not be downloaded")
        width, height = self._image_dimensions_from_bytes(data[:768 * 1024])
        if not width or not height:
            raise RuntimeError("The selected image is not supported")
        resource_id = self._artist_background_resource_id(provider_key, clean_id, name)
        folder = self._artist_background_dir_for(provider_key)
        stream_kind = self._artist_background_stream_kind(provider_key)
        if not resource_id:
            raise RuntimeError("Invalid artist")
        os.makedirs(folder, exist_ok=True)
        for extension in ("jpg", "jpeg", "png", "webp", "avif", "gif"):
            old_path = os.path.join(folder, f"{resource_id}.{extension}")
            try:
                if os.path.isfile(old_path):
                    os.remove(old_path)
            except Exception:
                pass
        extension = self._image_extension_from_content_type(content_type)
        output = os.path.join(folder, f"{resource_id}.{extension}")
        temporary = output + ".tmp"
        with open(temporary, "wb") as handle:
            handle.write(data)
        os.replace(temporary, output)
        selection_key = self._artist_background_selection_key(provider_key, clean_id, name)
        selections = self._load_artist_background_selections()
        selections[selection_key] = {
            "url": image_url,
            "width": width,
            "height": height,
            "source": str(candidate.get("source") or "Online"),
            "manual": True,
            "resourceId": resource_id,
            "artistName": name,
            "storedAt": time.time(),
        }
        self._artist_background_selections = selections
        self._save_artist_background_selections()
        if provider_key == "youtubeMusic":
            self._youtube_music_artist_background_cache[name.casefold()] = output
        else:
            self._local_music_artist_background_cache[name.casefold()] = output
        return {"path": output, "resourceId": resource_id, "streamKind": stream_kind, "width": width, "height": height}

    async def apply_artist_background(self, provider: str, artist_id: str, artist_name: str, candidate_id: str) -> Dict[str, Any]:
        try:
            result = await self._run_in_executor(
                self._cover_executor,
                self._download_selected_artist_background,
                provider,
                artist_id,
                artist_name,
                candidate_id,
            )
            base = await self.get_local_music_stream_base()
            path = str(result.get("path") or "")
            version = int(os.path.getmtime(path)) if path and os.path.isfile(path) else int(time.time())
            url = f"{base}/{result['streamKind']}/{urllib.parse.quote(str(result['resourceId']), safe='')}?v={version}"
            return {"ok": True, "url": url, "width": result.get("width", 0), "height": result.get("height", 0)}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}


    # --- YouTube Music artist backgrounds (independent store) -----------------
    def _set_youtube_music_artist_cache_progress(self, **changes: Any) -> None:
        with self._youtube_music_artist_cache_progress_lock:
            self._youtube_music_artist_cache_progress.update(changes)

    def _youtube_music_artist_cache_progress_snapshot(self) -> Dict[str, Any]:
        with self._youtube_music_artist_cache_progress_lock:
            return dict(self._youtube_music_artist_cache_progress)

    def _cache_youtube_music_artist_background(self, artist_name: str, image_url: str) -> str:
        name = str(artist_name or "").strip()
        url = str(image_url or "").strip()
        if not name or not url.startswith("http"):
            return ""
        background_id = self._local_music_hash("youtubemusic-artist-background", name.casefold())
        existing = self._local_music_stream_asset_path("youtubemusic-background", background_id)
        if existing:
            return existing
        try:
            host = str(urllib.parse.urlparse(url).hostname or "").lower()
            referer = "https://fanart.tv/" if host.endswith("fanart.tv") else "https://www.theaudiodb.com/" if host.endswith("theaudiodb.com") else ""
            data, content_type = self._download_remote_image_bytes(url, referer, 16 * 1024 * 1024, 20)
            if not data or len(data) > 16 * 1024 * 1024:
                return ""
            extension = self._image_extension_from_content_type(content_type)
            os.makedirs(self.youtube_music_artist_background_dir, exist_ok=True)
            output = os.path.join(self.youtube_music_artist_background_dir, f"{background_id}.{extension}")
            with open(output, "wb") as handle:
                handle.write(data)
            return output
        except Exception as exc:
            self._log(f"YouTube Music artist background download failed for {name}: {exc}")
            return ""

    def _youtube_music_artist_background_sync(self, artist_name: str) -> str:
        name = str(artist_name or "").strip()
        if not name:
            return ""
        key = name.casefold()
        cached = self._youtube_music_artist_background_cache.get(key)
        if cached is not None:
            if cached.startswith("http") or os.path.isfile(cached):
                return cached
            self._youtube_music_artist_background_cache.pop(key, None)
        background_id = self._local_music_hash("youtubemusic-artist-background", key)
        existing = self._local_music_stream_asset_path("youtubemusic-background", background_id)
        if existing:
            self._youtube_music_artist_background_cache[key] = existing
            return existing
        candidates: List[str] = []
        try:
            candidates.extend(str(item.get("url") or "") for item in self._fanart_artist_background_candidates(name, 8))
        except Exception as exc:
            self._log(f"YouTube Music artist fanart.tv lookup failed for {name}: {exc}")
        try:
            candidates.extend(str(item.get("url") or "") for item in self._theaudiodb_artist_background_candidates(name, 6))
        except Exception as exc:
            self._log(f"YouTube Music artist TheAudioDB lookup failed for {name}: {exc}")
        seen: Set[str] = set()
        for candidate in candidates:
            value = str(candidate or "").strip()
            if not value.startswith("http") or value in seen:
                continue
            seen.add(value)
            cached_path = self._cache_youtube_music_artist_background(name, value)
            if cached_path:
                self._youtube_music_artist_background_cache[key] = cached_path
                return cached_path
        if len(self._youtube_music_artist_background_cache) > 120:
            self._youtube_music_artist_background_cache.clear()
        self._youtube_music_artist_background_cache[key] = ""
        return ""

    async def get_youtube_music_artist_background(self, artist_name: str) -> str:
        name = str(artist_name or "").strip()[:180]
        if not name:
            return ""
        try:
            result = await self._run_in_executor(self._cover_executor, self._youtube_music_artist_background_sync, name)
            if result and os.path.isfile(result):
                base = await self.get_local_music_stream_base()
                background_id = self._local_music_hash("youtubemusic-artist-background", name.casefold())
                version = int(os.path.getmtime(result))
                return f"{base}/youtubemusic-background/{urllib.parse.quote(background_id, safe='')}?v={version}"
            return ""
        except Exception as exc:
            self._log(f"YouTube Music artist background lookup error for {name}: {exc}")
            return ""

    async def get_youtube_music_artist_cache_progress(self) -> Dict[str, Any]:
        return self._youtube_music_artist_cache_progress_snapshot()

    async def get_youtube_music_artist_cache_stats(self) -> Dict[str, int]:
        return self._split_asset_stats([self.youtube_music_artist_background_dir], "youtubeMusic")

    def _build_youtube_music_artist_cache_sync(self, artists: List[Dict[str, Any]]) -> Dict[str, Any]:
        if not self._youtube_music_artist_cache_build_lock.acquire(blocking=False):
            raise RuntimeError("YouTube Music artist cache is already being created")
        self._set_youtube_music_artist_cache_progress(active=True, phase="loading", current="", completed=0, total=0, error="")
        try:
            names: List[str] = []
            for item in artists if isinstance(artists, list) else []:
                nm = str((item or {}).get("name") or "").strip() if isinstance(item, dict) else ""
                if nm:
                    names.append(nm)
            names = list(dict.fromkeys(names))
            total = len(names)
            self._set_youtube_music_artist_cache_progress(phase="background", total=total)
            cached = 0
            for index, nm in enumerate(names):
                self._set_youtube_music_artist_cache_progress(active=True, phase="background", current=nm, completed=index, total=total)
                try:
                    if self._youtube_music_artist_background_sync(nm):
                        cached += 1
                except Exception as exc:
                    self._log(f"YouTube Music artist cache build error for {nm}: {exc}")
                self._set_youtube_music_artist_cache_progress(completed=index + 1)
            self._set_youtube_music_artist_cache_progress(active=False, phase="complete", current="", completed=total, total=total, error="")
            return {"artists": total, "cached": cached}
        except Exception as exc:
            self._set_youtube_music_artist_cache_progress(active=False, phase="error", current="", error=str(exc))
            raise
        finally:
            self._youtube_music_artist_cache_build_lock.release()

    async def build_youtube_music_artist_cache(self) -> Dict[str, Any]:
        try:
            library = await self._youtube_music_result(self._youtube_music.get_library, "artists", 200)
            artists: List[Dict[str, Any]] = []
            if isinstance(library, dict):
                data = library.get("data") if isinstance(library.get("data"), dict) else library
                container = data.get("artists") if isinstance(data, dict) else {}
                if isinstance(container, dict) and isinstance(container.get("items"), list):
                    artists = container.get("items")
            data = await self._run_in_executor(self._youtube_music_executor, self._build_youtube_music_artist_cache_sync, artists)
            return {"ok": True, "data": data}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def _clear_youtube_music_artist_cache_sync(self) -> Dict[str, int]:
        if not self._youtube_music_artist_cache_build_lock.acquire(blocking=False):
            raise RuntimeError("YouTube Music artist cache is busy")
        self._set_youtube_music_artist_cache_progress(active=True, phase="clearing", current="", completed=0, total=0, error="")
        try:
            preserve = self._manual_artist_background_paths("youtubeMusic")

            def update(path: str, index: int, total: int) -> None:
                self._set_youtube_music_artist_cache_progress(active=True, phase="clearing", current=os.path.basename(path), completed=index, total=total, error="")

            stats = self._clear_asset_folders([self.youtube_music_artist_background_dir], preserve, update)
            self._youtube_music_artist_background_cache.clear()
            self._set_youtube_music_artist_cache_progress(active=False, phase="cleared", current="", completed=stats["files"], total=stats["files"], error="")
            return stats
        except Exception as exc:
            self._set_youtube_music_artist_cache_progress(active=False, phase="error", current="", error=str(exc))
            raise
        finally:
            self._youtube_music_artist_cache_build_lock.release()

    async def clear_youtube_music_artist_cache(self) -> Dict[str, Any]:
        try:
            data = await self._run_in_executor(self._youtube_music_executor, self._clear_youtube_music_artist_cache_sync)
            return {"ok": True, "data": data}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def _remove_manual_artist_backgrounds_sync(self, provider: str) -> Dict[str, int]:
        provider_key = self._artist_background_provider_key(provider)
        lock = (
            self._youtube_music_artist_cache_build_lock if provider_key == "youtubeMusic"
            else self._local_music_cache_build_lock
        )
        if not lock.acquire(blocking=False):
            raise RuntimeError("Artist background storage is busy")
        update_progress = (
            self._set_youtube_music_artist_cache_progress if provider_key == "youtubeMusic"
            else self._set_local_music_cache_progress
        )
        phase_work = "clearing_manual"
        phase_done = "manual_cleared"
        try:
            paths = sorted(self._manual_artist_background_paths(provider_key))
            total = len(paths)
            update_progress(active=True, phase=phase_work, current="", completed=0, total=total, error="")
            removed_bytes = 0
            removed = 0
            for index, path in enumerate(paths, start=1):
                update_progress(active=True, phase=phase_work, current=os.path.basename(path), completed=index - 1, total=total, error="")
                try:
                    if os.path.isfile(path):
                        removed_bytes += max(0, int(os.path.getsize(path)))
                        os.remove(path)
                        removed += 1
                except Exception as exc:
                    self._log(f"manual artist background removal error for {path}: {exc}")
                update_progress(active=True, phase=phase_work, current=os.path.basename(path), completed=index, total=total, error="")

            selections = self._load_artist_background_selections()
            removed_keys = [key for key in list(selections) if str(key).startswith(provider_key + ":") and isinstance(selections.get(key), dict) and bool((selections.get(key) or {}).get("manual"))]
            for key in removed_keys:
                selections.pop(key, None)
            self._artist_background_selections = selections
            self._save_artist_background_selections()

            if provider_key == "youtubeMusic":
                self._youtube_music_artist_background_cache.clear()
            else:
                self._local_music_artist_background_cache.clear()

            update_progress(active=False, phase=phase_done, current="", completed=total, total=total, error="")
            return {"bytes": removed_bytes, "files": removed}
        except Exception as exc:
            update_progress(active=False, phase="error", current="", error=str(exc))
            raise
        finally:
            lock.release()

    async def clear_manual_artist_backgrounds(self, provider: str) -> Dict[str, Any]:
        provider_key = self._artist_background_provider_key(provider)
        try:
            data = await self._run_in_executor(self._local_music_executor, self._remove_manual_artist_backgrounds_sync, provider_key)
            if provider_key == "youtubeMusic":
                stats = await self.get_youtube_music_artist_cache_stats()
            else:
                stats = await self.get_local_music_settings()
            return {"ok": True, "data": data, "stats": stats}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}


    def _diagnostic_snapshot_summary(self, data: Any) -> Dict[str, Any]:
        snapshot = self._canonical_snapshot(data)
        players: List[Dict[str, Any]] = []
        for player in snapshot.get("players", []) if isinstance(snapshot.get("players"), list) else []:
            if not isinstance(player, dict):
                continue
            players.append({
                "id": str(player.get("id") or ""),
                "name": str(player.get("name") or ""),
                "appId": str(player.get("appId") or player.get("app_id") or ""),
                "processName": str(player.get("processName") or player.get("process") or ""),
                "status": str(player.get("status") or ""),
                "title": str(player.get("title") or "")[:160],
                "artist": str(player.get("artist") or "")[:160],
                "selected": bool(player.get("isSelected") or player.get("isCurrent")),
            })
        return {
            "activeService": self._normalized_active_service(),
            "selectedPlayer": str(snapshot.get("selectedPlayer") or ""),
            "currentPlayer": str(snapshot.get("currentPlayer") or ""),
            "players": players,
        }

    def _log_snapshot_diagnostic(self, data: Any, origin: str = "snapshot") -> None:
        try:
            summary = self._diagnostic_snapshot_summary(data)
            signature = json.dumps(summary, ensure_ascii=False, sort_keys=True)
            now = time.monotonic()
            with self._diagnostic_lock:
                if signature == self._diagnostic_snapshot_signature and now - self._diagnostic_snapshot_at < 12.0:
                    return
                self._diagnostic_snapshot_signature = signature
                self._diagnostic_snapshot_at = now
            self._log(f"diagnostic {origin}: {signature}")
        except Exception as exc:
            self._log(f"diagnostic snapshot error: {exc}")

    def _redact_diagnostic_text(self, value: str) -> str:
        text = str(value or "")
        patterns = [
            (r'(?i)(access_token|refresh_token|client_secret|client_id|api_key|client_key)([=\"\': ]+)([^\s\",}]+)', r'\1\2<redacted>'),
            (r'(?i)(authorization:\s*bearer\s+)[A-Za-z0-9._~-]+', r'\1<redacted>'),
        ]
        for pattern, replacement in patterns:
            try:
                text = re.sub(pattern, replacement, text)
            except Exception:
                pass
        return text

    def _read_runtime_logs_for_diagnostics(self) -> str:
        chunks: List[str] = []
        for suffix in (".3", ".2", ".1", ""):
            path = self.log_path + suffix
            if not os.path.isfile(path):
                continue
            try:
                with open(path, "r", encoding="utf-8", errors="replace") as handle:
                    text = handle.read()
                chunks.append(f"\n--- {os.path.basename(path)} ---\n{text[-1_000_000:]}")
            except Exception as exc:
                chunks.append(f"\n--- {os.path.basename(path)} unreadable: {exc} ---\n")
        return "".join(chunks) or "No runtime log files were found."

    def _diagnostic_process_summary(self) -> List[Dict[str, Any]]:
        interesting = {
            "mediabridge.exe", "appvolumebridge.exe", "thumbnailbridge.exe",
            "applemusic.exe", "steam.exe",
        }
        return [
            {
                "pid": int(item.get("pid") or 0),
                "name": str(item.get("name") or ""),
                "path": str(item.get("path") or ""),
                "sessionId": self._process_session_id(int(item.get("pid") or 0)),
            }
            for item in self._native_process_entries()
            if str(item.get("name") or "").strip().lower() in interesting
        ]

    def _export_diagnostic_log_sync(self) -> Dict[str, Any]:
        diagnostic_started = time.monotonic()
        operation_durations: Dict[str, float] = {}

        def timed(name: str, callback):
            started = time.monotonic()
            try:
                return callback()
            finally:
                operation_durations[name] = round((time.monotonic() - started) * 1000, 1)

        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        filename = f"Now-Playing-Diagnostics-{stamp}.txt"
        home = os.path.expanduser("~")
        candidates = [
            os.path.join(home, "Downloads"),
            os.path.join(os.environ.get("USERPROFILE", home), "Downloads"),
            os.path.join(home, "Desktop"),
            tempfile.gettempdir(),
        ]
        folder = next((value for value in candidates if value and os.path.isdir(value)), tempfile.gettempdir())
        output = os.path.join(folder, filename)

        try:
            with self._snapshot_lock:
                cached_snapshot = self._canonical_snapshot(dict(self._snapshot_cache))
        except Exception:
            cached_snapshot = self._empty_snapshot()
        live_snapshot = cached_snapshot
        live_snapshot_error = ""
        if self._normalized_active_service() not in {"localMusic", "youtubeMusic"}:
            try:
                live_snapshot = timed("mediaBridgeSnapshot", lambda: self._canonical_snapshot(self._request_json_once("/snapshot", timeout=1.5)))
            except Exception as exc:
                live_snapshot_error = str(exc)

        try:
            helper_health = timed("mediaBridgeHealth", lambda: self._helper_health(timeout=0.75) or {})
        except Exception as exc:
            helper_health = {"error": str(exc)}
        try:
            volume_names = self._current_volume_process_names()
            app_volume_status = timed(
                "directCoreAudio",
                lambda: self._run_app_volume_direct(None, volume_names)
                or {"ok": False, "reason": "direct-core-audio-unavailable"},
            )
        except Exception as exc:
            app_volume_status = {"ok": False, "error": str(exc)}
        app_states: Dict[str, bool] = {}
        for app_key in ("apple_music",):
            try:
                app_states[app_key] = bool(self._is_music_app_running_sync(app_key, max_age=0.0))
            except Exception:
                app_states[app_key] = False

        with self._diagnostic_lock:
            structured_events = list(self._diagnostic_events)
            counters = dict(self._diagnostic_counters)
        now_mono = time.monotonic()
        report = {
            "generatedAt": datetime.now().isoformat(timespec="seconds"),
            "pluginVersion": mix_identity.VERSION,
            "pluginVolume": mix_identity.VOLUME,
            "settingsMigration": getattr(self, "_settings_migration", {}),
            "python": sys.version,
            "platform": platform.platform(),
            "backend": {
                "pid": os.getpid(),
                "sessionId": self._process_session_id(os.getpid()),
                "user": os.environ.get("USERNAME") or os.environ.get("USER") or "",
                "thread": threading.current_thread().name,
            },
            "activeService": self._normalized_active_service(),
            "selectedPlayer": self.player,
            "helper": {
                "path": self.helper_path,
                "endpoint": self.base_url,
                "port": int(self.port or 0),
                "pid": int(self._helper_pid or 0),
                "sessionId": self._process_session_id(int(self._helper_pid or 0)),
                "ready": bool(self._helper_ready),
                "health": helper_health,
                "registeredState": self._load_helper_state(),
                "consecutiveFailures": int(self._helper_consecutive_failures),
                "lastFailure": self._helper_last_failure,
                "lastSuccessAgeSeconds": round(max(0.0, now_mono - self._helper_last_success_at), 3) if self._helper_last_success_at else None,
                "lastSnapshotSuccessAgeSeconds": round(max(0.0, now_mono - self._snapshot_last_success_at), 3) if self._snapshot_last_success_at else None,
                "recoveryScheduled": bool(self._helper_recovery_scheduled),
                "restartInProgress": bool(self._service_restart_in_progress),
            },
            "processes": self._diagnostic_process_summary(),
            "appRunning": app_states,
            "cachedSnapshot": self._diagnostic_snapshot_summary(cached_snapshot),
            "liveSnapshot": self._diagnostic_snapshot_summary(live_snapshot),
            "liveSnapshotError": live_snapshot_error,
            "directSmtc": {
                "runtimeAvailable": bool(self._prepare_direct_media_runtime()),
                "lastError": self._direct_media_last_error,
                "managerReady": self._direct_media_manager is not None,
                "snapshotAgeSeconds": round(max(0.0, now_mono - self._direct_media_snapshot_at), 3) if self._direct_media_snapshot_at else None,
                "cachedSnapshot": self._diagnostic_snapshot_summary(self._direct_media_snapshot),
            },
            "appVolumeBridge": {"shipped": False, "running": False},
            "directCoreAudio": app_volume_status,
            "fanart": {
                "keyConfigured": bool(self._artist_background_provider_settings.get("fanart_api_key")),
                "previewCacheFolder": self.artist_background_preview_dir,
                "candidateCount": len(self._artist_background_candidates),
            },
            "localMusic": {
                "streamBase": self._local_music_stream_server.base_url if self._local_music_stream_server else "",
                "frontendState": {
                    key: value for key, value in dict(self._local_music_frontend_state or {}).items()
                    if key in {"status", "title", "artist", "album", "currentIndex", "queueLength", "volume", "isPlaying"}
                },
            },
            "youtubeMusic": self._youtube_music.public_settings(),
            "topbar": {
                "enabled": bool(self.topbar_enabled),
                "left": bool(self.topbar_left),
                "cachedLabel": self._topbar_cached_label,
                "cachedService": self._topbar_cached_service,
                "lastSignature": self._topbar_last_signature,
            },
            "localhostServer": {
                "baseUrl": self._local_music_stream_server.base_url if self._local_music_stream_server else "",
                "running": bool(self._local_music_stream_server and self._local_music_stream_server.base_url),
            },
            "operationDurationsMs": operation_durations,
            "diagnosticCounters": counters,
            "structuredEventCount": len(structured_events),
        }
        operation_durations["totalBeforeWrite"] = round((time.monotonic() - diagnostic_started) * 1000, 1)
        runtime_logs = self._read_runtime_logs_for_diagnostics()
        content = (
            f"AWESOME MIX {mix_identity.VERSION} ({mix_identity.VOLUME}) DIAGNOSTICS\n"
            + json.dumps(report, ensure_ascii=False, indent=2)
            + "\n\nSTRUCTURED EVENTS (oldest to newest)\n"
            + "\n".join(json.dumps(item, ensure_ascii=False, sort_keys=True) for item in structured_events)
            + "\n\nRUNTIME LOGS\n"
            + self._redact_diagnostic_text(runtime_logs)
        )
        with open(output, "w", encoding="utf-8") as handle:
            handle.write(self._redact_diagnostic_text(content))
        self._log(f"diagnostic report exported to {output}")
        return {"ok": True, "path": output}

    async def export_diagnostic_log(self) -> Dict[str, Any]:
        try:
            return await asyncio.wait_for(
                self._run_in_executor(self._diagnostic_executor, self._export_diagnostic_log_sync),
                timeout=15.0,
            )
        except asyncio.TimeoutError:
            self._record_diagnostic_event("diagnostics", "export_timeout", {"timeoutSeconds": 15}, "error")
            return {"ok": False, "error": "Diagnostic export timed out after 15 seconds", "path": ""}
        except Exception as exc:
            self._log(f"diagnostic export error: {exc}")
            return {"ok": False, "error": str(exc), "path": ""}

    async def check_plugin_update(self, force: bool = False) -> Dict[str, Any]:
        return await self._run_in_executor(self._update_executor, self._update_channel.check, bool(force))

    async def prepare_plugin_update(self) -> Dict[str, Any]:
        return await self._run_in_executor(self._update_executor, self._update_channel.prepare)

    async def report_diagnostic_event(self, category: str, event: str, details: Optional[Dict[str, Any]] = None) -> bool:
        safe_details = details if isinstance(details, dict) else {}
        self._record_diagnostic_event(f"frontend:{str(category or 'ui')[:60]}", str(event or "")[:160], safe_details)
        # Playback happens in the Steam UI, so without this its skips, refusals
        # and recoveries never reached NowPlaying.log.
        if str(category or "") in {"playback", "youtubeMusic"}:
            summary = ", ".join(f"{key}={str(value)[:120]}" for key, value in list(safe_details.items())[:8])
            self._log(f"player: {str(event or '')[:80]}" + (f" ({summary})" if summary else ""))
        elif str(category or "") == "speedDial":
            # The Speed dial panel's one-time note: rendered or not, and why it is hidden.
            summary = ", ".join(f"{key}={str(value)[:120]}" for key, value in list(safe_details.items())[:8])
            self._log(f"[speed-dial] {str(event or '')[:80]}" + (f" ({summary})" if summary else ""))
        return True

    def _load_topbar_setting(self) -> bool:
        try:
            with open(self.topbar_settings_path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
                self.topbar_left = bool(data.get("topbar_left", data.get("left", False)))
                return bool(data.get("enabled", False))
        except Exception:
            return False

    def _save_topbar_setting(self, enabled: bool) -> None:
        try:
            with open(self.topbar_settings_path, "w", encoding="utf-8") as handle:
                json.dump({"enabled": bool(enabled), "topbar_left": bool(self.topbar_left)}, handle)
        except Exception as exc:
            self._log(f"topbar save error: {exc}")

    async def get_topbar_enabled(self) -> bool:
        return bool(self.topbar_enabled)

    async def get_topbar_left(self) -> bool:
        return bool(self.topbar_left)

    async def set_topbar_enabled(self, enabled: bool) -> bool:
        self.topbar_enabled = bool(enabled)
        self._topbar_settings_changed.set()
        self._save_topbar_setting(self.topbar_enabled)
        try:
            if self.topbar_enabled:
                await self._refresh_topbar_once(force=True)
            else:
                await self._run_in_executor(self._topbar_executor, self._inject_topbar_badge, "", False)
        except Exception as exc:
            self._log(f"set_topbar_enabled error: {exc}")
        return self.topbar_enabled

    async def set_topbar_left(self, enabled: bool) -> bool:
        self.topbar_left = bool(enabled)
        self._topbar_settings_changed.set()
        self._save_topbar_setting(self.topbar_enabled)
        try:
            await self._refresh_topbar_once(force=True)
        except Exception as exc:
            self._log(f"set_topbar_left error: {exc}")
        return self.topbar_left

    def _current_topbar_state(self) -> Dict[str, Any]:
        active_service = self._normalized_active_service()
        cached_for_source = bool(
            self._topbar_cached_label
            and self._topbar_cached_service == active_service
        )

        def cached_state() -> Dict[str, Any]:
            return {
                "playing": True,
                "title": self._topbar_cached_label,
                "service": self._topbar_cached_service,
                "cached": True,
            }

        def clear_cached_state() -> None:
            self._topbar_cached_label = ""
            self._topbar_cached_service = "music"
            self._topbar_cached_at = 0.0

        if active_service in {"localMusic", "youtubeMusic"}:
            current = self._current_track_from_data(self._local_music_snapshot_sync())
        else:
            current = self._current_track_snapshot()
        if not current:
            return {"playing": False, "title": "", "service": active_service, "cached": False}

        status = str(current.get("status") or "").strip().lower()
        title = str(current.get("title") or "").strip()
        service = active_service if active_service in SUPPORTED_SERVICES else self._service_key_from_snapshot(current)
        if status == "playing" and title:
            self._topbar_cached_label = title
            self._topbar_cached_service = service
            self._topbar_cached_at = time.time()
            return {"playing": True, "title": title, "service": service, "cached": False}

        # Windows SMTC can briefly expose an incomplete/unknown state during a
        # track handoff. Keep the previous label for that transient gap, but hide
        # it immediately for explicit pause/stop states.
        explicit_inactive = status in {"paused", "stopped", "closed"}
        if explicit_inactive:
            clear_cached_state()
        elif cached_for_source:
            return cached_state()
        return {"playing": False, "title": title, "service": service, "cached": False}

    def _format_topbar_label(self, title: str) -> str:
        title = (title or "").strip()
        if not title:
            return ""
        if len(title) > TOPBAR_MAX_CHARS:
            title = title[:TOPBAR_MAX_CHARS - 1].rstrip() + "…"
        return title

    def _topbar_injection_script(self, label: str, enabled: bool) -> str:
        cfg = {
            "badge": TOPBAR_BADGE_ID,
            "style": TOPBAR_STYLE_ID,
            "weather": TOPBAR_WEATHER_BADGE_ID,
            "label": label or "",
            "service": str(getattr(self, "_current_topbar_service_for_script", "music") or "music"),
            "left": bool(getattr(self, "_current_topbar_left_for_script", self.topbar_left)),
            "enabled": bool(enabled),
            "selectors": TOPBAR_CLOCK_SELECTORS,
        }
        c = json.dumps(cfg, ensure_ascii=False)
        return (
            "(function(){var C=" + c + ";"
            "function restoreClock(pc){if(!pc||!pc.hasAttribute('data-decky-nowplaying-clock'))return;"
            "var value=pc.getAttribute('data-decky-nowplaying-original-order');"
            "var priority=pc.getAttribute('data-decky-nowplaying-original-priority')||'';"
            "if(pc.style.getPropertyValue('order')==='-2'){if(value){pc.style.setProperty('order',value,priority);}else{pc.style.removeProperty('order');}}"
            "pc.removeAttribute('data-decky-nowplaying-clock');"
            "pc.removeAttribute('data-decky-nowplaying-original-order');"
            "pc.removeAttribute('data-decky-nowplaying-original-priority');}"
            "function removeBadge(){var l=document.querySelectorAll('#'+C.badge);"
            "for(var k=0;k<l.length;k++){l[k].remove();}"
            "document.documentElement.removeAttribute('data-decky-nowplaying-left');"
            "var marked=document.querySelectorAll('[data-decky-nowplaying-clock]');"
            "for(var m=0;m<marked.length;m++){restoreClock(marked[m]);}"
            "var oldStyle=document.getElementById(C.style);if(oldStyle){oldStyle.remove();}}"
            "if(!C.enabled||!C.label){removeBadge();return;}"
            "var s=document.getElementById(C.style);"
            "if(!s){s=document.createElement('style');s.id=C.style;document.head.appendChild(s);}"
            "s.textContent='#'+C.badge+'{display:inline-flex;align-items:center;gap:.34em;margin-left:.6em;opacity:.92;white-space:nowrap;font:inherit;line-height:1;color:#fff;pointer-events:none;vertical-align:middle;position:relative;align-self:center;transform:translateY(-1px);}#'+C.badge+' svg{width:.92em;height:.92em;display:block;fill:currentColor;flex:0 0 auto;}#'+C.badge+' span{display:inline-block;line-height:1;}';"
            "function iconSvg(k){var p={appleMusic:'M18.7 12.7c0-2.7 2.2-4 2.3-4.1-1.3-1.9-3.3-2.1-4-2.2-1.7-.2-3.3 1-4.1 1-.8 0-2.1-1-3.5-.9-1.8 0-3.5 1.1-4.4 2.7-1.9 3.3-.5 8.2 1.4 10.9.9 1.3 2 2.8 3.5 2.8 1.4-.1 1.9-.9 3.6-.9 1.7 0 2.2.9 3.7.9s2.5-1.3 3.4-2.7c1.1-1.5 1.5-3 1.5-3.1 0 0-2.9-1.1-2.9-4.4zM15.9 4.7c.8-1 1.3-2.3 1.1-3.7-1.1 0-2.5.7-3.3 1.7-.7.8-1.3 2.2-1.1 3.5 1.2.1 2.5-.6 3.3-1.5z',localMusic:'M3 9v6h4l5 4V5L7 9H3zm12.5 3a3.5 3.5 0 0 0-2-3.16v6.32A3.5 3.5 0 0 0 15.5 12zm-2-8.5v2.06A7 7 0 0 1 18 12a7 7 0 0 1-4.5 6.44v2.06A9 9 0 0 0 20 12a9 9 0 0 0-6.5-8.5z'};var path=p[k]||'M12 3v10.6A4 4 0 1 1 10 10V6l10-3v10.6A4 4 0 1 1 18 10V5.5L12 7.2z';return '<svg viewBox=\"0 0 24 24\" aria-hidden=\"true\"><path d=\"'+path+'\"></path></svg>';}"
            "var roots=Array.prototype.slice.call(document.querySelectorAll('#header,._1E_SL1bTibeQ3PQRBZoS_-,[class*=GamepadHeader],[class*=HeaderStatus],[class*=TopBar]'));"
            "var clock=null,i;"
            "function inRoot(node){if(!node)return false;if(roots.length<1)return false;for(var r=0;r<roots.length;r++){if(roots[r]===node||roots[r].contains(node))return true;}return false;}"
            "for(i=0;i<C.selectors.length;i++){var q=document.querySelector(C.selectors[i]);if(q&&inRoot(q)){clock=q;break;}}"
            "if(!clock){for(var rr=0;rr<roots.length&&!clock;rr++){var ns=Array.prototype.slice.call(roots[rr].querySelectorAll('div,span'));"
            "for(i=0;i<ns.length;i++){var t=(ns[i].textContent||'').trim();if(/^\\d{1,2}:\\d{2}(\\s|$)/.test(t)){clock=ns[i];break;}}}}"
            "if(!clock){removeBadge();return;}"
            "var previous=document.querySelectorAll('[data-decky-nowplaying-clock]');"
            "for(var p=0;p<previous.length;p++){if(previous[p]!==clock){restoreClock(previous[p]);}}"
            "try{if(C.left){if(!clock.hasAttribute('data-decky-nowplaying-clock')){"
            "clock.setAttribute('data-decky-nowplaying-original-order',clock.style.getPropertyValue('order'));"
            "clock.setAttribute('data-decky-nowplaying-original-priority',clock.style.getPropertyPriority('order'));}"
            "clock.setAttribute('data-decky-nowplaying-clock','1');clock.style.setProperty('order','-2');"
            "document.documentElement.setAttribute('data-decky-nowplaying-left','1');}"
            "else{restoreClock(clock);document.documentElement.removeAttribute('data-decky-nowplaying-left');}}catch(e){}"
            "var b=document.getElementById(C.badge);"
            "if(!b){b=document.createElement('span');b.id=C.badge;b.setAttribute('aria-hidden','true');}"
            "if(b.getAttribute('data-nowplaying-service')!==C.service||!b.lastChild){b.innerHTML=(C.service==='youtubeMusic'?'<svg viewBox=\"0 0 24 24\" aria-hidden=\"true\"><circle cx=\"12\" cy=\"12\" r=\"9\" fill=\"none\" stroke=\"currentColor\" stroke-width=\"2\"></circle><circle cx=\"12\" cy=\"12\" r=\"5\" opacity=\".32\"></circle><path d=\"M10 8.7 15.3 12 10 15.3z\"></path></svg>':iconSvg(C.service))+'<span></span>';b.setAttribute('data-nowplaying-service',C.service);}"
            "if(b.lastChild.textContent!==C.label){b.lastChild.textContent=C.label;}"
            "var duplicates=document.querySelectorAll('#'+C.badge);for(var d=0;d<duplicates.length;d++){if(duplicates[d]!==b){duplicates[d].remove();}}"
            "var w=document.getElementById(C.weather);"
            "if(w&&w.parentNode&&inRoot(w)){if(w.nextSibling!==b){w.parentNode.insertBefore(b,w.nextSibling);}}"
            "else if(b.parentNode!==clock){clock.appendChild(b);}"
            "})();"
        )

    def _steam_browser_targets(self):
        try:
            request = urllib.request.Request(
                f"http://127.0.0.1:{TOPBAR_CEF_PORT}/json/list",
                headers={"User-Agent": mix_identity.USER_AGENT},
            )
            with urllib.request.urlopen(request, timeout=2) as response:
                targets = json.loads(response.read().decode("utf-8"))
        except Exception:
            return []
        result = []
        for target in targets if isinstance(targets, list) else []:
            if not isinstance(target, dict):
                continue
            websocket_url = target.get("webSocketDebuggerUrl")
            target_type = target.get("type")
            if target_type and target_type != "page":
                continue
            if websocket_url and websocket_url not in result:
                result.append(websocket_url)
        return result

    def _websocket_text_frame(self, payload):
        frame = bytearray([0x81])
        length = len(payload)
        if length < 126:
            frame.append(0x80 | length)
        elif length < 65536:
            frame.append(0x80 | 126)
            frame.extend(struct.pack("!H", length))
        else:
            frame.append(0x80 | 127)
            frame.extend(struct.pack("!Q", length))
        mask = os.urandom(4)
        frame.extend(mask)
        frame.extend(byte ^ mask[index % 4] for index, byte in enumerate(payload))
        return bytes(frame)

    # One DevTools WebSocket per Steam page, reused for every badge update.
    # Opening a fresh connection to every page every two seconds left
    # thousands of sockets in TIME_WAIT and ran Windows out of ports
    # ("Only one usage of each socket address", WinError 10048/10055).
    TOPBAR_TARGETS_TTL = 15.0

    def _topbar_state(self):
        state = getattr(self, "_topbar_ws_state", None)
        if state is None:
            state = {"lock": threading.Lock(), "sockets": {}, "targets": [], "targets_at": 0.0}
            self._topbar_ws_state = state
        return state

    def _topbar_targets_cached(self, refresh: bool = False):
        state = self._topbar_state()
        now = time.monotonic()
        if refresh or not state["targets"] or now - state["targets_at"] > self.TOPBAR_TARGETS_TTL:
            state["targets"] = self._steam_browser_targets()
            state["targets_at"] = now
            live = set(state["targets"])
            for url in [url for url in state["sockets"] if url not in live]:
                self._topbar_close_socket(url)
        return list(state["targets"])

    def _topbar_close_socket(self, websocket_url) -> None:
        conn = self._topbar_state()["sockets"].pop(websocket_url, None)
        if conn is not None:
            try:
                conn.sock.close()
            except Exception:
                pass

    def _topbar_close_all_sockets(self) -> None:
        state = self._topbar_state()
        with state["lock"]:
            for url in list(state["sockets"]):
                self._topbar_close_socket(url)

    def _topbar_open_socket(self, websocket_url):
        parsed = urllib.parse.urlparse(websocket_url)
        if parsed.scheme != "ws":
            raise ValueError("unsupported DevTools URL")
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or TOPBAR_CEF_PORT
        path = (parsed.path or "/") + (("?" + parsed.query) if parsed.query else "")
        sock = socket.create_connection((host, port), timeout=3)
        try:
            sock.settimeout(3)
            key = base64.b64encode(os.urandom(16)).decode("ascii")
            sock.sendall((
                f"GET {path} HTTP/1.1\r\nHost: {host}:{port}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n"
            ).encode("ascii"))
            response = b""
            while b"\r\n\r\n" not in response and len(response) < 8192:
                chunk = sock.recv(1024)
                if not chunk:
                    break
                response += chunk
            if b" 101 " not in response.split(b"\r\n", 1)[0]:
                raise ConnectionError("DevTools refused the WebSocket upgrade")
            # Bytes after the handshake belong to the first frame; keep them.
            return _TopbarConnection(sock, response.split(b"\r\n\r\n", 1)[1])
        except Exception:
            sock.close()
            raise

    @staticmethod
    def _topbar_recv_exact(conn, count: int) -> bytes:
        buffered = conn.buffer
        while len(buffered) < count:
            chunk = conn.sock.recv(max(4096, count - len(buffered)))
            if not chunk:
                raise ConnectionError("DevTools closed the connection")
            buffered += chunk
        conn.buffer = buffered[count:]
        return buffered[:count]

    def _topbar_read_frame(self, sock):
        head = self._topbar_recv_exact(sock, 2)
        opcode = head[0] & 0x0F
        length = head[1] & 0x7F
        if length == 126:
            length = struct.unpack("!H", self._topbar_recv_exact(sock, 2))[0]
        elif length == 127:
            length = struct.unpack("!Q", self._topbar_recv_exact(sock, 8))[0]
        mask = self._topbar_recv_exact(sock, 4) if head[1] & 0x80 else b""
        payload = self._topbar_recv_exact(sock, length) if length else b""
        if mask:
            payload = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
        return opcode, payload

    def _topbar_evaluate_on(self, sock, script) -> None:
        command_id = random.randint(1, 2_000_000_000)
        command = {
            "id": command_id,
            "method": "Runtime.evaluate",
            "params": {"expression": script, "awaitPromise": False, "returnByValue": True},
        }
        sock.sock.sendall(self._websocket_text_frame(json.dumps(command, ensure_ascii=False).encode("utf-8")))
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            opcode, payload = self._topbar_read_frame(sock)
            if opcode == 0x8:
                raise ConnectionError("DevTools closed the connection")
            if opcode == 0x9:
                pong = payload[:125]
                sock.sock.sendall(bytes([0x8A, 0x80 | len(pong)]) + b"\x00\x00\x00\x00" + pong)  # zero mask
                continue
            if opcode == 0x1:
                try:
                    if json.loads(payload.decode("utf-8", "replace")).get("id") == command_id:
                        return
                except Exception:
                    continue
        raise TimeoutError("DevTools did not answer")

    def _evaluate_steam_target(self, websocket_url, script):
        state = self._topbar_state()
        with state["lock"]:
            for attempt in range(2):
                sock = state["sockets"].get(websocket_url)
                fresh = sock is None
                try:
                    if fresh:
                        sock = self._topbar_open_socket(websocket_url)
                        state["sockets"][websocket_url] = sock
                    self._topbar_evaluate_on(sock, script)
                    return True
                except Exception as error:
                    self._topbar_close_socket(websocket_url)
                    # A reused connection may simply have gone stale (page
                    # reloaded); retry once on a fresh one before reporting.
                    if fresh or attempt:
                        self._log(f"topbar target injection failed: {error}")
                        return False
            return False

    def _inject_topbar_badge(self, label: str, enabled: bool) -> None:
        script = self._topbar_injection_script(label, enabled)
        targets = self._topbar_targets_cached()
        failed = [url for url in targets if not self._evaluate_steam_target(url, script)]
        if failed:
            # Pages come and go; refresh the list next time instead of
            # retrying a closed one for TOPBAR_TARGETS_TTL seconds.
            self._topbar_state()["targets_at"] = 0.0

    async def _refresh_topbar_once(self, force: bool = False) -> None:
        if self.topbar_enabled:
            state = await self._run_in_executor(self._realtime_executor, self._current_topbar_state)
            label = self._format_topbar_label(str(state.get("title") or ""))
            enabled = bool(state.get("playing") and label)
        else:
            state = {"service": "music"}
            label, enabled = "", False
        service = str(state.get("service") or "music")
        signature = json.dumps([label, enabled, service, bool(self.topbar_left)], ensure_ascii=False)
        now = time.monotonic()
        if not force and signature == self._topbar_last_signature:
            # A hidden badge does not need fresh WebSocket connections to every
            # Steam page every two seconds. Keep a bounded cleanup for quiet
            # playback while preserving fast state detection and active repair.
            reinject_after = TOPBAR_FORCE_REINJECT_SECONDS if enabled else TOPBAR_IDLE_REINJECT_SECONDS
            if not self.topbar_enabled or now - self._topbar_last_injected_at < reinject_after:
                return
        await self._run_in_executor(self._topbar_executor, self._inject_topbar_badge_with_service, label, enabled, service, bool(self.topbar_left))
        self._topbar_last_signature = signature
        self._topbar_last_injected_at = now

    def _inject_topbar_badge_with_service(self, label: str, enabled: bool, service: str, left: bool) -> None:
        original = self._current_topbar_service_for_script if hasattr(self, "_current_topbar_service_for_script") else None
        original_left = self._current_topbar_left_for_script if hasattr(self, "_current_topbar_left_for_script") else None
        self._current_topbar_service_for_script = service
        self._current_topbar_left_for_script = bool(left)
        try:
            self._inject_topbar_badge(label, enabled)
        finally:
            if original is None:
                try:
                    delattr(self, "_current_topbar_service_for_script")
                except Exception:
                    pass
            else:
                self._current_topbar_service_for_script = original
            if original_left is None:
                try:
                    delattr(self, "_current_topbar_left_for_script")
                except Exception:
                    pass
            else:
                self._current_topbar_left_for_script = original_left

    async def _topbar_loop(self) -> None:
        while True:
            self._topbar_settings_changed.clear()
            try:
                await self._refresh_topbar_once()
            except Exception as exc:
                self._log(f"topbar loop error: {exc}")
            if self.topbar_enabled:
                await asyncio.sleep(TOPBAR_REINJECT_SECONDS)
            else:
                await self._topbar_settings_changed.wait()


    async def _main(self) -> None:
        self.plugin_dir = os.path.dirname(os.path.abspath(__file__))
        self.runtime_dir = self._resolve_runtime_dir()
        self.bundled_helper_dir = os.path.join(self.plugin_dir, "bin")
        self.bundled_helper_path = os.path.join(self.bundled_helper_dir, "MediaBridge.exe")
        self.bundled_thumbnail_bridge_path = os.path.join(self.bundled_helper_dir, "ThumbnailBridge.exe")
        self.bundled_app_volume_bridge_path = os.path.join(self.bundled_helper_dir, "AppVolumeBridge.exe")
        self.helper_dir = self.bundled_helper_dir
        self.helper_path = self.bundled_helper_path
        self.thumbnail_bridge_path = self.bundled_thumbnail_bridge_path
        self.app_volume_bridge_path = self.bundled_app_volume_bridge_path
        self._log(f"{mix_identity.DISPLAY_NAME} {mix_identity.VERSION} ({mix_identity.VOLUME}) runtime fallbacks: " + (", ".join(active_stdlib_fallbacks()) or "native stdlib"))
        self._log(f"plugin_dir={self.plugin_dir}")
        self._log(f"bundled_helper_path={self.bundled_helper_path}")
        try:
            self._ensure_helper()
            self._log(f"helper pronto: {self.helper_path}")
        except Exception as exc:
            self._log(f"startup error: {exc}")
        try:
            if self._local_music_stream_server is None:
                self._local_music_stream_server = LocalMusicStreamServer(
                    self._local_music_stream_path,
                    self._local_music_stream_asset_path,
                    self._log,
                    self._youtube_music.resolve_stream,
                )
                self._local_music_stream_server.start()
        except Exception as exc:
            self._log(f"local audio stream start error: {exc}")
        try:
            self.topbar_enabled = self._load_topbar_setting()
            self._topbar_task = asyncio.create_task(self._topbar_loop())
        except Exception as exc:
            self._log(f"topbar start error: {exc}")
        try:
            self._youtube_music_warmup_task = asyncio.create_task(self._warm_youtube_music_background())
        except Exception as exc:
            self._log(f"YouTube Music warm-up scheduling error: {exc}")

    async def _warm_youtube_music_background(self) -> None:
        # Let Steam, Decky and the network settle first, then load YouTube
        # Music's libraries on the prefetch worker so a press on Play never
        # waits behind it.
        try:
            await asyncio.sleep(6.0)
            if not (self._youtube_music._has_auth() or self._normalized_active_service() == "youtubeMusic"):
                return
            seconds = await self._run_in_executor(self._youtube_music_prefetch_executor, self._youtube_music.warm_up)
            self._log(f"YouTube Music warm-up done in {seconds:.1f}s")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._log(f"YouTube Music warm-up skipped: {type(exc).__name__}: {exc}")

    async def _unload(self) -> None:
        pending = []
        for name in ("_topbar_task", "_source_retry_task", "_youtube_music_warmup_task"):
            task = getattr(self, name, None)
            if task is not None and not task.done():
                task.cancel()
                pending.append(task)
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        # Decky v3.3.1 calls _unload only where it can deliver SIGTERM (Linux);
        # on Windows it uses TerminateProcess. Stop the helpers either way.
        try:
            await asyncio.wait_for(
                asyncio.get_running_loop().run_in_executor(None, self._stop_helpers_for_exit, "unload"), timeout=4.0
            )
        except Exception as exc:
            self._log(f"unload helper stop error: {type(exc).__name__}: {exc}")
        try:
            self._topbar_close_all_sockets()
        except Exception:
            pass
        try:
            self._youtube_music.cancel_browser_auth()
        except Exception:
            pass
        try:
            if self._topbar_task:
                self._topbar_task.cancel()
        except Exception:
            pass
        try:
            if self._source_retry_task:
                self._source_retry_task.cancel()
        except Exception:
            pass
        try:
            self._inject_topbar_badge("", False)
        except Exception:
            pass
        try:
            if self._local_music_stream_server is not None:
                self._local_music_stream_server.stop()
                self._local_music_stream_server = None
        except Exception:
            pass
        try:
            if self._local_music_player is not None:
                self._local_music_player.close()
        except Exception:
            pass
        for executor in (
            self._realtime_executor,
            self._topbar_executor,
            self._cover_executor,
            self._artist_background_search_executor,
            self._volume_executor,
            self._youtube_music_executor,
            self._source_lifecycle_executor,
            self._local_music_executor,
            self._update_executor,
        ):
            try:
                executor.shutdown(wait=False, cancel_futures=True)
            except TypeError:
                executor.shutdown(wait=False)
            except Exception:
                pass
