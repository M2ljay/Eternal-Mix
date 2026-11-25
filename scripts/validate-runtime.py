#!/usr/bin/env python3
"""Portable release smoke tests for Awesome Mix's backend invariants.

The script deliberately avoids Windows-only APIs.  It validates the localhost
streaming contract used by Steam Chromium and the source/session matching logic
that chooses the active media player.
"""

from __future__ import annotations

import importlib.util
import contextlib
import json
import os
import sys
import tempfile
import threading
import time
import types
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Decky injects this module at runtime. main.py only needs it to exist while the
# portable tests import the backend.
sys.modules.setdefault("decky_plugin", types.ModuleType("decky_plugin"))

spec = importlib.util.spec_from_file_location("now_playing_backend", ROOT / "main.py")
if spec is None or spec.loader is None:
    raise RuntimeError("Unable to load main.py")
backend = importlib.util.module_from_spec(spec)
spec.loader.exec_module(backend)

sys.path.insert(0, str(ROOT / "vendor"))
from ytmusic_service import YouTubeMusicService
from ytmusicapi import setup as setup_youtube_music


def request(url: str, range_header: str | None = None, method: str = "GET"):
    headers = {"Range": range_header} if range_header else {}
    return urllib.request.urlopen(urllib.request.Request(url, headers=headers, method=method), timeout=5)


def test_local_stream_ranges() -> None:
    payload = bytes(range(256)) * 8
    with tempfile.TemporaryDirectory(prefix="now-playing-range-test-") as directory:
        media_path = Path(directory) / "range-test.m4a"
        media_path.write_bytes(payload)
        server = backend.LocalMusicStreamServer(
            lambda resource_id: str(media_path) if resource_id == "track-1" else "",
            lambda _kind, _resource_id: "",
            lambda _message: None,
        )
        server.start()
        try:
            url = f"{server.base_url}/track/track-1"
            with request(url, "bytes=-17") as response:
                body = response.read()
                assert response.status == 206
                assert response.headers["Content-Range"] == f"bytes {len(payload) - 17}-{len(payload) - 1}/{len(payload)}"
                assert body == payload[-17:]
                assert response.headers["Cache-Control"] == "no-store, no-cache, must-revalidate"

            with request(url, "bytes=10-29") as response:
                assert response.status == 206
                assert response.read() == payload[10:30]

            with request(url, "bytes=40-") as response:
                assert response.status == 206
                assert response.read() == payload[40:]

            with request(url, method="HEAD") as response:
                assert response.status == 200
                assert int(response.headers["Content-Length"]) == len(payload)
                assert response.read() == b""

            for invalid_range in ("bytes=", "bytes=12-4", "bytes=99999-", "bytes=0-1,4-5"):
                try:
                    request(url, invalid_range)
                except urllib.error.HTTPError as error:
                    assert error.code == 416
                    assert error.headers["Content-Range"] == f"bytes */{len(payload)}"
                else:
                    raise AssertionError(f"Expected HTTP 416 for {invalid_range}")
        finally:
            server.stop()


def test_active_service_matching() -> None:
    plugin = backend.Plugin.__new__(backend.Plugin)
    examples = {
        "appleMusic": {"app_id": "AppleInc.AppleMusicWin_nzyj5cx40ttey!App", "process": "AppleMusic.exe", "status": "Paused", "title": "Track"},
    }
    for service, matching_player in examples.items():
        decoy = {"id": "UnrelatedPlayer", "status": "Playing", "title": "Other track"}
        assert plugin._media_player_match_score(matching_player, service) > plugin._media_player_match_score(decoy, service)

        plugin.active_service = service
        plugin.player = ""
        normalized = plugin._normalize_snapshot_for_active_service(
            {"players": [decoy, matching_player], "selected": decoy, "selectedPlayer": "UnrelatedPlayer"}
        )
        selected = normalized.get("selected") or {}
        assert selected.get("title") == "Track", f"Failed to select {service} session"


