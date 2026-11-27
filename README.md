<div align="center">

# Awesome Mix

### Eternal-Mix

YouTube Music (with podcasts), your own music files and Apple Music inside Steam Big Picture, with gamepad controls and full-screen visualizers.

**Based on [Now Playing](https://github.com/LoZazaMastro/Now-Playing) by Andrea Sgarro (LoZazaMastro).**

</div>

> **Awesome Mix** (repository: Eternal-Mix) is a fork of [Now Playing](https://github.com/LoZazaMastro/Now-Playing)
> 2.5.2 by Andrea Sgarro (LoZazaMastro), formerly published as "Now Playing Plus". It installs as its own Decky
> plugin (folder `Awesome Mix`) and carries no Playhub release marker, so Playhub updates of the original never
> overwrite it. Changes on top of Now Playing 2.5.2: YouTube Music playback fix (yt-dlp 2026.08.19, visionos client,
> stream probing), missing-stdlib fallbacks for Decky on Windows, podcasts in search and library with resume,
> Continue listening and played marks, seeking and playback speed, play-on-select, an optional "Match Steam theme"
> mode for the quick access panel, a chunked YouTube relay and steadier audio output. Awesome Mix then removed Spotify
> and every source other than YouTube Music, Your Music and Apple Music.
>
> Themes (Settings, Appearance): **Core** (default: the Core palette of black, gunmetal, steel and one crimson accent,
> in the quick access panel and every full-screen view), **Match Steam theme** (quick access panel follows the Steam/CSS
> Loader theme) and **Service colours**. A "Match Steam theme" choice made before themes existed is kept.

## Versions are volumes

`package.json` holds the version and is the only place it is written. Decky shows it as is (`1.0.0`); the plugin
shows the major version as a volume: 1.x.y is **Vol. 1**, 2.x.y is **Vol. 2**. The backend reads it through
`mix_identity.py`, the interface through `src/version.ts`.

## Moving from Now Playing Plus

Decky names a plugin's settings folder after the plugin folder (`homebrew/settings/<plugin folder>`), so the new
folder name starts with empty settings. The first time Awesome Mix starts it copies, once, these entries from
`homebrew/settings/Now Playing Plus` to `homebrew/settings/Awesome Mix`:

- files: `source-behavior.json`, `local-music.json`, `local-music-library.json`, `youtube-music.json`,
  `youtube-music-library-cache.json`, `youtube-music-unavailable.json`, `artist-background-selections.json`,
  `artist-background-providers.json`;
- folders: `local-music-covers`, `local-music-artist-profiles`, `local-music-artist-backgrounds`,
  `youtube-music-artist-backgrounds`.

`youtube-music.json` contains your YouTube Music sign-in and `artist-background-providers.json` your fanart.tv API
key: they are copied as opaque files (never opened, parsed or logged) so you stay signed in. The copy never moves,
changes or deletes anything in the old folder and never overwrites a file that already exists in the new one. A
marker file (`.awesome-mix-migration.json`, listing the copied names) makes it run only once; if the copy fails it is
retried at the next start.

Not copied: Spotify settings, tokens and caches (`spotify-plus.json`, `spotify-*-cache`, and Spotify's
artist-background store `artist-backgrounds`, `artist-background-cache.json`), the YouTube Music sign-in
browser profile (`youtube-music-browser-profile`, a full browser profile with cookies that is only used while signing
in), the YouTube Music response cache (rebuilt on demand), temporary files, and anything else.

Settings kept by Steam's browser storage (podcast resume positions and played marks, playback speed, per-source
volume, enabled sources, visualizer choice, theme) are not tied to the plugin folder and carry over by
themselves, as do the top-bar and cover-source settings in the Windows temporary folder.

After the first start of Awesome Mix, remove **Now Playing Plus** in Decky (Settings → Plugins → Uninstall). Running
both at once makes them compete for the same media helpers and top-bar badge. Uninstalling it deletes its plugin folder
(`homebrew/plugins/Now Playing Plus`, including any backup folders kept inside it) but not
`homebrew/settings/Now Playing Plus`; delete that one yourself once you are happy.

## Music no longer interrupts your game

Awesome Mix combines an instant QAM panel with a full-screen music application. Control your tracks from the quick access menu without leaving your game, or browse libraries, albums, playlists, podcasts and artists from the full page via a TV-friendly interface.

Each source keeps its own volume and status. When you switch sources, the plugin starts the new player and closes the previous one, preventing overlapping audio and unnecessary processes.

## The game's own soundtrack

While a game runs and YouTube Music is the source, the Quick Access panel shows **Play this game's soundtrack**. It
looks for the game's official soundtrack album on YouTube Music ("original soundtrack" / "OST" albums first, then
playlists), preferring the base game over DLC and bonus albums and the game's own release over remixes, covers and
user playlists. A clear match plays at once, whole and in album order; otherwise up to three candidates are offered.
Your choice is remembered per game, so next time it is one press, and **Choose another soundtrack** changes it.

