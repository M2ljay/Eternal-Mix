import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import ts from "typescript";

const source = readFileSync(new URL("../src/speedDial.ts", import.meta.url), "utf8");
const js = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS } }).outputText;
const mod = { exports: {} };
new Function("module", "exports", js)(mod, mod.exports);
const {
  SPEED_DIAL_EVENT, COMPACT_COLUMNS, COMPACT_ROWS, FULL_COLUMNS, FULL_ROWS,
  sanitizeResult, compactItems, fullItems, playPlan, pickEpisodeIndex, sourceHintKey, panelDiagnostic,
  isStale, stepListen, LISTEN_IDLE, LISTEN_AFTER_MS, OPEN_MAX_AGE_S, LISTEN_MAX_AGE_S,
} = mod.exports;

function tile(type, id, extra = {}) {
  return { type, id, title: `${type} ${id}`, subtitle: "", thumbnail: "", images: [], round: false, pinned: false,
    browseId: "", videoId: "", playlistId: "", params: "", ...extra };
}

test("the window event keeps the nowPlaying prefix", () => {
  assert.match(SPEED_DIAL_EVENT, /^nowPlaying:/);
});

test("grids: 2 by 4 in Quick Access, up to 3 by 9 in the full view", () => {
  assert.equal(COMPACT_COLUMNS * COMPACT_ROWS, 8);
  assert.equal(FULL_COLUMNS * FULL_ROWS, 27);
  const items = Array.from({ length: 30 }, (_, index) => tile("album", `MPRE${index}`));
  assert.equal(compactItems(items).length, 8);
  assert.equal(fullItems(items).length, 27);
  assert.equal(compactItems(items.slice(0, 3)).length, 3);
});

test("play routing per tile type", () => {
  assert.deepEqual(playPlan(tile("album", "MPREb_1", { browseId: "MPREb_1" })), { kind: "collection", collection: "album", id: "MPREb_1" });
  assert.deepEqual(playPlan(tile("playlist", "PL1", { playlistId: "PL1" })), { kind: "collection", collection: "playlist", id: "PL1" });
  assert.deepEqual(playPlan(tile("podcast", "MPSPPL1", { browseId: "MPSPPL1" })), { kind: "podcast", id: "MPSPPL1" });
  for (const type of ["song", "mix", "artist"]) assert.deepEqual(playPlan(tile(type, "x1")), { kind: "queue" }, type);
  const track = { id: "ep1", videoId: "ep1", type: "track", isPodcast: true, sourceKey: "youtubeMusic" };
  assert.deepEqual(playPlan(tile("episode", "ep1", { videoId: "ep1", track })), { kind: "episode", track });
  // Without the backend's ready track, an episode is still a resumable podcast track.
  const built = playPlan(tile("episode", "ep2", { videoId: "ep2" }));
  assert.equal(built.kind, "episode");
  assert.deepEqual([built.track.videoId, built.track.isPodcast, built.track.sourceKey, built.track.type], ["ep2", true, "youtubeMusic", "track"]);
  assert.equal(playPlan(tile("video", "v1")), null);
  assert.equal(playPlan(null), null);
});

test("a podcast resumes the episode in progress, else plays the latest", () => {
  const episodes = [{ videoId: "new" }, { videoId: "mid" }, { videoId: "old" }];
  const progress = (map) => (episode) => map[episode.videoId] ?? null;
  assert.equal(pickEpisodeIndex(episodes, progress({})), 0);
  assert.equal(pickEpisodeIndex(episodes, progress({ mid: { played: false, positionMs: 600000, lengthMs: 3600000 } })), 1);
  assert.equal(pickEpisodeIndex(episodes, progress({ new: { played: true, positionMs: 0, lengthMs: 0 }, old: { played: false, positionMs: 20000, lengthMs: 1 } })), 2);
  assert.equal(pickEpisodeIndex([], progress({})), -1);
});

test("answers are checked: unknown types and empty tiles are dropped, reasons kept", () => {
  const result = sanitizeResult({ items: [tile("album", "MPRE1"), { type: "video", id: "v", title: "t" }, { type: "song", id: "", title: "x" },
    { type: "song", id: "s1", title: "  " }], reason: "", title: "Speed dial", fetchedAt: 5 });
  assert.deepEqual(result.items.map((item) => item.id), ["MPRE1"]);
  assert.equal(result.reason, "");
  assert.deepEqual(sanitizeResult({ items: [], reason: "signed_out" }).reason, "signed_out");
  assert.deepEqual(sanitizeResult({ items: [], reason: "missing" }).reason, "missing");
  assert.deepEqual(sanitizeResult(undefined), { items: [], reason: "error", title: "", source: "", fetchedAt: 0 });
  assert.equal(sanitizeResult({ items: [], reason: "bogus" }).reason, "error");
});

