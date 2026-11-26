import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import ts from "typescript";

const source = readFileSync(new URL("../src/gameDucking.ts", import.meta.url), "utf8");
const js = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS } }).outputText;
const mod = { exports: {} };
new Function("module", "exports", js)(mod, mod.exports);
const { installGameDucking, duckedVolumes, fadeFrames } = mod.exports;

function harness({ enabled = true, volumes = { youtubeMusic: 80, localMusic: 60 }, ignore } = {}) {
  let emit;
  const writes = [];
  const stop = installGameDucking({
    subscribe: (callback) => { emit = callback; return () => { emit = undefined; }; },
    read: async () => ({ ...volumes }),
    write: async (frame) => { writes.push(frame); },
    enabled: () => enabled,
    ignore,
    wait: async () => {},
  });
  const settle = () => new Promise((resolve) => setTimeout(resolve, 5));
  return { emit: (...a) => emit?.(...a), writes, stop, settle };
}

test("ducked volume is a quarter, fades arrive in steps", () => {
  assert.deepEqual(duckedVolumes({ a: 80, b: 61 }), { a: 20, b: 15 });
  const frames = fadeFrames({ a: 80 }, { a: 20 }, 3);
  assert.deepEqual(frames.map((f) => f.a), [60, 40, 20]);
});

test("a game starting fades the music down; the last game closing fades it back exactly", async () => {
  const h = harness();
  h.emit(101, true); await h.settle();
  assert.deepEqual(h.writes.at(-1), { youtubeMusic: 20, localMusic: 15 });
  h.emit(202, true); await h.settle();
  const afterSecond = h.writes.length;
  h.emit(101, false); await h.settle();
  assert.equal(h.writes.length, afterSecond, "still a game running: no change");
  h.emit(202, false); await h.settle();
  assert.deepEqual(h.writes.at(-1), { youtubeMusic: 80, localMusic: 60 });
});

test("off in settings: nothing moves", async () => {
  const h = harness({ enabled: false });
  h.emit(101, true); await h.settle(); h.emit(101, false); await h.settle();
  assert.equal(h.writes.length, 0);
});

test("ignored apps (the music apps themselves) never duck", async () => {
  const h = harness({ ignore: (id) => id === 7 });
  h.emit(7, true); await h.settle();
  assert.equal(h.writes.length, 0);
});

test("unloading mid-game restores the music at once", async () => {
  const h = harness();
  h.emit(101, true); await h.settle();
  h.stop();
  assert.deepEqual(h.writes.at(-1), { youtubeMusic: 80, localMusic: 60 });
});
