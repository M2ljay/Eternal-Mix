# Eternal-Mix (Awesome Mix)

Decky Loader plugin for Windows (Steam Big Picture via Eternal Core / Playhub). Fork of Now Playing 2.5.2 by
LoZazaMastro, then the "Now Playing Plus" patches, renamed to Awesome Mix. Sources: YouTube Music (main, with
podcasts), Your Music (local files), Apple Music (Windows media session only).

## Build and test (all verified on Linux)

- `npm ci` then `npm run build`: translations check, strict `tsc`, rollup to `dist/index.js` (+ `.map`, committed).
- `npm test`: `node --test "tests/*.test.mjs"` and `python -m unittest discover -s tests`.
- `npm run check:python`: py_compile plus `scripts/validate-frozen-runtime.py` (re-runs `validate-runtime.py` and
  `validate-loopback.py` with stdlib modules masked, as Decky's frozen Python does).
- `node scripts/validate-steam-bundle.mjs` and `python scripts/validate-steam-ui.py --node`.
- `python scripts/package-release.py --output-dir release`: Decky installer ZIP (top folder = `plugin.json` name,
  `Awesome Mix`) and project ZIP, with self-checks.
- Offline fallback: `NP_ESBUILD=<esbuild pkg> node scripts/build-esbuild.mjs`. Its output uses `var`, which
  `validate-steam-bundle.mjs` rejects; ship rollup builds.

## Traps

- The version is written only in `package.json` (and mirrored by `npm` in `package-lock.json`). Backend reads it in
  `mix_identity.py`, frontend in `src/version.ts`; shown as "Vol. N" (major). `tests/test_mix_identity.py` fails on
  a hardcoded copy. A release also needs `CHANGELOG_<version>.md` (package-release.py requires it).
- Decky keys settings/data/logs by plugin **folder** name (`homebrew/settings/<folder>`), and the folder is the
  installer ZIP's top folder. Renaming `plugin.json` `name` moves the user's settings: extend
  `legacy_migration.py` (copy-only allow-list, marker file) instead.
- New top-level Python modules must be added to `runtime_names` in `scripts/package-release.py` and to
  `package-win.ps1`, or the installed plugin fails to import them.
- Decky's Windows Python lacks some stdlib modules; `runtime_compat.py` + `runtime_stdlib/` provide fallbacks.
  Import new stdlib modules only after checking `validate-frozen-runtime.py`.
- localStorage keys keep the upstream `nowPlaying` prefix on purpose: podcast resume, speed, volumes and theme
  carry over from Now Playing Plus without migration. Temp-folder files (`NowPlaying-*`) and the log name
  `NowPlaying.log` are also unchanged.
- GitHub push protection flags AWS-looking constants in `vendor/yt_dlp/extractor/shahid.py` (public yt-dlp code,
  upstream commit a6e033b). Do not rewrite upstream history to hide them.
- `bin/MediaBridge.exe` (~89 MB) has no source in this repo (upstream binary). `AppVolumeBridge/` is source only
  and is not shipped.

## Conventions

- Keep LICENSE (MIT, LoZazaMastro), NOTICE and the credit "Based on Now Playing by Andrea Sgarro (LoZazaMastro)" in
  the README and both settings views.
- Upstream remote: `upstream` = LoZazaMastro/Now-Playing. Merge upstream releases; do not rebase published history.
- Every backend method called from `src/python.ts` must exist in `main.py` (`tests/test_callable_parity.py`).
- Do not reintroduce Spotify, TIDAL, Deezer, Amazon Music or SoundCloud (tests check for them).