test("the grid shows only for YouTube Music, uses the Core palette and never gold", () => {
  const panel = readFileSync(new URL("../src/speedDialPanel.tsx", import.meta.url), "utf8");
  const index = readFileSync(new URL("../src/index.tsx", import.meta.url), "utf8");
  assert.match(index, /enabledAppKeys\[0\] === "youtubeMusic" \? \(\s*<SpeedDialGrid variant="compact"/);
  assert.match(index, /homeTop=\{<SpeedDialGrid variant="full" \/>\}/);
  assert.match(panel, /CORE\.crimson\b/);
  assert.match(panel, /CORE\.crimsonText/);
  assert.doesNotMatch(panel, /gold|#ffd700|#d4af37|#c9a227/i);
  assert.match(panel, /if \(!items\.length\) return null;/);
});

test("fallback sources: kept when known, hinted only for fallbacks", () => {
  for (const source of ["speed_dial", "listen_again", "quick_picks", "library"]) {
    assert.equal(sanitizeResult({ items: [tile("album", "MPRE1")], reason: "", source }).source, source);
  }
  // An answer from before the fallback chain had no source: it was Speed dial.
  assert.equal(sanitizeResult({ items: [tile("album", "MPRE1")], reason: "" }).source, "speed_dial");
  assert.equal(sanitizeResult({ items: [], reason: "missing", source: "bogus" }).source, "");
  assert.equal(sourceHintKey("speed_dial"), "");
  assert.equal(sourceHintKey(""), "");
  assert.equal(sourceHintKey("listen_again"), "fromListenAgain");
  assert.equal(sourceHintKey("quick_picks"), "fromQuickPicks");
  assert.equal(sourceHintKey("library"), "fromLibrary");
  const locales = JSON.parse(readFileSync(new URL("../src/locales.json", import.meta.url), "utf8"));
  for (const [language, catalog] of Object.entries(locales)) {
    for (const key of ["fromListenAgain", "fromQuickPicks", "fromLibrary"]) assert.ok(catalog.speedDial[key], `${language} ${key}`);
  }
});

test("the panel diagnostic says why the grid is hidden and carries no titles or ids", () => {
  const shown = sanitizeResult({ items: [tile("album", "MPRE_secret_1", { title: "Secret Album" })], reason: "", source: "listen_again" });
  const details = panelDiagnostic(shown, 1);
  assert.deepEqual(details, { rendered: true, items: 1, source: "listen_again", reason: "ok" });
  assert.doesNotMatch(JSON.stringify(details), /Secret|MPRE_secret/);
  assert.equal(panelDiagnostic(sanitizeResult({ items: [], reason: "signed_out" }), 0).hiddenBecause, "not signed in to YouTube Music");
  assert.match(panelDiagnostic(sanitizeResult({ items: [], reason: "missing" }), 0).hiddenBecause, /no Speed dial, Listen again, Quick picks or library/);
  assert.equal(panelDiagnostic(sanitizeResult({ reason: "error" }), 0).hiddenBecause, "backend call failed");
  assert.equal(panelDiagnostic(null, 0).rendered, false);
});

test("the panel logs once through the diagnostic route, and Quick Access says when it does not mount the grid", () => {
  const panel = readFileSync(new URL("../src/speedDialPanel.tsx", import.meta.url), "utf8");
  const index = readFileSync(new URL("../src/index.tsx", import.meta.url), "utf8");
  assert.match(panel, /reportDiagnosticEvent\("speedDial", event, details\)/);
  assert.match(panel, /reportSpeedDialOnce\("call_failed"/);
  assert.match(panel, /reportSpeedDialOnce\(`panel_\$\{variant\}`, panelDiagnostic\(result, items\.length\)\)/);
  assert.match(index, /reportSpeedDialOnce\("panel_compact_not_mounted"/);
  // The label stays "Speed dial"; the source hint is in the full view only.
  assert.equal((panel.match(/sourceHintKey\(/g) ?? []).length, 1);
  assert.ok(panel.indexOf("sourceHintKey(") > panel.indexOf("if (full) {"));
});

test("freshness: no answer or an old one is stale", () => {
  const now = 1_000_000_000_000;
  assert.equal(isStale(null, now, OPEN_MAX_AGE_S), true);
  assert.equal(isStale({ items: [], reason: "error", title: "", source: "", fetchedAt: 0 }, now, OPEN_MAX_AGE_S), true);
  const fresh = { items: [], reason: "", title: "", source: "speed_dial", fetchedAt: now / 1000 - 60 };
  assert.equal(isStale(fresh, now, OPEN_MAX_AGE_S), false);
  assert.equal(isStale({ ...fresh, fetchedAt: now / 1000 - 200 }, now, LISTEN_MAX_AGE_S), true);
});

test("listening: one trigger per track after it has played a while", () => {
  const song = { sourceKey: "youtubeMusic", videoId: "abc" };
  let state = LISTEN_IDLE, triggers = 0, t = 0;
  for (let i = 0; i < 30; i += 1) {
    const step = stepListen(state, song, true, t); state = step.state; triggers += step.trigger ? 1 : 0; t += 5000;
  }
  assert.equal(triggers, 1);
  assert.ok(state.playedMs >= LISTEN_AFTER_MS);
});

test("listening: a track that ends after 15 s triggers; pauses, sleep gaps and local files do not count", () => {
  const a = { sourceKey: "youtubeMusic", videoId: "a" }, b = { sourceKey: "youtubeMusic", videoId: "b" };
  let state = stepListen(LISTEN_IDLE, a, true, 0).state;
  for (let t = 5000; t <= 20000; t += 5000) state = stepListen(state, a, true, t).state;
  assert.equal(stepListen(state, b, true, 25000).trigger, true);
  // Paused: no time counts.
  let paused = stepListen(LISTEN_IDLE, a, true, 0).state;
  for (let t = 5000; t <= 120000; t += 5000) assert.equal(stepListen(paused, a, false, t).trigger, false), paused = stepListen(paused, a, false, t).state;
  assert.equal(paused.playedMs, 0);
  // One huge gap counts 10 s at most.
  const gap = stepListen(stepListen(LISTEN_IDLE, a, true, 1000).state, a, true, 3_600_000);
  assert.equal(gap.trigger, false);
  assert.equal(gap.state.playedMs, 10000);
  // Local files are not YouTube Music listening.
  let local = LISTEN_IDLE;
  for (let t = 0; t < 120000; t += 5000) { const step = stepListen(local, { sourceKey: "localMusic", id: "x" }, true, t); assert.equal(step.trigger, false); local = step.state; }
});
