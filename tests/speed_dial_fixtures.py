"""Home-feed responses shaped like YouTube Music's ``browse`` answer for ``FEmusic_home``.

No live capture is committed (it would hold the account's name, avatar and listening
history). The shapes are rebuilt from:
  - ytmusicapi's navigation paths for the home feed and two-row tiles
    (vendor/ytmusicapi/navigation.py: SINGLE_COLUMN_TAB, SECTION_LIST, CAROUSEL,
    CAROUSEL_TITLE, MTRIR, MRLIR, TITLE, SUBTITLE_RUNS, THUMBNAIL_RENDERER,
    THUMBNAIL_OVERLAY_NAVIGATION, MENU_ITEMS, PAGE_TYPE), its tile parsers
    (parsers/browsing.py parse_album, parse_playlist, parse_related_artist,
    parse_watch_playlist, parse_song; parsers/podcasts.py parse_podcast) and its pin
    menu reading (parsers/songs.py parse_song_menu_data: KEEP / KEEP_OFF);
  - how two other YouTube Music clients recognise the Speed dial shelf: Mochy-Music's
    innertube HomePage.kt (numItemsPerColumn >= 3, header thumbnail, all two-row
    tiles) and Prism's innertube Parser.kt (stacked card shelves, ``gridRenderer``
    shelves with ``items``, station tiles as watch endpoints with an RD list).
Ids are made up but keep YouTube's prefixes (MPRE albums, VL/PL playlists, UC channels,
MPSP podcast shows, RD radios, OLAK album lists, RDCLAK curated playlists).
"""
from __future__ import annotations

import copy
from typing import Any, Dict, List, Optional

ALBUM = "MUSIC_PAGE_TYPE_ALBUM"
ARTIST = "MUSIC_PAGE_TYPE_ARTIST"
LIBRARY_ARTIST = "MUSIC_PAGE_TYPE_LIBRARY_ARTIST"
PLAYLIST = "MUSIC_PAGE_TYPE_PLAYLIST"
PODCAST = "MUSIC_PAGE_TYPE_PODCAST_SHOW_DETAIL_PAGE"
EPISODE_VIDEO = "MUSIC_VIDEO_TYPE_PODCAST_EPISODE"


def thumbs(seed: str, sizes=(226, 544)) -> Dict[str, Any]:
    return {"thumbnails": [{"url": f"https://lh3.googleusercontent.com/{seed}=w{s}-h{s}-l90-rj", "width": s, "height": s} for s in sizes]}


def browse(browse_id: str, page_type: str) -> Dict[str, Any]:
    return {"clickTrackingParams": "CAE=", "browseEndpoint": {"browseId": browse_id, "browseEndpointContextSupportedConfigs": {
        "browseEndpointContextMusicConfig": {"pageType": page_type}}}}


def watch(video_id: str, playlist_id: Optional[str] = None, params: Optional[str] = None, video_type: str = "MUSIC_VIDEO_TYPE_ATV") -> Dict[str, Any]:
    endpoint: Dict[str, Any] = {"videoId": video_id, "watchEndpointMusicSupportedConfigs": {"watchEndpointMusicConfig": {"musicVideoType": video_type}}}
    if playlist_id:
        endpoint["playlistId"] = playlist_id
    if params:
        endpoint["params"] = params
    return {"clickTrackingParams": "CAE=", "watchEndpoint": endpoint}


def watch_playlist(playlist_id: str, params: Optional[str] = None) -> Dict[str, Any]:
    endpoint: Dict[str, Any] = {"playlistId": playlist_id}
    if params:
        endpoint["params"] = params
    return {"clickTrackingParams": "CAE=", "watchPlaylistEndpoint": endpoint}