def test_only_supported_sources_remain() -> None:
    assert backend.SUPPORTED_SERVICES == ("localMusic", "youtubeMusic", "appleMusic")
    plugin = backend.Plugin.__new__(backend.Plugin)
    for removed in ("spotify", "spotifyPlayer", "tidal", "deezer", "amazonMusic", "soundCloud"):
        assert plugin._normalized_service_key(removed) not in backend.SUPPORTED_SERVICES, removed
        assert plugin._music_app_key_for_service(removed) == "", removed
    assert plugin._normalized_service_key("Apple Music") == "appleMusic"
    assert set(plugin._music_app_launchers()) == {"apple_music"}
    with tempfile.TemporaryDirectory(prefix="awesome-mix-sources-") as directory:
        plugin.source_behavior_settings_path = os.path.join(directory, "source-behavior.json")
        Path(plugin.source_behavior_settings_path).write_text(json.dumps({"active_service": "spotify"}), encoding="utf-8")
        assert plugin._load_source_behavior_settings()["active_service"] == "localMusic"
    backend_source = (ROOT / "main.py").read_text(encoding="utf-8").lower()
    for word in ("spotify", "tidal", "deezer", "soundcloud", "amazon"):
        assert word not in backend_source, word



def test_local_frontend_state_is_authoritative() -> None:
    plugin = backend.Plugin.__new__(backend.Plugin)
    plugin._local_music_frontend_state = {
        "track": {"id": "local-1", "name": "Local track", "artists": [], "album": {}},
        "status": "Playing",
        "position": 1200,
        "length": 5000,
        "volume": 73,
        "canNext": False,
        "canPrevious": False,
        "shuffleActive": False,
        "repeatMode": "None",
    }
    plugin._local_music_player = None
    snapshot = plugin._local_music_snapshot_sync()
    assert snapshot["selected"]["title"] == "Local track"
    assert snapshot["selected"]["position"] == 1200
    assert plugin._local_music_player is None, "Reading local state must not start the legacy COM player"



def test_empty_snapshots_are_canonical() -> None:
    plugin = backend.Plugin.__new__(backend.Plugin)
    for value in (None, {}, {"players": None}, {"players": [], "selected": None}):
        snapshot = plugin._canonical_snapshot(value)
        assert snapshot["players"] == []
        assert snapshot["selected"] is None
        assert isinstance(snapshot["selectedPlayer"], str)
        assert isinstance(snapshot["currentPlayer"], str)


def test_fanart_relative_asset_urls() -> None:
    plugin = backend.Plugin.__new__(backend.Plugin)
    mbid = "12345678-1234-1234-1234-123456789abc"
    expected = f"https://assets.fanart.tv/fanart/music/{mbid}/artistbackground/sample.jpg"
    assert plugin._fanart_asset_url("sample.jpg", mbid, "artistbackground") == expected
    assert plugin._fanart_asset_url("//assets.fanart.tv/example.jpg", mbid, "artistbackground") == "https://assets.fanart.tv/example.jpg"
    assert plugin._fanart_asset_url("https://assets.fanart.tv/example.jpg", mbid, "artistbackground") == "https://assets.fanart.tv/example.jpg"


def test_fanart_filename_payload_candidates() -> None:
    plugin = backend.Plugin.__new__(backend.Plugin)
    plugin._artist_background_provider_settings = {"fanart_api_key": "test-key"}
    mbid = "12345678-1234-1234-1234-123456789abc"
    calls: list[str] = []
    plugin._musicbrainz_artist_mbids = lambda _name, _limit=6: [mbid]

    def payload(requested_mbid: str):
        calls.append(requested_mbid)
        return {"artistbackground": [{"url": "hero.jpg", "width": 1920, "height": 1080}]}

    plugin._fanart_artist_payload = payload
    plugin._probe_remote_image = lambda _url, _referer="": (0, 0, "")
    candidates = plugin._fanart_artist_background_candidates("Test Artist", 24)
    assert len(candidates) == 1
    assert calls == [mbid], "One artist MBID must produce one fanart.tv API request"
    assert candidates[0]["source"] == "fanart.tv"
    assert candidates[0]["url"] == f"https://assets.fanart.tv/fanart/music/{mbid}/artistbackground/hero.jpg"



