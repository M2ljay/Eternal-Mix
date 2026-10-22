import { useEffect, useState, useSyncExternalStore } from "react";
import * as python from "./python";
import { getTranslations } from "./i18n";

export type LocalRepeatMode = "None" | "One" | "All";

export type LocalAudioState = {
  track: any | null;
  queue: any[];
  index: number;
  status: "Playing" | "Paused" | "Stopped";
  position: number;
  length: number;
  volume: number;
  shuffleActive: boolean;
  repeatMode: LocalRepeatMode;
  canPrevious: boolean;
  canNext: boolean;
  error: string;
  playbackRate: number;
};

export type LocalAudioLevels = {
  energy: number;
  bass: number;
  mid: number;
  treble: number;
};

const emptyState: LocalAudioState = {
  track: null,
  queue: [],
  index: -1,
  status: "Stopped",
  position: 0,
  length: 0,
  volume: 100,
  shuffleActive: false,
  repeatMode: "None",
  canPrevious: false,
  canNext: false,
  error: "",
  playbackRate: 1,
};
const LEGACY_LOCAL_SESSION_STORAGE_KEY = "nowPlaying:lastLocalTrack:v1";

// Podcast resume: per-episode positions for YouTube Music podcast episodes only.
// Songs and local files are never saved, and nothing is restored when a view
// mounts; a saved position is applied only when that episode is loaded.
const PODCAST_RESUME_STORAGE_KEY = "nowPlaying:podcastResume:v1";
const PODCAST_RESUME_MAX_ENTRIES = 300;
const PODCAST_RESUME_MIN_MS = 10000;
const PODCAST_RESUME_END_MARGIN_MS = 30000;
function podcastResumeKey(track: any) {
  if (!track || String(track.sourceKey ?? "") !== "youtubeMusic" || track.isPodcast !== true) return "";
  return String(track.videoId ?? track.id ?? "");
}
function readPodcastResume(): Record<string, any> {
  try {
    const raw = window.localStorage?.getItem(PODCAST_RESUME_STORAGE_KEY);
    const value = raw ? JSON.parse(raw) : {};
    return value && typeof value === "object" && !Array.isArray(value) ? value : {};
  } catch {
    return {};
  }
}
function writePodcastResume(entries: Record<string, any>) {
  try {
    const keys = Object.keys(entries);
    if (keys.length > PODCAST_RESUME_MAX_ENTRIES) {
      keys.sort((left, right) => Number(entries[left]?.t || 0) - Number(entries[right]?.t || 0));
      for (const key of keys.slice(0, keys.length - PODCAST_RESUME_MAX_ENTRIES)) delete entries[key];
    }
    window.localStorage?.setItem(PODCAST_RESUME_STORAGE_KEY, JSON.stringify(entries));
  } catch {
    // Storage can be unavailable in restricted Steam CEF contexts.
  }
}
// Finished episodes, for the "played" marks in episode lists.
const PODCAST_PLAYED_STORAGE_KEY = "nowPlaying:podcastPlayed:v1";
const PODCAST_PLAYED_MAX_ENTRIES = 2000;
const PODCAST_PROGRESS_EVENT = "nowPlaying:podcastProgress";
function readPodcastPlayed(): Record<string, number> {
  try {
    const raw = window.localStorage?.getItem(PODCAST_PLAYED_STORAGE_KEY);
    const value = raw ? JSON.parse(raw) : {};
    return value && typeof value === "object" && !Array.isArray(value) ? value : {};
  } catch {
    return {};
  }
}
function writePodcastPlayed(entries: Record<string, number>) {
  try {
    const keys = Object.keys(entries);
    if (keys.length > PODCAST_PLAYED_MAX_ENTRIES) {
      keys.sort((left, right) => Number(entries[left] || 0) - Number(entries[right] || 0));
      for (const key of keys.slice(0, keys.length - PODCAST_PLAYED_MAX_ENTRIES)) delete entries[key];
    }
    window.localStorage?.setItem(PODCAST_PLAYED_STORAGE_KEY, JSON.stringify(entries));
  } catch {
    // Storage can be unavailable in restricted Steam CEF contexts.
  }
}
let lastProgressNotice = 0;
function notifyPodcastProgress(force: boolean) {
  // Lists re-render on this; regular position saves are throttled.
  const now = Date.now();
  if (!force && now - lastProgressNotice < 15000) return;
  lastProgressNotice = now;
  try { window.dispatchEvent(new Event(PODCAST_PROGRESS_EVENT)); } catch { /* no window */ }
}
// Just enough of the episode to list it under "Continue listening".
function episodeSummary(track: any) {
  if (!track) return undefined;
  const images = Array.isArray(track.images) ? track.images.filter((image: any) => image?.url).slice(-1) : [];
  return {
    id: String(track.id ?? track.videoId ?? ""),
    videoId: String(track.videoId ?? track.id ?? ""),
    name: String(track.name ?? ""),
    artists: Array.isArray(track.artists) ? track.artists.slice(0, 2).map((artist: any) => ({ name: String(artist?.name ?? ""), id: String(artist?.id ?? "") })) : [],
    album: track.album && typeof track.album === "object" ? { id: String(track.album.id ?? ""), name: String(track.album.name ?? ""), images } : undefined,
    images,
    duration_ms: Number(track.duration_ms || 0),
  };
}
function markPodcastPlayed(key: string) {
  if (!key) return;
  const played = readPodcastPlayed();
  played[key] = Date.now();
  writePodcastPlayed(played);
}
function clearPodcastPosition(key: string) {
  if (!key) return;
  const entries = readPodcastResume();
  if (key in entries) {
    delete entries[key];
    writePodcastResume(entries);
    notifyPodcastProgress(true);
  }
}
function finishPodcastEpisode(key: string) {
  if (!key) return;
  markPodcastPlayed(key);
  clearPodcastPosition(key);
  notifyPodcastProgress(true);
}
function savePodcastPosition(key: string, positionMs: number, lengthMs: number, track?: any) {
  if (!key) return;
  const position = Math.floor(Number(positionMs || 0));
  const length = Math.floor(Number(lengthMs || 0));
  if (length > 0 && position >= length - PODCAST_RESUME_END_MARGIN_MS) {
    finishPodcastEpisode(key);
    return;
  }
  if (position < PODCAST_RESUME_MIN_MS) return;
  const entries = readPodcastResume();
  const isNew = !(key in entries);
  entries[key] = { p: position, l: length, t: Date.now(), m: episodeSummary(track) ?? entries[key]?.m };
  writePodcastResume(entries);
  const played = readPodcastPlayed();
  if (key in played) {
    // Listening again: show progress instead of the played mark.
    delete played[key];
    writePodcastPlayed(played);
  }
  notifyPodcastProgress(isNew);
}

