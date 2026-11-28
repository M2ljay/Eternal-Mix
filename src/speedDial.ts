// YouTube Music's "Speed dial" in Awesome Mix: the shapes the backend answers with and
// how one press plays a tile. Self-contained (no Decky imports) so the tests can load it
// alone; the grid lives in src/speedDialPanel.tsx and the shelf parsing in mix_speed_dial.py.

export type SpeedDialType = "song" | "album" | "playlist" | "artist" | "mix" | "podcast" | "episode";
export type SpeedDialReason = "" | "signed_out" | "missing" | "error";

export type SpeedDialItem = {
  type: SpeedDialType;
  id: string;
  title: string;
  subtitle: string;
  thumbnail: string;
  images: { url: string; width?: number; height?: number }[];
  round: boolean;
  pinned: boolean;
  browseId: string;
  videoId: string;
  playlistId: string;
  params: string;
  track?: any;
};

// Where the tiles came from: YouTube Music's Speed dial shelf, or (when the home feed has
// none, as on the web client) Listen again, Quick picks or the library. The grid is
// labelled "Speed dial" either way.
export type SpeedDialSource = "" | "speed_dial" | "listen_again" | "quick_picks" | "library";

export type SpeedDialResult = { items: SpeedDialItem[]; reason: SpeedDialReason; title: string; source: SpeedDialSource; fetchedAt: number };

// The Quick Access grid, and the full view (YouTube Music shows 3 rows of 9).
export const COMPACT_COLUMNS = 4;
export const COMPACT_ROWS = 2;
export const FULL_COLUMNS = 9;
export const FULL_ROWS = 3;

// Window event: the grid in Quick Access and the one in the full view stay in step.
export const SPEED_DIAL_EVENT = "nowPlaying:speedDialChanged";

const TYPES: readonly SpeedDialType[] = ["song", "album", "playlist", "artist", "mix", "podcast", "episode"];
const REASONS: readonly SpeedDialReason[] = ["", "signed_out", "missing", "error"];
const SOURCES: readonly SpeedDialSource[] = ["", "speed_dial", "listen_again", "quick_picks", "library"];

function text(value: unknown, max = 300): string {
  return String(value ?? "").replace(/\s+/g, " ").trim().slice(0, max);
}

/** A backend answer, checked: unknown types and tiles without an id or title are dropped. */
export function sanitizeResult(value: unknown): SpeedDialResult {
  const raw = (value && typeof value === "object" ? value : {}) as any;
  const items: SpeedDialItem[] = [];
  for (const entry of Array.isArray(raw.items) ? raw.items : []) {
    const type = String(entry?.type ?? "") as SpeedDialType;
    const id = text(entry?.id, 200);
    const title = text(entry?.title);
    if (!TYPES.includes(type) || !id || !title) continue;
    items.push({
      type,
      id,
      title,
      subtitle: text(entry?.subtitle),
      thumbnail: text(entry?.thumbnail, 2000),
      images: Array.isArray(entry?.images) ? entry.images.filter((image: any) => image?.url) : [],
      round: Boolean(entry?.round),
      pinned: Boolean(entry?.pinned),
      browseId: text(entry?.browseId, 200),
      videoId: text(entry?.videoId, 64),
      playlistId: text(entry?.playlistId, 200),
      params: text(entry?.params, 200),
      ...(entry?.track && typeof entry.track === "object" ? { track: entry.track } : {}),
    });
  }
  const reason = REASONS.includes(raw.reason) ? raw.reason : items.length ? "" : "error";
  const source: SpeedDialSource = SOURCES.includes(raw.source) ? raw.source : items.length ? "speed_dial" : "";
  return { items, reason, title: text(raw.title), source, fetchedAt: Number(raw.fetchedAt) || 0 };
}

/** The full view's small header hint for a fallback source; "" for Speed dial itself. */
export function sourceHintKey(source: SpeedDialSource): "" | "fromListenAgain" | "fromQuickPicks" | "fromLibrary" {
  if (source === "listen_again") return "fromListenAgain";
  if (source === "quick_picks") return "fromQuickPicks";
  if (source === "library") return "fromLibrary";
  return "";
}

/**
 * The panel's one-time diagnostic: whether the grid shows and, if not, why. Plain fields
 * only (counts and fixed words); no titles or ids.
 */
export function panelDiagnostic(result: SpeedDialResult | null, shown: number): Record<string, string | number | boolean> {
  if (!result) return { rendered: false, items: 0, hiddenBecause: "no answer from the backend yet" };
  const hiddenBecause = shown > 0 ? "" : {
    signed_out: "not signed in to YouTube Music",
    missing: "backend found no Speed dial, Listen again, Quick picks or library items",
    error: "backend call failed",
    "": "backend answered no playable tiles",
  }[result.reason];
  return { rendered: shown > 0, items: shown, source: result.source || "none", reason: result.reason || "ok", ...(hiddenBecause ? { hiddenBecause } : {}) };
}

export function compactItems(items: SpeedDialItem[]): SpeedDialItem[] {
  return items.slice(0, COMPACT_COLUMNS * COMPACT_ROWS);
}

