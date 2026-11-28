"""Speed dial on the home shapes from a real NowPlaying.log: the account-headed shelf in any
position, bounded continuation pages, the last good set and pins (mix_speed_dial.py,
ytmusic_service.get_speed_dial)."""
import copy
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT, ROOT / "tests"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import mix_speed_dial as sd  # noqa: E402
import speed_dial_fixtures as fx  # noqa: E402
import ytmusic_service  # noqa: E402


class PagedClient:
    """The first browse answers `home`; each continuation request answers the next page."""

    def __init__(self, log, home, pages=()):
        self.context = {"context": {"client": {"clientName": "WEB_REMIX", "clientVersion": "1.0"}}}
        self.log, self.home, self.pages = log, home, list(pages)

    def _send_request(self, endpoint, body, additionalParams=""):
        self.log.append(("send", endpoint, dict(body), additionalParams))
        if "ctoken=" in additionalParams:
            index = sum(1 for entry in self.log if "ctoken=" in entry[3]) - 1
            return copy.deepcopy(self.pages[index]) if index < len(self.pages) else fx.continuation_page([])
        return copy.deepcopy(self.home)


ACCOUNT = {"name": "Test User", "photo": "https://lh3.googleusercontent.com/avatar=s120"}


class SelectionTests(unittest.TestCase):
    def test_2330_the_account_shelf_at_index_1_is_chosen(self):
        grid = sd.select_grid(fx.logged_home_2330(), account=ACCOUNT)
        self.assertEqual((grid["source"], grid["index"], grid["why"]), (sd.SOURCE_SPEED_DIAL, 1, "account-headed shelf (strapline is the account name)"))
        self.assertEqual(len(grid["items"]), 10)
        self.assertTrue(grid["checked"][sd.SOURCE_SPEED_DIAL])

    def test_0156_the_account_shelf_beats_quick_picks(self):
        grid = sd.select_grid(fx.logged_home_0156(), account=ACCOUNT)
        self.assertEqual((grid["source"], grid["index"]), (sd.SOURCE_SPEED_DIAL, 0))

    def test_2257_without_an_account_shelf_it_is_quick_picks(self):
        grid = sd.select_grid(fx.logged_home_2257(), account=ACCOUNT)
        self.assertEqual((grid["source"], grid["index"]), (sd.SOURCE_QUICK_PICKS, 0))

    def test_only_the_account_own_header_counts(self):
        for label in ("SIMILAR TO", "Start radio from a song", "More from", "Holiday favorites", "Test User and friends"):
            row = fx.logged_two_row_shelf("Chill Satellite", strapline=label)
            self.assertFalse(sd.is_account_shelf(row, ACCOUNT), label)
        self.assertTrue(sd.is_account_shelf(fx.logged_two_row_shelf("Mixed for you", strapline="test  user"), ACCOUNT))
        self.assertFalse(sd.is_account_shelf(fx.logged_two_row_shelf("Mixed for you", strapline="Test User"), None))
        self.assertFalse(sd.is_account_shelf(fx.logged_two_row_shelf("Mixed for you", strapline="Test User"), {"name": "", "photo": ""}))

    def test_the_account_avatar_counts_without_a_name(self):
        row = fx.speed_dial_shelf(fx.speed_dial_tiles(), title="Mixed for you", per_column="1", strapline=False)
        self.assertEqual(sd.account_header_match(row, {"name": "", "photo": "https://lh3.googleusercontent.com/avatar=s240-c-k"}), "avatar")
        self.assertEqual(sd.account_header_match(row, {"name": "", "photo": "https://lh3.googleusercontent.com/someone=s240"}), "")

    def test_holiday_shelf_is_never_chosen(self):
        # 2026-11-28 00:13: "#0 [title,strapline] perColumn=1 items=10 (musicTwoRowItemRenderer:10)" was a Christmas shelf.
        for home in (fx.logged_home_holiday(), fx.logged_home_holiday_only()):
            for account in (ACCOUNT, None, {"name": "", "photo": ""}):
                grid = sd.select_grid(home, account=account)
                self.assertNotIn("Christmas", grid["title"])
                self.assertFalse(any("Christmas" in item["title"] for item in grid["items"]))
        grid = sd.select_grid(fx.logged_home_holiday(), account=ACCOUNT)
        self.assertEqual((grid["source"], grid["index"]), (sd.SOURCE_SPEED_DIAL, 1))
        self.assertFalse(sd.select_grid(fx.logged_home_holiday_only(), account=ACCOUNT)["found"])

    def test_a_holiday_grid_with_three_per_column_and_an_avatar_is_not_speed_dial(self):
        row = fx.holiday_shelf(per_column="3", avatar=True)
        self.assertEqual(sd.shelf_score(row, ACCOUNT), 0)
        self.assertEqual(sd.listen_again_score(row), 0)

    def test_listen_again_only_by_title_or_pin_entries(self):
        mixed = fx.listen_again_shelf()
        header = mixed["musicCarouselShelfRenderer"]["header"]["musicCarouselShelfBasicHeaderRenderer"]
        header["title"] = {"runs": [{"text": "Your mix"}]}
        for entry in mixed["musicCarouselShelfRenderer"]["contents"]:
            entry["musicTwoRowItemRenderer"]["menu"] = fx.pin_menu(None)
        self.assertEqual(sd.listen_again_score(mixed), 0)  # strapline, avatar and mixed kinds alone
        self.assertGreaterEqual(sd.listen_again_score(fx.listen_again_shelf()), 3)

    def test_continuation_token_and_rows(self):
        self.assertEqual(sd.continuation_token(fx.logged_home_2330()), "tok1")
        page = fx.continuation_page([fx.tastebuilder()], "tok2")
        self.assertEqual(sd.continuation_token(page), "tok2")
        self.assertEqual(len(sd.continuation_rows(page)), 1)
        self.assertEqual(sd.continuation_token(fx.home([])), "")


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.work = tempfile.mkdtemp(prefix="mix-speed-dial-shapes-")
        self.addCleanup(shutil.rmtree, self.work, True)
        self.lines = []
        self.log = []

    def service(self, home, pages=()):
        service = ytmusic_service.YouTubeMusicService(self.work, str(ROOT / "vendor"), self.lines.append)
        service.settings["auth"] = {"placeholder": "not-a-credential"}
        service.settings["profile"] = {"accountName": "Test User", "accountPhotoUrl": "https://lh3.googleusercontent.com/avatar=s120"}
        service._independent_client_for_request = lambda authenticated=None: PagedClient(self.log, home, pages)
        service._speed_dial_from_library = lambda: [{"type": "album", "id": "MPREb_lib", "title": "Library album", "subtitle": "",
                                                     "thumbnails": [], "browseId": "MPREb_lib", "videoId": "", "playlistId": "",
                                                     "params": "", "pinned": False}]
        return service

    def outcome(self):
        return [line for line in self.lines if "speed dial: called (" in line]

    def test_chosen_shelf_index_and_reason_are_logged(self):
        result = self.service(fx.logged_home_2330()).get_speed_dial()
        self.assertEqual((result["source"], len(result["items"])), (sd.SOURCE_SPEED_DIAL, 10))
        self.assertIn("speed dial shelf=yes", self.outcome()[-1])
        self.assertIn('shelf=#1 of 3 title="Mixed for you" (account-headed shelf (strapline is the account name)), continuation pages read=0', self.outcome()[-1])
        self.assertNotIn("Test User", "\n".join(self.lines))

    def test_continuation_is_followed_before_falling_back(self):
        page = fx.continuation_page([fx.logged_two_row_shelf("Mixed for you", strapline="Test User", prefix="c")], "tok2")
        result = self.service(fx.logged_home_2257(), [page]).get_speed_dial()
        self.assertEqual(result["source"], sd.SOURCE_SPEED_DIAL)
        self.assertEqual(result["items"][0]["id"], "MPREb_c00000")
        sent = [entry for entry in self.log if "ctoken=" in entry[3]]
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0][3], "&ctoken=tok1&continuation=tok1")
        self.assertIn('shelf=#3 of 4 title="Mixed for you"', self.outcome()[-1])
        self.assertIn("continuation pages read=1", self.outcome()[-1])

    def test_at_most_two_continuation_pages(self):
        empty_page = fx.continuation_page([fx.tastebuilder()], "again")
        result = self.service(fx.logged_home_2257(), [empty_page] * 5).get_speed_dial()
        self.assertEqual(sum(1 for entry in self.log if "ctoken=" in entry[3]), sd.MAX_CONTINUATION_PAGES)
        self.assertEqual(result["source"], sd.SOURCE_QUICK_PICKS)  # cold start: nothing good kept yet

    def test_a_failed_continuation_keeps_the_first_page(self):
        service = self.service(fx.logged_home_2257())
        client = PagedClient(self.log, fx.logged_home_2257())
        real = client._send_request

        def send(endpoint, body, additionalParams=""):
            if "ctoken=" in additionalParams:
                raise RuntimeError("offline")
            return real(endpoint, body, additionalParams)
        client._send_request = send
        service._independent_client_for_request = lambda authenticated=None: client
        self.assertEqual(service.get_speed_dial()["source"], sd.SOURCE_QUICK_PICKS)
        self.assertIn("continuation page 1 failed: RuntimeError", "\n".join(self.lines))

    def test_a_fallback_never_replaces_a_good_set_even_after_a_restart(self):
        good = self.service(fx.logged_home_2330()).get_speed_dial()
        # A restart: a new service on the same folder, the cache cleared, home has only Quick picks.
        later = self.service(fx.logged_home_2257())
        later.clear_cache()
        kept = later.get_speed_dial(refresh=True)
        self.assertEqual(kept["source"], sd.SOURCE_SPEED_DIAL)
        self.assertEqual([item["id"] for item in kept["items"]], [item["id"] for item in good["items"]])
        self.assertGreater(kept["fetchedAt"], 0)
        self.assertIn("kept the last good set: source=speed_dial, items=10", self.outcome()[-1])
        self.assertIn("a fallback never replaces a good set", self.outcome()[-1])

    def test_cold_start_uses_quick_picks_then_library(self):
        self.assertEqual(self.service(fx.logged_home_2257()).get_speed_dial()["source"], sd.SOURCE_QUICK_PICKS)
        nothing = fx.home_page([fx.logged_two_row_shelf("Albums for you"), fx.tastebuilder()])
        self.assertEqual(self.service(nothing).get_speed_dial(refresh=True)["source"], sd.SOURCE_LIBRARY)

    def test_the_holiday_home_is_not_taken_and_the_account_name_is_never_logged(self):
        result = self.service(fx.logged_home_holiday_only()).get_speed_dial()
        self.assertNotEqual(result["source"], sd.SOURCE_LISTEN_AGAIN)
        self.assertFalse(any("Christmas" in item["title"] for item in result["items"]))
        self.service(fx.logged_home_holiday()).get_speed_dial(refresh=True)
        self.assertNotIn("Test User", "\n".join(self.lines))

    def test_a_1_1_1_last_good_set_is_discarded(self):
        import json, os
        with open(os.path.join(self.work, "youtube-music-speed-dial-last-good.json"), "w", encoding="utf-8") as handle:
            json.dump({"items": [{"type": "album", "id": "MPREb_xmas", "title": "Christmas Album"}], "title": "Christmas hits",
                       "source": "listen_again", "savedAt": 1}, handle)
        result = self.service(fx.logged_home_2257()).get_speed_dial()
        self.assertEqual(result["source"], sd.SOURCE_QUICK_PICKS)
        self.assertFalse(os.path.exists(os.path.join(self.work, "youtube-music-speed-dial-last-good.json")))

    def test_without_a_profile_the_account_info_is_fetched_once(self):
        service = self.service(fx.logged_home_2330())
        service.settings["profile"] = {}
        calls = []
        service._save_settings = lambda: None

        class Client(PagedClient):
            def get_account_info(self):
                calls.append(1)
                return {"accountName": "Test User", "accountPhotoUrl": ""}
        service._independent_client_for_request = lambda authenticated=None: Client(self.log, fx.logged_home_2330())
        self.assertEqual(service.get_speed_dial()["source"], sd.SOURCE_SPEED_DIAL)
        service.get_speed_dial(refresh=True)
        self.assertEqual(len(calls), 1)

    def test_signing_out_forgets_the_last_good_set(self):
        service = self.service(fx.logged_home_2330())
        service.get_speed_dial()
        self.assertIsNotNone(service._speed_dial_last_good())
        service._forget_speed_dial_last_good()
        self.assertIsNone(service._speed_dial_last_good())
        source = (ROOT / "ytmusic_service.py").read_text(encoding="utf-8")
        body = source[source.index("    def disconnect(self)"):source.index("\n    def ", source.index("    def disconnect(self)") + 10)]
        self.assertIn("self._forget_speed_dial_last_good()", body)