export type PodcastProgress = { played: boolean; positionMs: number; lengthMs: number };
export function podcastProgress(track: any): PodcastProgress | null {
  const key = String(track?.videoId ?? track?.id ?? "");
  if (!key || track?.isPodcast !== true) return null;
  const entry = readPodcastResume()[key];
  if (entry && Number(entry.p) >= PODCAST_RESUME_MIN_MS) {
    return { played: false, positionMs: Number(entry.p), lengthMs: Number(entry.l || track?.duration_ms || 0) };
  }
  return key in readPodcastPlayed() ? { played: true, positionMs: 0, lengthMs: 0 } : null;
}
// In-progress episodes, most recently listened first.
export function continueListeningEpisodes(limit = 12): any[] {
  const entries = readPodcastResume();
  return Object.entries(entries)
    .filter(([, entry]) => entry?.m?.name && Number(entry?.p) >= PODCAST_RESUME_MIN_MS)
    .sort((left, right) => Number(right[1]?.t || 0) - Number(left[1]?.t || 0))
    .slice(0, limit)
    .map(([key, entry]) => ({ ...entry.m, id: entry.m.id || key, videoId: entry.m.videoId || key, type: "track", sourceKey: "youtubeMusic", isPodcast: true, duration_ms: Number(entry.m.duration_ms || entry.l || 0) }));
}
export function usePodcastProgressVersion() {
  const [version, setVersion] = useState(0);
  useEffect(() => {
    const listener = () => setVersion((value) => value + 1);
    window.addEventListener(PODCAST_PROGRESS_EVENT, listener);
    return () => window.removeEventListener(PODCAST_PROGRESS_EVENT, listener);
  }, []);
  return version;
}
function savedPodcastPosition(key: string, lengthMs: number): number {
  if (!key) return 0;
  const entry = readPodcastResume()[key];
  const position = Number(entry?.p || 0);
  const length = Number(lengthMs || entry?.l || 0);
  if (position < PODCAST_RESUME_MIN_MS) return 0;
  if (length > 0 && position >= length - PODCAST_RESUME_END_MARGIN_MS) return 0;
  return position;
}

export function isPodcastEpisode(track: any) {
  return Boolean(podcastResumeKey(track));
}

// Playback speed for podcast episodes; music always plays at 1x.
const PODCAST_SPEED_STORAGE_KEY = "nowPlaying:podcastSpeed:v1";
export const PODCAST_SPEEDS = [0.75, 1, 1.25, 1.5, 1.75, 2];
function readPodcastSpeed(): number {
  try {
    const value = Number(window.localStorage?.getItem(PODCAST_SPEED_STORAGE_KEY));
    return PODCAST_SPEEDS.includes(value) ? value : 1;
  } catch {
    return 1;
  }
}

// Playback events are written to NowPlaying.log by the backend.
function logPlayback(event: string, track: any, details: Record<string, unknown> = {}) {
  const id = String(track?.videoId ?? track?.id ?? "");
  void python.reportDiagnosticEvent("playback", event, {
    track: String(track?.name ?? "").slice(0, 80),
    id,
    source: String(track?.sourceKey ?? "localMusic"),
    ...details,
  }).catch(() => {});
}

function normalizeTrack(entry: any) {
  return entry?.track ?? entry?.item ?? entry;
}

class LocalAudioEngine {
  private audio: HTMLAudioElement | null = null;
  private state: LocalAudioState = { ...emptyState };
  private listeners = new Set<() => void>();
  private streamBase = "";
  private streamBasePromise: Promise<string> | null = null;
  private loadingToken = 0;
  private syncTimer = 0;
  private lastBackendSyncAt = 0;
  private originalQueue: any[] = [];
  private recoveryAttempts = 0;
  private recoveryInFlight = false;
  private lastProgressAt = 0;
  private stallRecoveryTimer = 0;
  private audioContext: AudioContext | null = null;
  private analyser: AnalyserNode | null = null;
  private frequencyData: Uint8Array<ArrayBuffer> | null = null;
  private lastContextResumeAt = 0;
  private bufferingSince = 0;
  private elementRouted = false;
  private captureStream: any = null;
  private captureSource: MediaStreamAudioSourceNode | null = null;
  private captureTrackId = "";
  private prefetchedVideoIds = new Set<string>();
  private failedYouTubeMusicIds = new Set<string>();
  private audioTrackKey = "";
  private audioTrackItem: any = null;
  private pendingResumeMs = 0;
  private lastResumeSaveAt = 0;
  private playRetryCleanup: (() => void) | null = null;
  private podcastSpeed = typeof window === "undefined" ? 1 : readPodcastSpeed();

  constructor() {
    if (typeof window === "undefined") return;
    // Local playback is intentionally session-only. Older builds persisted the
    // last track/queue/position, which could overwrite the live player state
    // whenever QAM, Big Picture or Settings remounted. Remove that legacy entry
    // once and never restore playback state from disk again.
    try {
      window.localStorage?.removeItem(LEGACY_LOCAL_SESSION_STORAGE_KEY);
    } catch {
      // Storage can be unavailable in restricted Steam CEF contexts.
    }
  }

  private bufferedAheadMs(audio: HTMLAudioElement) {
    try {
      const now = audio.currentTime || 0;
      for (let index = 0; index < audio.buffered.length; index += 1) {
        if (audio.buffered.start(index) <= now + 0.05 && audio.buffered.end(index) >= now) return Math.round((audio.buffered.end(index) - now) * 1000);
      }
    } catch { /* no buffered ranges */ }
    return 0;
  }

  private clearStallRecoveryTimer() {
    if (this.stallRecoveryTimer) window.clearTimeout(this.stallRecoveryTimer);
    this.stallRecoveryTimer = 0;
  }

