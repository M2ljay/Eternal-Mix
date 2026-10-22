#!/usr/bin/env node
// Strict type check of src/ (same flags as `npm run check:types`) for machines
// without node_modules. Point it at type definitions fetched from GitHub:
//   NP_TYPES_REACT    DefinitelyTyped checkout containing types/react, types/react-dom
//   NP_CSSTYPE        frenic/csstype checkout (needed by the React types)
//   NP_DECKY_UI_SRC   SteamDeckHomebrew/decky-frontend-lib checkout at the pinned tag (src/)
//   NP_THREE_TYPES    three-types/three-ts-types checkout at r158 (types/three)
//   NP_REACT_ICONS    react-icons package
// Errors inside those libraries are reported separately and do not fail the run.
import { spawnSync } from "node:child_process";
import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const env = (name) => {
  const value = process.env[name];
  if (!value) throw new Error(`${name} is not set (see the header of this script)`);
  return resolve(value);
};
const react = env("NP_TYPES_REACT");
const work = mkdtempSync(join(tmpdir(), "np-typecheck-"));
writeFileSync(join(work, "tsconfig.json"), JSON.stringify({
  extends: join(root, "tsconfig.json"),
  compilerOptions: {
    ignoreDeprecations: "6.0",
    noEmit: true,
    noUnusedLocals: true,
    noUnusedParameters: true,
    skipLibCheck: true,
    types: [],
    typeRoots: [],
    paths: {
      react: [join(react, "types/react/index.d.ts")],
      "react/jsx-runtime": [join(react, "types/react/jsx-runtime.d.ts")],
      "react-dom": [join(react, "types/react-dom/index.d.ts")],
      "react-dom/*": [join(react, "types/react-dom/*")],
      csstype: [join(env("NP_CSSTYPE"), "index.d.ts")],
      "@decky/ui": [join(env("NP_DECKY_UI_SRC"), "src/index.ts")],
      three: [join(env("NP_THREE_TYPES"), "types/three/index.d.ts")],
      "react-icons": [join(env("NP_REACT_ICONS"), "lib/index.d.ts")],
      "react-icons/*": [join(env("NP_REACT_ICONS"), "*/index.d.ts")],
    },
  },
  include: [join(root, "src/**/*.ts"), join(root, "src/**/*.tsx"), join(root, "scripts/offline-shims/decky-api.d.ts")],
}, null, 2));
const tsc = process.env.NP_TSC || "tsc";
const run = spawnSync(tsc, ["-p", join(work, "tsconfig.json")], { cwd: work, encoding: "utf8", shell: process.platform === "win32" });
const lines = `${run.stdout || ""}${run.stderr || ""}`.split("\n").filter((line) => /error TS\d+/.test(line));
const sourceDir = join(root, "src");
// tsc prints paths relative to the temporary project directory.
const ours = lines.filter((line) => resolve(work, line.slice(0, line.indexOf("("))).startsWith(sourceDir));
const libraries = lines.filter((line) => !ours.includes(line));
for (const line of ours) console.log(line);
if (libraries.length) console.log(`(${libraries.length} error(s) inside library sources ignored)`);
console.log(ours.length ? `Type check FAILED: ${ours.length} error(s) in src/` : "Type check OK: no errors in src/");
process.exit(ours.length ? 1 : 0);
