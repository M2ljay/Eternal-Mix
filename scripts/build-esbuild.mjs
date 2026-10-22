#!/usr/bin/env node
// Builds dist/index.js from src/ with esbuild, as an alternative to the rollup
// build (`pnpm run build`) for machines that cannot install the npm packages.
//
// The output follows the @decky/rollup conventions Decky expects:
//   - React, react/jsx-runtime, react-dom and @decky/ui are provided by Steam
//     at runtime as the globals SP_REACT, SP_JSX, SP_REACTDOM and DFL;
//   - @decky/api is bundled, with the plugin manifest (plugin.json name);
//   - an ES module whose default export is the plugin definition.
//
// Packages are taken from node_modules when present. Otherwise:
//   NP_ESBUILD=<path to esbuild package>      (default: search NODE_PATH)
//   NP_THREE=<path to a three.js checkout or package with build/three.module.js>
//   NP_REACT_ICONS=<path to the react-icons package>
// and @decky/api falls back to scripts/offline-shims/decky-api.js.
//
// Usage: node scripts/build-esbuild.mjs [--outfile dist/index.js] [--entry src/index.tsx]
//                                        [--expose name,name] (test builds only)
import { createRequire } from "node:module";
import { existsSync, readFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const require = createRequire(join(root, "package.json"));
const args = process.argv.slice(2);
const option = (name, fallback) => {
  const index = args.indexOf(name);
  return index >= 0 ? args[index + 1] : fallback;
};

function locate(packageName, envName, probe) {
  const fromEnv = process.env[envName];
  if (fromEnv && existsSync(join(fromEnv, probe))) return fromEnv;
  const local = join(root, "node_modules", packageName);
  if (existsSync(join(local, probe))) return local;
  for (const base of (process.env.NODE_PATH || "").split(":").filter(Boolean)) {
    if (existsSync(join(base, packageName, probe))) return join(base, packageName);
  }
  return null;
}

const esbuildDir = locate("esbuild", "NP_ESBUILD", "package.json");
if (!esbuildDir) throw new Error("esbuild not found: install it or set NP_ESBUILD");
const esbuild = createRequire(join(esbuildDir, "package.json"))(esbuildDir);
const threeDir = locate("three", "NP_THREE", "build/three.module.js");
if (!threeDir) throw new Error("three.js not found: install it or set NP_THREE");
const iconsDir = locate("react-icons", "NP_REACT_ICONS", "package.json");
if (!iconsDir) throw new Error("react-icons not found: install it or set NP_REACT_ICONS");
const deckyApiDir = locate("@decky/api", "NP_DECKY_API", "package.json");

const pluginName = JSON.parse(readFileSync(join(root, "plugin.json"), "utf8")).name;
const globals = { react: "SP_REACT", "react/jsx-runtime": "SP_JSX", "react-dom": "SP_REACTDOM", "@decky/ui": "DFL" };
const expose = (option("--expose", "") || "").split(",").filter(Boolean);
const entry = resolve(root, option("--entry", "src/index.tsx"));

const deckyPlugin = {
  name: "decky-conventions",
  setup(build) {
    build.onResolve({ filter: /^(react|react\/jsx-runtime|react-dom|@decky\/ui)$/ }, (a) => ({ path: a.path, namespace: "decky-global" }));
    build.onLoad({ filter: /.*/, namespace: "decky-global" }, (a) => ({ contents: `module.exports = ${globals[a.path]};`, loader: "js" }));
    build.onResolve({ filter: /^@decky\/manifest$/ }, () => ({ path: "manifest", namespace: "decky-manifest" }));
    build.onLoad({ filter: /.*/, namespace: "decky-manifest" }, () => ({ contents: `export default ${JSON.stringify({ name: pluginName })};`, loader: "js" }));
    if (!deckyApiDir) {
      build.onResolve({ filter: /^@decky\/api$/ }, () => ({ path: join(root, "scripts/offline-shims/decky-api.js") }));
    }
    build.onResolve({ filter: /^three$/ }, () => ({ path: join(threeDir, "build/three.module.js") }));
    build.onResolve({ filter: /^react-icons(\/.*)?$/ }, (a) => {
      const sub = a.path.slice("react-icons".length) || "/lib";
      for (const candidate of [`${sub}/index.mjs`, `${sub}/index.esm.js`, `${sub}/index.js`]) {
        if (existsSync(join(iconsDir, candidate))) return { path: join(iconsDir, candidate) };
      }
      return undefined;
    });
    if (expose.length) {
      // Test builds: also export internal components from the entry module.
      build.onLoad({ filter: /src[\\/]index\.tsx$/ }, (a) => ({
        contents: readFileSync(a.path, "utf8") + `\nexport { ${expose.join(", ")} };\n`,
        loader: "tsx",
      }));
    }
  },
};

const result = await esbuild.build({
  entryPoints: [entry],
  outfile: resolve(root, option("--outfile", "dist/index.js")),
  bundle: true,
  format: "esm",
  platform: "browser",
  target: "es2020",
  jsx: "automatic",
  charset: "utf8",
  legalComments: "inline",
  logLevel: "warning",
  tsconfig: join(root, "tsconfig.json"),
  plugins: [deckyPlugin],
  metafile: true,
});
const bytes = Object.values(result.metafile.outputs).reduce((sum, output) => sum + output.bytes, 0);
console.log(`Built ${option("--outfile", "dist/index.js")} for "${pluginName}" (${(bytes / 1e6).toFixed(2)} MB, @decky/api ${deckyApiDir ? "from node_modules" : "offline shim"})`);
