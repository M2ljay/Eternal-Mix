// node --test tests/*.test.mjs  (Node 22.18+ loads the .ts file by stripping types)
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { volumeLabel } from "../src/volume.ts";

test("versions are shown as volumes", () => {
  assert.equal(volumeLabel("1.0.0"), "Vol. 1");
  assert.equal(volumeLabel("1.4.2"), "Vol. 1");
  assert.equal(volumeLabel("2.0.0"), "Vol. 2");
  assert.equal(volumeLabel("3.1.0-rc.1"), "Vol. 3");
  assert.equal(volumeLabel(""), "Vol. ?");
});

test("package.json is the version source used by the interface", () => {
  const pkg = JSON.parse(readFileSync(new URL("../package.json", import.meta.url), "utf8"));
  assert.match(pkg.version, /^\d+\.\d+\.\d+/);
  const versionTs = readFileSync(new URL("../src/version.ts", import.meta.url), "utf8");
  assert.match(versionTs, /from "\.\.\/package\.json"/);
  assert.doesNotMatch(versionTs, new RegExp(pkg.version.replace(/\./g, "\\.")));
});