  private scheduleStallRecovery(reason: string) {
    if (this.state.status !== "Playing" || this.recoveryInFlight || this.recoveryAttempts >= 2) return;
    this.clearStallRecoveryTimer();
    const progressMarker = this.lastProgressAt;
    this.stallRecoveryTimer = window.setTimeout(() => {
      this.stallRecoveryTimer = 0;
      if (this.state.status !== "Playing" || this.recoveryInFlight || this.recoveryAttempts >= 2) return;
      if (this.lastProgressAt !== progressMarker || Date.now() - this.lastProgressAt < 2400) return;
      void this.reloadAt(this.state.position, reason);
    }, 2800);
  }

  private ensureAudio() {
    if (this.audio || typeof Audio === "undefined") return this.audio;
    const audio = new Audio();
    audio.crossOrigin = "anonymous";
    audio.preload = "auto";
    audio.volume = Math.max(0, Math.min(1, this.state.volume / 100));
    audio.addEventListener("timeupdate", () => {
      this.lastProgressAt = Date.now();
      this.clearStallRecoveryTimer();
      this.patch({ position: Math.max(0, Math.floor((audio.currentTime || 0) * 1000)) });
      this.scheduleStallRecovery("progress-timeout");
      if (this.audioTrackKey && Date.now() - this.lastResumeSaveAt >= 5000) {
        this.lastResumeSaveAt = Date.now();
        this.persistEpisodePosition();
      }
    });
    audio.addEventListener("durationchange", () => {
      const duration = Number.isFinite(audio.duration) ? Math.max(0, Math.floor(audio.duration * 1000)) : 0;
      this.patch({ length: duration || Number(this.state.track?.duration_ms || 0) });
    });
    audio.addEventListener("loadedmetadata", () => {
      const duration = Number.isFinite(audio.duration) ? Math.max(0, Math.floor(audio.duration * 1000)) : 0;
      this.patch({ length: duration || Number(this.state.track?.duration_ms || 0), error: "" });
      this.applyPendingResume(audio);
    });
    audio.addEventListener("play", () => {
      // `play` fires the instant play() is called, before the stream has buffered.
      // Do not flip to "Playing" here or the smooth progress bar starts ticking
      // from 0 while the audio is still resolving, then snaps back when the real
      // audio finally starts. Wait for the `playing` event (actual audio start).
      this.ensureAudioAnalysis();
      void this.audioContext?.resume().catch(() => {});
      this.patch({ error: "" });
    });
    audio.addEventListener("playing", () => {
      if (this.bufferingSince) {
        logPlayback("buffering_ended", this.state.track, { waitedMs: Date.now() - this.bufferingSince });
        this.bufferingSince = 0;
      }
      this.ensureAudioAnalysis();
      void this.audioContext?.resume().catch(() => {});
      this.lastProgressAt = Date.now();
      this.patch({ status: "Playing", position: Math.max(0, Math.floor((audio.currentTime || 0) * 1000)), error: "" });
      this.scheduleStallRecovery("progress-timeout");
      this.prefetchNext();
    });
    audio.addEventListener("pause", () => {
      this.persistEpisodePosition();
      this.clearStallRecoveryTimer();
      if (!audio.ended && !this.recoveryInFlight) this.patch({ status: this.state.track ? "Paused" : "Stopped" });
    });
    audio.addEventListener("ended", () => void this.handleEnded());
    audio.addEventListener("stalled", () => this.scheduleStallRecovery("stalled"));
    audio.addEventListener("waiting", () => {
      if (this.state.status === "Playing" && !this.bufferingSince) {
        this.bufferingSince = Date.now();
        logPlayback("buffering", this.state.track, { positionMs: Math.round((audio.currentTime || 0) * 1000), bufferedAheadMs: this.bufferedAheadMs(audio), readyState: audio.readyState, networkState: audio.networkState });
      }
      this.scheduleStallRecovery("waiting");
    });
    audio.addEventListener("error", () => {
      if (this.recoveryInFlight) return;
      logPlayback("media_error", this.state.track, { code: audio.error?.code ?? 0, status: this.state.status });
      if (this.state.track && this.state.status === "Playing" && this.recoveryAttempts < 2) {
        void this.reloadAt(this.state.position, "media-error");
        return;
      }
      const code = audio.error?.code ?? 0;
      const t = getTranslations("runtime");
      const message = code === 4 ? t.unsupportedLocalFormat : t.localMediaError;
      this.patch({ status: "Stopped", error: message });
    });
    this.audio = audio;
    return audio;
  }

  // 8 output channels, WebAudio discrete order: FL, FR, FC, LFE, SL, SR, BL, BR.
  static readonly SPEAKER_COUNT = 8;
  private surroundMode: "off" | "5.1" | "7.1" = (() => {
    try {
      const stored = String(window.localStorage.getItem("nowPlaying.surroundMode") || "");
      if (stored === "5.1" || stored === "7.1" || stored === "off") return stored;
      if (window.localStorage.getItem("nowPlaying.upmix71") === "1") return "7.1";
    } catch { /* fall through */ }
    return "off";
  })();
  private speakerVolumes: number[] = (() => {
    try {
      const raw = JSON.parse(String(window.localStorage.getItem("nowPlaying.speakerVolumes") || "[]"));
      if (Array.isArray(raw)) return Array.from({ length: 8 }, (_, i) => Math.max(0, Math.min(100, Number(raw[i] ?? 100))));
    } catch { /* fall through */ }
    return Array.from({ length: 8 }, () => 100);
  })();
  private upmixNodes: AudioNode[] = [];
  private channelContribs: { node: GainNode; base: number; channel: number }[] = [];

