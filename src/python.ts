import { call } from "@decky/api";

export type PlayerSnapshot = {
  id: string;
  name: string;
  title?: string;
  artist?: string;
  album?: string;
  status: string;
  length: number;
  position: number;
  canNext: boolean;
  canPrevious: boolean;
  canPlay: boolean;
  canPause: boolean;
  canTogglePlayPause: boolean;
  isSelected: boolean;
  isCurrent: boolean;
  canShuffle?: boolean;
  canRepeat?: boolean;
  shuffleActive?: boolean;
  repeatMode?: string;
  artworkUrl?: string;
  volume?: number;
  audioLevel?: number;
};

export type Snapshot = {
  selectedPlayer: string;
  currentPlayer: string;
  selected: PlayerSnapshot | null;
  players: PlayerSnapshot[];
};

export type AppVolume = {
  ok: boolean;
  volume: number;
  matched?: string;
  stale?: boolean;
  origin?: string;
};

export type CoverSource = "online" | "windows";


export type SourceBehaviorSettings = {
  autoLaunch: boolean;
  closeOnSwitch: boolean;
};

export function getSourceBehaviorSettings(): Promise<SourceBehaviorSettings> {
  return call<[], SourceBehaviorSettings>("get_source_behavior_settings");
}

export function setSourceBehaviorSettings(autoLaunch: boolean, closeOnSwitch: boolean): Promise<SourceBehaviorSettings> {
  return call<[autoLaunch: boolean, closeOnSwitch: boolean], SourceBehaviorSettings>(
    "set_source_behavior_settings",
    autoLaunch,
    closeOnSwitch,
  );
}

const emptySnapshotValue = (): Snapshot => ({
  selectedPlayer: "",
  currentPlayer: "",
  selected: null,
  players: [],
});

function normalizeSnapshot(value: unknown): Snapshot {
  if (!value || typeof value !== "object") return emptySnapshotValue();
  const raw = value as Partial<Snapshot>;
  const players = Array.isArray(raw.players)
    ? raw.players.filter((entry): entry is PlayerSnapshot => Boolean(entry && typeof entry === "object"))
    : [];
  const selected = raw.selected && typeof raw.selected === "object"
    ? raw.selected as PlayerSnapshot
    : players.find((entry) => entry.isSelected || entry.isCurrent) ?? players[0] ?? null;
  const selectedPlayer = typeof raw.selectedPlayer === "string"
    ? raw.selectedPlayer
    : String(selected?.id ?? "");
  const currentPlayer = typeof raw.currentPlayer === "string"
    ? raw.currentPlayer
    : selectedPlayer;
  return { selectedPlayer, currentPlayer, selected, players };
}

export async function getSnapshot(): Promise<Snapshot> {
  try {
    return normalizeSnapshot(await call<[], Snapshot>("get_snapshot"));
  } catch {
    return emptySnapshotValue();
  }
}

export function getTopbarEnabled(): Promise<boolean> {
  return call<[], boolean>("get_topbar_enabled");
}

export function setTopbarEnabled(enabled: boolean): Promise<boolean> {
  return call<[enabled: boolean], boolean>("set_topbar_enabled", enabled);
}

export function getTopbarLeft(): Promise<boolean> {
  return call<[], boolean>("get_topbar_left");
}

export function setTopbarLeft(enabled: boolean): Promise<boolean> {
  return call<[enabled: boolean], boolean>("set_topbar_left", enabled);
}

export function setMediaPlayer(player: string): Promise<string> {
  return call<[player: string], string>("set_media_player", player);
}

export function getCover(title: string, artist: string, album: string): Promise<string> {
  return call<[title: string, artist: string, album: string], string>(
    "get_cover",
    title,
    artist,
    album
  );
}

export function getCoverForService(service: string, title: string, artist: string, album: string): Promise<string> {
  return call<[service: string, title: string, artist: string, album: string], string>(
    "get_cover_for_service",
    service,
    title,
    artist,
    album
  );
}

export function getCoverSource(): Promise<CoverSource> {
  return call<[], CoverSource>("get_cover_source");
}