class PinTests(unittest.TestCase):
    def setUp(self):
        self.work = tempfile.mkdtemp(prefix="mix-speed-dial-pins-")
        self.addCleanup(shutil.rmtree, self.work, True)
        self.lines = []

    def service(self):
        return ytmusic_service.YouTubeMusicService(self.work, str(ROOT / "vendor"), self.lines.append)

    def item(self, kind, item_id, **extra):
        return {"type": kind, "id": item_id, "title": f"{kind} {item_id}", "thumbnail": "https://example.invalid/a.jpg", **extra}

    def test_pins_keep_order_drop_junk_and_persist(self):
        saved = self.service().set_speed_dial_pins([
            self.item("album", "B"), self.item("song", "A"), self.item("album", "B"),
            {"type": "bogus", "id": "x", "title": "x"}, {"type": "album", "id": "", "title": "no id"}, "junk"])
        self.assertEqual([(pin["type"], pin["id"]) for pin in saved], [("album", "B"), ("song", "A")])
        self.assertTrue(all(pin["pinned"] for pin in saved))
        again = self.service()
        again.clear_cache()
        self.assertEqual([(pin["type"], pin["id"]) for pin in again.get_speed_dial_pins()], [("album", "B"), ("song", "A")])

    def test_pins_are_capped(self):
        saved = self.service().set_speed_dial_pins([self.item("album", str(index)) for index in range(40)])
        self.assertEqual(len(saved), sd.MAX_ITEMS)

    def test_no_pins_file_is_an_empty_list(self):
        self.assertEqual(self.service().get_speed_dial_pins(), [])


