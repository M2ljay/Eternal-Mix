import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import ts from "typescript";

const source = readFileSync(new URL("../src/gameSoundtrack.ts", import.meta.url), "utf8");
const js = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS } }).outputText;
const mod = { exports: {} };
new Function("module", "exports", js)(mod, mod.exports);
const {
  PICK_STORAGE_KEY, OFFER_STORAGE_KEY, MAX_REMEMBERED, normalizeTitle, collectorRecord, gameKey, resolveRunningGame,
  recallPick, rememberPick, forgetPick, offerEnabled, setOfferEnabled, createRunningGameTracker, isMusicApp,
} = mod.exports;

function memoryStorage() {
  const map = new Map();
  return { getItem: (k) => (map.has(k) ? map.get(k) : null), setItem: (k, v) => { map.set(k, String(v)); }, map };
}

const COLLECTOR_DOC = { v: 1, source: "collector", records: { "3000000123": {
  app_id: 3000000123, rom_id: 42, name: "The Legend of Zelda: Ocarina of Time", platform: "Nintendo 64",
  started_at: 1764190000000, cover_url: null, extra: "ignored" } } };

test("storage keys keep the nowPlaying prefix", () => {
  assert.match(PICK_STORAGE_KEY, /^nowPlaying:/);
  assert.match(OFFER_STORAGE_KEY, /^nowPlaying:/);
});

test("titles normalise like the backend: accents, case, trademarks, colons, hyphens", () => {
  assert.equal(normalizeTitle("Pokémon™: Let's Go!"), "pokemon let s go");
  assert.equal(normalizeTitle("Celeste B-Sides"), "celeste bsides");
  assert.equal(normalizeTitle("  The Legend of Zelda:  Ocarina of Time "), "the legend of zelda ocarina of time");
});

test("the Collector's record is read for the running app id only, version 1 only", () => {
  assert.deepEqual(collectorRecord(COLLECTOR_DOC, 3000000123), { name: "The Legend of Zelda: Ocarina of Time", platform: "Nintendo 64" });
  assert.equal(collectorRecord(COLLECTOR_DOC, 1), null);
  assert.equal(collectorRecord({ ...COLLECTOR_DOC, v: 2 }, 3000000123), null);
  assert.equal(collectorRecord(undefined, 3000000123), null);
  assert.equal(collectorRecord({ v: 1, records: { 7: { name: "   " } } }, 7), null);
});

test("ROM title and console win over the shortcut's Steam name", () => {
  const record = collectorRecord(COLLECTOR_DOC, 3000000123);
  const game = resolveRunningGame(3000000123, "RetroArch - N64", record);
  assert.equal(game.source, "collector");
  assert.equal(game.title, "The Legend of Zelda: Ocarina of Time");
  assert.equal(game.platform, "Nintendo 64");
  assert.equal(game.key, "rom:the legend of zelda ocarina of time|nintendo 64");
});

test("without the Collector the Steam name is used; music apps are not games", () => {
  const game = resolveRunningGame(1145360, "Hades", null);
  assert.deepEqual(game, { appId: 1145360, title: "Hades", platform: "", source: "steam", key: "steam:1145360" });
  assert.equal(resolveRunningGame(0, "Hades", null), null);
  assert.equal(resolveRunningGame(99, "YouTube Music", null), null);
  assert.equal(resolveRunningGame(99, "", null), null);
  assert.ok(isMusicApp("Awesome Mix"));
  assert.equal(gameKey("steam", 0, "Some Game"), "name:some game");
});

test("the same ROM keeps its pick across a recreated shortcut (new app id)", () => {
  const storage = memoryStorage();
  const before = resolveRunningGame(3000000123, "x", { name: "Chrono Trigger", platform: "SNES" });
  rememberPick(storage, before.key, { kind: "album", id: "MPRE1", name: "Chrono Trigger OST", artist: "Yasunori Mitsuda", image: "" });
  const after = resolveRunningGame(3000000999, "y", { name: "Chrono Trigger", platform: "SNES" });
  assert.equal(recallPick(storage, after.key).id, "MPRE1");
  const otherConsole = resolveRunningGame(3000000999, "y", { name: "Chrono Trigger", platform: "Nintendo DS" });
  assert.equal(recallPick(storage, otherConsole.key), null);
});

test("per-game memory: remember, replace, forget, survive junk", () => {
  const storage = memoryStorage();
  assert.equal(recallPick(storage, "steam:1"), null);
  rememberPick(storage, "steam:1", { kind: "album", id: "MPRE1", name: "Hades (Original Soundtrack)", artist: "Darren Korb", image: "u" }, 10);
  rememberPick(storage, "steam:2", { kind: "playlist", id: "VLPL2", name: "Celeste", artist: "", image: "" }, 11);
  assert.deepEqual(recallPick(storage, "steam:1"), { kind: "album", id: "MPRE1", name: "Hades (Original Soundtrack)", artist: "Darren Korb", image: "u", at: 10 });
  rememberPick(storage, "steam:1", { kind: "album", id: "MPRE9", name: "Other", artist: "", image: "" }, 12);
  assert.equal(recallPick(storage, "steam:1").id, "MPRE9");
  forgetPick(storage, "steam:1");
  assert.equal(recallPick(storage, "steam:1"), null);
  assert.equal(recallPick(storage, "steam:2").kind, "playlist");
  rememberPick(storage, "steam:3", { kind: "track", id: "v", name: "", artist: "", image: "" });
  assert.equal(recallPick(storage, "steam:3"), null);
  storage.setItem(PICK_STORAGE_KEY, "{not json");
  assert.equal(recallPick(storage, "steam:2"), null);
  rememberPick(storage, "steam:4", { kind: "album", id: "MPRE4", name: "", artist: "", image: "" });
  assert.equal(recallPick(storage, "steam:4").id, "MPRE4");
});

test("per-game memory keeps the most recent picks only", () => {
  const storage = memoryStorage();
  for (let i = 0; i < MAX_REMEMBERED + 5; i++) rememberPick(storage, `steam:${i}`, { kind: "album", id: `MPRE${i}`, name: "", artist: "", image: "" }, i);
  assert.equal(Object.keys(JSON.parse(storage.getItem(PICK_STORAGE_KEY))).length, MAX_REMEMBERED);
  assert.equal(recallPick(storage, "steam:0"), null);
  assert.equal(recallPick(storage, `steam:${MAX_REMEMBERED + 4}`).id, `MPRE${MAX_REMEMBERED + 4}`);
});

test("auto-offer is off by default", () => {
  const storage = memoryStorage();
  assert.equal(offerEnabled(storage), false);
  setOfferEnabled(storage, true);
  assert.equal(offerEnabled(storage), true);
  setOfferEnabled(storage, false);
  assert.equal(offerEnabled(storage), false);
  assert.equal(offerEnabled(undefined), false);
});

test("tracker: latest running game, launches flagged, music apps ignored", () => {
  let emit;
  const heard = [];
  const tracker = createRunningGameTracker({
    subscribe: (cb) => { emit = cb; return () => { emit = undefined; }; },
    initial: () => 10,
    ignore: (id) => id === 99,
  });
  tracker.listen((id, started) => heard.push([id, started]));
  assert.equal(tracker.current(), 10);
  emit(20, true);
  emit(20, true);
  emit(99, true);
  assert.equal(tracker.current(), 20);
  emit(20, false);
  assert.equal(tracker.current(), 10);
  emit(10, false);
  assert.equal(tracker.current(), 0);
  assert.deepEqual(heard, [[20, true], [10, false], [0, false]]);
  tracker.stop();
  assert.equal(emit, undefined);
});