export function setCoverSource(source: CoverSource): Promise<CoverSource> {
  return call<[source: CoverSource], CoverSource>("set_cover_source", source);
}

export function playPause(): Promise<string> {
  return call<[], string>("play_pause");
}

export function pauseExternalPlayback(): Promise<string> {
  return call<[], string>("pause_external_playback");
}

export function nextTrack(): Promise<string> {
  return call<[], string>("next");
}

export function previousTrack(): Promise<string> {
  return call<[], string>("previous");
}

export function openYouTubeMusic(): Promise<string> {
  return call<[], string>("open_youtube_music");
}

export function openAppleMusic(): Promise<string> {
  return call<[], string>("open_apple_music");
}

export function shuffle(): Promise<string> {
  return call<[], string>("shuffle");
}

export function repeat(): Promise<string> {
  return call<[], string>("repeat");
}

export function getAppVolume(service = ""): Promise<AppVolume> {
  return call<[service: string], AppVolume>("get_app_volume", service);
}

let lastVolumeRevision = Date.now() * 1000;

export function setAppVolume(volume: number, service = ""): Promise<AppVolume> {
  lastVolumeRevision = Math.max(lastVolumeRevision + 1, Date.now() * 1000);
  return call<[volume: number, service: string, revision: number], AppVolume>("set_app_volume", volume, service, lastVolumeRevision);
}

export type PluginServiceRestartResult = {
  ok: boolean;
  message?: string;
  snapshot?: Snapshot;
  mediaBridgePid?: number;
  mediaBridgePort?: number;
  warnings?: string[];
  steps?: Array<Record<string, unknown>>;
};

export type DiagnosticExportResult = { ok: boolean; path?: string; error?: string };

export function exportDiagnosticLog(): Promise<DiagnosticExportResult> {
  return call<[], DiagnosticExportResult>("export_diagnostic_log");
}

export function restartPluginServices(): Promise<PluginServiceRestartResult> {
  const request = call<[], PluginServiceRestartResult>("restart_plugin_services");
  return new Promise((resolve) => {
    const timer = globalThis.setTimeout(() => resolve({ ok: false, message: "Plugin service recovery exceeded 35 seconds. Export diagnostics for details." }), 35000);
    request.then((result) => {
      globalThis.clearTimeout(timer);
      resolve(result);
    }).catch((error) => {
      globalThis.clearTimeout(timer);
      resolve({ ok: false, message: String(error?.message ?? error ?? "Plugin service restart failed") });
    });
  });
}


export function reportDiagnosticEvent(category: string, event: string, details: Record<string, unknown> = {}): Promise<boolean> {
  return call<[category: string, event: string, details: Record<string, unknown>], boolean>("report_diagnostic_event", category, event, details);
}

export function isMusicAppRunning(appKey: string): Promise<boolean> {
  return call<[appKey: string], boolean>("is_music_app_running", appKey);
}

export function closeMusicApp(appKey: string): Promise<string> {
  return call<[appKey: string], string>("close_music_app", appKey);
}


export type ApiResult<T = any> = {
  ok: boolean;
  data?: T;
  error?: string;
};

export type YouTubeMusicSettings = {
  authenticated: boolean;
  displayName?: string;
  userId?: string;
  avatar?: string;
  audioQuality: "low" | "medium" | "high";
  compactSavedTracks?: boolean;
};

export type YouTubeMusicBrowserAuthStatus = {
  running: boolean;
  phase: "idle" | "opening" | "waitingForLogin" | "connected" | "cancelled" | "error";
  error?: string;
  settings?: YouTubeMusicSettings;
};

export function getYouTubeMusicSettings(): Promise<YouTubeMusicSettings> {
  return call<[], YouTubeMusicSettings>("get_youtube_music_settings");
}

export function connectYouTubeMusic(headersRaw: string): Promise<ApiResult<YouTubeMusicSettings>> {
  return call<[headersRaw: string], ApiResult<YouTubeMusicSettings>>("connect_youtube_music", headersRaw);
}

export function startYouTubeMusicBrowserAuth(): Promise<ApiResult<YouTubeMusicBrowserAuthStatus>> {
  return call<[], ApiResult<YouTubeMusicBrowserAuthStatus>>("start_youtube_music_browser_auth");
}