  // Route the analyser output either straight to a stereo destination, or through
  // a stereo→7.1 upmix matrix when the option is on and the output device
  // actually supports multichannel. Each output channel has an adjustable volume.
  // Used by Your Music and YouTube Music (both play through this in-browser
  // player); Spotify plays through its own bridge and is unaffected.
  private applyOutputRouting() {
    const context = this.audioContext;
    const analyser = this.analyser;
    if (!context || !analyser) return;
    try { analyser.disconnect(); } catch { /* was not connected yet */ }
    // Direct playback: the analyser only listens and must not output.
    if (!this.elementRouted) return;
    for (const node of this.upmixNodes) { try { node.disconnect(); } catch { /* ignore */ } }
    this.upmixNodes = [];
    this.channelContribs = [];
    const destination = context.destination;
    const maxChannels = Number(destination.maxChannelCount || 2);
    if (this.surroundMode !== "off" && maxChannels >= 6) {
      const channels = this.surroundMode === "7.1" ? Math.min(8, maxChannels) : Math.min(6, maxChannels);
      try {
        destination.channelCount = channels;
        destination.channelCountMode = "explicit";
        destination.channelInterpretation = "discrete";
      } catch { /* device may reject an explicit layout */ }
      const splitter = context.createChannelSplitter(2);
      const merger = context.createChannelMerger(channels);
      analyser.connect(splitter);
      const route = (input: number, output: number, base: number, lowPass = false) => {
        const gain = context.createGain();
        gain.gain.value = base * (Math.max(0, Math.min(100, this.speakerVolumes[output] ?? 100)) / 100);
        if (lowPass) {
          const filter = context.createBiquadFilter();
          filter.type = "lowpass";
          filter.frequency.value = 120;
          filter.Q.value = 0.707;
          splitter.connect(filter, input, 0);
          filter.connect(gain);
          this.upmixNodes.push(filter);
        } else {
          splitter.connect(gain, input, 0);
        }
        gain.connect(merger, 0, output);
        this.upmixNodes.push(gain);
        this.channelContribs.push({ node: gain, base, channel: output });
      };
      route(0, 0, 1);                       // Front Left  = L
      route(1, 1, 1);                       // Front Right = R
      route(0, 2, 0.5); route(1, 2, 0.5);   // Center = (L+R)/2
      route(0, 3, 0.15, true); route(1, 3, 0.15, true); // LFE = low-passed mono, restrained
      if (channels >= 6) { route(0, 4, 0.9); route(1, 5, 0.9); }   // Side L/R
      if (channels >= 8) { route(0, 6, 0.75); route(1, 7, 0.75); } // Back L/R
      merger.connect(destination);
      this.upmixNodes.push(splitter, merger);
    } else {
      try { destination.channelCount = Math.min(2, maxChannels); } catch { /* ignore */ }
      analyser.connect(destination);
    }
  }

  setSurroundMode(mode: "off" | "5.1" | "7.1") {
    this.surroundMode = mode === "5.1" || mode === "7.1" ? mode : "off";
    try { window.localStorage.setItem("nowPlaying.surroundMode", this.surroundMode); } catch { /* storage unavailable */ }
    if (this.surroundMode !== "off" && this.audio) this.ensureElementRouting();
    this.applyOutputRouting();
  }

  getSurroundMode(): "off" | "5.1" | "7.1" {
    return this.surroundMode;
  }

  getSpeakerVolumes(): number[] {
    return [...this.speakerVolumes];
  }

  setSpeakerVolume(channel: number, value: number) {
    if (channel < 0 || channel >= LocalAudioEngine.SPEAKER_COUNT) return;
    const volume = Math.max(0, Math.min(100, Math.round(Number(value) || 0)));
    this.speakerVolumes[channel] = volume;
    try { window.localStorage.setItem("nowPlaying.speakerVolumes", JSON.stringify(this.speakerVolumes)); } catch { /* storage unavailable */ }
    // Update the live gain nodes for this channel without rebuilding the graph.
    for (const contrib of this.channelContribs) {
      if (contrib.channel === channel) contrib.node.gain.value = contrib.base * (volume / 100);
    }
  }

  // Playback normally goes straight from the <audio> element to Windows, and
  // the visualizer listens to a copy (captureStream). Routing the sound itself
  // through Web Audio made it pause whenever Steam's interface was busy, for
  // example while moving around menus. Only the 5.1/7.1 upmix needs the sound
  // to pass through Web Audio, so that path is used only when it is turned on.
  private ensureAudioAnalysis() {
    if (!this.audio || typeof window === "undefined") return;
    if (this.surroundMode !== "off" || this.elementRouted) {
      this.ensureElementRouting();
      return;
    }
    if (!this.createAnalysisContext()) return;
    this.refreshCaptureSource();
  }

  private createAnalysisContext() {
    if (this.audioContext && this.analyser) return true;
    const AudioContextConstructor = window.AudioContext;
    if (!AudioContextConstructor) return false;
    try {
      const context = new AudioContextConstructor({ latencyHint: "playback" });
      const analyser = context.createAnalyser();
      analyser.fftSize = 256;
      analyser.smoothingTimeConstant = .78;
      this.audioContext = context;
      this.analyser = analyser;
      this.frequencyData = new Uint8Array(analyser.frequencyBinCount);
      return true;
    } catch {
      this.audioContext = null;
      this.analyser = null;
      this.frequencyData = null;
      return false;
    }
  }

  private refreshCaptureSource() {
    const audio = this.audio as any;
    const context = this.audioContext;
    const analyser = this.analyser;
    if (this.elementRouted || !audio || !context || !analyser) return;
    if (!this.captureStream) {
      const capture = typeof audio.captureStream === "function" ? audio.captureStream() : null;
      if (!capture) {
        // No way to listen without taking over the output; fall back.
        this.ensureElementRouting();
        return;
      }
      this.captureStream = capture;
      // A new source (next track) replaces the stream's audio track.
      capture.addEventListener?.("addtrack", () => this.refreshCaptureSource());
      this.logAudioOutput("direct");
    }
    const track = this.captureStream.getAudioTracks?.()[0];
    if (!track || (track.id === this.captureTrackId && this.captureSource)) return;
    try { this.captureSource?.disconnect(); } catch { /* already gone */ }
    try {
      this.captureSource = context.createMediaStreamSource(new MediaStream([track]));
      this.captureSource.connect(analyser);
      this.captureTrackId = track.id;
    } catch {
      this.captureSource = null;
      this.captureTrackId = "";
    }
  }

  private ensureElementRouting() {
    if (this.elementRouted || !this.audio) return;
    if (!this.createAnalysisContext()) return;
    const context = this.audioContext!;
    try { this.captureSource?.disconnect(); } catch { /* already gone */ }
    this.captureSource = null;
    this.captureTrackId = "";
    try {
      const source = context.createMediaElementSource(this.audio);
      source.connect(this.analyser!);
      this.elementRouted = true;
      this.applyOutputRouting();
      this.logAudioOutput("web-audio");
    } catch {
      // Leave playback on the direct path.
    }
  }

  private logAudioOutput(path: string) {
    const context = this.audioContext;
    if (!context) return;
    logPlayback("audio_output", this.state.track, {
      path,
      sampleRate: context.sampleRate,
      baseLatencyMs: Math.round(Number((context as any).baseLatency || 0) * 1000),
      surround: this.surroundMode,
    });
  }

