"""Game soundtrack matching and ranking (mix_soundtrack.py)."""
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import mix_soundtrack as ms  # noqa: E402
import ytmusic_service  # noqa: E402


def album(name, album_type="Album", artist="Composer", item_id=None):
    return {"type": "album", "id": item_id or f"MPRE{abs(hash((name, album_type))) % 10**8}", "name": name,
            "albumType": album_type, "artists": [{"name": artist, "id": ""}], "owner": {"display_name": ""}}


def playlist(name, author="Some Fan", count=40, item_id=None):
    return {"type": "playlist", "id": item_id or f"VLPL{abs(hash(name)) % 10**8}", "name": name, "artists": [],
            "owner": {"display_name": author}, "author": author, "itemCount": count}


def best(game, items, platform=""):
    ranked = ms.rank_candidates(game, items, platform)
    return ranked["candidates"][0]["name"] if ranked["candidates"] else None, ranked


class CanonicalNameTests(unittest.TestCase):
    def test_store_editions_and_trademarks_go_sequels_and_subtitles_stay(self):
        cases = {
            "Celeste - Deluxe Edition": "Celeste",
            "The Witcher® 3: Wild Hunt - Game of the Year Edition": "The Witcher 3: Wild Hunt",
            "DARK SOULS™ III": "DARK SOULS III",
            "Ghost of Tsushima DIRECTOR'S CUT": "Ghost of Tsushima",
            "Hades II": "Hades II",
            "Persona 5 Royal": "Persona 5 Royal",
            "Final Fantasy VII Remake Intergrade": "Final Fantasy VII Remake Intergrade",
            r"C:\Games\Hollow Knight\hollow_knight.exe": "hollow knight",
            "Ori and the Blind Forest: Definitive Edition": "Ori and the Blind Forest",
        }
        for raw, expected in cases.items():
            self.assertEqual(ms.canonical_game_name(raw), expected, raw)

    def test_rom_titles_lose_dump_tags_and_get_their_article_back(self):
        self.assertEqual(ms.canonical_game_name("Legend of Zelda, The - Ocarina of Time (USA) (Rev 1)"),
                         "The Legend of Zelda - Ocarina of Time")
        self.assertEqual(ms.canonical_game_name("Super Metroid (Japan, USA) (En,Ja) [!]"), "Super Metroid")
        self.assertEqual(ms.canonical_game_name("Chrono Trigger (USA).sfc"), "Chrono Trigger")
        self.assertEqual(ms.canonical_game_name("Final Fantasy VII (Disc 1)"), "Final Fantasy VII")

    def test_queries_ask_for_albums_first(self):
        queries = ms.search_queries("Celeste - Deluxe Edition")
        self.assertEqual(queries[0], ("Celeste original soundtrack", "albums"))
        self.assertEqual([f for _q, f in queries], ["albums", "albums", "playlists"])
        self.assertEqual(ms.search_queries("   "), [])

    def test_normalize_keeps_hyphenated_words_whole(self):
        self.assertEqual(ms.normalize_text("Celeste B-Sides: Lo-Fi"), "celeste bsides lofi")
        self.assertEqual(ms.normalize_text("Pokémon™ & Friends"), "pokemon and friends")