export function getYouTubeMusicBrowserAuthStatus(): Promise<ApiResult<YouTubeMusicBrowserAuthStatus>> {
  return call<[], ApiResult<YouTubeMusicBrowserAuthStatus>>("get_youtube_music_browser_auth_status");
}

export function cancelYouTubeMusicBrowserAuth(): Promise<ApiResult<YouTubeMusicBrowserAuthStatus>> {
  return call<[], ApiResult<YouTubeMusicBrowserAuthStatus>>("cancel_youtube_music_browser_auth");
}

export function disconnectYouTubeMusic(): Promise<ApiResult<YouTubeMusicSettings>> {
  return call<[], ApiResult<YouTubeMusicSettings>>("disconnect_youtube_music");
}

export function setYouTubeMusicAudioQuality(quality: "low" | "medium" | "high"): Promise<ApiResult<YouTubeMusicSettings>> {
  return call<[quality: string], ApiResult<YouTubeMusicSettings>>("set_youtube_music_audio_quality", quality);
}

export function setYouTubeMusicCompactSavedTracks(enabled: boolean): Promise<ApiResult<YouTubeMusicSettings>> {
  return call<[enabled: boolean], ApiResult<YouTubeMusicSettings>>("set_youtube_music_compact_saved_tracks", enabled);
}

export function refreshYouTubeMusicCache(): Promise<ApiResult<{ entries: number }>> {
  return call<[], ApiResult<{ entries: number }>>("refresh_youtube_music_cache");
}

export function youtubeMusicGetHome(): Promise<ApiResult> {
  return call<[], ApiResult>("youtube_music_get_home");
}

export function youtubeMusicSearch(query: string): Promise<ApiResult> {
  return call<[query: string], ApiResult>("youtube_music_search", query);
}

export function youtubeMusicGetLibrary(section: "tracks" | "albums" | "playlists" | "artists" | "podcasts" | "episodes", maxItems = 100): Promise<ApiResult> {
  return call<[section: string, maxItems: number], ApiResult>("youtube_music_get_library", section, maxItems);
}

export function youtubeMusicGetDetail(kind: "album" | "playlist" | "artist", itemId: string): Promise<ApiResult> {
  return call<[kind: string, itemId: string], ApiResult>("youtube_music_get_detail", kind, itemId);
}

export function youtubeMusicGetSpeedDial(refresh = false, maxAge = 0): Promise<ApiResult<unknown>> {
  return call<[refresh: boolean, maxAge: number], ApiResult<unknown>>("youtube_music_get_speed_dial", refresh, maxAge);
}

export function youtubeMusicGetSpeedDialPins(): Promise<ApiResult<unknown>> {
  return call<[], ApiResult<unknown>>("youtube_music_get_speed_dial_pins");
}

export function youtubeMusicSetSpeedDialPins(items: object[]): Promise<ApiResult<unknown>> {
  return call<[items: object[]], ApiResult<unknown>>("youtube_music_set_speed_dial_pins", items);
}

export function youtubeMusicGetSpeedDialQueue(item: object): Promise<ApiResult<{ tracks: any[]; mode: string; playlistId: string }>> {
  return call<[item: object], ApiResult<{ tracks: any[]; mode: string; playlistId: string }>>("youtube_music_get_speed_dial_queue", item);
}

export type SoundtrackCandidate = {
  type: "album" | "playlist";
  id: string;
  name: string;
  artists?: { name: string; id?: string }[];
  owner?: { display_name?: string };
  images?: { url: string; width?: number; height?: number }[];
  release_date?: string;
  albumType?: string;
  itemCount?: number | null;
  score: number;
};
export type SoundtrackSearch = { game: string; candidates: SoundtrackCandidate[]; confident: boolean };

export function youtubeMusicFindSoundtrack(game: string, platform = ""): Promise<ApiResult<SoundtrackSearch>> {
  return call<[game: string, platform: string], ApiResult<SoundtrackSearch>>("youtube_music_find_soundtrack", game, platform);
}

