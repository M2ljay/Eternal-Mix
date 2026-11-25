import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

// src/volumeStep.ts is plain enough to evaluate without a TypeScript toolchain.
const source = readFileSync(new URL("../src/volumeStep.ts", import.meta.url), "utf8")
  .replace(/^export /gm, "")
  .replace(/\(current: number, direction: number, step = VOLUME_STEP\): number/, "(current, direction, step = VOLUME_STEP)");
const { VOLUME_STEP, steppedVolume } = new Function(`${source}; return { VOLUME_STEP, steppedVolume };`)();

test("one press moves the volume by 5", () => {
  assert.equal(VOLUME_STEP, 5);
  assert.equal(steppedVolume(40, 1), 45);
  assert.equal(steppedVolume(40, -1), 35);
});

test("an off-grid volume snaps to the next step in the pressed direction", () => {
  assert.equal(steppedVolume(37, 1), 40);
  assert.equal(steppedVolume(37, -1), 35);
});

test("the volume stays between 0 and 100", () => {
  assert.equal(steppedVolume(100, 1), 100);
  assert.equal(steppedVolume(98, 1), 100);
  assert.equal(steppedVolume(0, -1), 0);
  assert.equal(steppedVolume(3, -1), 0);
});

test("every gamepad/key volume nudge goes through steppedVolume", () => {
  for (const file of ["index.tsx", "youtubeMusic.tsx", "localMusic.tsx"]) {
    const text = readFileSync(new URL(`../src/${file}`, import.meta.url), "utf8");
    assert.doesNotMatch(text, /[Vv]olume\w*\([^)]*(?:volumeRef|volumeValueRef)\.current \+ /, file);
    assert.match(text, /steppedVolume\(/, file);
  }
});