Games launched from The Collector (RomM) use the ROM's title and console instead of the shortcut's name. An optional
setting, off by default (Settings, Games), shows a small notice when a game starts; it never plays on its own.

The matching ideas come from DeckTune by Jayden (MIT; see [NOTICE](NOTICE)).

## Speed dial

While YouTube Music is the source and you are signed in, the top of the Quick Access panel shows YouTube Music's own
**Speed dial**: the artists, albums, playlists, mixes and podcasts you play most, including the ones you pinned, as
a 2 by 4 grid. One press plays the tile:

- album or playlist: the whole list, in order
- song: its radio
- mix or station: its radio queue
- artist: what the tile's own play button starts on YouTube Music (usually the artist's radio), else the artist's
  radio, else their top songs
- podcast: the episode you are part-way through (it resumes), else the latest; an episode tile plays that episode

X (or a long press, or a right click) opens **More**: **Refresh speed dial** and **Open in full view**, which opens
the full-screen YouTube Music view with the whole grid (up to 3 rows of 9) at the top of Home. The grid is read from
YouTube Music's home page and kept for 30 minutes. YouTube Music's web home often has no Speed dial (it may exist
only in the mobile apps); then the grid, still labelled Speed dial, takes the closest shelf of the same home page:
**Listen again**, else **Quick picks** (songs, which play their radio), else your library's playlists and albums.
The full view names the stand-in in small type ("from Listen again"). The grid is hidden only when there is nothing
at all (signed out, or an empty home and library). Pinning and unpinning stay in the YouTube Music app.

Every fetch writes one `speed dial` line to `NowPlaying.log` (client, whether the Speed dial shelf was found, the
source used, the tile count and the reason) and one line with the home page's structure (shelf and item renderer
types, header features, counts; never titles, names, ids or images). The panel adds one `[speed-dial]` line per
session saying whether it showed the grid and, if not, why.