export function getCollectorNowPlaying(appId: number): Promise<ApiResult<{ name?: string; platform?: string }>> {
  return call<[appId: number], ApiResult<{ name?: string; platform?: string }>>("get_collector_now_playing", appId);
}

export function youtubeMusicPrepareStream(videoId: string): Promise<ApiResult<{ url: string; durationMs?: number; title?: string; artist?: string; album?: string; thumbnail?: string }>> {
  return call<[videoId: string], ApiResult<{ url: string; durationMs?: number; title?: string; artist?: string; album?: string; thumbnail?: string }>>(
    "youtube_music_prepare_stream",
    videoId,
  );
}

export function youtubeMusicPrefetchStream(videoId: string): Promise<ApiResult<{}>> {
  return call<[videoId: string], ApiResult<{}>>("youtube_music_prefetch_stream", videoId);
}

export function youtubeMusicInvalidateStream(videoId: string): Promise<ApiResult<{ removed: number }>> {
  return call<[videoId: string], ApiResult<{ removed: number }>>(
    "youtube_music_invalidate_stream",
    videoId,
  );
}

export function openExternalUrl(url: string): Promise<boolean> {
  return call<[url: string], boolean>("open_external_url", url);
}

export type ArtistBackgroundCandidate = {
  id: string;
  previewUrl: string;
  width: number;
  height: number;
  source: string;
  selected: boolean;
};

export type ArtistBackgroundSearchResult = {
  ok: boolean;
  data?: { items: ArtistBackgroundCandidate[]; selectedUrl?: string };
  error?: string;
};

export type ArtistBackgroundApplyResult = {
  ok: boolean;
  url?: string;
  width?: number;
  height?: number;
  error?: string;
};

export type ArtistCacheProgress = {
  active: boolean;
  phase: "idle" | "loading" | "background" | "clearing" | "cleared" | "complete" | "error" | string;
  current: string;
  completed: number;
  total: number;
  error?: string;
};

export type ArtistBackgroundSource = "all";

export function searchArtistBackgrounds(provider: "local" | "youtubeMusic", artistId: string, artistName: string, source: ArtistBackgroundSource = "all"): Promise<ArtistBackgroundSearchResult> {
  return call<[provider: string, artistId: string, artistName: string, source: string], ArtistBackgroundSearchResult>(
    "search_artist_backgrounds", provider, artistId, artistName, source,
  );
}

export function applyArtistBackground(provider: "local" | "youtubeMusic", artistId: string, artistName: string, candidateId: string): Promise<ArtistBackgroundApplyResult> {
  return call<[provider: string, artistId: string, artistName: string, candidateId: string], ArtistBackgroundApplyResult>(
    "apply_artist_background", provider, artistId, artistName, candidateId,
  );
}

export type AssetCacheStats = { bytes: number; files: number; manualBytes?: number; manualFiles?: number; totalBytes?: number; totalFiles?: number };

export function getYouTubeMusicArtistBackground(artistName: string): Promise<string> {
  return call<[artistName: string], string>("get_youtube_music_artist_background", artistName);
}

export function buildYouTubeMusicArtistCache(): Promise<{ ok: boolean; data?: { artists: number; cached: number }; error?: string }> {
  return call<[], { ok: boolean; data?: { artists: number; cached: number }; error?: string }>("build_youtube_music_artist_cache");
}

export function clearYouTubeMusicArtistCache(): Promise<{ ok: boolean; data?: AssetCacheStats; error?: string }> {
  return call<[], { ok: boolean; data?: AssetCacheStats; error?: string }>("clear_youtube_music_artist_cache");
}

export function getYouTubeMusicArtistCacheStats(): Promise<AssetCacheStats> {
  return call<[], AssetCacheStats>("get_youtube_music_artist_cache_stats");
}

export function getYouTubeMusicArtistCacheProgress(): Promise<ArtistCacheProgress> {
  return call<[], ArtistCacheProgress>("get_youtube_music_artist_cache_progress");
}


export function clearManualArtistBackgrounds(provider: "local" | "youtubeMusic"): Promise<{ ok: boolean; data?: AssetCacheStats; stats?: any; error?: string }> {
  return call<[provider: string], { ok: boolean; data?: AssetCacheStats; stats?: any; error?: string }>("clear_manual_artist_backgrounds", provider);
}