class RankingTests(unittest.TestCase):
    def test_official_album_beats_playlists_and_uploads(self):
        name, ranked = best("Celeste", [
            playlist("Celeste OST Full Soundtrack", count=21),
            playlist("Celeste - Original Soundtrack", author="YouTube Music", count=21),
            album("Celeste (Original Soundtrack)", artist="Lena Raine"),
        ])
        self.assertEqual(name, "Celeste (Original Soundtrack)")
        self.assertEqual(ranked["candidates"][0]["type"], "album")

    def test_base_game_beats_dlc_and_bonus_albums(self):
        name, ranked = best("Celeste", [
            album("Celeste B-Sides"),
            album("Celeste Farewell (Original Soundtrack)"),
            album("Celeste (Original Soundtrack)"),
        ])
        self.assertEqual(name, "Celeste (Original Soundtrack)")
        self.assertTrue(ranked["confident"])
        name, _ = best("Hollow Knight", [
            album("Hollow Knight: Godmaster (Original Soundtrack)", artist="Christopher Larkin"),
            album("Hollow Knight (Original Soundtrack)", artist="Christopher Larkin"),
            album("Hollow Knight Gods & Nightmares DLC"),
        ])
        self.assertEqual(name, "Hollow Knight (Original Soundtrack)")

    def test_a_dlc_named_in_the_game_title_is_wanted(self):
        name, _ = best("Celeste B-Sides", [album("Celeste (Original Soundtrack)"), album("Celeste B-Sides")])
        self.assertEqual(name, "Celeste B-Sides")

    def test_subtitled_sequel_beats_the_first_game(self):
        name, ranked = best("Hollow Knight: Silksong", [
            album("Hollow Knight (Original Soundtrack)"),
            album("Hollow Knight: Silksong (Original Soundtrack)"),
        ])
        self.assertEqual(name, "Hollow Knight: Silksong (Original Soundtrack)")
        self.assertTrue(ranked["confident"])
        name, _ = best("Marvel's Spider-Man: Miles Morales", [
            album("Marvel's Spider-Man (Original Video Game Soundtrack)"),
            album("Marvel's Spider-Man: Miles Morales (Original Video Game Soundtrack)"),
        ])
        self.assertEqual(name, "Marvel's Spider-Man: Miles Morales (Original Video Game Soundtrack)")

    def test_colons_and_dashes_in_titles_do_not_matter(self):
        name, ranked = best("SEKIRO™: Shadows Die Twice - GOTY Edition", [
            album("Sekiro - Shadows Die Twice (Original Soundtrack)", artist="FromSoftware Sound Team"),
            playlist("Sekiro OST", count=50),
        ])
        self.assertEqual(name, "Sekiro - Shadows Die Twice (Original Soundtrack)")
        self.assertTrue(ranked["confident"])

    def test_sequel_numbers_must_agree_roman_or_arabic(self):
        name, _ = best("Hades", [album("Hades II (Original Soundtrack)"), album("Hades (Original Soundtrack)")])
        self.assertEqual(name, "Hades (Original Soundtrack)")
        name, _ = best("Hades II", [album("Hades (Original Soundtrack)"), album("Hades II (Original Soundtrack)")])
        self.assertEqual(name, "Hades II (Original Soundtrack)")
        name, _ = best("FINAL FANTASY VII", [
            album("Final Fantasy VIII Original Soundtrack"),
            album("Final Fantasy VII Original Soundtrack"),
        ])
        self.assertEqual(name, "Final Fantasy VII Original Soundtrack")
        name, _ = best("Dark Souls 3", [album("Dark Souls II Soundtrack"), album("DARK SOULS III Original Soundtrack")])
        self.assertEqual(name, "DARK SOULS III Original Soundtrack")

    def test_years_and_volume_numbers_are_not_sequel_numbers(self):
        name, _ = best("DOOM", [album("DOOM (2016) Original Game Soundtrack"), album("DOOM Eternal (Original Game Soundtrack)")])
        self.assertEqual(name, "DOOM (2016) Original Game Soundtrack")
        name, _ = best("Persona 5", [
            album("Persona 5 Original Soundtrack Vol. 2"),
            album("Persona 5 Original Soundtrack Vol. 1"),
        ])
        self.assertEqual(name, "Persona 5 Original Soundtrack Vol. 1")

    def test_edition_suffixes_on_albums_are_fine_remakes_are_not(self):
        name, _ = best("Final Fantasy VII", [
            album("Final Fantasy VII Remake Original Soundtrack"),
            album("Final Fantasy VII Original Soundtrack (Deluxe Edition)"),
        ])
        self.assertEqual(name, "Final Fantasy VII Original Soundtrack (Deluxe Edition)")

    def test_arrangements_and_singles_lose(self):
        name, _ = best("Undertale", [
            album("Undertale Piano Collections"),
            album("Undertale Soundtrack", album_type="Single"),
            album("UNDERTALE Soundtrack", artist="Toby Fox"),
            album("Undertale Lofi Remixes"),
        ])
        self.assertEqual(name, "UNDERTALE Soundtrack")

    def test_unrelated_results_are_dropped_and_at_most_three_offered(self):
        ranked = ms.rank_candidates("Celeste", [
            album("Stardew Valley (Original Soundtrack)"),
            album("Minecraft - Volume Alpha"),
            album("Celeste (Original Soundtrack)", item_id="MPRE1"),
            album("Celeste (Original Soundtrack)", item_id="MPRE1"),
            album("Celeste B-Sides"),
            album("Celeste Farewell"),
            playlist("Celeste ost"),
            playlist("celeste soundtrack all songs"),
        ])
        names = [c["name"] for c in ranked["candidates"]]
        self.assertLessEqual(len(names), ms.MAX_CANDIDATES)
        self.assertNotIn("Stardew Valley (Original Soundtrack)", names)
        self.assertEqual(len({c["id"] for c in ranked["candidates"]}), len(names))
        self.assertEqual(ms.rank_candidates("Celeste", [album("Stardew Valley (Original Soundtrack)")])["candidates"], [])

    def test_unsure_when_two_candidates_are_close(self):
        ranked = ms.rank_candidates("Celeste", [
            playlist("Celeste Original Soundtrack", author="YouTube Music", count=21),
            playlist("Celeste Original Soundtrack (Complete)", author="YouTube Music", count=22),
        ])
        self.assertFalse(ranked["confident"])
        self.assertEqual(len(ranked["candidates"]), 2)

    def test_rom_title_with_platform(self):
        name, ranked = best("Legend of Zelda, The - Ocarina of Time (USA)", [
            album("The Legend of Zelda: Majora's Mask Original Soundtrack"),
            album("The Legend of Zelda: Ocarina of Time Original Soundtrack", artist="Nintendo"),
            playlist("Zelda Ocarina of Time 3D OST", count=90),
        ], platform="Nintendo 64")
        self.assertEqual(name, "The Legend of Zelda: Ocarina of Time Original Soundtrack")
        self.assertEqual(ranked["game"], "The Legend of Zelda - Ocarina of Time")
        self.assertTrue(ranked["confident"])


