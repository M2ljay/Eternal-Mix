// node --test tests/*.test.mjs  (Node 22.18+ loads the .ts file by stripping types)
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { installCheckPollDelay, shouldShowInstallBanner } from "../src/installCheckText.ts";

test("the banner shows for a broken install or safe mode only", () => {
  assert.equal(shouldShowInstallBanner(null), false);
  assert.equal(shouldShowInstallBanner({ status: "ok", phase: "done" }), false);
  assert.equal(shouldShowInstallBanner({ status: "unverified", phase: "done" }), false);
  assert.equal(shouldShowInstallBanner({ status: "broken", phase: "hashing" }), true);
  assert.equal(shouldShowInstallBanner({ status: "ok", phase: "done", safeMode: true }), true);
});

test("polling follows the background hash check and stops", () => {
  assert.equal(installCheckPollDelay({ status: "ok", phase: "hashing" }, 1), 4000);
  assert.equal(installCheckPollDelay({ status: "ok", phase: "done" }, 1), null);
  assert.equal(installCheckPollDelay({ status: "ok", phase: "hashing" }, 60), null);
  assert.equal(installCheckPollDelay(null, 0), null);
});

test("every install check string exists and the main message is the plain one", () => {
  const catalog = JSON.parse(readFileSync(new URL("../src/locales.json", import.meta.url), "utf8"));
  const english = catalog.en.installCheck;
  assert.equal(english.body.replace("{name}", "Awesome Mix"), "Awesome Mix's last update didn't finish copying.");
  assert.equal(english.reinstall.replace("{volume}", "Vol. 1"), "Reinstall Vol. 1");
  const backend = readFileSync(new URL("../main.py", import.meta.url), "utf8");
  for (const reason of ["helpers_running", "reinstall_unavailable"]) assert.ok(backend.includes(`"${reason}"`), reason);
});