export type ArtistBackgroundProviderSettings = { fanartApiKey: string };

export function getArtistBackgroundProviderSettings(): Promise<ArtistBackgroundProviderSettings> {
  return call<[], ArtistBackgroundProviderSettings>("get_artist_background_provider_settings");
}

export function setFanartApiKey(apiKey: string): Promise<ArtistBackgroundProviderSettings> {
  return call<[apiKey: string], ArtistBackgroundProviderSettings>("set_fanart_api_key", apiKey);
}

export type LocalMusicStats = {
  tracks: number;
  albums: number;
  artists: number;
  playlists: number;
  scannedAt: number;
};

export type LocalMusicSettings = {
  folders: string[];
  files: string[];
  lastScan: number;
  stats: LocalMusicStats;
  cacheBytes?: number;
  cacheFiles?: number;
  manualBackgroundBytes?: number;
  manualBackgroundFiles?: number;
};

export type LocalMusicCacheProgress = {
  active: boolean;
  phase: "idle" | "scanning" | "profile" | "background" | "clearing" | "cleared" | "complete" | "error" | string;
  current: string;
  completed: number;
  total: number;
  error?: string;
};

export type LocalMusicResult<T = any> = {
  ok: boolean;
  data?: T;
  stats?: LocalMusicStats;
  settings?: LocalMusicSettings;
  folder?: string;
  file?: string;
  error?: string;
  volume?: number;
  matched?: string;
};

export function setActiveService(service: string): Promise<string> {
  return call<[service: string], string>("set_active_service", service);
}

export function getActiveService(): Promise<string> {
  return call<[], string>("get_active_service");
}

export function getLocalMusicSettings(): Promise<LocalMusicSettings> {
  return call<[], LocalMusicSettings>("get_local_music_settings");
}

export function pickLocalMusicFolder(): Promise<LocalMusicResult> {
  return call<[], LocalMusicResult>("pick_local_music_folder");
}

export function addLocalMusicFolder(folder: string): Promise<LocalMusicResult> {
  return call<[folder: string], LocalMusicResult>("add_local_music_folder", folder);
}

export function removeLocalMusicFolder(folder: string): Promise<LocalMusicResult> {
  return call<[folder: string], LocalMusicResult>("remove_local_music_folder", folder);
}

export type LocalMusicDirectoryListing = {
  ok: boolean;
  path: string;
  dirs: string[];
  files: string[];
  error?: string;
};

export function listLocalMusicDirectory(path: string): Promise<LocalMusicDirectoryListing> {
  return call<[path: string], LocalMusicDirectoryListing>("list_local_music_directory", path);
}

export function addLocalMusicFile(path: string): Promise<LocalMusicResult> {
  return call<[path: string], LocalMusicResult>("add_local_music_file", path);
}

export function removeLocalMusicFile(path: string): Promise<LocalMusicResult> {
  return call<[path: string], LocalMusicResult>("remove_local_music_file", path);
}

export function scanLocalMusic(): Promise<LocalMusicResult> {
  return call<[], LocalMusicResult>("scan_local_music");
}

export function getLocalMusicHome(): Promise<any> {
  return call<[], any>("get_local_music_home");
}

export function getLocalMusicLibrary(section: "tracks" | "albums" | "playlists" | "artists", offset = 0, limit = 300): Promise<any> {
  return call<[section: string, offset: number, limit: number], any>("get_local_music_library", section, offset, limit);
}

export function searchLocalMusic(query: string): Promise<any> {
  return call<[query: string], any>("search_local_music", query);
}

export function getLocalMusicDetail(kind: "album" | "playlist" | "artist", itemId: string): Promise<any> {
  return call<[kind: string, itemId: string], any>("get_local_music_detail", kind, itemId);
}

export function getArtistBackground(artistName: string): Promise<string> {
  return call<[artistName: string], string>("get_artist_background", artistName);
}

export function getLocalMusicCover(coverId: string): Promise<string> {
  return call<[coverId: string], string>("get_local_music_cover", coverId);
}