  getAudioLevels(): LocalAudioLevels {
    if (!this.analyser && this.audio && this.state.status === "Playing") {
      // The play-time hookup can fail transiently in restricted CEF contexts;
      // retry lazily while a visualizer is actually reading levels.
      this.ensureAudioAnalysis();
    }
    if (this.audioContext && this.audioContext.state === "suspended" && Date.now() - this.lastContextResumeAt > 1500) {
      this.lastContextResumeAt = Date.now();
      void this.audioContext.resume().catch(() => {});
    }
    const analyser = this.analyser;
    const data = this.frequencyData;
    if (!analyser || !data || this.state.status !== "Playing") {
      return { energy: 0, bass: 0, mid: 0, treble: 0 };
    }
    analyser.getByteFrequencyData(data);
    const average = (from: number, to: number) => {
      let total = 0;
      const end = Math.max(from + 1, Math.min(data.length, to));
      for (let index = from; index < end; index += 1) total += data[index];
      return total / (end - from) / 255;
    };
    const bass = average(0, Math.ceil(data.length * .12));
    const mid = average(Math.ceil(data.length * .12), Math.ceil(data.length * .45));
    const treble = average(Math.ceil(data.length * .45), data.length);
    return {
      bass,
      mid,
      treble,
      energy: Math.min(1, bass * .48 + mid * .36 + treble * .16),
    };
  }

  restoreLastTrack(): Promise<LocalAudioState> {
    // Kept as a compatibility no-op for callers compiled against older builds.
    // Playback state now comes only from this live singleton.
    return Promise.resolve(this.state);
  }

  initialize(): Promise<string> {
    if (this.streamBase) return Promise.resolve(this.streamBase);
    if (!this.streamBasePromise) {
      this.streamBasePromise = python.getLocalMusicStreamBase()
        .then((value) => {
          this.streamBase = String(value || "").replace(/\/$/, "");
          return this.streamBase;
        })
        .finally(() => { this.streamBasePromise = null; });
    }
    return this.streamBasePromise;
  }

  private async streamUrl(track: any) {
    if (String(track?.sourceKey ?? "") === "youtubeMusic") {
      const result = await python.youtubeMusicPrepareStream(String(track?.videoId ?? track?.id ?? ""));
      if (!result?.ok || !result.data?.url) {
        throw new Error(String(result?.error ?? "YouTube Music stream is unavailable"));
      }
      // Replace the small search thumbnail with yt-dlp's full-resolution square
      // cover so the fullscreen and top-bar artwork for the playing track is crisp.
      const thumbnail = String((result.data as any)?.thumbnail ?? "").trim();
      if (thumbnail && track && typeof track === "object") {
        const highRes = { url: thumbnail, width: 1200, height: 1200 };
        track.images = [...(Array.isArray(track.images) ? track.images : []), highRes];
        if (track.album && typeof track.album === "object") {
          track.album.images = [...(Array.isArray(track.album.images) ? track.album.images : []), highRes];
        }
        this.emit(false);
      }
      return String(result.data.url);
    }

    const id = encodeURIComponent(String(track?.id ?? ""));
    return `${this.streamBase}/track/${id}`;
  }

  // Warm the backend stream cache for the next queued YouTube Music track while
  // the current one plays, so skipping/advancing resolves instantly instead of
  // waiting ~1-2 s for yt-dlp. Best-effort and de-duplicated per next track.
  private prefetchNext() {
    try {
      const queue = this.state.queue;
      if (!queue || !queue.length) return;
      // Warm a few upcoming tracks on the backend's dedicated single-worker
      // prefetch executor. That executor never shares threads with on-demand
      // playback resolution, so prefetch can no longer slow down selecting or
      // advancing a track (the problem the 6-at-once version caused). Requests are
      // de-duplicated so each track is only prepared once.
      const AHEAD = 4;
      for (let offset = 1; offset <= AHEAD; offset += 1) {
        const track = queue[this.state.index + offset];
        if (!track || String((track as any)?.sourceKey ?? "") !== "youtubeMusic") continue;
        const videoId = String((track as any)?.videoId ?? (track as any)?.id ?? "");
        if (!videoId || this.prefetchedVideoIds.has(videoId)) continue;
        this.prefetchedVideoIds.add(videoId);
        void python.youtubeMusicPrefetchStream(videoId).catch(() => { this.prefetchedVideoIds.delete(videoId); });
      }
      if (this.prefetchedVideoIds.size > 60) {
        this.prefetchedVideoIds = new Set(Array.from(this.prefetchedVideoIds).slice(-30));
      }
    } catch {
      // Prefetch is purely an optimisation; ignore any failure.
    }
  }

  private emit(syncBackend = true) {
    for (const listener of this.listeners) listener();
    if (this.syncTimer) window.clearTimeout(this.syncTimer);
    this.syncTimer = 0;
    if (!syncBackend) return;
    const elapsed = Date.now() - this.lastBackendSyncAt;
    const delay = Math.max(180, 750 - elapsed);
    this.syncTimer = window.setTimeout(() => {
      this.syncTimer = 0;
      this.lastBackendSyncAt = Date.now();
      const state = this.state;
      void python.updateLocalMusicFrontendState({
        track: state.track,
        index: state.index,
        queueLength: state.queue.length,
        status: state.status,
        position: state.position,
        length: state.length,
        volume: state.volume,
        shuffleActive: state.shuffleActive,
        repeatMode: state.repeatMode,
        canPrevious: state.canPrevious,
        canNext: state.canNext,
        sourceKey: String(state.track?.sourceKey ?? "localMusic"),
      }).catch(() => {});
    }, delay);
  }

  private patch(update: Partial<LocalAudioState>) {
    this.state = { ...this.state, ...update };
    this.emit();
  }

  subscribe = (listener: () => void) => {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  };

  getSnapshot = () => this.state;

