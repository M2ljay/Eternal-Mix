"""YouTube Music's "Speed dial" shelf: find it in the raw home feed and route a tile to playback.

Pure logic, no network: ytmusic_service.YouTubeMusicService.get_speed_dial sends the
home browse request (``FEmusic_home``) with the signed-in client and hands the raw
response here.

Why raw: ytmusicapi's get_home (parsers/browsing.parse_mixed_content) keeps only a
shelf's title and parsed items. It drops what tells Speed dial apart from the other
shelves (the header's avatar and strapline, ``numItemsPerColumn``, the pin menu on each
tile), and it skips any shelf whose body has no ``contents`` (a ``gridRenderer`` keeps
its tiles under ``items``). The shelf title is localised, so it is a hint, not the key.

Known response shape (no live capture in this repository yet; see tests/test_speed_dial.py):
  - Speed dial is the first shelf of the home section list: a
    ``musicCarouselShelfRenderer`` with ``numItemsPerColumn`` "3" (it replaced
    "Listen again", which used "2"), a ``musicCarouselShelfBasicHeaderRenderer`` with
    the account avatar in ``thumbnail`` and the account name in ``strapline``, and
    27 ``musicTwoRowItemRenderer`` tiles. Two independent YouTube Music clients
    identify it this way: Mochy-Music's innertube HomePage.kt (numItemsPerColumn >= 3,
    a header thumbnail, all two-row tiles) and Prism's innertube Parser.kt (stacked
    card shelves, and ``gridRenderer`` shelves read from ``items``).
  - Tiles use ytmusicapi's two-row item layout (navigation.py: TITLE, SUBTITLE_RUNS,
    THUMBNAIL_RENDERER, THUMBNAIL_OVERLAY_NAVIGATION, MENU_ITEMS). A pinned tile's menu
    offers "unpin" (``toggleMenuServiceItemRenderer`` with icon ``KEEP_OFF``), an
    unpinned one offers "pin" (``KEEP``); ytmusicapi reads the same icons in
    parsers/songs.parse_song_menu_data as ``pinnedToListenAgain``. Only Speed dial and
    Listen again tiles carry that menu entry.

When the feed has no Speed dial (the web client often shows "Listen again" and
"Quick picks" instead; Speed dial may exist only in the mobile apps), select_grid falls
back to the closest shelf of the same response: Listen again (stacked two-row tiles of
mixed kinds from the listening history, near the top), then Quick picks (list rows of
songs). ytmusic_service adds the last fallback, the user's library.

summarize_home describes a response's structure for the log (renderer types, header
features, item counts) and never carries a title, name, id, thumbnail or account detail.

Standard library only (typing is in Decky's frozen Python).
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

MTRIR = "musicTwoRowItemRenderer"
MRLIR = "musicResponsiveListItemRenderer"
CAROUSEL = "musicCarouselShelfRenderer"
GRID = "gridRenderer"

# English names of the shelf. The client asks for hl=en, so the title is normally one of
# these, but a title is only one signal among several (see shelf_score).
KNOWN_TITLES = ("speed dial", "listen again")

# A shelf needs at least this score to be taken for Speed dial.
MIN_SCORE = 5
# Listen again and Quick picks sit among the first shelves of the home feed.
TOP_ROWS = 6

SOURCE_SPEED_DIAL = "speed_dial"
SOURCE_LISTEN_AGAIN = "listen_again"
SOURCE_QUICK_PICKS = "quick_picks"
SOURCE_LIBRARY = "library"
SOURCES = (SOURCE_SPEED_DIAL, SOURCE_LISTEN_AGAIN, SOURCE_QUICK_PICKS, SOURCE_LIBRARY)

# English shelf names (the client asks for hl=en). For the log they become these fixed
# keys, never the text itself.
TITLE_KEYS = {"speed dial": SOURCE_SPEED_DIAL, "listen again": SOURCE_LISTEN_AGAIN, "quick picks": SOURCE_QUICK_PICKS}
# The full grid on YouTube Music: 3 rows of 9.
MAX_ITEMS = 27

SHUFFLE_PARAMS = {"wAEB8gECKAE%3D", "wAEB8gECKAE="}
RADIO_PARAMS = {"wAEB"}

ITEM_TYPES = ("song", "album", "playlist", "artist", "mix", "podcast", "episode")
# Types the frontend plays through its existing detail paths (album/playlist order,
# podcast resume); the others need a queue from the backend.
QUEUE_TYPES = ("song", "mix", "artist")


def _nav(value: Any, *path: Any) -> Any:
    for key in path:
        if isinstance(key, int):
            if not isinstance(value, list) or not -len(value) <= key < len(value):
                return None
            value = value[key]
        else:
            if not isinstance(value, dict):
                return None
            value = value.get(key)
        if value is None:
            return None
    return value


def _runs_text(value: Any) -> str:
    runs = _nav(value, "runs")
    if isinstance(runs, list):
        return "".join(str(run.get("text") or "") for run in runs if isinstance(run, dict)).strip()
    return str(_nav(value, "simpleText") or "").strip()


def _str(value: Any) -> str:
    return str(value or "").strip() if isinstance(value, (str, int)) else ""


def _thumbnails(renderer: Any) -> List[Dict[str, Any]]:
    value = _nav(renderer, "thumbnailRenderer", "musicThumbnailRenderer", "thumbnail", "thumbnails")
    if not isinstance(value, list):
        value = _nav(renderer, "thumbnail", "musicThumbnailRenderer", "thumbnail", "thumbnails")
    return [entry for entry in value if isinstance(entry, dict) and entry.get("url")] if isinstance(value, list) else []


def _menu_icons(renderer: Any) -> List[str]:
    icons: List[str] = []
    items = _nav(renderer, "menu", "menuRenderer", "items")
    for item in items if isinstance(items, list) else []:
        entry = _nav(item, "toggleMenuServiceItemRenderer") or _nav(item, "menuServiceItemRenderer")
        icon = _nav(entry, "defaultIcon", "iconType") or _nav(entry, "icon", "iconType")
        if isinstance(icon, str):
            icons.append(icon)
    return icons


def pin_state(renderer: Any) -> Optional[bool]:
    """True when pinned, False when it can be pinned, None when the tile has no pin entry."""
    icons = _menu_icons(renderer)
    if "KEEP_OFF" in icons:
        return True
    if "KEEP" in icons:
        return False
    return None


def _endpoint(value: Any) -> Dict[str, str]:
    """videoId / playlistId / params of a watch or watch-playlist endpoint."""
    watch = _nav(value, "watchEndpoint")
    if isinstance(watch, dict):
        return {
            "videoId": _str(watch.get("videoId")),
            "playlistId": _str(watch.get("playlistId")),
            "params": _str(watch.get("params")),
            "videoType": _str(_nav(watch, "watchEndpointMusicSupportedConfigs", "watchEndpointMusicConfig", "musicVideoType")),
        }
    playlist = _nav(value, "watchPlaylistEndpoint")
    if isinstance(playlist, dict):
        return {"videoId": "", "playlistId": _str(playlist.get("playlistId")), "params": _str(playlist.get("params")), "videoType": ""}
    return {}


def _strip_vl(value: str) -> str:
    return value[2:] if value.startswith("VL") else value


def _is_mix_playlist(playlist_id: str) -> bool:
    # RD… lists are radios and mixes (RDAMVM song radio, RDEM artist radio, RDTMAK
    # supermix). RDCLAK lists are YouTube Music's curated playlists, played in order.
    return playlist_id.startswith("RD") and not playlist_id.startswith("RDCLAK")


def parse_tile(renderer: Any) -> Optional[Dict[str, Any]]:
    """One Speed dial tile (a musicTwoRowItemRenderer) as a plain item, or None."""
    if not isinstance(renderer, dict):
        return None
    title = _runs_text(renderer.get("title"))
    subtitle = _runs_text(renderer.get("subtitle"))
    navigation = renderer.get("navigationEndpoint")
    play = _endpoint(_nav(renderer, "thumbnailOverlay", "musicItemThumbnailOverlayRenderer", "content",
                          "musicPlayButtonRenderer", "playNavigationEndpoint"))
    item: Dict[str, Any] = {
        "type": "",
        "id": "",
        "title": title,
        "subtitle": subtitle,
        "thumbnails": _thumbnails(renderer),
        "browseId": "",
        "videoId": "",
        "playlistId": "",
        "params": "",
        "pinned": pin_state(renderer) is True,
    }
    watch = _endpoint(navigation)
    browse = _nav(navigation, "browseEndpoint")
    if watch:
        video_id, playlist_id = watch["videoId"], watch["playlistId"]
        item.update(videoId=video_id, playlistId=playlist_id, params=watch["params"])
        if video_id and watch["videoType"] == "MUSIC_VIDEO_TYPE_PODCAST_EPISODE":
            item.update(type="episode", id=video_id)
        elif playlist_id and (not video_id or (_is_mix_playlist(playlist_id) and playlist_id != "RDAMVM" + video_id)):
            # A station or mix: a radio list, sometimes seeded by a song. A song tile
            # carries no list or its own song radio (RDAMVM + its id).
            item.update(type="mix", id=playlist_id)
        elif video_id:
            item.update(type="song", id=video_id)
    elif isinstance(browse, dict):
        browse_id = _str(browse.get("browseId"))
        page_type = _str(_nav(browse, "browseEndpointContextSupportedConfigs", "browseEndpointContextMusicConfig", "pageType"))
        item["browseId"] = browse_id
        if page_type in ("MUSIC_PAGE_TYPE_ALBUM", "MUSIC_PAGE_TYPE_AUDIOBOOK") or browse_id.startswith("MPRE"):
            item.update(type="album", id=browse_id)
        elif page_type in ("MUSIC_PAGE_TYPE_ARTIST", "MUSIC_PAGE_TYPE_USER_CHANNEL", "MUSIC_PAGE_TYPE_LIBRARY_ARTIST") or browse_id.startswith(("UC", "MPLA")):
            channel = browse_id[4:] if browse_id.startswith("MPLA") else browse_id
            item.update(type="artist", id=channel, browseId=channel)
        elif page_type == "MUSIC_PAGE_TYPE_PODCAST_SHOW_DETAIL_PAGE" or browse_id.startswith("MPSP"):
            item.update(type="podcast", id=browse_id)
        elif page_type == "MUSIC_PAGE_TYPE_PLAYLIST" or browse_id.startswith("VL"):
            playlist_id = _strip_vl(browse_id)
            item.update(type="mix" if _is_mix_playlist(playlist_id) else "playlist", id=playlist_id, playlistId=playlist_id)
        if play:
            # The tile's play button: what YouTube Music itself starts from the tile.
            item["videoId"] = item["videoId"] or play["videoId"]
            if item["type"] in ("artist", "mix"):
                item["playlistId"] = play["playlistId"] or item["playlistId"]
                item["params"] = play["params"]
    if item["type"] not in ITEM_TYPES or not item["id"] or not title:
        return None
    return item


def _shelf_body(row: Any) -> Tuple[str, Dict[str, Any], List[Any]]:
    if not isinstance(row, dict):
        return "", {}, []
    body = row.get(CAROUSEL)
    if isinstance(body, dict):
        contents = body.get("contents")
        return CAROUSEL, body, contents if isinstance(contents, list) else []
    body = row.get(GRID)
    if isinstance(body, dict):
        items = body.get("items")
        return GRID, body, items if isinstance(items, list) else []
    return "", {}, []


def shelf_title(row: Any) -> str:
    kind, body, _ = _shelf_body(row)
    if kind == CAROUSEL:
        return _runs_text(_nav(body, "header", "musicCarouselShelfBasicHeaderRenderer", "title"))
    if kind == GRID:
        return _runs_text(_nav(body, "header", "gridHeaderRenderer", "title"))
    return ""


def _image_base(url: Any) -> str:
    """An image URL without its query and its "=s88-..." size suffix, for comparing avatars."""
    text = str(url or "").strip().split("?", 1)[0].split("#", 1)[0]
    if not text.startswith("https://"):
        return ""
    head, _, tail = text.rpartition("/")
    if "=" in tail:
        tail = tail.split("=", 1)[0]
    return (head + "/" + tail).casefold() if tail else ""


def _header(row: Any) -> Dict[str, Any]:
    _kind, body, _entries = _shelf_body(row)
    header = _nav(body, "header", "musicCarouselShelfBasicHeaderRenderer")
    return header if isinstance(header, dict) else {}


def account_header_match(row: Any, account: Optional[Dict[str, Any]]) -> str:
    """How a shelf's header is the signed-in account's own: "name" (the strapline is the
    account name), "avatar" (the header thumbnail is the account photo) or "" (it is not).
    The account (accountName / accountPhotoUrl from get_account_info) is compared in
    memory only. A strapline alone proves nothing: seasonal and editorial shelves have one."""
    if not isinstance(account, dict):
        return ""
    header = _header(row)
    name = " ".join(str(account.get("name") or "").split()).casefold()
    strapline = " ".join(_runs_text(header.get("strapline")).split()).casefold()
    if name and strapline and strapline == name:
        return "name"
    photo = _image_base(account.get("photo"))
    if photo:
        for thumbnail in _thumbnails(header):
            if _image_base(thumbnail.get("url") if isinstance(thumbnail, dict) else "") == photo:
                return "avatar"
    return ""


def _pin_capable(row: Any) -> bool:
    """Most tiles carry YouTube Music's pin entry: only Speed dial and Listen again do."""
    _kind, _body, entries = _shelf_body(row)
    tiles = [entry.get(MTRIR) for entry in entries if isinstance(entry, dict) and isinstance(entry.get(MTRIR), dict)]
    return bool(tiles) and sum(1 for tile in tiles if pin_state(tile) is not None) * 2 >= len(tiles)