export function getLocalMusicArtistProfile(artistId: string, artistName: string): Promise<string> {
  return call<[artistId: string, artistName: string], string>("get_local_music_artist_profile", artistId, artistName);
}

export function clearLocalMusicCache(): Promise<LocalMusicResult> {
  return call<[], LocalMusicResult>("clear_local_music_cache");
}

export function buildLocalMusicCache(): Promise<LocalMusicResult> {
  return call<[], LocalMusicResult>("build_local_music_cache");
}

export function getLocalMusicCacheProgress(): Promise<LocalMusicCacheProgress> {
  return call<[], LocalMusicCacheProgress>("get_local_music_cache_progress");
}

export function updateLocalMusicFrontendState(state: any): Promise<boolean> {
  return call<[state: any], boolean>("update_local_music_frontend_state", state);
}

export function getLocalMusicStreamBase(): Promise<string> {
  return call<[], string>("get_local_music_stream_base");
}

export function getLocalMusicTrack(trackId: string): Promise<LocalMusicResult> {
  return call<[trackId: string], LocalMusicResult>("get_local_music_track", trackId);
}

export function getLocalMusicTracks(trackIds: string[]): Promise<LocalMusicResult> {
  return call<[trackIds: string[]], LocalMusicResult>("get_local_music_tracks", trackIds);
}

export function playLocalMusicItems(trackIds: string[], startIndex = 0): Promise<LocalMusicResult> {
  return call<[trackIds: string[], startIndex: number], LocalMusicResult>("play_local_music_items", trackIds, startIndex);
}

export function localMusicCommand(command: "play_pause" | "next" | "previous" | "shuffle" | "repeat"): Promise<LocalMusicResult> {
  return call<[command: string], LocalMusicResult>("local_music_command", command);
}

export function getLocalMusicState(): Promise<LocalMusicResult> {
  return call<[], LocalMusicResult>("get_local_music_state");
}

export function setLocalMusicVolume(volume: number): Promise<LocalMusicResult> {
  return call<[volume: number], LocalMusicResult>("set_local_music_volume", volume);
}

export function openLocalMusic(): Promise<string> {
  return call<[], string>("open_local_music");
}

export type PluginUpdateFailure = {
  ok: false;
  reason: string;
  message: string;
  current: string;
};

export type PluginUpdateCheck =
  | {
      ok: true;
      current: string;
      updateAvailable: boolean;
      checkedAt: number | null;
      version?: string;
      notes?: string;
      size?: number;
    }
  | PluginUpdateFailure;

export type PreparedPluginUpdate =
  | { ok: true; current: string; version: string; artifact: string; sha256: string; size: number }
  | PluginUpdateFailure;

export function checkPluginUpdate(force = false): Promise<PluginUpdateCheck> {
  return call<[force: boolean], PluginUpdateCheck>("check_plugin_update", force);
}

export function preparePluginUpdate(): Promise<PreparedPluginUpdate> {
  return call<[], PreparedPluginUpdate>("prepare_plugin_update");
}

export function preparePluginReinstall(): Promise<PreparedPluginUpdate> {
  return call<[], PreparedPluginUpdate>("prepare_plugin_reinstall");
}

export type PluginInstallReady = { ok: true; stopped: { pid: number; name: string }[] } | PluginUpdateFailure;

/** Stop the plugin's helper programs so Decky can replace every file (main.py). */
export function prepareForPluginInstall(): Promise<PluginInstallReady> {
  return call<[], PluginInstallReady>("prepare_for_plugin_install");
}

export type InstallProblem = { path: string; kind: "missing" | "size" | "hash" | "unreadable" | "version" | "invalid" };

/** mix_install_check.InstallCheck.snapshot() */
export type InstallCheck = {
  status: "ok" | "broken" | "unverified";
  phase: "sizes" | "hashing" | "done" | "stopped";
  version: string;
  totalFiles: number;
  hashedFiles: number;
  problemCount: number;
  problems: InstallProblem[];
  manifestError: string;
  backendError: string;
  safeMode: boolean;
};

export function getInstallCheck(): Promise<InstallCheck> {
  return call<[], InstallCheck>("get_install_check");
}
