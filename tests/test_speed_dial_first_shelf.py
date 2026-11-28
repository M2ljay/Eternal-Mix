import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import mix_speed_dial as m  # noqa: E402


def _tile(i):
    return {"musicTwoRowItemRenderer": {
        "title": {"runs": [{"text": "A%d" % i}]}, "subtitle": {"runs": [{"text": "Album"}]},
        "navigationEndpoint": {"browseEndpoint": {"browseId": "MPREb_x%d" % i, "browseEndpointContextSupportedConfigs": {
            "browseEndpointContextMusicConfig": {"pageType": "MUSIC_PAGE_TYPE_ALBUM"}}}},
        "thumbnailRenderer": {"musicThumbnailRenderer": {"thumbnail": {"thumbnails": [{"url": "https://x/y", "width": 1, "height": 1}]}}}}}


def _row(strapline, tiles):
    header = {"title": {"runs": [{"text": "Shelf"}]}}
    if strapline:
        header["strapline"] = {"runs": [{"text": "Account"}]}
    return {"musicCarouselShelfRenderer": {"header": {"musicCarouselShelfBasicHeaderRenderer": header},
                                           "contents": [_tile(i) for i in range(tiles)]}}


def _response(*rows):
    return {"contents": {"singleColumnBrowseResultsRenderer": {"tabs": [{"tabRenderer": {"content": {
        "sectionListRenderer": {"contents": list(rows)}}}}]}}}


class FirstShelfTests(unittest.TestCase):
    def test_first_account_shelf_is_used_even_one_per_column_and_one_kind(self):
        result = m.select_grid(_response(_row(True, 10)))
        self.assertEqual(result["source"], m.SOURCE_SPEED_DIAL)
        self.assertEqual(len(result["items"]), 10)

    def test_a_later_account_shelf_is_chosen(self):
        result = m.select_grid(_response(_row(False, 10), _row(True, 10)))
        self.assertEqual((result["source"], result["index"]), (m.SOURCE_SPEED_DIAL, 1))

    def test_first_shelf_without_account_name_is_not_promoted(self):
        result = m.select_grid(_response(_row(False, 10)))
        self.assertNotEqual(result["source"], m.SOURCE_SPEED_DIAL)


if __name__ == "__main__":
    unittest.main()