def shelf_score(row: Any, account: Optional[Dict[str, Any]] = None) -> int:
    """How much a home shelf looks like Speed dial; 0 when it cannot be.

    Needs one stable sign that the shelf is the user's own: the known title, pin entries on
    the tiles, or the account's own header (account_header_match). Layout alone (tiles per
    column, a grid) never qualifies a shelf, so seasonal and editorial shelves never do."""
    kind, body, entries = _shelf_body(row)
    if not kind:
        return 0
    tiles = [entry.get(MTRIR) for entry in entries if isinstance(entry, dict) and isinstance(entry.get(MTRIR), dict)]
    # Quick picks and other song-row shelves use list rows, never two-row tiles.
    if len(tiles) < 2 or len(tiles) * 5 < len(entries) * 4:
        return 0
    own = account_header_match(row, account)
    pins = _pin_capable(row)
    titled = shelf_title(row).casefold() in KNOWN_TITLES
    if not (own or pins or titled):
        return 0
    score = 0
    per_column = _per_column(body)
    if per_column >= 3:
        score += 3
    elif per_column == 2:
        score += 2
    if kind == GRID:
        score += 2
    if own:
        score += 3
    if pins:
        score += 3
    if titled:
        score += 3
    return score


def home_rows(response: Any) -> List[Any]:
    """The home feed's shelves, in order: the usual section list, else any shelf found in it."""
    tabs = _nav(response, "contents", "singleColumnBrowseResultsRenderer", "tabs")
    for tab in tabs if isinstance(tabs, list) else []:
        rows = _nav(tab, "tabRenderer", "content", "sectionListRenderer", "contents")
        if isinstance(rows, list):
            return rows
    # Other clients (the mobile one used for OAuth) wrap the list differently: walk it.
    found: List[Any] = []

    def walk(value: Any, depth: int) -> None:
        if depth > 14 or len(found) > 60:
            return
        if isinstance(value, dict):
            if CAROUSEL in value or GRID in value:
                found.append(value)
                return
            for child in value.values():
                walk(child, depth + 1)
        elif isinstance(value, list):
            for child in value:
                walk(child, depth + 1)

    walk(response, 0)
    return found


