// node --test tests/*.test.mjs  (Node 22.18+ loads the .ts file by stripping types)
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { formatMegabytes, updateReasonKey } from "../src/updateText.ts";

test("every backend refusal reason has a translated message", () => {
  const backend = readFileSync(new URL("../mix_update.py", import.meta.url), "utf8");
  const block = backend.split("# Reasons the frontend translates")[1].split("class UpdateError")[0];
  const reasons = [...block.matchAll(/^[A-Z_]+ = "([a-z_]+)"$/gm)].map((match) => match[1]);
  assert.ok(reasons.length >= 8, reasons.join(","));
  const english = JSON.parse(readFileSync(new URL("../src/locales.json", import.meta.url), "utf8")).en.update;
  for (const reason of reasons) {
    const key = updateReasonKey(reason);
    assert.notEqual(key, "errorUnknown", reason);
    assert.ok(english[key], `${reason} -> ${key}`);
  }
  assert.equal(updateReasonKey("something_new"), "errorUnknown");
  assert.equal(updateReasonKey(undefined), "errorUnknown");
  assert.equal(updateReasonKey("toString"), "errorUnknown");
  assert.ok(english.errorUnknown.includes("{error}"));
});

test("sizes are shown in megabytes", () => {
  assert.equal(formatMegabytes(37_412_345), "37.4");
  assert.equal(formatMegabytes(0), "?");
  assert.equal(formatMegabytes("x"), "?");
});