def test_generic_running_player_fallback() -> None:
    plugin = backend.Plugin.__new__(backend.Plugin)
    plugin.active_service = "appleMusic"
    plugin.player = ""
    plugin._is_music_app_running_sync = lambda _key, max_age=0.0: True
    plugin._log = lambda _message: None
    plugin._diagnostic_lock = __import__("threading").Lock()
    plugin._diagnostic_snapshot_signature = ""
    plugin._diagnostic_snapshot_at = 0.0
    snapshot = plugin._normalize_snapshot_for_active_service({
        "players": [{"id": "generic-session", "name": "Media Session", "status": "Playing", "title": "Track"}]
    })
    assert (snapshot.get("selected") or {}).get("id") == "generic-session"


def test_fanart_preview_url_and_no_external_curl() -> None:
    plugin = backend.Plugin.__new__(backend.Plugin)
    full = "https://assets.fanart.tv/fanart/music/artist/artistbackground/hero.jpg"
    preview = "https://assets.fanart.tv/preview/music/artist/artistbackground/hero.jpg"
    assert plugin._fanart_preview_url(full) == preview
    variants = plugin._remote_image_url_variants(preview)
    assert preview in variants
    assert any("static.fanart.tv" in value for value in variants)
    assert any("/fanart/" in value for value in variants)
    backend_source = (ROOT / "main.py").read_text(encoding="utf-8")
    assert 'shutil.which("curl' not in backend_source
    assert 'curl.exe"' not in backend_source
    assert "_download_remote_image_bytes_winhttp" in backend_source
    assert "_download_remote_image_bytes_ipv4" in backend_source


def test_helper_recovery_architecture() -> None:
    backend_source = (ROOT / "main.py").read_text(encoding="utf-8")
    assert "self.port = 0" in backend_source
    assert "NowPlaying-MediaBridge-state.json" in backend_source
    assert "_snapshot_refresh_lock" in backend_source
    assert "_recovery_executor" in backend_source
    assert "_stop_helper_unlocked(force=True" in backend_source
    assert "--port" in backend_source
    assert "CreateProcessWithTokenW" in backend_source
    assert "interactive-token" in backend_source
    assert '"shell-interactive"' in backend_source
    assert "ShellExecuteExW" in backend_source
    assert '"direct-interactive"' in backend_source
    assert "DETACHED_PROCESS" in backend_source
    assert "SetWindowPos(target, ctypes.c_void_p(-1)" in backend_source
    assert "_direct_media_snapshot_async" in backend_source
    assert "winrt.windows.media.control" in backend_source


def test_media_helper_is_owned_by_plugin() -> None:
    plugin = backend.Plugin.__new__(backend.Plugin)
    with tempfile.TemporaryDirectory(prefix="now-playing-helper-roots-") as directory:
        plugin.helper_cache_root = os.path.join(directory, "NowPlaying-MediaBridge")
        plugin.bundled_helper_dir = os.path.join(directory, "plugin", "bin")
        plugin._is_windows = lambda: True
        plugin._native_process_entries = lambda: [
            {"pid": 11, "name": "MediaBridge.exe", "path": os.path.join(plugin.helper_cache_root, "hash", "MediaBridge.exe")},
            {"pid": 12, "name": "MediaBridge.exe", "path": os.path.join(plugin.bundled_helper_dir, "MediaBridge.exe")},
            {"pid": 13, "name": "MediaBridge.exe", "path": os.path.join(directory, "unrelated", "MediaBridge.exe")},
        ]
        assert plugin._plugin_helper_process_ids(["MediaBridge.exe"]) == {11, 12}