  async playItems(entries: any[], startIndex = 0) {
    const sourceQueue = entries.map(normalizeTrack).filter((track: any) => track?.id);
    if (!sourceQueue.length) throw new Error(getTranslations("runtime").noPlayableLocalTracks);
    const requestedIndex = Math.max(0, Math.min(Math.floor(startIndex || 0), sourceQueue.length - 1));
    this.originalQueue = [...sourceQueue];
    this.failedYouTubeMusicIds.clear();
    const queue = this.state.shuffleActive
      ? [sourceQueue[requestedIndex], ...this.shuffleEntries(sourceQueue.filter((_, index) => index !== requestedIndex))]
      : sourceQueue;
    const index = this.state.shuffleActive ? 0 : requestedIndex;
    // Keep the currently audible track and metadata visible while a remote
    // YouTube Music URL is resolving. `loadCurrent` swaps the source and the
    // metadata together once the new stream is actually ready.
    this.state = {
      ...this.state,
      queue,
      index,
      canPrevious: index > 0 || queue.length > 1,
      canNext: index < queue.length - 1 || queue.length > 1,
      error: "",
    };
    this.emit();
    await this.loadCurrent(true);
    return this.state;
  }

  private async loadCurrent(autoPlay: boolean) {
    const audio = this.ensureAudio();
    const track = this.state.queue[this.state.index];
    if (!audio || !track) throw new Error(getTranslations("runtime").localPlayerUnavailable);
    await this.initialize();
    const token = ++this.loadingToken;
    this.recoveryAttempts = 0;
    this.recoveryInFlight = false;
    this.clearStallRecoveryTimer();
    this.clearPlayRetry();
    this.lastProgressAt = Date.now();
    // Show the new track's title/cover IMMEDIATELY (like local music) so QAM and
    // Big Picture update the instant you press next/previous or pick a track —
    // never waiting for the remote stream to resolve. The old audio is paused so
    // its position can't tick under the new metadata; the progress bar stays at 0
    // until the real `playing` event fires with the new audio.
    this.persistEpisodePosition();
    audio.pause();
    this.patch({ track, position: 0, length: Number(track?.duration_ms || 0), status: autoPlay ? "Paused" : "Stopped", error: "" });
    // YouTube Music: start playback now, while the button press is still being
    // handled, instead of after the 1-10 s stream lookup. The local relay
    // address is fixed and the relay resolves the stream itself, so the audio
    // simply begins once the first bytes arrive.
    const isYouTube = String(track?.sourceKey ?? "") === "youtubeMusic";
    const relayId = String(track?.videoId ?? track?.id ?? "").replace(/[^A-Za-z0-9_-]/g, "").slice(0, 32);
    const earlyUrl = isYouTube && autoPlay && this.streamBase && relayId ? `${this.streamBase}/ytmusic-track/${encodeURIComponent(relayId)}` : "";
    let playAttempt: Promise<void> | null = null;
    if (earlyUrl) {
      this.attachSource(audio, track, earlyUrl);
      playAttempt = audio.play();
      playAttempt.catch(() => {});
    }
    let streamUrl = "";
    try {
      streamUrl = await this.streamUrl(track);
      if (isYouTube) {
        this.failedYouTubeMusicIds.delete(String(track?.videoId ?? track?.id ?? ""));
      }
    } catch (error: any) {
      if (token !== this.loadingToken) return;
      logPlayback("stream_lookup_failed", track, { error: String(error?.message ?? error).slice(0, 200) });
      if (playAttempt) audio.pause();
      if (await this.skipUnavailableYouTubeMusicTrack(track, error, autoPlay)) return;
      const message = String(error?.message ?? error ?? getTranslations("runtime").localPlaybackStartFailed);
      this.patch({ status: "Stopped", error: message });
      throw error;
    }
    if (token !== this.loadingToken) return;
    if (!playAttempt || streamUrl !== earlyUrl) {
      this.attachSource(audio, track, streamUrl);
      playAttempt = autoPlay ? audio.play() : null;
    }
    if (playAttempt) {
      try {
        await playAttempt;
      } catch (error: any) {
        if (token !== this.loadingToken) return;
        // Refused or interrupted is not the track's fault: keep it loaded
        // instead of skipping through the whole queue.
        if (error?.name === "NotAllowedError") {
          logPlayback("play_refused", track, { retry: "next press or timer" });
          this.patch({ status: "Paused", error: "" });
          this.schedulePlayRetry(token);
          return;
        }
        if (error?.name === "AbortError") {
          logPlayback("play_interrupted", track);
          this.patch({ status: audio.paused ? "Paused" : this.state.status, error: "" });
          return;
        }
        if (await this.skipUnavailableYouTubeMusicTrack(track, error, autoPlay)) return;
        const message = String(error?.message ?? error ?? getTranslations("runtime").localPlaybackStartFailed);
        this.patch({ status: "Stopped", error: message });
        throw error;
      }
    }
  }

  private attachSource(audio: HTMLAudioElement, track: any, url: string) {
    this.audioTrackKey = podcastResumeKey(track);
    this.audioTrackItem = this.audioTrackKey ? track : null;
    this.pendingResumeMs = savedPodcastPosition(this.audioTrackKey, Number(track?.duration_ms || 0));
    this.lastResumeSaveAt = Date.now();
    audio.src = `${url}?v=${encodeURIComponent(String(track?.modifiedAt ?? track?.id ?? Date.now()))}`;
    audio.load();
    this.applyPlaybackRate();
  }

  // Playback was refused (for example before Steam has registered any input
  // after boot). Try again on the next button press or tap, and a few times
  // on a timer in case the audio device was still starting up.
  private schedulePlayRetry(token: number) {
    this.clearPlayRetry();
    if (typeof window === "undefined") return;
    const attempt = () => {
      const audio = this.audio;
      if (token !== this.loadingToken || !audio || !audio.paused || !audio.src) {
        this.clearPlayRetry();
        return;
      }
      audio.play().then(() => {
        logPlayback("play_retry_succeeded", this.state.track);
        this.clearPlayRetry();
      }).catch(() => {});
    };
    const events = ["pointerdown", "keydown", "vgp_onbuttondown"];
    for (const name of events) window.addEventListener(name, attempt, true);
    const timers = [1500, 4000, 8000, 15000].map((delay) => window.setTimeout(attempt, delay));
    this.playRetryCleanup = () => {
      for (const name of events) window.removeEventListener(name, attempt, true);
      for (const timer of timers) window.clearTimeout(timer);
    };
  }

  private clearPlayRetry() {
    const cleanup = this.playRetryCleanup;
    this.playRetryCleanup = null;
    cleanup?.();
  }