if __name__ == "__main__":
    unittest.main()


class UnexpectedResponseTests(unittest.TestCase):
    """2026-11-28 00:01:53: the saved-episodes (SE) playlist answered without contents and the
    raw ytmusicapi error, with the whole response echoed, reached the user."""

    ERROR = ("Unable to find 'contents' using path ['contents', 'twoColumnBrowseResultsRenderer', 'tabs', 0, 'tabRenderer', "
             "'content', 'sectionListRenderer', 'contents', 0] on {'responseContext': {'serviceTrackingParams': []}}, exception: 'contents'")

    def test_parser_errors_are_recognised_and_shortened(self):
        self.assertTrue(sd.is_parser_error(Exception(self.ERROR)))
        self.assertTrue(sd.is_parser_error(KeyError("contents")))
        self.assertFalse(sd.is_parser_error(RuntimeError("offline")))
        short = sd.short_error(Exception(self.ERROR))
        self.assertTrue(short.endswith("on <response>"))
        self.assertNotIn("responseContext", short)

    def test_saved_episodes_without_contents_show_none(self):
        work = tempfile.mkdtemp(prefix="mix-saved-episodes-")
        self.addCleanup(shutil.rmtree, work, True)
        lines = []
        service = ytmusic_service.YouTubeMusicService(work, str(ROOT / "vendor"), lines.append)
        service.settings["auth"] = {"placeholder": "not-a-credential"}
        error = self.ERROR

        class Client:
            context = {"context": {"client": {"clientName": "WEB_REMIX"}}}

            def get_saved_episodes(self, limit=None):
                raise Exception(error)
        service._client_for_request = lambda authenticated=None: Client()
        self.assertEqual(service.get_library("episodes", 50)["items"], [])
        text = "\n".join(lines)
        self.assertIn("saved episodes unavailable", text)
        self.assertNotIn("responseContext", text)

    def test_the_backend_answers_a_short_line_for_parser_errors(self):
        backend = (ROOT / "mix_backend.py").read_text(encoding="utf-8")
        start = backend.index("    async def _youtube_music_result(")
        body = backend[start:backend.index("\n    async def ", start + 10)]
        self.assertIn("mix_speed_dial.is_parser_error(exc)", body)
        self.assertIn('"error": YOUTUBE_MUSIC_UNEXPECTED_RESPONSE', body)
        i18n = (ROOT / "src" / "i18n.ts").read_text(encoding="utf-8")
        self.assertIn('"YouTube Music returned an unexpected response": "youtubeMusicUnexpected"', i18n)
