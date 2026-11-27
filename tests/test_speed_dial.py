"""YouTube Music Speed dial: shelf extraction, play routing, cache and the empty paths (mix_speed_dial.py)."""
import copy
from pathlib import Path
import sys
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT, ROOT / "tests"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import mix_speed_dial as sd  # noqa: E402
import speed_dial_fixtures as fx  # noqa: E402
import ytmusic_service  # noqa: E402


def by_title(items):
    return {item["title"]: item for item in items}


class ExtractionTests(unittest.TestCase):
    def test_finds_speed_dial_and_reads_every_tile_kind(self):
        shelf = sd.extract_speed_dial(fx.full_home())
        self.assertTrue(shelf["found"])
        self.assertEqual(shelf["title"], "Speed dial")
        items = by_title(shelf["items"])
        self.assertEqual(len(shelf["items"]), len(fx.speed_dial_tiles()))
        expected = {
            "Gravity": ("song", "EludZd6lfts"),
            "Sentiment": ("album", "MPREb_QtqXtd2xZMR"),
            "Late Night Drive": ("playlist", "PLz7-xrYmULdSLRZGk-6GKUtaBZcgQNwel"),
            "Chill Satellite": ("artist", "UCrPLFBWdOroD57bkqPbZJog"),
            "My Supermix": ("mix", "RDTMAK5uy_supermix0000000000000000000000"),
            "Two Steps From Hell Radio": ("mix", "RDEMtwosteps0000000000"),
            "Chill Hits": ("playlist", "RDCLAK5uy_chillhits000000000000000000"),
            "The Daily Show": ("podcast", "MPSPPLtestpodcast00000000000000000000"),
            "Episode 42: Keys": ("episode", "ep42videoid"),
            "Saved Artist": ("artist", "UCsavedartist00000000000"),
            "Daily Mix 1": ("mix", "RDAMPLsomemix000000000000"),
        }
        self.assertEqual({title: (item["type"], item["id"]) for title, item in items.items()}, expected)

    def test_tile_details(self):
        items = by_title(sd.extract_speed_dial(fx.full_home())["items"])
        self.assertEqual(items["Gravity"]["subtitle"], "Song • yetep")
        self.assertTrue(items["Gravity"]["pinned"])
        self.assertTrue(items["Chill Satellite"]["pinned"])
        self.assertFalse(items["Sentiment"]["pinned"])
        self.assertEqual(len(items["Sentiment"]["thumbnails"]), 2)
        # The artist tile keeps its own play button: YouTube Music's artist radio.
        self.assertEqual(items["Chill Satellite"]["playlistId"], "RDEMrPLFBWdOroD57bkqPbZJog")
        self.assertEqual(items["Chill Satellite"]["params"], "wAEB")
        # A supermix is a watch endpoint seeded by a song.
        self.assertEqual(items["My Supermix"]["videoId"], "bR5l0hJDnX8")
        self.assertEqual(items["Saved Artist"]["playlistId"], "")

    def test_the_title_is_not_needed(self):
        response = fx.home([fx.quick_picks(), fx.speed_dial_shelf(title="Titre localisé (placeholder)")])
        shelf = sd.extract_speed_dial(response)
        self.assertTrue(shelf["found"])
        self.assertEqual(shelf["title"], "Titre localisé (placeholder)")

    def test_other_shelves_are_never_taken(self):
        response = fx.home([fx.quick_picks(), fx.plain_shelf("More from Chill Satellite", avatar=True, strapline="SIMILAR TO"),
                            fx.plain_shelf("Recommended albums")])
        self.assertEqual(sd.extract_speed_dial(response), {"found": False, "title": "", "score": 0, "items": []})

    def test_an_english_title_alone_is_not_enough(self):
        bare = fx.speed_dial_shelf(per_column="1", avatar=False, strapline=False)
        for entry in bare["musicCarouselShelfRenderer"]["contents"]:
            entry["musicTwoRowItemRenderer"]["menu"] = fx.pin_menu(None)
        self.assertLess(sd.shelf_score(bare), sd.MIN_SCORE)
        self.assertFalse(sd.extract_speed_dial(fx.home([bare]))["found"])

    def test_listen_again_and_grid_layouts(self):
        listen_again = fx.speed_dial_shelf(title="Listen again", per_column="2")
        self.assertTrue(sd.extract_speed_dial(fx.home([listen_again]))["found"])
        grid = {"gridRenderer": {"header": {"gridHeaderRenderer": {"title": {"runs": [{"text": "Titre localisé"}]}}},
                                 "items": fx.speed_dial_tiles()}}
        shelf = sd.extract_speed_dial(fx.home([fx.quick_picks(), grid]))
        self.assertTrue(shelf["found"])
        self.assertEqual(len(shelf["items"]), len(fx.speed_dial_tiles()))

    def test_the_best_scoring_shelf_wins(self):
        weaker = fx.speed_dial_shelf(title="Something else", avatar=False, strapline=False)
        stronger = fx.speed_dial_shelf()
        stronger["musicCarouselShelfRenderer"]["contents"] = stronger["musicCarouselShelfRenderer"]["contents"][:3]
        shelf = sd.extract_speed_dial(fx.home([weaker, stronger]))
        self.assertEqual(shelf["title"], "Speed dial")
        self.assertEqual(len(shelf["items"]), 3)

    def test_other_wrappers_are_walked(self):
        # The mobile client (used with OAuth) does not use singleColumnBrowseResultsRenderer.
        response = {"contents": {"elementRenderer": {"newElement": {"type": {"componentType": {"model": {
            "sections": [fx.quick_picks(), fx.speed_dial_shelf()]}}}}}}}
        self.assertTrue(sd.extract_speed_dial(response)["found"])

    def test_cap_duplicates_and_broken_tiles(self):
        tiles = fx.speed_dial_tiles() * 4
        tiles.insert(0, {"musicTwoRowItemRenderer": {"title": {"runs": [{"text": "No endpoint"}]}, "menu": fx.pin_menu(False)}})
        many = [fx.tile(f"Album {index}", ["Album", "X"], fx.browse(f"MPREb_cap{index:05d}", fx.ALBUM), seed=str(index)) for index in range(40)]
        shelf = sd.extract_speed_dial(fx.home([fx.speed_dial_shelf(tiles + many)]))
        ids = [(item["type"], item["id"]) for item in shelf["items"]]
        self.assertEqual(len(ids), sd.MAX_ITEMS)
        self.assertEqual(len(set(ids)), len(ids))
        self.assertNotIn("No endpoint", [item["title"] for item in shelf["items"]])

    def test_garbage_is_harmless(self):
        for value in (None, {}, [], "x", {"contents": None}, fx.home([])):
            self.assertFalse(sd.extract_speed_dial(value)["found"])
        self.assertIsNone(sd.parse_tile(None))
        self.assertEqual(sd.queue_request(None), {"mode": "none"})