TODO (Core UI): the black areas behind the grid are meant to show the Core Starfield. Awesome Mix has no starfield
of its own (the Core Starfield is Core's Steam screensaver), so Core UI will supply it.

## Sources

- **YouTube Music** (the main source): Home, Search and Library, podcasts (search, library tabs, episodes, resume, Continue listening, played marks), sign-in through a temporary browser window, seeking, playback speed 0.75x to 2x for podcasts, and next-track prefetching.
- **Your Music:** recursive scanning of local folders containing MP3, FLAC, M4A, AAC, OGG and WAV files, with tags, cover art, search and a live queue.
- **Apple Music:** now playing and transport controls for the Apple Music app for Windows, through its Windows media session (System Media Transport Controls). Awesome Mix can open and close the app; browsing the Apple Music catalogue is not supported.

Spotify, TIDAL, Deezer, Amazon Music and SoundCloud were removed, together with the Spotify playback helper (`SpotifyPlaybackBridge`, librespot) and the libraries only it used (`spotify_scraper`, `httpx`, `httpcore`, `anyio`, `h11`).

## Visualizers and full-screen listening

The full-screen player features 3D particle fields, spheres, waves, rings, knots, cones, flowers, circles, cover blur, and glow effects. Your Music and YouTube Music drive them from the live audio; Apple Music, whose audio the plugin cannot measure, uses a synthetic pulse.

Main controls:

- **LB / RB:** change effect;
- **X:** change information layout;
- **Y:** change cover art size;
- **LT / RT:** previous or next track;
- **R3:** play or pause;
- **D-pad up / down:** rotate 3D effects.

If the Weather plugin is installed, the visualizer can also display the forecast.

## Surround upmix

Stereo tracks from Your Music and YouTube Music can be distributed to 5.1 or 7.1 audio systems. Each channel features dedicated volume controls adjustable from the controller with immediate preview. On a stereo device, the system automatically downmixes the audio.

## Configuration

### YouTube Music

Choose **Connect YouTube Music**. The plugin opens a temporary window for login and captures the session upon completion, requiring no Google Cloud configuration.

### Your Music

Add one or more folders in the settings and start **Scan library**. The indexing of tracks, tags, and covers will continue in the background.

### Apple Music

Install Apple Music from the Microsoft Store and sign in there. Select **Apple Music** as the source; Awesome Mix follows whatever the app plays.

## Installation

Download the Eternal-Mix ZIP (top folder `Awesome Mix`) and choose **Decky → Settings → Developer → Install plugin from ZIP**. Awesome Mix is not distributed through the Playhub Plugin Store.

## Updates

Awesome Mix updates itself from the `release` branch of
[M2ljay/Eternal-Mix](https://github.com/M2ljay/Eternal-Mix/tree/release); no other source is ever used. Open
**Settings → Updates**: it shows the installed volume and version, and **Check for updates** looks right now (otherwise
the plugin reads the release branch at most every 6 hours). When a newer version is out, **Install Vol. N** appears
with its notes and size, and everything works with the controller.

Installing asks for confirmation, then:

1. The plugin downloads the installer and checks it against the published size and SHA-256, checks that it installs
   into the `Awesome Mix` folder, and checks every file inside it against the file list it carries
   (`install-manifest.json`). If anything differs, nothing is installed and you are told why; if the update was
   published moments ago, try again in a few minutes.
2. The plugin stops its helper programs (MediaBridge and any other program running from the plugin folder or its
   temp copies, never anything else) so Windows can replace every file. If one cannot be stopped, nothing is
   installed and you are told which; restart Steam and try again.
3. Decky shows its own *update* prompt. Confirm it and Decky replaces the plugin folder and reloads Awesome Mix. Cancel
   it and nothing changes.

Your settings (YouTube Music sign-in, music library, artist backgrounds, fanart.tv key) live in
`homebrew/settings/Awesome Mix`, outside the plugin folder, and are kept. If your Decky Loader cannot install plugins
from a plugin, the install button is greyed out; install the ZIP from the `release` branch by hand instead.

### Install check

On Windows, Decky replaces a plugin by deleting its folder and unpacking the new ZIP. A file that is still in use can
stop either step half-way, leaving some files new and others old or missing. At every start Awesome Mix compares its
folder with `install-manifest.json` (sizes at once, SHA-256 hashes in the background) and logs any difference. If the
last update did not finish copying, the panel says so and offers **Reinstall Vol. N**, which downloads the current
version from the `release` branch and hands it to Decky as above. This works even when the damaged files keep the rest
of the plugin from starting (the panel then shows only this message).

## Troubleshooting

- VPNs and network filters may slow down the resolution of YouTube Music streams.
- The first track of a new YouTube Music context may take a few seconds; subsequent tracks are prepared in advance.
- Upmixing is only audible as multi-channel if Windows and the playback device natively expose 5.1 or 7.1 channels.
- **Restart plugin services** remains available as a diagnostic tool.
- The plugin log is `homebrew/logs/Awesome Mix/NowPlaying.log` (the file keeps its upstream name). Install check
  results are logged there as `install check: ...`.

## Development

```sh
npm ci
npm run build          # translations check, strict type check, rollup -> dist/index.js
npm test               # node + python unit tests
npm run check:python   # py_compile + frozen-runtime regression suite (incl. Decky v3.3.1's exact stdlib)
npm run check:steam    # source/bundle parity + Steam UI fixtures
python scripts/package-release.py --output-dir release   # Decky installer ZIP + project ZIP
```

### Publishing an update

1. Raise the version in `package.json` (`npm version patch --no-git-tag-version` also updates `package-lock.json`;
   only `X.Y.Z` versions are offered as updates) and add `CHANGELOG_<version>.md`. Its text after the first line
   becomes the notes shown in Settings.
2. Commit, then build the update files:

   ```sh
   python scripts/make-update-release.py --output-dir release/update --release-clone ../Eternal-Mix-release
   ```

   This runs `npm run build` (skip with `--skip-build`) and `scripts/package-release.py`, writes `awesome-mix.zip` and
   `update.json` (`version`, `sha256`, `size`, `notes`, `url`), checks both exactly as the plugin will, and copies them
   into a clone checked out on the `release` branch.
3. Publish: commit and push that clone's `release` branch. The push is the publish; installed copies see it at their
   next check.

The first time, create the branch as an orphan so it carries no source history:

```sh
git clone https://github.com/M2ljay/Eternal-Mix.git ../Eternal-Mix-release
cd ../Eternal-Mix-release && git switch --orphan release
```

### Building without npm

When the npm registry is not reachable, the interface can still be built and
type-checked from `src/` with packages fetched from GitHub:

```sh
# three.js r158 (build/three.module.js) and the pinned react-icons 5.7.0
NP_THREE=/path/to/three.js NP_REACT_ICONS=/path/to/react-icons NODE_PATH=/path/with/esbuild \
  node scripts/build-esbuild.mjs            # writes dist/index.js

# DefinitelyTyped (types/react, types/react-dom), csstype, decky-frontend-lib v4.11.6, three-ts-types r158
NP_TYPES_REACT=... NP_CSSTYPE=... NP_DECKY_UI_SRC=... NP_THREE_TYPES=... NP_REACT_ICONS=... \
  node scripts/typecheck-offline.mjs
```

The esbuild output follows the same Decky conventions as the rollup build
(Steam-provided `SP_REACT`, `SP_JSX`, `DFL`; bundled `@decky/api`; ES module
default export). `@decky/api` falls back to `scripts/offline-shims/` when it is
not installed.

## License

Awesome Mix, like Now Playing, is distributed under the [MIT](LICENSE) license (copyright LoZazaMastro). Third-party components and tools are documented in [NOTICE](NOTICE) and inside the `licenses` folder.

<div align="center">

Now Playing was created and is maintained by **Andrea Sgarro ([LoZazaMastro](https://github.com/LoZazaMastro))**.<br>Awesome Mix is maintained by [M2ljay](https://github.com/M2ljay).

</div>