  private async skipUnavailableYouTubeMusicTrack(track: any, error: unknown, autoPlay: boolean) {
    if (!autoPlay || String(track?.sourceKey ?? "") !== "youtubeMusic" || this.state.queue.length < 2) return false;
    const failedId = String(track?.videoId ?? track?.id ?? "");
    if (failedId) this.failedYouTubeMusicIds.add(failedId);
    void python.reportDiagnosticEvent("youtubeMusic", "unplayable_track_skipped", {
      videoId: failedId,
      title: String(track?.name ?? track?.title ?? ""),
      error: String((error as any)?.message ?? error ?? "stream unavailable"),
    }).catch(() => {});

    for (let step = 1; step < this.state.queue.length; step += 1) {
      const nextIndex = (this.state.index + step) % this.state.queue.length;
      const candidate = this.state.queue[nextIndex];
      const candidateId = String(candidate?.videoId ?? candidate?.id ?? "");
      if (!candidateId || this.failedYouTubeMusicIds.has(candidateId)) continue;
      this.patch({ index: nextIndex, error: "" });
      await this.loadCurrent(true);
      return true;
    }
    return false;
  }

  async playPause() {
    this.clearPlayRetry();
    const audio = this.ensureAudio();
    if (!audio) return this.state;
    if ((!this.state.track && this.state.queue.length) || (this.state.track && !audio.src)) await this.loadCurrent(true);
    else if (audio.paused) await audio.play();
    else audio.pause();
    return this.state;
  }

  async next() {
    if (!this.state.queue.length) return this.state;
    let index = this.state.index;
    if (index < this.state.queue.length - 1) index += 1;
    else if (this.state.repeatMode === "All") {
      if (this.state.shuffleActive && this.originalQueue.length > 1) {
        const currentId = String(this.state.track?.id ?? "");
        let queue = this.shuffleEntries(this.originalQueue);
        if (String(queue[0]?.id ?? "") === currentId) {
          const replacement = queue.findIndex((track) => String(track?.id ?? "") !== currentId);
          if (replacement > 0) [queue[0], queue[replacement]] = [queue[replacement], queue[0]];
        }
        this.patch({ queue, index: 0 });
        await this.loadCurrent(true);
        return this.state;
      }
      index = 0;
    }
    else return this.state;
    this.patch({ index });
    await this.loadCurrent(true);
    return this.state;
  }

  async previous() {
    const audio = this.ensureAudio();
    if (audio && audio.currentTime > 4) {
      audio.currentTime = 0;
      this.patch({ position: 0 });
      return this.state;
    }
    if (!this.state.queue.length) return this.state;
    let index = this.state.index;
    index = index > 0 ? index - 1 : (this.state.repeatMode === "All" ? this.state.queue.length - 1 : 0);
    this.patch({ index });
    await this.loadCurrent(true);
    return this.state;
  }

  async playIndex(index: number) {
    if (!this.state.queue.length) return this.state;
    const nextIndex = Math.max(0, Math.min(Math.floor(index || 0), this.state.queue.length - 1));
    if (nextIndex === this.state.index) {
      const audio = this.ensureAudio();
      if (audio?.paused) await audio.play();
      return this.state;
    }
    this.patch({ index: nextIndex });
    await this.loadCurrent(true);
    return this.state;
  }

  async command(command: "play_pause" | "next" | "previous" | "shuffle" | "repeat") {
    if (command === "play_pause") return this.playPause();
    if (command === "next") return this.next();
    if (command === "previous") return this.previous();
    if (command === "shuffle") {
      const shuffleActive = !this.state.shuffleActive;
      const currentId = String(this.state.track?.id ?? "");
      if (shuffleActive) {
        const prefix = this.state.index >= 0 ? this.state.queue.slice(0, this.state.index + 1) : [];
        const future = this.state.index >= 0 ? this.state.queue.slice(this.state.index + 1) : this.state.queue;
        this.patch({ shuffleActive, queue: [...prefix, ...this.shuffleEntries(future)] });
      } else {
        const restored = this.originalQueue.length ? [...this.originalQueue] : [...this.state.queue];
        const restoredIndex = Math.max(0, restored.findIndex((track) => String(track?.id ?? "") === currentId));
        this.patch({ shuffleActive, queue: restored, index: restoredIndex, track: restored[restoredIndex] ?? this.state.track });
      }
      return this.state;
    }
    const next: LocalRepeatMode = this.state.repeatMode === "None" ? "All" : this.state.repeatMode === "All" ? "One" : "None";
    this.patch({ repeatMode: next });
    return this.state;
  }

  stop() {
    this.clearPlayRetry();
    this.persistEpisodePosition();
    const audio = this.ensureAudio();
    if (audio) {
      audio.pause();
      audio.currentTime = 0;
    }
    this.patch({ status: "Stopped", position: 0 });
    return this.state;
  }

  setVolume(value: number) {
    const volume = Math.max(0, Math.min(100, Math.round(value)));
    const audio = this.ensureAudio();
    if (audio) audio.volume = volume / 100;
    this.patch({ volume });
    return this.state;
  }

  seek(ms: number) {
    const audio = this.ensureAudio();
    if (!audio) return;
    const target = Math.max(0, Number(ms || 0));
    // A deliberate seek wins over a resume position that has not applied yet.
    this.pendingResumeMs = 0;
    audio.currentTime = target / 1000;
    this.lastProgressAt = Date.now();
    this.patch({ position: Math.floor(target) });
  }

  seekBy(deltaMs: number) {
    const audio = this.ensureAudio();
    if (!audio || !this.state.track || !audio.src) return;
    const length = Number.isFinite(audio.duration) && audio.duration > 0 ? audio.duration * 1000 : Number(this.state.length || 0);
    const target = (audio.currentTime || 0) * 1000 + deltaMs;
    this.seek(length > 0 ? Math.min(target, Math.max(0, length - 1000)) : target);
  }

  setPodcastSpeed(rate: number) {
    if (!PODCAST_SPEEDS.includes(rate)) return this.state;
    this.podcastSpeed = rate;
    try {
      window.localStorage?.setItem(PODCAST_SPEED_STORAGE_KEY, String(rate));
    } catch {
      // Storage can be unavailable in restricted Steam CEF contexts.
    }
    this.applyPlaybackRate();
    return this.state;
  }

  cyclePodcastSpeed() {
    const index = PODCAST_SPEEDS.indexOf(this.podcastSpeed);
    return this.setPodcastSpeed(PODCAST_SPEEDS[(index + 1) % PODCAST_SPEEDS.length]);
  }