class RoutingTests(unittest.TestCase):
    def setUp(self):
        self.items = by_title(sd.extract_speed_dial(fx.full_home())["items"])

    def test_song_starts_its_radio(self):
        self.assertEqual(sd.queue_request(self.items["Gravity"]),
                         {"mode": "watch", "videoId": "EludZd6lfts", "playlistId": "RDAMVMEludZd6lfts", "radio": False, "shuffle": False})

    def test_mix_and_station_play_their_radio_queue(self):
        self.assertEqual(sd.queue_request(self.items["My Supermix"]),
                         {"mode": "watch", "videoId": "bR5l0hJDnX8", "playlistId": "RDTMAK5uy_supermix0000000000000000000000", "radio": False, "shuffle": False})
        self.assertEqual(sd.queue_request(self.items["Two Steps From Hell Radio"])["playlistId"], "RDEMtwosteps0000000000")
        self.assertEqual(sd.queue_request(self.items["Daily Mix 1"])["playlistId"], "RDAMPLsomemix000000000000")

    def test_artist_follows_the_tile_play_button_else_artist_radio(self):
        self.assertEqual(sd.queue_request(self.items["Chill Satellite"]),
                         {"mode": "watch", "videoId": "", "playlistId": "RDEMrPLFBWdOroD57bkqPbZJog", "radio": True, "shuffle": False})
        self.assertEqual(sd.queue_request(self.items["Saved Artist"]), {"mode": "artist", "channelId": "UCsavedartist00000000000"})
        shuffled = dict(self.items["Saved Artist"], playlistId="RDAOshuffle", params="wAEB8gECKAE%3D")
        self.assertTrue(sd.queue_request(shuffled)["shuffle"])

    def test_containers_are_left_to_the_frontend(self):
        for title in ("Sentiment", "Late Night Drive", "Chill Hits", "The Daily Show", "Episode 42: Keys"):
            self.assertEqual(sd.queue_request(self.items[title]), {"mode": "none"}, title)