class CollectorRecordTests(unittest.TestCase):
    DOC = {"v": 1, "source": "collector", "records": {"3000000123": {
        "app_id": 3000000123, "rom_id": 42, "name": "The Legend of Zelda: Ocarina of Time",
        "platform": "Nintendo 64", "started_at": 1, "cover_url": None, "future": "ignored"}}}

    def test_reads_the_record_for_the_running_app(self):
        self.assertEqual(ms.parse_collector_document(self.DOC, 3000000123),
                         {"name": "The Legend of Zelda: Ocarina of Time", "platform": "Nintendo 64"})

    def test_absent_other_version_or_other_app_means_none(self):
        self.assertIsNone(ms.parse_collector_document(self.DOC, 999))
        self.assertIsNone(ms.parse_collector_document({**self.DOC, "v": 2}, 3000000123))
        self.assertIsNone(ms.parse_collector_document(None, 3000000123))
        self.assertIsNone(ms.parse_collector_document({"v": 1, "records": {"5": {"name": "  "}}}, 5))


class CollectorFileTests(unittest.TestCase):
    def test_paths_from_decky_home_or_runtime_sibling(self):
        import os
        home = os.path.join("H", "homebrew")
        self.assertEqual(ms.collector_file_paths(home, os.path.join(home, "data", "Awesome Mix")),
                         [os.path.join(home, "data", "eternal-now-playing", "now-playing.json")])
        self.assertEqual(ms.collector_file_paths("", os.path.join(home, "data", "Awesome Mix")),
                         [os.path.join(home, "data", "eternal-now-playing", "now-playing.json")])
        self.assertEqual(ms.collector_file_paths("", os.path.join("plugin", "dir")), [])

    def test_reads_softly(self):
        import json, os, tempfile, shutil
        work = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, work, True)
        path = os.path.join(work, "now-playing.json")
        missing = os.path.join(work, "missing.json")
        self.assertIsNone(ms.read_collector_record([missing], 5))
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("{half a docu")
        self.assertIsNone(ms.read_collector_record([path], 5))
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(CollectorRecordTests.DOC, handle)
        self.assertEqual(ms.read_collector_record([missing, path], 3000000123)["platform"], "Nintendo 64")


class FakeClient:
    """ytmusicapi.search shapes for filter=albums / filter=playlists."""

    def __init__(self, calls, fail=False):
        self.calls = calls
        self.fail = fail

    def search(self, query, filter=None, limit=20):
        self.calls.append((query, filter))
        if self.fail:
            raise RuntimeError("offline")
        if filter == "albums":
            return [
                {"resultType": "album", "type": "Single", "title": "Celeste (Original Soundtrack)",
                 "browseId": "MPRE_single", "artists": [{"name": "Lena Raine", "id": "UC1"}], "year": "2018"},
                {"resultType": "album", "type": "Album", "title": "Celeste (Original Soundtrack)",
                 "browseId": "MPRE_album", "artists": [{"name": "Lena Raine", "id": "UC1"}], "year": "2018"},
                {"resultType": "song", "title": "First Steps", "videoId": "vid1", "artists": []},
            ]
        return [{"resultType": "playlist", "title": "Celeste OST", "browseId": "VLPLfan", "author": "fan",
                 "itemCount": 21}]


class ServiceTests(unittest.TestCase):
    def make_service(self, fail=False):
        import tempfile
        work = tempfile.mkdtemp(prefix="mix-soundtrack-test-")
        self.addCleanup(__import__("shutil").rmtree, work, True)
        service = ytmusic_service.YouTubeMusicService(work, str(ROOT / "vendor"), lambda *_: None)
        calls = []
        service._independent_client_for_request = lambda authenticated=None: FakeClient(calls, fail)
        return service, calls

    def test_searches_albums_and_playlists_and_keeps_only_containers(self):
        service, calls = self.make_service()
        result = service.find_soundtrack("Celeste - Deluxe Edition", "")
        self.assertEqual(sorted(calls), sorted([("Celeste original soundtrack", "albums"), ("Celeste OST", "albums"),
                                                ("Celeste soundtrack", "playlists")]))
        self.assertEqual(result["game"], "Celeste")
        self.assertEqual(result["candidates"][0]["id"], "MPRE_album")
        self.assertEqual(result["candidates"][0]["albumType"], "Album")
        self.assertTrue(all(c["type"] in {"album", "playlist"} for c in result["candidates"]))
        self.assertTrue(result["confident"])
        # Second ask is answered from the cache.
        service.find_soundtrack("Celeste", "")
        self.assertEqual(len(calls), 3)

    def test_a_failed_search_answers_empty_and_is_not_cached(self):
        service, calls = self.make_service(fail=True)
        self.assertEqual(service.find_soundtrack("Celeste")["candidates"], [])
        self.assertEqual(service.find_soundtrack("Celeste")["candidates"], [])
        self.assertEqual(len(calls), 6)


if __name__ == "__main__":
    unittest.main()
