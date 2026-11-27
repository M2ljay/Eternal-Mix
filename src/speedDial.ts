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

export type SpeedDialResult = { items: SpeedDialItem[]; reason: SpeedDialReason; title: string; fetchedAt: number };

// The Quick Access grid, and the full view (YouTube Music shows 3 rows of 9).
export const COMPACT_COLUMNS = 4;
export const COMPACT_ROWS = 2;
export const FULL_COLUMNS = 9;
export const FULL_ROWS = 3;

// Window event: the grid in Quick Access and the one in the full view stay in step.
export const SPEED_DIAL_EVENT = "nowPlaying:speedDialChanged";

const TYPES: readonly SpeedDialType[] = ["song", "album", "playlist", "artist", "mix", "podcast", "episode"];
const REASONS: readonly SpeedDialReason[] = ["", "signed_out", "missing", "error"];

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
  return { items, reason, title: text(raw.title), fetchedAt: Number(raw.fetchedAt) || 0 };
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