def pin_menu(pinned: Optional[bool]) -> Dict[str, Any]:
    items: List[Dict[str, Any]] = [
        {"menuNavigationItemRenderer": {"text": {"runs": [{"text": "Start radio"}]}, "icon": {"iconType": "MIX"}}},
        {"toggleMenuServiceItemRenderer": {"defaultIcon": {"iconType": "BOOKMARK_BORDER"}, "toggledIcon": {"iconType": "BOOKMARK"},
                                           "defaultServiceEndpoint": {"feedbackEndpoint": {"feedbackToken": "lib-add"}}}},
    ]
    if pinned is not None:
        default, toggled = ("KEEP_OFF", "KEEP") if pinned else ("KEEP", "KEEP_OFF")
        items.append({"toggleMenuServiceItemRenderer": {
            "defaultText": {"runs": [{"text": "Unpin from Speed dial" if pinned else "Pin to Speed dial"}]},
            "defaultIcon": {"iconType": default}, "toggledIcon": {"iconType": toggled},
            "defaultServiceEndpoint": {"feedbackEndpoint": {"feedbackToken": "pin-a"}},
            "toggledServiceEndpoint": {"feedbackEndpoint": {"feedbackToken": "pin-b"}}}})
    return {"menuRenderer": {"items": items}}


def tile(title: str, subtitle: List[str], navigation: Dict[str, Any], *, seed: str, play: Optional[Dict[str, Any]] = None,
         pinned: Optional[bool] = False, circle: bool = False) -> Dict[str, Any]:
    runs: List[Dict[str, Any]] = []
    for index, text in enumerate(subtitle):
        if index:
            runs.append({"text": " • "})
        runs.append({"text": text})
    renderer: Dict[str, Any] = {
        "thumbnailRenderer": {"musicThumbnailRenderer": {"thumbnail": thumbs(seed),
                                                         "thumbnailCrop": "MUSIC_THUMBNAIL_CROP_CIRCLE" if circle else "MUSIC_THUMBNAIL_CROP_UNSPECIFIED",
                                                         "thumbnailScale": "MUSIC_THUMBNAIL_SCALE_ASPECT_FILL"}},
        "aspectRatio": "MUSIC_TWO_ROW_ITEM_THUMBNAIL_ASPECT_RATIO_SQUARE",
        "title": {"runs": [{"text": title, "navigationEndpoint": navigation}]},
        "subtitle": {"runs": runs},
        "navigationEndpoint": navigation,
        "trackingParams": "CAE=",
        "menu": pin_menu(pinned),
    }
    if play:
        renderer["thumbnailOverlay"] = {"musicItemThumbnailOverlayRenderer": {
            "background": {"verticalGradient": {"gradientLayerColors": ["0", "0"]}},
            "content": {"musicPlayButtonRenderer": {"playNavigationEndpoint": play, "playIcon": {"iconType": "PLAY_ARROW"}}},
            "contentPosition": "MUSIC_ITEM_THUMBNAIL_OVERLAY_CONTENT_POSITION_CENTERED", "displayStyle": "MUSIC_ITEM_THUMBNAIL_OVERLAY_DISPLAY_STYLE_PERSISTENT"}}
    return {"musicTwoRowItemRenderer": renderer}


def speed_dial_tiles() -> List[Dict[str, Any]]:
    """One tile of every kind, in the order the tests expect."""
    return [
        tile("Gravity", ["Song", "yetep"], watch("EludZd6lfts", "RDAMVMEludZd6lfts"), seed="song", pinned=True),
        tile("Sentiment", ["Album", "Chill Satellite"], browse("MPREb_QtqXtd2xZMR", ALBUM), seed="album",
             play=watch_playlist("OLAK5uy_album000000000000000000000000000", "wAEB")),
        tile("Late Night Drive", ["Playlist", "Test User", "42 songs"], browse("VLPLz7-xrYmULdSLRZGk-6GKUtaBZcgQNwel", PLAYLIST), seed="pl",
             play=watch_playlist("PLz7-xrYmULdSLRZGk-6GKUtaBZcgQNwel")),
        tile("Chill Satellite", ["Artist", "374K subscribers"], browse("UCrPLFBWdOroD57bkqPbZJog", ARTIST), seed="artist",
             play=watch_playlist("RDEMrPLFBWdOroD57bkqPbZJog", "wAEB"), circle=True, pinned=True),
        tile("My Supermix", ["Mix", "yetep, Chill Satellite and more"], watch("bR5l0hJDnX8", "RDTMAK5uy_supermix0000000000000000000000", "wAEB8gECAQ%3D%3D"),
             seed="supermix"),
        tile("Two Steps From Hell Radio", ["Station", "Two Steps From Hell"], watch_playlist("RDEMtwosteps0000000000"), seed="station"),
        tile("Chill Hits", ["Playlist", "YouTube Music"], browse("VLRDCLAK5uy_chillhits000000000000000000", PLAYLIST), seed="curated"),
        tile("The Daily Show", ["Podcast", "Test Network"], browse("MPSPPLtestpodcast00000000000000000000", PODCAST), seed="podcast"),
        tile("Episode 42: Keys", ["Episode", "The Daily Show"], watch("ep42videoid", video_type=EPISODE_VIDEO), seed="episode"),
        tile("Saved Artist", ["Artist"], browse("MPLAUCsavedartist00000000000", LIBRARY_ARTIST), seed="libartist", circle=True),
        tile("Daily Mix 1", ["Mix"], browse("VLRDAMPLsomemix000000000000", PLAYLIST), seed="dailymix"),
    ]


