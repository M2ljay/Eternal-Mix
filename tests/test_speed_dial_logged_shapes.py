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


class SelectionTests(unittest.TestCase):
    def test_2330_the_account_shelf_at_index_1_is_chosen(self):
        grid = sd.select_grid(fx.logged_home_2330())
        self.assertEqual((grid["source"], grid["index"], grid["why"]), (sd.SOURCE_SPEED_DIAL, 1, "first account-headed shelf"))
        self.assertEqual(len(grid["items"]), 10)
        self.assertTrue(grid["checked"][sd.SOURCE_SPEED_DIAL])

    def test_0156_the_account_shelf_beats_quick_picks(self):
        grid = sd.select_grid(fx.logged_home_0156())
        self.assertEqual((grid["source"], grid["index"]), (sd.SOURCE_SPEED_DIAL, 0))

    def test_2257_without_an_account_shelf_it_is_quick_picks(self):
        grid = sd.select_grid(fx.logged_home_2257())
        self.assertEqual((grid["source"], grid["index"]), (sd.SOURCE_QUICK_PICKS, 0))

    def test_label_straplines_are_not_an_account(self):
        for label in ("SIMILAR TO", "Start radio from a song", "More from"):
            row = fx.logged_two_row_shelf("Chill Satellite", strapline=label)
            self.assertFalse(sd.is_account_shelf(row), label)
        self.assertTrue(sd.is_account_shelf(fx.logged_two_row_shelf("Mixed for you", strapline="Test User")))

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
        self.assertIn("shelf=#1 of 3 (first account-headed shelf), continuation pages read=0", self.outcome()[-1])
        self.assertNotIn("Test User", "\n".join(self.lines))

    def test_continuation_is_followed_before_falling_back(self):
        page = fx.continuation_page([fx.logged_two_row_shelf("Mixed for you", strapline="Test User", prefix="c")], "tok2")
        result = self.service(fx.logged_home_2257(), [page]).get_speed_dial()
        self.assertEqual(result["source"], sd.SOURCE_SPEED_DIAL)
        self.assertEqual(result["items"][0]["id"], "MPREb_c00000")
        sent = [entry for entry in self.log if "ctoken=" in entry[3]]
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0][3], "&ctoken=tok1&continuation=tok1")
        self.assertIn("shelf=#3 of 4 (first account-headed shelf), continuation pages read=1", self.outcome()[-1])

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
