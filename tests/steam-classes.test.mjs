import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import vm from "node:vm";

// src/steamClasses.ts must find Steam classes on Decky 3.3.1 (@decky/ui 4,
// webpack Steam) and Decky 4.0 (@decky/ui 5, ES-module Steam with renamed
// classes), and fail soft when the loader offers no class maps at all.
const require = createRequire(import.meta.url);
const ts = require("typescript");
const source = readFileSync(new URL("../src/steamClasses.ts", import.meta.url), "utf8");
const js = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } }).outputText;

function load(dfl, extra = {}) {
  const warnings = [];
  const context = { exports: {}, require: () => dfl, console: { warn: (...a) => warnings.push(a) }, ...extra };
  vm.runInNewContext(js, context);
  return { api: context.exports, warnings };
}

test("Decky 3.3.1 shape: class-map values first, readable names kept", () => {
  const { api } = load({ classMap: [{ Other: "x" }, { BasicFooter: "_3T1iFy2maYycwqgFuzERyf" }], footerClasses: { BasicFooter: "_3T1iFy2maYycwqgFuzERyf" } });
  assert.deepEqual([...api.steamClassSelectors(["BasicFooter"])], ["._3T1iFy2maYycwqgFuzERyf", '[class*="BasicFooter"]']);
});

test("Decky 4.0 shape: renamed classes from the named module and the var-mapped class map", () => {
  const { api } = load({
    footerClasses: { QuickAccessFooter: "oLvmrIFWfh8-" },
    classMap: [{ TopBar: "aB1-c2" }, { TopBar: "d3 e4" }],
  });
  assert.deepEqual([...api.steamClassSelectors(["QuickAccessFooter", "TopBar"])],
    [".oLvmrIFWfh8-", ".aB1-c2", ".d3.e4", '[class*="QuickAccessFooter"]', '[class*="TopBar"]']);
  assert.deepEqual([...api.steamClassSelectors(["TopBar"], false)], [".aB1-c2", ".d3.e4"]);
});

test("no class maps, broken maps and throwing getters fail soft", () => {
  assert.deepEqual([...load(undefined).api.steamClassSelectors(["TopBar"])], ['[class*="TopBar"]']);
  assert.deepEqual([...load({}).api.steamClassSelectors(["TopBar"], false)], []);
  const hostile = { get footerClasses() { throw new Error("getter"); }, classMap: [null, { get TopBar() { throw new Error("x"); } }, { TopBar: 7 }] };
  assert.deepEqual([...load(hostile).api.steamClassSelectors(["TopBar"])], ['[class*="TopBar"]']);
});

test("lookups are cached and the cache can be reset", () => {
  const dfl = { classMap: [{ TopBar: "one" }] };
  const { api } = load(dfl);
  assert.deepEqual([...api.steamClassValues("TopBar")], ["one"]);
  dfl.classMap[0].TopBar = "two";
  assert.deepEqual([...api.steamClassValues("TopBar")], ["one"]);
  api.resetSteamClassCache();
  assert.deepEqual([...api.steamClassValues("TopBar")], ["two"]);
});

test("class escaping works without CSS.escape and prefers it when present", () => {
  const { api } = load({});
  assert.equal(api.cssEscapeClass("_-5HDHEcJvzI-"), "_-5HDHEcJvzI-");
  assert.equal(api.cssEscapeClass("1abc"), "\\31 abc");
  assert.equal(api.cssEscapeClass("a.b"), "a\\.b");
  const withCss = load({}, { CSS: { escape: (v) => `<${v}>` } }).api;
  assert.equal(withCss.cssEscapeClass("x"), "<x>");
});

test("scoped selector lists cross scopes with selectors", () => {
  const { api } = load({});
  assert.equal(api.scopedSelectorList(["html.a", "html.b"], [".x", ".y"]), "html.a .x,\nhtml.a .y,\nhtml.b .x,\nhtml.b .y");
});

test("Steam chrome and modal selectors go through the class-map helper", () => {
  for (const file of ["../src/index.tsx", "../src/localMusic.tsx"]) {
    const text = readFileSync(new URL(file, import.meta.url), "utf8");
    assert.match(text, /from "\.\/steamClasses"/);
    assert.doesNotMatch(text, /\[class\*="(TopBar|BasicFooter|QuickAccessFooter|ModalPosition)"\]/, file);
  }
});
