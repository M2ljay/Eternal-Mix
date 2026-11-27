// "Play this game's soundtrack": which game is running, and which soundtrack the user
// picked for it last time. Self-contained (no Decky imports) so the tests can load it
// alone; the panel and the Steam hooks live in src/gameSoundtrackPanel.tsx, and the
// album search and ranking in mix_soundtrack.py.

// localStorage keys keep the upstream `nowPlaying` prefix, like every other key here.
export const PICK_STORAGE_KEY = "nowPlaying:gameSoundtrack:v1";
export const OFFER_STORAGE_KEY = "nowPlaying:gameSoundtrackOffer:v1";
export const MAX_REMEMBERED = 200;

// The Collector (Eternal-Collector) publishes the running ROM here; see its
// docs/architecture/now-playing-presence.md. Version 1 only; anything else is ignored.
export const COLLECTOR_WINDOW_KEY = "__eternalNowPlaying";
export const COLLECTOR_EVENT = "eternal_now_playing";

// Music apps can be Steam shortcuts; they are never "the game".
const MUSIC_APP = /youtube music|apple music|spotify|awesome mix/i;

export type GameSource = "collector" | "steam";
export type RunningGame = { appId: number; title: string; platform: string; source: GameSource; key: string };
export type CollectorRecord = { name: string; platform: string };
export type SoundtrackPick = { kind: "album" | "playlist"; id: string; name: string; artist: string; image: string; at: number };
export type StorageLike = { getItem(key: string): string | null; setItem(key: string, value: string): void };

function oneLine(value: unknown, max = 128): string {
  return String(value ?? "").replace(/\s+/g, " ").trim().slice(0, max);
}

/** Comparison form (mirrors mix_soundtrack.normalize_text): no accents, no case, no punctuation. */
export function normalizeTitle(value: unknown): string {
  return String(value ?? "")
    .replace(/[™®©]/g, "")
    .normalize("NFKD")
    .replace(/[̀-ͯ]/g, "")
    .toLowerCase()
    .replace(/&/g, " and ")
    .replace(/(?<=[\p{L}\p{N}])-(?=[\p{L}\p{N}])/gu, "")
    .replace(/[^\p{L}\p{N}]+/gu, " ")
    .trim();
}

export function isMusicApp(name: string): boolean {
  return MUSIC_APP.test(String(name ?? ""));
}

/** The Collector's record for this app id, from its window document or file document. */
export function collectorRecord(document: unknown, appId: number): CollectorRecord | null {
  const doc = document as any;
  if (!doc || typeof doc !== "object" || doc.v !== 1 || !doc.records || typeof doc.records !== "object") return null;
  const record = doc.records[String(appId)];
  if (!record || typeof record !== "object") return null;
  const name = oneLine(record.name);
  return name ? { name, platform: oneLine(record.platform) } : null;
}

/** A per-game key that survives renames of the Steam shortcut: ROMs by title and console. */
export function gameKey(source: GameSource, appId: number, title: string, platform = ""): string {
  if (source === "collector" && title) return `rom:${normalizeTitle(title)}|${normalizeTitle(platform)}`;
  return appId > 0 ? `steam:${appId}` : `name:${normalizeTitle(title)}`;
}

/** The game to find a soundtrack for: The Collector's ROM title and console first, else Steam's name. */
export function resolveRunningGame(appId: number, steamName: string, collector?: CollectorRecord | null): RunningGame | null {
  if (!appId) return null;
  if (collector?.name) {
    return { appId, title: collector.name, platform: collector.platform, source: "collector", key: gameKey("collector", appId, collector.name, collector.platform) };
  }
  const title = oneLine(steamName, 180);
  if (!title || isMusicApp(title)) return null;
  return { appId, title, platform: "", source: "steam", key: gameKey("steam", appId, title) };
}

function readPicks(storage: StorageLike): Record<string, SoundtrackPick> {
  try {
    const value = JSON.parse(storage.getItem(PICK_STORAGE_KEY) ?? "{}");
    return value && typeof value === "object" && !Array.isArray(value) ? value : {};
  } catch {
    return {};
  }
}

export function recallPick(storage: StorageLike, key: string): SoundtrackPick | null {
  const pick = readPicks(storage)[key];
  if (!pick || (pick.kind !== "album" && pick.kind !== "playlist") || !pick.id) return null;
  return pick;
}

/** Remember the pick for this game; the oldest picks go past MAX_REMEMBERED. */
export function rememberPick(storage: StorageLike, key: string, pick: Omit<SoundtrackPick, "at">, now = Date.now()): void {
  if (!key || !pick?.id || (pick.kind !== "album" && pick.kind !== "playlist")) return;
  const picks = readPicks(storage);
  picks[key] = { kind: pick.kind, id: String(pick.id), name: oneLine(pick.name, 200), artist: oneLine(pick.artist, 200), image: String(pick.image ?? "").slice(0, 2048), at: now };
  const kept = Object.entries(picks).sort((left, right) => Number(right[1]?.at || 0) - Number(left[1]?.at || 0)).slice(0, MAX_REMEMBERED);
  try { storage.setItem(PICK_STORAGE_KEY, JSON.stringify(Object.fromEntries(kept))); } catch { /* storage full or unavailable */ }
}

export function forgetPick(storage: StorageLike, key: string): void {
  const picks = readPicks(storage);
  if (!(key in picks)) return;
  delete picks[key];
  try { storage.setItem(PICK_STORAGE_KEY, JSON.stringify(picks)); } catch { /* storage unavailable */ }
}

/** Auto-offer on game launch: off unless the user turned it on. */
export function offerEnabled(storage: StorageLike | undefined): boolean {
  try { return storage?.getItem(OFFER_STORAGE_KEY) === "on"; } catch { return false; }
}

export function setOfferEnabled(storage: StorageLike | undefined, enabled: boolean): void {
  try { storage?.setItem(OFFER_STORAGE_KEY, enabled ? "on" : "off"); } catch { /* storage unavailable */ }
}

export type TrackerDeps = {
  subscribe: (callback: (appId: number, running: boolean) => void) => () => void;
  initial?: () => number;
  ignore?: (appId: number) => boolean;
};

/**
 * The running game: the most recently started app that is still running. Listeners hear
 * every change, and `started` is true only for a launch seen while listening (the toast).
 */
export function createRunningGameTracker(deps: TrackerDeps) {
  const running: number[] = [];
  const listeners = new Set<(appId: number, started: boolean) => void>();
  const notify = (started: boolean) => { const id = current(); for (const listener of listeners) { try { listener(id, started); } catch { /* one listener cannot stop the others */ } } };
  function current(): number { return running.length ? running[running.length - 1] : 0; }
  try {
    const first = Number(deps.initial?.() || 0);
    if (first > 0 && !deps.ignore?.(first)) running.push(first);
  } catch { /* nothing known yet */ }
  const unsubscribe = deps.subscribe((appId, isRunning) => {
    if (!appId || deps.ignore?.(appId)) return;
    const index = running.indexOf(appId);
    if (isRunning) {
      if (index >= 0) return;
      running.push(appId);
      notify(true);
    } else if (index >= 0) {
      running.splice(index, 1);
      notify(false);
    }
  });
  return {
    current,
    listen(listener: (appId: number, started: boolean) => void) { listeners.add(listener); return () => { listeners.delete(listener); }; },
    stop() { listeners.clear(); unsubscribe(); },
  };
}