class FakeClient:
    def __init__(self, log, response=None, fail=False, artist=None):
        self.log = log
        self.response = fx.full_home() if response is None else response
        self.fail = fail
        self.artist = artist

    def _send_request(self, endpoint, body, additionalParams=""):
        self.log.append(("send", endpoint, dict(body)))
        if self.fail:
            raise RuntimeError("offline")
        return copy.deepcopy(self.response)

    def get_watch_playlist(self, videoId=None, playlistId=None, limit=25, radio=False, shuffle=False):
        self.log.append(("watch", videoId, playlistId, radio, shuffle))
        if playlistId == "RDEMempty":
            return {"tracks": [], "playlistId": playlistId}
        seed = videoId or "first0000001"
        return {"playlistId": playlistId, "tracks": [
            {"videoId": seed, "title": "Seed", "length": "3:05", "artists": [{"name": "yetep", "id": "UC1"}],
             "album": {"name": "Gravity", "id": "MPRE1"}, "thumbnail": [{"url": "https://i.ytimg.com/vi/x/sddefault.jpg", "width": 640, "height": 480}]},
            {"videoId": "next0000002", "title": "Next", "length": "4:00", "artists": [{"name": "Other", "id": "UC2"}], "thumbnail": []},
        ]}

    def get_artist(self, channelId):
        self.log.append(("artist", channelId))
        return self.artist if self.artist is not None else {"radioId": "RDEMsaved", "shuffleId": "RDAOsaved", "songs": {"results": []}}