def extract_speed_dial(response: Any, max_items: int = MAX_ITEMS, account: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """{"found", "title", "score", "items"} for the Speed dial shelf of a raw home response."""
    best: Optional[Tuple[int, Any]] = None
    for row in home_rows(response):
        score = shelf_score(row, account)
        if score >= MIN_SCORE and _speed_dial_layout(row) and (best is None or score > best[0]):
            best = (score, row)
    if best is None:
        return {"found": False, "title": "", "score": 0, "items": []}
    score, row = best
    return {"found": True, "title": shelf_title(row), "score": score, "items": _tiles(row, max_items)}


def _per_column(body: Dict[str, Any]) -> int:
    try:
        return int(str(body.get("numItemsPerColumn") or "1"))
    except ValueError:
        return 1


def _title_key(row: Any) -> str:
    return TITLE_KEYS.get(shelf_title(row).casefold(), "")


def _speed_dial_layout(row: Any) -> bool:
    """Speed dial's own layout: 3 or more tiles per column or a grid (Listen again stacks 2)."""
    kind, body, _ = _shelf_body(row)
    key = _title_key(row)
    if key == SOURCE_LISTEN_AGAIN:
        return False
    return kind == GRID or _per_column(body) >= 3 or key == SOURCE_SPEED_DIAL


def _dedupe(parsed: List[Optional[Dict[str, Any]]], max_items: int) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    seen = set()
    for item in parsed:
        if not item or (item["type"], item["id"]) in seen:
            continue
        seen.add((item["type"], item["id"]))
        items.append(item)
        if len(items) >= max_items:
            break
    return items


def _tiles(row: Any, max_items: int) -> List[Dict[str, Any]]:
    _, _, entries = _shelf_body(row)
    return _dedupe([parse_tile(entry.get(MTRIR) if isinstance(entry, dict) else None) for entry in entries], max_items)


# How many continuation pages of the home feed are read, at most, looking for a shelf.
MAX_CONTINUATION_PAGES = 2
# Sources that come from the listening history: once one is shown, a later answer that
# only has Quick picks or the library does not replace it (ytmusic_service keeps it).
GOOD_SOURCES = (SOURCE_SPEED_DIAL, SOURCE_LISTEN_AGAIN)


def _two_row_tiles(row: Any) -> int:
    _kind, _body, entries = _shelf_body(row)
    return sum(1 for entry in entries if isinstance(entry, dict) and isinstance(entry.get(MTRIR), dict))


def _strapline(row: Any) -> str:
    _kind, body, _entries = _shelf_body(row)
    header = _nav(body, "header", "musicCarouselShelfBasicHeaderRenderer") or {}
    return _runs_text(header.get("strapline") if isinstance(header, dict) else None)


def is_account_shelf(row: Any, account: Optional[Dict[str, Any]] = None) -> bool:
    """The web client's account-headed shelf, in any position: two-row tiles under the
    signed-in account's own header (account_header_match). Never Quick picks."""
    kind, _body, entries = _shelf_body(row)
    if kind != CAROUSEL or _title_key(row) == SOURCE_QUICK_PICKS:
        return False
    tiles = _two_row_tiles(row)
    return tiles >= 2 and tiles * 5 >= len(entries) * 4 and bool(account_header_match(row, account))


def listen_again_score(row: Any) -> int:
    """How much a shelf looks like Listen again; 0 when it cannot be.

    Only by stable signs: the known title, or pin entries on most tiles (YouTube Music
    offers "pin" only on Speed dial and Listen again tiles). A header strapline or avatar
    counts for nothing (seasonal and editorial shelves have them). Its tiles are of mixed
    kinds (albums, playlists, artists, songs); stacked two per column adds weight.
    """
    kind, body, entries = _shelf_body(row)
    if not kind:
        return 0
    tiles = [entry.get(MTRIR) for entry in entries if isinstance(entry, dict) and isinstance(entry.get(MTRIR), dict)]
    if len(tiles) < 2 or len(tiles) * 5 < len(entries) * 4:
        return 0
    kinds = {item["type"] for item in (parse_tile(tile) for tile in tiles) if item}
    if len(kinds) < 2:
        return 0
    titled = _title_key(row) == SOURCE_LISTEN_AGAIN
    pins = _pin_capable(row)
    if not (titled or pins):
        return 0
    score = 0
    if _per_column(body) == 2:
        score += 2
    if pins:
        score += 3
    if titled:
        score += 3
    return score


def parse_list_row(renderer: Any) -> Optional[Dict[str, Any]]:
    """One Quick picks row (a musicResponsiveListItemRenderer) as a song item, or None."""
    if not isinstance(renderer, dict):
        return None
    columns = renderer.get("flexColumns") if isinstance(renderer.get("flexColumns"), list) else []
    texts = [_nav(column, "musicResponsiveListItemFlexColumnRenderer", "text") for column in columns]
    title = _runs_text(texts[0]) if texts else ""
    subtitle = " • ".join(text for text in (_runs_text(value) for value in texts[1:]) if text)
    play = _endpoint(_nav(renderer, "overlay", "musicItemThumbnailOverlayRenderer", "content",
                          "musicPlayButtonRenderer", "playNavigationEndpoint"))
    title_runs = _nav(texts[0], "runs") if texts else None
    title_watch = _endpoint(_nav(title_runs, 0, "navigationEndpoint")) if isinstance(title_runs, list) else {}
    video_id = (_str(_nav(renderer, "playlistItemData", "videoId")) or play.get("videoId", "")
                or title_watch.get("videoId", ""))
    if not video_id or not title:
        return None
    video_type = play.get("videoType") or title_watch.get("videoType") or ""
    return {
        "type": "episode" if video_type == "MUSIC_VIDEO_TYPE_PODCAST_EPISODE" else "song",
        "id": video_id,
        "title": title,
        "subtitle": subtitle,
        "thumbnails": _thumbnails(renderer),
        "browseId": "",
        "videoId": video_id,
        "playlistId": "",
        "params": "",
        "pinned": False,
    }


def quick_picks_score(row: Any) -> int:
    """How much a shelf looks like Quick picks (a carousel of song rows); 0 when it cannot be."""
    kind, body, entries = _shelf_body(row)
    if kind != CAROUSEL:
        return 0
    rows = [entry for entry in entries if isinstance(entry, dict) and isinstance(entry.get(MRLIR), dict)]
    if len(rows) < 2 or len(rows) * 5 < len(entries) * 4:
        return 0
    score = 0
    if _per_column(body) >= 3:
        score += 2
    if _title_key(row) == SOURCE_QUICK_PICKS:
        score += 3
    return score


def _quick_pick_items(row: Any, max_items: int) -> List[Dict[str, Any]]:
    _, _, entries = _shelf_body(row)
    return _dedupe([parse_list_row(entry.get(MRLIR) if isinstance(entry, dict) else None) for entry in entries], max_items)


def select_grid(response: Any, max_items: int = MAX_ITEMS, account: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """select_rows over one raw home response."""
    return select_rows(home_rows(response), max_items, account)


def select_rows(rows: List[Any], max_items: int = MAX_ITEMS, account: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """The grid to show from the home feed's shelves (in order), with the fallback chain.

    {"found", "source", "title", "score", "items", "checked", "index", "why"}:
      1. Speed dial (its own layout: 3 tiles per column or a grid);
      2. else the first shelf under the signed-in account's own header, in any position
         (source speed_dial: on the web client it is the Speed dial's place, laid out one
         tile per column). `account` ({"name", "photo"}) is compared in memory only;
         without it this step finds nothing, never a shelf that merely has a strapline;
      3. else Listen again, else Quick picks (both among the first shelves).
    "checked" says, per source, whether a candidate shelf was seen (a shelf whose tiles all
    failed to parse is seen but gives no items). "index" is the chosen shelf's position in
    `rows` (-1 when none) and "why" a fixed phrase for the log. source is "" when nothing
    fits; the library fallback is ytmusic_service's.
    """
    checked = {SOURCE_SPEED_DIAL: False, SOURCE_LISTEN_AGAIN: False, SOURCE_QUICK_PICKS: False}

    def chosen(source: str, index: int, score: int, items: List[Dict[str, Any]], why: str) -> Dict[str, Any]:
        return {"found": True, "source": source, "title": shelf_title(rows[index]), "score": score,
                "items": items, "checked": checked, "index": index, "why": why}

    best: Optional[Tuple[int, int]] = None
    for index, row in enumerate(rows):
        score = shelf_score(row, account)
        if score >= MIN_SCORE and _speed_dial_layout(row) and (best is None or score > best[0]):
            best = (score, index)
    if best is not None:
        checked[SOURCE_SPEED_DIAL] = True
        items = _tiles(rows[best[1]], max_items)
        if items:
            return chosen(SOURCE_SPEED_DIAL, best[1], best[0], items, "speed dial layout")
    for index, row in enumerate(rows):
        if not is_account_shelf(row, account):
            continue
        how = account_header_match(row, account)
        # Named Listen again (or carrying its pin entries), it stays Listen again: the hint says so.
        source = SOURCE_LISTEN_AGAIN if (_title_key(row) == SOURCE_LISTEN_AGAIN or listen_again_score(row) >= 3) else SOURCE_SPEED_DIAL
        checked[source] = True
        items = _tiles(row, max_items)
        if items:
            why = "account-headed shelf (strapline is the account name)" if how == "name" else "account-headed shelf (account avatar)"
            return chosen(source, index, shelf_score(row, account), items, why)
    top = rows[:TOP_ROWS]
    for source, scorer, reader, minimum, why in (
            (SOURCE_LISTEN_AGAIN, listen_again_score, _tiles, 3, "listen again shelf"),
            (SOURCE_QUICK_PICKS, quick_picks_score, _quick_pick_items, 2, "quick picks shelf")):
        found: Optional[Tuple[int, int]] = None
        for index, row in enumerate(top):
            score = scorer(row)
            if score >= minimum and (found is None or score > found[0]):
                found = (score, index)
        if found is None:
            continue
        checked[source] = True
        items = reader(rows[found[1]], max_items)
        if items:
            return chosen(source, found[1], found[0], items, why)
    return {"found": False, "source": "", "title": "", "score": 0, "items": [], "checked": checked, "index": -1, "why": ""}


def is_parser_error(exc: BaseException) -> bool:
    """ytmusicapi's navigation failures: a response shape it cannot read, not a network fault."""
    text = str(exc)
    return text.startswith("Unable to find") or isinstance(exc, (KeyError, IndexError)) or "using path [" in text


def short_error(exc: BaseException, limit: int = 160) -> str:
    """An exception's text for the log, cut before any echoed response body."""
    text = " ".join(str(exc).split())
    for marker in (" on {", " on ["):
        if marker in text:
            return text[:text.index(marker)][:limit] + " on <response>"
    return text[:limit]


def continuation_token(response: Any) -> str:
    """The token for the home feed's next page: from the first page's section list or from
    a continuation page; "" when there is none."""
    lists = (
        _nav(response, "contents", "singleColumnBrowseResultsRenderer", "tabs", 0, "tabRenderer", "content", "sectionListRenderer"),
        _nav(response, "continuationContents", "sectionListContinuation"),
    )
    for section in lists:
        token = _str(_nav(section, "continuations", 0, "nextContinuationData", "continuation"))
        if token:
            return token
    return ""


def continuation_rows(response: Any) -> List[Any]:
    """The shelves of a home continuation page."""
    rows = _nav(response, "continuationContents", "sectionListContinuation", "contents")
    return rows if isinstance(rows, list) else []


def _renderer_name(value: Any) -> str:
    if isinstance(value, dict):
        for key in value:
            if isinstance(key, str) and key.endswith("Renderer"):
                return key
        for key in value:
            if isinstance(key, str):
                return key[:60]
    return type(value).__name__


def _wrapper_name(response: Any) -> str:
    if _nav(response, "contents", "singleColumnBrowseResultsRenderer", "tabs"):
        return "singleColumnBrowseResultsRenderer"
    if _nav(response, "contents", "twoColumnBrowseResultsRenderer"):
        return "twoColumnBrowseResultsRenderer"
    contents = _nav(response, "contents")
    return _renderer_name(contents) if contents is not None else "none"


def summarize_home(response: Any, max_rows: int = 12) -> Dict[str, Any]:
    """The structure of a home response, safe to log.

    Per top-level shelf: its renderer type, a set of header-feature flags, items per
    column, the item count, the item renderer types with counts, and how many items
    carry a pin entry. A known English shelf name appears only as a fixed key
    (title=listen_again); no text, id, URL or account detail is ever copied.
    """
    rows = home_rows(response)
    summary_rows: List[Dict[str, Any]] = []
    for row in rows[:max_rows]:
        kind, body, entries = _shelf_body(row)
        renderer = kind or _renderer_name(row)
        flags: List[str] = []
        header = (_nav(body, "header", "musicCarouselShelfBasicHeaderRenderer")
                  or _nav(body, "header", "gridHeaderRenderer") or {}) if kind else {}
        if isinstance(header, dict) and header:
            if _runs_text(header.get("title")):
                flags.append("title")
            if _runs_text(header.get("strapline")):
                flags.append("strapline")
            if _nav(header, "thumbnail"):
                flags.append("avatar")
            if _nav(header, "moreContentButton"):
                flags.append("more")
        key = _title_key(row) if kind else ""
        if key:
            flags.append("title=" + key)
        types: Dict[str, int] = {}
        pins = 0
        for entry in entries:
            name = _renderer_name(entry)
            types[name] = types.get(name, 0) + 1
            inner = entry.get(name) if isinstance(entry, dict) else None
            if pin_state(inner) is not None:
                pins += 1
        summary_rows.append({
            "renderer": renderer,
            "header": flags,
            "perColumn": _per_column(body) if kind else 0,
            "items": len(entries),
            "itemTypes": types,
            "pins": pins,
        })
    continuation = bool(_nav(response, "contents", "singleColumnBrowseResultsRenderer", "tabs", 0, "tabRenderer",
                             "content", "sectionListRenderer", "continuations"))
    return {"wrapper": _wrapper_name(response), "rows": len(rows), "continuation": continuation, "shelves": summary_rows}


def format_summary(summary: Dict[str, Any]) -> str:
    """summarize_home's answer on one log line."""
    parts = [f"wrapper={summary.get('wrapper')} rows={summary.get('rows')} continuation={'yes' if summary.get('continuation') else 'no'}"]
    for index, row in enumerate(summary.get("shelves") or []):
        types = ",".join(f"{name}:{count}" for name, count in sorted((row.get("itemTypes") or {}).items()))
        parts.append(f"#{index} {row.get('renderer')}[{','.join(row.get('header') or []) or '-'}] perColumn={row.get('perColumn')} "
                     f"items={row.get('items')} ({types or '-'}) pins={row.get('pins')}")
    return " | ".join(parts)


def queue_request(item: Any) -> Dict[str, Any]:
    """How the backend builds the play queue for a song, mix or artist tile.

    {"mode": "watch", "videoId", "playlistId", "radio", "shuffle"} for a watch queue
    (ytmusicapi get_watch_playlist), {"mode": "artist", "channelId"} for an artist
    without a play button (artist radio, else top songs), {"mode": "none"} otherwise.
    """
    if not isinstance(item, dict):
        return {"mode": "none"}
    kind = _str(item.get("type"))
    video_id = _str(item.get("videoId"))
    playlist_id = _strip_vl(_str(item.get("playlistId")))
    params = _str(item.get("params"))
    if kind == "song":
        video_id = video_id or _str(item.get("id"))
        if not video_id:
            return {"mode": "none"}
        # Song radio, as YouTube Music starts it from a song: the song first, then RDAMVM + id.
        return {"mode": "watch", "videoId": video_id, "playlistId": "RDAMVM" + video_id, "radio": False, "shuffle": False}
    if kind == "mix":
        playlist_id = playlist_id or _strip_vl(_str(item.get("id")))
        if not playlist_id:
            return {"mode": "none"}
        return {"mode": "watch", "videoId": video_id, "playlistId": playlist_id,
                "radio": params in RADIO_PARAMS, "shuffle": params in SHUFFLE_PARAMS}
    if kind == "artist":
        if playlist_id or video_id:
            # The tile's own play button (usually the artist's radio or a shuffle of their songs).
            return {"mode": "watch", "videoId": video_id, "playlistId": playlist_id,
                    "radio": params in RADIO_PARAMS, "shuffle": params in SHUFFLE_PARAMS and bool(playlist_id)}
        channel = _str(item.get("browseId")) or _str(item.get("id"))
        return {"mode": "artist", "channelId": channel} if channel else {"mode": "none"}
    return {"mode": "none"}