  destroy() {
    if (this.syncTimer) window.clearTimeout(this.syncTimer);
    this.syncTimer = 0;
    this.lastBackendSyncAt = 0;
    this.clearPlayRetry();
    this.persistEpisodePosition();
    this.audioTrackKey = "";
    this.pendingResumeMs = 0;
    if (this.audio) {
      this.audio.pause();
      this.audio.removeAttribute("src");
      this.audio.load();
    }
    this.audio = null;
    if (this.audioContext) void this.audioContext.close().catch(() => {});
    this.audioContext = null;
    this.analyser = null;
    this.frequencyData = null;
    this.elementRouted = false;
    this.captureStream = null;
    this.captureSource = null;
    this.captureTrackId = "";
    this.originalQueue = [];
    this.failedYouTubeMusicIds.clear();
    this.recoveryAttempts = 0;
    this.recoveryInFlight = false;
    this.clearStallRecoveryTimer();
    this.state = { ...emptyState };
    this.emit(false);
  }

  // Save the position of the episode whose audio is loaded (not the track on
  // screen, which switches before the old audio's pause event arrives).
  private persistEpisodePosition() {
    const audio = this.audio;
    if (!audio || !this.audioTrackKey || this.pendingResumeMs || this.recoveryInFlight) return;
    const length = Number.isFinite(audio.duration) && audio.duration > 0 ? audio.duration * 1000 : Number(this.state.length || 0);
    savePodcastPosition(this.audioTrackKey, (audio.currentTime || 0) * 1000, length, this.audioTrackItem);
  }

  private applyPendingResume(audio: HTMLAudioElement) {
    const target = this.pendingResumeMs;
    this.pendingResumeMs = 0;
    if (!target || !this.audioTrackKey) return;
    const duration = Number.isFinite(audio.duration) && audio.duration > 0 ? audio.duration * 1000 : 0;
    if (duration && target >= duration - PODCAST_RESUME_END_MARGIN_MS) return;
    audio.currentTime = target / 1000;
    this.patch({ position: Math.floor(target) });
    logPlayback("episode_resumed", this.state.track, { positionMs: Math.round(target) });
  }

  // load() resets the rate, so this runs after every source change.
  private applyPlaybackRate() {
    const rate = this.audioTrackKey ? this.podcastSpeed : 1;
    const audio = this.audio;
    if (audio) {
      audio.defaultPlaybackRate = rate;
      audio.playbackRate = rate;
      (audio as any).preservesPitch = true;
    }
    if (this.state.playbackRate !== rate) this.patch({ playbackRate: rate });
  }

  private async handleEnded() {
    const expectedLength = Math.max(Number(this.state.length || 0), Number(this.state.track?.duration_ms || 0));
    const actualPosition = Math.max(Number(this.state.position || 0), Math.floor((this.audio?.currentTime || 0) * 1000));
    if (expectedLength > 0 && expectedLength - actualPosition > 2500 && this.recoveryAttempts < 2) {
      await this.reloadAt(Math.min(expectedLength - 1000, actualPosition + 80), "premature-ended");
      return;
    }
    finishPodcastEpisode(this.audioTrackKey);
    if (this.state.repeatMode === "One") {
      await this.loadCurrent(true);
      return;
    }
    if (this.state.index < this.state.queue.length - 1 || this.state.repeatMode === "All") {
      await this.next();
      return;
    }
    this.patch({ status: "Stopped", position: this.state.length });
  }

  private shuffleEntries(entries: any[]) {
    const result = [...entries];
    for (let index = result.length - 1; index > 0; index -= 1) {
      const swap = Math.floor(Math.random() * (index + 1));
      [result[index], result[swap]] = [result[swap], result[index]];
    }
    return result;
  }

  private async reloadAt(positionMs: number, reason: string) {
    const audio = this.ensureAudio();
    const track = this.state.track;
    if (!audio || !track || this.recoveryInFlight || this.recoveryAttempts >= 2) return;
    await this.initialize();
    this.recoveryInFlight = true;
    this.clearStallRecoveryTimer();
    this.recoveryAttempts += 1;
    const token = ++this.loadingToken;
    const shouldResume = this.state.status === "Playing";
    const resumeAt = Math.max(0, Number(positionMs || 0));
    logPlayback("stall_recovery", track, { reason, positionMs: Math.round(resumeAt), attempt: this.recoveryAttempts });
    try {
      const streamUrl = await this.streamUrl(track);
      if (token !== this.loadingToken) return;
      audio.pause();
      this.pendingResumeMs = 0;
      audio.src = `${streamUrl}?v=${encodeURIComponent(String(track?.modifiedAt ?? track?.id ?? "track"))}&recover=${Date.now()}&reason=${encodeURIComponent(reason)}`;
      audio.load();
      this.applyPlaybackRate();
      await new Promise<void>((resolve, reject) => {
        let settled = false;
        const finish = (error?: Error) => {
          if (settled) return;
          settled = true;
          window.clearTimeout(timeout);
          audio.removeEventListener("loadedmetadata", ready);
          audio.removeEventListener("canplay", ready);
          audio.removeEventListener("error", failed);
          if (error) reject(error); else resolve();
        };
        const ready = () => finish();
        const failed = () => finish(new Error(getTranslations("runtime").localAudioRecoveryFailed));
        const timeout = window.setTimeout(() => finish(), 4500);
        audio.addEventListener("loadedmetadata", ready, { once: true });
        audio.addEventListener("canplay", ready, { once: true });
        audio.addEventListener("error", failed, { once: true });
      });
      if (token !== this.loadingToken) return;
      const maximum = Number.isFinite(audio.duration) && audio.duration > 0 ? Math.max(0, audio.duration * 1000 - 250) : resumeAt;
      audio.currentTime = Math.max(0, Math.min(resumeAt, maximum)) / 1000;
      this.lastProgressAt = Date.now();
      this.patch({ position: Math.floor(audio.currentTime * 1000), error: "" });
      if (shouldResume) await audio.play();
    } catch (error: any) {
      if (token === this.loadingToken) this.patch({ error: String(error?.message ?? error ?? getTranslations("runtime").localAudioRecoveryFailed) });
    } finally {
      if (token === this.loadingToken) this.recoveryInFlight = false;
    }
  }
}

export const localAudioPlayer = new LocalAudioEngine();

export function useLocalAudioState() {
  return useSyncExternalStore(localAudioPlayer.subscribe, localAudioPlayer.getSnapshot, localAudioPlayer.getSnapshot);
}
