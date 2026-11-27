"""Find a game's official soundtrack album on YouTube Music.

Pure ranking logic, no network: ytmusic_service.YouTubeMusicService.find_soundtrack
runs the searches and hands the raw ytmusicapi results here.

A search *result* is never queued as a soundtrack. Only containers are
considered: albums (MPRE browse ids, YouTube Music's own releases) first, then
playlists. A container is played whole, in its own order, which for an album is
the release's track order.

Ideas and parts of the code (text normalisation, the store-edition suffix
pattern, the remix/cover word list, the DLC markers and the "official album
first" weighting) are adapted from DeckTune's soundtrack.py, by Jayden,
MIT License: https://github.com/Brick098TheFirst/Decktune (see NOTICE).

Standard library only (re, unicodedata, difflib, json, os are in Decky's frozen Python).
"""
from __future__ import annotations

import difflib
import json
import os
import re
import unicodedata
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

MAX_CANDIDATES = 3

# A top candidate at or above this score, with at least CONFIDENT_GAP over the
# runner-up, is played without asking. Otherwise up to three are offered.
CONFIDENT_SCORE = 120.0
CONFIDENT_GAP = 25.0
# Below this a container is not a plausible soundtrack of the game at all.
MIN_SCORE = 55.0

# Words that describe "a soundtrack" rather than name the game.
_SOUNDTRACK_WORDS = {
    "ost", "original", "official", "game", "video", "videogame", "soundtrack", "soundtracks",
    "score", "album", "music", "complete", "full", "edition", "version", "deluxe", "selection",
    "the", "from", "vol", "volume", "disc", "cd",
}
_STOPWORDS = {"the", "a", "an", "of", "and", "to", "in", "on", "for"}

# From DeckTune: words that mark an arrangement, not the game's own soundtrack.
_ARRANGEMENT_WORDS = (
    "remix", "remixed", "remixes", "reimagined", "cover", "covers", "tribute", "piano", "orchestral",
    "symphonic", "symphony", "lofi", "lo-fi", "metal", "karaoke", "8-bit", "8 bit", "chiptune",
    "extended", "slowed", "reverb", "ambience", "medley", "arrangement", "arrangements", "arranged",
    "fanmade", "fan-made", "fan made", "live", "concert", "acoustic", "jazz", "vocal collection",
    "guitar", "a cappella", "sleep", "relaxing", "study", "nightcore", "1 hour", "10 hours",
)
# DLC and bonus releases: only right when the game's own name says so.
_DLC_WORDS = (
    "dlc", "expansion", "b-sides", "b sides", "bonus", "season pass", "add-on", "addon", "episode",
    "chapter", "prologue", "epilogue", "side story", "sidequel", "pack", "extras", "supplemental",
)
# A different version of the game: only right when the game's own name says so.
_VERSION_WORDS = ("remake", "remaster", "remastered", "reboot", "reimagined", "hd", "3d", "redux", "reloaded")
# Labels and publisher channels that release official game soundtracks.
_OFFICIAL_LABELS = (
    "square enix music", "sound team", "nintendo", "sega", "capcom", "atlus", "bandai namco",
    "konami", "laced records", "blizzard entertainment", "xbox music", "playstation", "riot games",
    "hoyo-mix", "hoyoverse", "fromsoftware", "bethesda", "ubisoft", "electronic arts", "ea games",
    "valve", "devolver", "annapurna", "team cherry", "supergiant games", "cd projekt", "bungie",
    "game music", "materia collective", "iam8bit", "data discs", "ship to shore", "brave wave",
)

_ROMAN = {
    "ii": "2", "iii": "3", "iv": "4", "v": "5", "vi": "6", "vii": "7", "viii": "8", "ix": "9",
    "x": "10", "xi": "11", "xii": "12", "xiii": "13", "xiv": "14", "xv": "15", "xvi": "16",
}

# No-Intro / TOSEC style tags in ROM names: (USA), (Rev 1), [!], (En,Fr,De), (Disc 1)...
_ROM_TAG = re.compile(r"\s*[\(\[][^\)\]]*[\)\]]")