class ServiceTests(unittest.TestCase):
    def make_service(self, signed_in=True, **client_options):
        import tempfile
        work = tempfile.mkdtemp(prefix="mix-speed-dial-test-")
        self.addCleanup(__import__("shutil").rmtree, work, True)
        service = ytmusic_service.YouTubeMusicService(work, str(ROOT / "vendor"), lambda *_: None)
        if signed_in:
            # A stand-in: the service only checks that some auth is stored.
            service.settings["auth"] = {"placeholder": "not-a-credential"}
        log = []
        service._independent_client_for_request = lambda authenticated=None: FakeClient(log, **client_options)
        return service, log

    def test_signed_out_answers_empty_without_a_request(self):
        service, log = self.make_service(signed_in=False)
        self.assertEqual(service.get_speed_dial(), {"items": [], "reason": "signed_out", "title": "", "fetchedAt": 0})
        self.assertEqual(log, [])

    def test_items_are_shaped_for_the_interface(self):
        service, log = self.make_service()
        result = service.get_speed_dial()
        self.assertEqual(log[0][:2], ("send", "browse"))
        self.assertEqual(log[0][2]["browseId"], "FEmusic_home")
        self.assertEqual(result["reason"], "")
        self.assertEqual(result["title"], "Speed dial")
        items = by_title(result["items"])
        album = items["Sentiment"]
        self.assertEqual(album["thumbnail"], "https://lh3.googleusercontent.com/album=w544-h544-l90-rj")
        self.assertTrue(album["images"] and album["images"][0]["url"].startswith("https://lh3.googleusercontent.com/album="))
        self.assertTrue(items["Chill Satellite"]["round"])
        self.assertFalse(album["round"])
        episode = items["Episode 42: Keys"]["track"]
        self.assertEqual((episode["videoId"], episode["isPodcast"], episode["sourceKey"], episode["type"]),
                         ("ep42videoid", True, "youtubeMusic", "track"))
        self.assertNotIn("track", album)

    def test_cached_for_thirty_minutes_and_refreshed_on_demand(self):
        service, log = self.make_service()
        service.get_speed_dial()
        service.get_speed_dial()
        self.assertEqual(len(log), 1)
        service.get_speed_dial(refresh=True)
        self.assertEqual(len(log), 2)
        # A restart reads the disk copy while it is fresh.
        service._cache.clear()
        service.get_speed_dial()
        self.assertEqual(len(log), 2)
        real_monotonic, real_time = ytmusic_service.time.monotonic, ytmusic_service.time.time
        with mock.patch.object(ytmusic_service.time, "monotonic", lambda: real_monotonic() + 1801), \
                mock.patch.object(ytmusic_service.time, "time", lambda: real_time() + 1801):
            service.get_speed_dial()
        self.assertEqual(len(log), 3)
        self.assertEqual(service.SPEED_DIAL_TTL, 1800.0)

    def test_signing_out_or_clearing_drops_the_cache(self):
        service, log = self.make_service()
        service.get_speed_dial()
        service.clear_cache()
        service.get_speed_dial()
        self.assertEqual(len(log), 2)

    def test_missing_shelf_answers_empty_with_a_reason(self):
        service, log = self.make_service(response=fx.home([fx.quick_picks(), fx.plain_shelf("Recommended albums")]))
        result = service.get_speed_dial()
        self.assertEqual((result["items"], result["reason"]), ([], "missing"))

    def test_a_failed_request_answers_empty_and_is_not_cached(self):
        service, log = self.make_service(fail=True)
        result = service.get_speed_dial()
        self.assertEqual((result["items"], result["reason"]), ([], "error"))
        service.get_speed_dial()
        self.assertEqual(len(log), 2)

    def test_song_queue_is_its_radio_with_the_song_first(self):
        service, log = self.make_service()
        song = by_title(service.get_speed_dial()["items"])["Gravity"]
        queue = service.get_speed_dial_queue(song)
        self.assertEqual(log[-1], ("watch", "EludZd6lfts", "RDAMVMEludZd6lfts", False, False))
        self.assertEqual([track["videoId"] for track in queue["tracks"]], ["EludZd6lfts", "next0000002"])
        self.assertEqual(queue["tracks"][0]["duration_ms"], 185000)
        self.assertEqual(queue["tracks"][0]["type"], "track")
        self.assertEqual(queue["mode"], "radio")

    def test_artist_without_a_play_button_uses_artist_radio_then_top_songs(self):
        service, log = self.make_service()
        artist = by_title(service.get_speed_dial()["items"])["Saved Artist"]
        service.get_speed_dial_queue(artist)
        self.assertEqual(log[-2:], [("artist", "UCsavedartist00000000000"), ("watch", None, "RDEMsaved", False, False)])

        service, log = self.make_service(artist={"radioId": "RDEMempty", "songs": {"results": [
            {"videoId": "top0000001", "title": "Top song", "artists": [{"name": "Saved Artist", "id": "UCsaved"}]}]}})
        queue = service.get_speed_dial_queue(artist)
        self.assertEqual(queue["mode"], "top")
        self.assertEqual([track["videoId"] for track in queue["tracks"]], ["top0000001"])

    def test_tracks_that_failed_before_are_left_out(self):
        service, log = self.make_service()
        service._unavailable["next0000002"] = {"reason": "private", "until": ytmusic_service.time.time() + 3600}
        song = by_title(service.get_speed_dial()["items"])["Gravity"]
        self.assertEqual([track["videoId"] for track in service.get_speed_dial_queue(song)["tracks"]], ["EludZd6lfts"])

    def test_containers_are_refused(self):
        service, log = self.make_service()
        album = by_title(service.get_speed_dial()["items"])["Sentiment"]
        with self.assertRaises(RuntimeError):
            service.get_speed_dial_queue(album)


if __name__ == "__main__":
    unittest.main()
