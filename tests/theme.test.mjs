import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import ts from "typescript";

function load(file, modules = {}) {
  const source = readFileSync(new URL(`../src/${file}`, import.meta.url), "utf8");
  const js = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS } }).outputText;
  const mod = { exports: {} };
  new Function("module", "exports", "require", js)(mod, mod.exports, (name) => modules[name]);
  return mod.exports;
}

const palette = load("corePalette.ts");
const store = new Map();
globalThis.window = {
  localStorage: { getItem: (k) => (store.has(k) ? store.get(k) : null), setItem: (k, v) => store.set(k, String(v)) },
  dispatchEvent() {},
};
const theme = load("themeMatch.ts", { react: { useSyncExternalStore: () => undefined }, "./corePalette": palette });

test("new users get the Core theme", () => {
  store.clear();
  assert.equal(theme.getMixTheme(), "core");
});

test("an explicit old Match Steam theme choice is kept", () => {
  store.clear();
  store.set("nowPlaying:qamMatchTheme:v1", "1");
  assert.equal(theme.getMixTheme(), "steam");
  store.set("nowPlaying:qamMatchTheme:v1", "0");
  assert.equal(theme.getMixTheme(), "service");
});

test("the chosen theme is stored under the nowPlaying prefix and wins", () => {
  store.clear();
  store.set("nowPlaying:qamMatchTheme:v1", "1");
  theme.setMixTheme("core");
  assert.equal(store.get("nowPlaying:theme:v1"), "core");
  assert.equal(theme.getMixTheme(), "core");
  store.set("nowPlaying:theme:v1", "bogus");
  assert.equal(theme.getMixTheme(), "steam");
});

test("Core variables use only the approved palette", () => {
  const allowed = new Set(["#000000", "#1A1D21", "#2A2F36", "#C9D1D9", "#9AA4AE", "#FFFFFF", "#E6EAEE", "#D7263D", "#6FA37F", "#C98B4A"]);
  const text = JSON.stringify(palette.CORE_VARS) + palette.CORE_ROOT_CSS;
  for (const hex of text.match(/#[0-9A-Fa-f]{6}\b/g)) assert.ok(allowed.has(hex.toUpperCase()), hex);
  assert.doesNotMatch(text, /#d4a72c|#d9a337|#ffc83c|#66c0f4|#ff0033|#1db954/i);
});