def _collapse(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def normalize_text(value: Any) -> str:
    """Comparison form: accents off, casefolded, punctuation to spaces (DeckTune)."""
    text = unicodedata.normalize("NFKD", re.sub(r"[™®©]", "", str(value or "")))
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.casefold().replace("&", " and ")
    text = re.sub(r"[™®©]", "", text)
    # "B-Sides", "Lo-Fi", "Add-on": keep as one word.
    text = re.sub(r"(?<=\w)-(?=\w)", "", text)
    text = re.sub(r"[^\w]+", " ", text, flags=re.UNICODE)
    return _collapse(text.replace("_", " "))


def _numeral(token: str) -> str:
    return _ROMAN.get(token, token)


def canonical_game_name(raw: Any) -> str:
    """A search-safe title from a Steam display name or a ROM title.

    Sequel numbers and subtitles stay; store editions, trademark signs,
    executable syntax and ROM dump tags go. "Legend of Zelda, The" is turned
    back into "The Legend of Zelda".
    """
    name = unicodedata.normalize("NFKC", re.sub(r"[™®©]", "", str(raw or ""))).strip().strip("\"'")
    if "\\" in name or re.match(r"^[A-Za-z]:[/\\]", name) or re.search(r"\.(?:exe|lnk|bat|cmd)$", name, re.I):
        name = re.split(r"[\\/]", name)[-1]
    name = re.sub(r"\.(?:exe|lnk|bat|cmd|zip|7z|iso|chd|rvz|nsp|xci|cue|bin|sfc|smc|nes|gba|gbc|gb|n64|z64|nds|3ds)$", "", name, flags=re.I)
    name = re.sub(r"[™®©]", "", name)
    name = _ROM_TAG.sub("", name)
    name = re.sub(r"\s*[-–—:]\s*(?:steam|proton|wine)\s*$", "", name, flags=re.I)
    # From DeckTune, widened: "- Deluxe Edition", ": Game of the Year Edition", "Definitive Edition"...
    name = re.sub(
        r"\s*(?:[-–—:|]\s*)?(?:digital\s+)?(?:deluxe|ultimate|collector'?s?|complete|definitive|"
        r"enhanced|special|gold|premium|standard|anniversary|director'?s\s+cut|"
        r"game\s+of\s+the\s+year|goty)(?:\s+edition)?\s*$",
        "",
        name,
        flags=re.I,
    ) if re.search(r"(?:edition|goty|director'?s\s+cut)\s*$", name, re.I) else name
    # "Legend of Zelda, The - Ocarina of Time" -> "The Legend of Zelda - Ocarina of Time"
    article = re.match(r"^(.+?),\s*(The|A|An)\b(.*)$", name, flags=re.I)
    if article:
        name = f"{article.group(2)} {article.group(1)}{article.group(3)}"
    name = _collapse(name.replace("_", " ").strip(" -–—:|"))
    return name or _collapse(str(raw or ""))


def _split_subtitle(name: str) -> Tuple[str, str]:
    parts = re.split(r"\s*(?::|\s[-–—]\s)\s*", name, maxsplit=1)
    return (parts[0], parts[1]) if len(parts) == 2 else (name, "")


def search_queries(game: str) -> List[Tuple[str, str]]:
    """(query, ytmusicapi filter) pairs, most specific first."""
    title = canonical_game_name(game)
    if not title:
        return []
    return [
        (f"{title} original soundtrack", "albums"),
        (f"{title} OST", "albums"),
        (f"{title} soundtrack", "playlists"),
    ]


def _tokens(text: Any) -> List[str]:
    return normalize_text(text).split()


def _name_tokens(text: Any) -> List[str]:
    return [_numeral(t) for t in _tokens(text) if t not in _STOPWORDS]


def _has_phrase(text: Any, phrase: str) -> bool:
    """Token-boundary match: "live" must not match "Deliverance" (DeckTune)."""
    needle = normalize_text(phrase)
    return bool(needle) and f" {needle} " in f" {normalize_text(text)} "


def _coverage(game_tokens: Sequence[str], title_tokens: Sequence[str]) -> float:
    if not game_tokens:
        return 0.0
    present = set(title_tokens)
    return sum(token in present for token in game_tokens) / len(game_tokens)


def _numbers(tokens: Iterable[str]) -> List[str]:
    """Sequel numbers among the tokens, ignoring years and disc/volume numbers."""
    result: List[str] = []
    previous = ""
    for token in tokens:
        if token.isdigit() and not (len(token) == 4 and 1950 <= int(token) <= 2100) \
                and previous not in {"vol", "volume", "disc", "cd", "part", "no"}:
            result.append(token.lstrip("0") or "0")
        previous = token
    return result


def _volume_number(title: str) -> int:
    match = re.search(r"\b(?:vol(?:ume)?|disc|cd|part)\.?\s*(\d+|[ivx]+)\b", normalize_text(title))
    if not match:
        return 0
    value = _numeral(match.group(1))
    return int(value) if value.isdigit() else 0


def _artist_names(item: Dict[str, Any]) -> str:
    names = [str(a.get("name") or "") for a in item.get("artists") or [] if isinstance(a, dict)]
    owner = item.get("owner") if isinstance(item.get("owner"), dict) else {}
    return " ".join(names + [str(owner.get("display_name") or ""), str(item.get("author") or "")])


def score_candidate(game: str, item: Dict[str, Any], platform: str = "") -> float:
    """Score a normalized container (album or playlist) as this game's soundtrack.

    `item` uses ytmusic_service.normalize_item's shape plus the raw search fields
    `albumType` ("Album", "Single", "EP") and `itemCount`.
    """
    title = str(item.get("name") or "")
    kind = str(item.get("type") or "")
    if kind not in {"album", "playlist"} or not title:
        return float("-inf")
    canonical = canonical_game_name(game)
    game_tokens = _name_tokens(canonical)
    title_tokens = _name_tokens(title)
    if not game_tokens:
        return float("-inf")

    coverage = _coverage(game_tokens, title_tokens)
    main, subtitle = _split_subtitle(canonical)
    if subtitle:
        # An album named after the main title only ("Ori" for "Ori: Definitive"), well behind: a subtitle
        # often names a different game ("Marvel's Spider-Man: Miles Morales").
        main_tokens = _name_tokens(main)
        if main_tokens and len(main_tokens) < len(game_tokens):
            coverage = max(coverage, _coverage(main_tokens, title_tokens) * 0.7)
    title_core = " ".join(t for t in _tokens(title) if t not in _SOUNDTRACK_WORDS)
    game_core = " ".join(t for t in _tokens(canonical) if t not in _SOUNDTRACK_WORDS)
    similarity = difflib.SequenceMatcher(None, game_core, title_core).ratio()

    score = coverage * 70.0 + similarity * 25.0
    if coverage < 0.5:
        score -= 40.0

    norm_title = normalize_text(title)
    if "original game soundtrack" in norm_title or "original video game soundtrack" in norm_title:
        score += 28.0
    elif "original soundtrack" in norm_title or "original score" in norm_title:
        score += 24.0
    elif "official soundtrack" in norm_title:
        score += 20.0
    elif {"soundtrack", "ost", "score"} & set(norm_title.split()):
        score += 14.0

    if kind == "album":
        score += 45.0  # YouTube Music's own release, in release order
        album_type = str(item.get("albumType") or "").strip().lower()
        if album_type == "single":
            score -= 45.0
        elif album_type == "ep":
            score -= 15.0
    else:
        owner = normalize_text(_artist_names(item))
        if owner.startswith("youtube music") or owner.endswith(" topic"):
            score += 10.0
        try:
            count = int(item.get("itemCount") or 0)
        except (TypeError, ValueError):
            count = 0
        if 0 < count < 4:
            score -= 30.0

    artists = normalize_text(_artist_names(item))
    if any(_has_phrase(artists, label) for label in _OFFICIAL_LABELS):
        score += 6.0
    if platform and _has_phrase(title, platform):
        score += 4.0

    for marker in _ARRANGEMENT_WORDS:
        if _has_phrase(title, marker) and not _has_phrase(canonical, marker):
            score -= 30.0
    for marker in _DLC_WORDS:
        if _has_phrase(title, marker) and not _has_phrase(canonical, marker):
            score -= 22.0

    for marker in _VERSION_WORDS:
        if _has_phrase(title, marker) and not _has_phrase(canonical, marker):
            score -= 12.0

    # Sequel numbers must agree: Hades is not Hades II, FF VII is not FF VIII.
    game_numbers = set(_numbers(game_tokens))
    title_numbers = set(_numbers(title_tokens))
    score -= 45.0 * len(title_numbers - game_numbers)
    score -= 45.0 * len(game_numbers - title_numbers)

    # Words the game does not have: a DLC or spin-off name ("Godmaster", "Remake").
    extra = [t for t in title_tokens if t not in set(game_tokens) and t not in _SOUNDTRACK_WORDS
             and not t.isdigit() and not _has_phrase(platform, t)]
    score -= min(45.0, 18.0 * len(extra))

    volume = _volume_number(title)
    if volume > 1:
        score -= 8.0 * min(volume - 1, 3)
    return round(score, 2)


def rank_candidates(game: str, items: Iterable[Dict[str, Any]], platform: str = "") -> Dict[str, Any]:
    """Ranked, de-duplicated candidates and whether the best one is a safe pick."""
    seen = set()
    scored: List[Tuple[float, int, Dict[str, Any]]] = []
    for order, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        key = (str(item.get("type") or ""), str(item.get("id") or ""))
        if not key[1] or key in seen:
            continue
        seen.add(key)
        score = score_candidate(game, item, platform)
        if score == float("-inf") or score < MIN_SCORE:
            continue
        scored.append((score, order, item))
    scored.sort(key=lambda entry: (-entry[0], entry[1]))
    candidates = [dict(item, score=score) for score, _order, item in scored[:MAX_CANDIDATES]]
    confident = bool(candidates) and candidates[0]["score"] >= CONFIDENT_SCORE and (
        len(candidates) == 1 or candidates[0]["score"] - candidates[1]["score"] >= CONFIDENT_GAP)
    return {"game": canonical_game_name(game), "candidates": candidates, "confident": confident}


def parse_collector_document(document: Any, app_id: Any) -> Optional[Dict[str, str]]:
    """The Collector's now-playing record for this app id (v1), or None."""
    if not isinstance(document, dict) or document.get("v") != 1:
        return None
    records = document.get("records")
    if not isinstance(records, dict):
        return None
    record = records.get(str(app_id))
    if not isinstance(record, dict):
        return None
    name = _collapse(str(record.get("name") or ""))[:128]
    if not name:
        return None
    return {"name": name, "platform": _collapse(str(record.get("platform") or ""))[:128]}


COLLECTOR_FOLDER = "eternal-now-playing"
COLLECTOR_FILE = "now-playing.json"
_COLLECTOR_MAX_BYTES = 256 * 1024


def collector_file_paths(decky_home: str = "", runtime_dir: str = "") -> List[str]:
    """Where The Collector writes its record: <Decky home>/data/eternal-now-playing/now-playing.json.

    Decky home when known, else the sibling of this plugin's runtime folder
    (<Decky home>/data/<plugin>), which is how The Collector chooses it.
    """
    paths: List[str] = []
    if str(decky_home or "").strip():
        paths.append(os.path.join(str(decky_home).strip(), "data", COLLECTOR_FOLDER, COLLECTOR_FILE))
    runtime = str(runtime_dir or "").strip().rstrip("/\\")
    if runtime and os.path.basename(os.path.dirname(runtime)).lower() == "data":
        paths.append(os.path.join(os.path.dirname(runtime), COLLECTOR_FOLDER, COLLECTOR_FILE))
    unique: List[str] = []
    for path in paths:
        if os.path.normcase(os.path.normpath(path)) not in {os.path.normcase(os.path.normpath(p)) for p in unique}:
            unique.append(path)
    return unique


def read_collector_record(paths: Iterable[str], app_id: Any) -> Optional[Dict[str, str]]:
    """The record for app_id from the first readable Collector file; None when absent or unreadable."""
    for path in paths:
        try:
            if not os.path.isfile(path) or os.path.getsize(path) > _COLLECTOR_MAX_BYTES:
                continue
            with open(path, "r", encoding="utf-8") as handle:
                document = json.load(handle)
        except (OSError, ValueError):
            continue
        record = parse_collector_document(document, app_id)
        if record:
            return record
    return None
