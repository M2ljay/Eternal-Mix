<div align="center">

# Awesome Mix

### Eternal-Mix · Vol. 1

YouTube Music (with podcasts), your own music files and Windows media sessions inside Steam Big Picture, with gamepad controls and full-screen visualizers.

**Based on [Now Playing](https://github.com/LoZazaMastro/Now-Playing) by Andrea Sgarro (LoZazaMastro).**

</div>

> **Awesome Mix** (repository: Eternal-Mix) is a fork of [Now Playing](https://github.com/LoZazaMastro/Now-Playing)
> 2.5.2 by Andrea Sgarro (LoZazaMastro), formerly published as "Now Playing Plus". It installs as its own Decky
> plugin (folder `Awesome Mix`) and carries no Playhub release marker, so Playhub updates of the original never
> overwrite it. Changes on top of Now Playing 2.5.2: YouTube Music playback fix (yt-dlp 2026.08.19, visionos client,
> stream probing), missing-stdlib fallbacks for Decky on Windows, podcasts in search and library with resume,
> Continue listening and played marks, seeking and playback speed, play-on-select, an optional "Match Steam theme"
> mode for the quick access panel, a chunked YouTube relay and steadier audio output.

## Versions are volumes

`package.json` holds the version and is the only place it is written. Decky shows it as is (`1.0.0`); the plugin
shows the major version as a volume: 1.x.y is **Vol. 1**, 2.x.y is **Vol. 2**. The backend reads it through
`mix_identity.py`, the interface through `src/version.ts`.

## Moving from Now Playing Plus

Decky names a plugin's settings folder after the plugin folder (`homebrew/settings/<plugin folder>`), so the new
folder name starts with empty settings. The first time Awesome Mix starts it copies, once, these entries from
`homebrew/settings/Now Playing Plus` to `homebrew/settings/Awesome Mix`:

- files: `source-behavior.json`, `local-music.json`, `local-music-library.json`, `youtube-music.json`,
  `youtube-music-library-cache.json`, `youtube-music-unavailable.json`, `artist-background-cache.json`,
  `artist-background-selections.json`, `artist-background-providers.json`;
- folders: `artist-backgrounds`, `local-music-covers`, `local-music-artist-profiles`,
  `local-music-artist-backgrounds`, `youtube-music-artist-backgrounds`.

`youtube-music.json` contains your YouTube Music sign-in and `artist-background-providers.json` your fanart.tv API
key: they are copied as opaque files (never opened, parsed or logged) so you stay signed in. The copy never moves,
changes or deletes anything in the old folder and never overwrites a file that already exists in the new one. A
marker file (`.awesome-mix-migration.json`, listing the copied names) makes it run only once; if the copy fails it is
retried at the next start.

Not copied: Spotify settings, tokens and caches (`spotify-plus.json`, `spotify-*-cache`), the YouTube Music sign-in
browser profile (`youtube-music-browser-profile`, a full browser profile with cookies that is only used while signing
in), the YouTube Music response cache (rebuilt on demand), temporary files, and anything else.

Settings kept by Steam's browser storage (podcast resume positions and played marks, playback speed, per-source
volume, enabled sources, visualizer choice, "Match Steam theme") are not tied to the plugin folder and carry over by
themselves, as do the top-bar and cover-source settings in the Windows temporary folder.

After the first start of Awesome Mix, remove **Now Playing Plus** in Decky (Settings → Plugins → Uninstall). Running
both at once makes them compete for the same media helpers and top-bar badge. Uninstalling it deletes its plugin folder
(`homebrew/plugins/Now Playing Plus`, including any backup folders kept inside it) but not
`homebrew/settings/Now Playing Plus`; delete that one yourself once you are happy.

## Music no longer interrupts your game

Now Playing combines an instant QAM panel with a full-screen music application. Control your tracks from the quick access menu without leaving your game, or browse libraries, albums, playlists, and artists from the full page via a TV-friendly interface.

Each source maintains its own separate volume and status. When you switch services, the plugin starts the new player and closes the previous one, preventing overlapping audio and unnecessary processes.

## Supported sources

- **Your Music:** recursive scanning of local folders containing MP3, FLAC, M4A, AAC, OGG, and WAV files, complete with tags, cover art, search, and a live queue.
- **Spotify:** library, playlists, albums, integrated playback, local audio cache up to 5 GB, and an API usage indicator.
- **YouTube Music:** Home, Search, and Library, login via temporary window, playback, and next-track prefetching.
- **Windows Media Sessions:** metadata and transport controls for TIDAL, Apple Music, Deezer, Amazon Music, SoundCloud, and other compatible players.

## Visualizers and full-screen listening

The full-screen player features 3D particle fields, spheres, waves, rings, knots, cones, flowers, circles, cover blur, and glow effects. The responsiveness is balanced across Spotify, local files, and YouTube Music, ensuring visual energy remains consistent when switching sources.

Main controls:

- **LB / RB:** change effect;
- **X:** change information layout;
- **Y:** change cover art size;
- **LT / RT:** previous or next track;
- **R3:** play or pause;
- **D-pad up / down:** rotate 3D effects.

If the Weather plugin is installed, the visualizer can also display the forecast.

## Surround upmix

Stereo tracks from Spotify, Your Music, and YouTube Music can be distributed to 5.1 or 7.1 audio systems. Each channel features dedicated volume controls adjustable from the controller with immediate preview. On a stereo device, the system automatically downmixes the audio.

## What's new in version 2.5.0

- Spotify opens the audio output only when playback starts and automatically reopens it after a pause or device change.
- A Spotify track is only marked as started once the decoded audio reaches an active output.
- Spotify Home is prepared as a single cacheable payload, while independent calls run in parallel.
- Large libraries display the first page instantly and complete loading in the background.
- YouTube Music Home, Search, and Library utilize persistent caches; independent sections are requested in parallel.
- **Play all**, shuffle, and progressive browsing continue to use the entire library, not just the currently visible elements.

## Configuration

### Spotify

1. create an app in the [Spotify Developer Dashboard](https://developer.spotify.com/dashboard);
2. use `Playhub Now Playing` as the name and add exactly `http://127.0.0.1:43821/callback` to the Redirect URIs;
3. select only **Web API**;
4. copy the Client ID into the Now Playing settings and choose **Connect Spotify**.

### YouTube Music

Choose **Connect YouTube Music**. The plugin opens a temporary window for login and captures the session upon completion, requiring no Google Cloud configuration.

### Your Music

Add one or more folders in the settings and start **Scan library**. The indexing of tracks, tags, and covers will continue in the background.

## Installation

Install and update Now Playing from the [Playhub](https://github.com/LoZazaMastro/Playhub) Plugin Store, or download the ZIP from the [latest release](https://github.com/LoZazaMastro/Now-Playing/releases/latest) and choose **Decky → Settings → Developer → Install plugin from ZIP**.

## Troubleshooting

- VPNs and network filters may slow down the resolution of YouTube Music streams.
- The first track of a new YouTube Music context may take a few seconds; subsequent tracks are prepared in advance.
- Upmixing is only audible as multi-channel if Windows and the playback device natively expose 5.1 or 7.1 channels.
- **Restart plugin services** remains available as a diagnostic tool, but normal Spotify initialization and output device changes should not require it.

## Development

```powershell
pnpm install
pnpm run build
python -m py_compile main.py ytmusic_service.py
.\package-win.ps1
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