def test_youtube_music_search_runs_categories_in_parallel() -> None:
    with tempfile.TemporaryDirectory(prefix="now-playing-ytm-search-") as directory:
        service = YouTubeMusicService(directory, str(ROOT / "vendor"), lambda _message: None)
        active = 0
        maximum_active = 0
        counter_lock = threading.Lock()

        class Client:
            def search(self, *_args, **_kwargs):
                nonlocal active, maximum_active
                with counter_lock:
                    active += 1
                    maximum_active = max(maximum_active, active)
                time.sleep(0.08)
                with counter_lock:
                    active -= 1
                return []

        service._independent_client_for_request = lambda *_args: Client()
        service._request_context = lambda _client: contextlib.nullcontext()
        result = service.search("parallel test")
        assert set(result) == {"tracks", "albums", "artists", "playlists", "podcasts", "episodes"}
        assert maximum_active == 6


def test_youtube_music_response_cache_survives_restart() -> None:
    with tempfile.TemporaryDirectory(prefix="now-playing-ytm-cache-") as directory:
        first = YouTubeMusicService(directory, str(ROOT / "vendor"), lambda _message: None)
        expected = {"tracks": {"items": [{"id": "cached"}]}}
        assert first._cached("search:cached", 900.0, lambda: expected) == expected
        second = YouTubeMusicService(directory, str(ROOT / "vendor"), lambda _message: None)
        assert second._cached(
            "search:cached",
            900.0,
            lambda: (_ for _ in ()).throw(AssertionError("disk cache was not used")),
        ) == expected


def test_cache_invalidation_wins_over_inflight_results() -> None:
    with tempfile.TemporaryDirectory(prefix="now-playing-cache-generation-") as directory:
        service = YouTubeMusicService(directory, str(ROOT / "vendor"), lambda _message: None)

        def cleared_during_request():
            service.clear_cache()
            return {"old": True}

        assert service._cached("home", 900.0, cleared_during_request) == {"old": True}
        assert "home" not in service._cache
        assert not os.path.exists(service._response_cache_path("home"))
        stale_generation = service._cache_generation - 1
        service._write_library_cache("tracks", {"items": []}, True, stale_generation)
        assert not os.path.exists(service.library_cache_path)


def test_youtube_music_chromium_header_normalization() -> None:
    copied_headers = (
        ":authority\r\nmusic.youtube.com\r\n"
        ":method\r\nPOST\r\n"
        ":path\r\n/youtubei/v1/browse?prettyPrint=false\r\n"
        ":scheme\r\nhttps\r\n"
        "authorization\r\nSAPISIDHASH fake\r\n"
        "cookie\r\nSID=fake\r\n"
        "x-goog-authuser\r\n0\r\n"
    )
    normalized = YouTubeMusicService._normalize_browser_headers(copied_headers)
    auth = json.loads(setup_youtube_music(headers_raw=normalized))
    keys = {key.lower() for key in auth}
    assert {"authorization", "cookie", "x-goog-authuser"}.issubset(keys)
    assert not any(key.startswith("/") or key in {"music.youtube.com", "https"} for key in keys)

def main() -> None:
    test_local_stream_ranges()
    test_active_service_matching()
    test_only_supported_sources_remain()
    test_local_frontend_state_is_authoritative()
    test_empty_snapshots_are_canonical()
    test_fanart_relative_asset_urls()
    test_fanart_filename_payload_candidates()
    test_generic_running_player_fallback()
    test_fanart_preview_url_and_no_external_curl()
    test_helper_recovery_architecture()
    test_media_helper_is_owned_by_plugin()
    test_youtube_music_search_runs_categories_in_parallel()
    test_youtube_music_response_cache_survives_restart()
    test_cache_invalidation_wins_over_inflight_results()
    test_youtube_music_chromium_header_normalization()
    print("Runtime smoke tests OK: ranges, Apple Music SMTC matching, removed sources rejected, SMTC fallbacks, canonical snapshots, local state, parallel YouTube Music search, restart-persistent generation-safe caches, helper ownership, Chromium headers, and in-process fanart.tv transports.")


if __name__ == "__main__":
    main()