def speed_dial_shelf(tiles: Optional[List[Dict[str, Any]]] = None, *, title: str = "Speed dial", per_column: str = "3",
                     avatar: bool = True, strapline: bool = True) -> Dict[str, Any]:
    header: Dict[str, Any] = {"title": {"runs": [{"text": title}]}, "headerStyle": "MUSIC_CAROUSEL_SHELF_HEADER_STYLE_DEFAULT"}
    if strapline:
        header["strapline"] = {"runs": [{"text": "Test User"}]}
    if avatar:
        header["thumbnail"] = {"musicThumbnailRenderer": {"thumbnail": thumbs("avatar", (88,)), "thumbnailCrop": "MUSIC_THUMBNAIL_CROP_CIRCLE"}}
    return {"musicCarouselShelfRenderer": {
        "header": {"musicCarouselShelfBasicHeaderRenderer": header},
        "contents": speed_dial_tiles() if tiles is None else tiles,
        "trackingParams": "CAE=",
        "itemSize": "COLLECTION_STYLE_ITEM_SIZE_SMALL",
        "numItemsPerColumn": per_column,
    }}


def quick_picks() -> Dict[str, Any]:
    def row(video_id: str, title: str) -> Dict[str, Any]:
        return {"musicResponsiveListItemRenderer": {
            "flexColumns": [{"musicResponsiveListItemFlexColumnRenderer": {"text": {"runs": [{"text": title, "navigationEndpoint": watch(video_id)}]}}}],
            "overlay": {"musicItemThumbnailOverlayRenderer": {"content": {"musicPlayButtonRenderer": {
                "playNavigationEndpoint": watch(video_id, "RDAMVM" + video_id)}}}},
            "menu": pin_menu(None)}}
    return {"musicCarouselShelfRenderer": {
        "header": {"musicCarouselShelfBasicHeaderRenderer": {"title": {"runs": [{"text": "Quick picks"}]}, "strapline": {"runs": [{"text": "Start radio from a song"}]}}},
        "contents": [row(f"qp{index:09d}", f"Quick pick {index}") for index in range(8)],
        "numItemsPerColumn": "4"}}


def plain_shelf(title: str, *, avatar: bool = False, strapline: Optional[str] = None) -> Dict[str, Any]:
    header: Dict[str, Any] = {"title": {"runs": [{"text": title}]}}
    if strapline:
        header["strapline"] = {"runs": [{"text": strapline}]}
    if avatar:
        header["thumbnail"] = {"musicThumbnailRenderer": {"thumbnail": thumbs("similar", (88,))}}
    tiles = [tile(f"{title} album {index}", ["Album", "Someone"], browse(f"MPREb_{title[:4]}{index:05d}", ALBUM), seed=f"{title}{index}", pinned=None)
             for index in range(6)]
    return {"musicCarouselShelfRenderer": {"header": {"musicCarouselShelfBasicHeaderRenderer": header}, "contents": tiles}}


def home(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {"responseContext": {"serviceTrackingParams": []}, "contents": {"singleColumnBrowseResultsRenderer": {"tabs": [{"tabRenderer": {
        "endpoint": {"browseEndpoint": {"browseId": "FEmusic_home"}}, "title": "Home", "selected": True,
        "content": {"sectionListRenderer": {"contents": copy.deepcopy(rows), "trackingParams": "CAE="}}}}]}}}


def full_home() -> Dict[str, Any]:
    """Speed dial first, then the shelves that must not be taken for it."""
    return home([
        speed_dial_shelf(),
        quick_picks(),
        plain_shelf("More from Chill Satellite", avatar=True, strapline="SIMILAR TO"),
        plain_shelf("Recommended albums"),
    ])