export function fullItems(items: SpeedDialItem[]): SpeedDialItem[] {
  return items.slice(0, FULL_COLUMNS * FULL_ROWS);
}

export type PlayPlan =
  | { kind: "collection"; collection: "album" | "playlist"; id: string }
  | { kind: "podcast"; id: string }
  | { kind: "episode"; track: any }
  | { kind: "queue" };

/**
 * How one press plays a tile:
 * - album, playlist: the whole list in its own order (the existing detail path)
 * - podcast: the episode in progress, else the latest (existing podcast resume)
 * - episode: that episode (resumes where it was left)
 * - song: its radio; mix: its radio queue; artist: what the tile's play button
 *   starts, else the artist's radio, else their top songs (all from the backend)
 */
export function playPlan(item: SpeedDialItem | null | undefined): PlayPlan | null {
  if (!item || !item.id) return null;
  switch (item.type) {
    case "album":
      return { kind: "collection", collection: "album", id: item.browseId || item.id };
    case "playlist":
      return { kind: "collection", collection: "playlist", id: item.playlistId || item.id };
    case "podcast":
      return { kind: "podcast", id: item.browseId || item.id };
    case "episode": {
      const track = item.track && typeof item.track === "object"
        ? item.track
        : { id: item.videoId || item.id, videoId: item.videoId || item.id, type: "track", name: item.title, images: item.images, sourceKey: "youtubeMusic", isPodcast: true };
      return { kind: "episode", track };
    }
    case "song":
    case "mix":
    case "artist":
      return { kind: "queue" };
    default:
      return null;
  }
}

export type EpisodeProgress = { played: boolean; positionMs: number; lengthMs: number } | null;

/**
 * Which episode of a show to start: the first one (newest first, as YouTube Music lists
 * them) that is part-way through, otherwise the latest. -1 when there is none.
 */
export function pickEpisodeIndex(episodes: any[], progress: (episode: any) => EpisodeProgress): number {
  if (!Array.isArray(episodes) || !episodes.length) return -1;
  for (let index = 0; index < episodes.length; index += 1) {
    const state = progress(episodes[index]);
    if (state && !state.played && state.positionMs > 0) return index;
  }
  return 0;
}


// Keeping the grid fresh without a refresh button. Ages are in seconds and go to the
// backend as max_age, which clamps them and fetches YouTube Music's home only when its
// cached answer is older; these checks just avoid asking when the answer we hold is fresh.
export const OPEN_MAX_AGE_S = 300; // page or panel opens, Steam regains focus, resume from sleep
export const LISTEN_MAX_AGE_S = 180; // after listening: at most one home fetch every 3 minutes
export const LISTEN_AFTER_MS = 45_000; // a track counts once it has played this long
export const LISTEN_FINISHED_MS = 15_000; // or when it ends (track changes or stops) after this long
export const LISTEN_DEBOUNCE_MS = 20_000; // listening triggers settle before one fetch
export const MIN_ASK_GAP_MS = 60_000; // never ask the backend more often than this for one reason

/** Whether `result` is older than `maxAgeS` (no answer, or one never fetched, is stale). */
export function isStale(result: SpeedDialResult | null, nowMs: number, maxAgeS: number): boolean {
  if (!result || !result.fetchedAt) return true;
  return nowMs - result.fetchedAt * 1000 >= maxAgeS * 1000;
}

export type ListenState = { key: string; playedMs: number; lastAt: number; counted: boolean };
export const LISTEN_IDLE: ListenState = { key: "", playedMs: 0, lastAt: 0, counted: false };

/**
 * One look at the player (polled every few seconds). Counts the time a YouTube Music
 * track has been playing and says when listening should refresh the grid: once per
 * track after LISTEN_AFTER_MS, or when it gives way to another after LISTEN_FINISHED_MS. A gap between
 * looks counts at most 10 seconds, so sleep or a stalled poll never counts as listening.
 */
export function stepListen(prev: ListenState, track: any, playing: boolean, nowMs: number): { state: ListenState; trigger: boolean } {
  const key = track && String(track.sourceKey ?? "") === "youtubeMusic" ? String(track.videoId || track.id || "") : "";
  if (key !== prev.key) {
    // The previous track ended or was changed: it counts if it played long enough.
    const finished = !!prev.key && !prev.counted && prev.playedMs >= LISTEN_FINISHED_MS;
    return { state: { key, playedMs: 0, lastAt: playing && key ? nowMs : 0, counted: false }, trigger: finished };
  }
  // Paused: the clock stops until it plays again.
  if (!playing) return { state: { ...prev, lastAt: 0 }, trigger: false };
  if (!key) return { state: prev, trigger: false };
  const step = prev.lastAt ? Math.max(0, Math.min(10_000, nowMs - prev.lastAt)) : 0;
  const playedMs = prev.playedMs + step;
  const trigger = !prev.counted && playedMs >= LISTEN_AFTER_MS;
  return { state: { key, playedMs, lastAt: nowMs, counted: prev.counted || trigger }, trigger };
}
