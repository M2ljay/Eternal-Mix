// "Play this game's soundtrack" in the Quick Access panel, and the optional launch notice.
// The running game comes from Steam (lifetime notifications, Router.MainRunningApp) and,
// for The Collector's ROM shortcuts, from its now-playing record (window first, its file
// through the backend second). Logic that needs no Steam lives in src/gameSoundtrack.ts.
import { DialogButton, Focusable, Navigation, QuickAccessTab, Router } from "@decky/ui";
import { toaster } from "@decky/api";
import { useEffect, useRef, useState } from "react";
import type { CSSProperties } from "react";
import { FaCompactDisc, FaListUl, FaRedo } from "react-icons/fa";
import * as python from "./python";
import type { SoundtrackCandidate } from "./python";
import { CORE } from "./corePalette";
import { formatTranslation, getTranslations } from "./i18n";
import { MediaRow } from "./mediaRows";
import { playYouTubeMusicCollection } from "./youtubeMusic";
import {
  COLLECTOR_EVENT,
  COLLECTOR_WINDOW_KEY,
  collectorRecord,
  createRunningGameTracker,
  isMusicApp,
  offerEnabled,
  recallPick,
  rememberPick,
  resolveRunningGame,
  type RunningGame,
  type SoundtrackPick,
} from "./gameSoundtrack";

const RUNNING_GAME_EVENT = "nowPlaying:runningGameChanged";
// The Collector publishes its record right after the session starts; give it a moment.
const LAUNCH_SETTLE_MS = 1500;

let tracker: ReturnType<typeof createRunningGameTracker> | null = null;

function storage(): Storage | undefined {
  try { return window.localStorage; } catch { return undefined; }
}

function steamName(appId: number): string {
  try { return String((window as any).appStore?.GetAppOverviewByAppID?.(appId)?.display_name ?? ""); } catch { return ""; }
}

function mainRunningAppId(): number {
  try {
    const router = Router as any;
    const app = router?.MainRunningApp ?? (window as any).SteamUIStore?.MainRunningApp;
    return Number(app?.appid ?? app?.unAppID ?? 0) || 0;
  } catch { return 0; }
}

/** The running game, with The Collector's ROM title when it has one. Never throws. */
export async function currentRunningGame(): Promise<RunningGame | null> {
  const appId = tracker?.current() ?? 0;
  if (!appId) return null;
  let record = collectorRecord((window as any)[COLLECTOR_WINDOW_KEY], appId);
  if (!record && appId > 0x7fffffff) {
    // Non-Steam shortcut and no window record (e.g. Awesome Mix reloaded): try the file.
    try {
      const result = await python.getCollectorNowPlaying(appId);
      record = result?.ok && result.data?.name ? { name: String(result.data.name), platform: String(result.data.platform ?? "") } : null;
    } catch { record = null; }
  }
  return resolveRunningGame(appId, steamName(appId), record);
}

function candidateArtist(candidate: SoundtrackCandidate | SoundtrackPick): string {
  if ("artist" in candidate) return candidate.artist;
  const names = (candidate.artists ?? []).map((artist) => String(artist?.name ?? "")).filter(Boolean);
  return names.join(", ") || String(candidate.owner?.display_name ?? "");
}

function candidateImage(candidate: SoundtrackCandidate): string {
  const images = [...(candidate.images ?? [])].filter((image) => image?.url);
  images.sort((left, right) => Number(right.width || 0) * Number(right.height || 0) - Number(left.width || 0) * Number(left.height || 0));
  return String(images[0]?.url ?? "");
}

function asPick(candidate: SoundtrackCandidate): Omit<SoundtrackPick, "at"> {
  return { kind: candidate.type, id: candidate.id, name: candidate.name, artist: candidateArtist(candidate), image: candidateImage(candidate) };
}

async function playPick(game: RunningGame, pick: Omit<SoundtrackPick, "at">): Promise<boolean> {
  const played = await playYouTubeMusicCollection(pick.kind, pick.id);
  if (played) {
    const store = storage();
    if (store) rememberPick(store, game.key, pick);
  } else {
    const t = getTranslations("soundtrack");
    toaster.toast({ title: t.title, body: t.empty, duration: 3500 });
  }
  return played;
}

function openQuickAccess() {
  try { Navigation.OpenQuickAccessMenu(QuickAccessTab.Decky); } catch {
    try { Navigation.OpenQuickAccessMenu(); } catch { /* nothing else to open */ }
  }
}

async function offerOnLaunch(appName: string) {
  await new Promise((resolve) => window.setTimeout(resolve, LAUNCH_SETTLE_MS));
  const game = await currentRunningGame();
  if (!game) return;
  const t = getTranslations("soundtrack");
  const store = storage();
  const pick = store ? recallPick(store, game.key) : null;
  toaster.toast({
    title: game.title,
    body: pick ? formatTranslation(t.offerToastPlay, { album: pick.name }) : formatTranslation(t.offerToastPick, { app: appName }),
    duration: 6000,
    // Only on the user's press: the notice itself never plays anything.
    onClick: () => { if (pick) void playPick(game, pick); else openQuickAccess(); },
  });
}

/**
 * Follow the running game for the panel and, when the setting is on and YouTube Music is
 * the active source, offer the soundtrack with a small notice on launch. Returns the stop.
 */
export function startGameSoundtrack(options: { appName: string; available: () => boolean }): () => void {
  const client = (window as any).SteamClient;
  if (typeof client?.GameSessions?.RegisterForAppLifetimeNotifications !== "function") return () => {};
  const announce = () => { try { window.dispatchEvent(new Event(RUNNING_GAME_EVENT)); } catch { /* window gone */ } };
  tracker = createRunningGameTracker({
    subscribe: (callback) => {
      const handle = client.GameSessions.RegisterForAppLifetimeNotifications((n: any) => callback(Number(n?.unAppID) || 0, Boolean(n?.bRunning)));
      return () => { try { handle?.unregister?.(); } catch { /* already gone */ } };
    },
    initial: mainRunningAppId,
    ignore: (appId) => isMusicApp(steamName(appId)),
  });
  const unlisten = tracker.listen((appId, started) => {
    announce();
    if (started && appId && offerEnabled(storage()) && options.available()) void offerOnLaunch(options.appName).catch(() => undefined);
  });
  window.addEventListener(COLLECTOR_EVENT, announce);
  return () => {
    window.removeEventListener(COLLECTOR_EVENT, announce);
    unlisten();
    tracker?.stop();
    tracker = null;
  };
}

function useRunningGame(): RunningGame | null {
  const [game, setGame] = useState<RunningGame | null>(null);
  useEffect(() => {
    let serial = 0;
    const refresh = () => {
      const id = ++serial;
      void currentRunningGame().then((next) => {
        if (id !== serial) return;
        setGame((previous) => previous?.key === next?.key && previous?.title === next?.title ? previous : next);
      });
    };
    refresh();
    window.addEventListener(RUNNING_GAME_EVENT, refresh);
    window.addEventListener(COLLECTOR_EVENT, refresh);
    return () => {
      serial += 1;
      window.removeEventListener(RUNNING_GAME_EVENT, refresh);
      window.removeEventListener(COLLECTOR_EVENT, refresh);
    };
  }, []);
  return game;
}

const card: CSSProperties = {
  width: "100%",
  boxSizing: "border-box",
  borderRadius: "10px",
  border: `1px solid var(--mix-line, ${CORE.gunmetalRaised})`,
  background: `var(--mix-surface, ${CORE.gunmetal})`,
  padding: "10px",
  display: "flex",
  flexDirection: "column",
  gap: "6px",
};
const label: CSSProperties = { fontSize: ".72em", fontWeight: 800, letterSpacing: ".035em", textTransform: "uppercase", color: `var(--mix-accent-text, ${CORE.crimsonText})` };
const gameLine: CSSProperties = { fontSize: ".82em", color: `var(--mix-steel, ${CORE.steel})`, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" };
const hint: CSSProperties = { fontSize: ".76em", lineHeight: 1.35, color: `var(--mix-hint, ${CORE.hint})` };
const button: CSSProperties = { width: "100%", minWidth: "100%", maxWidth: "100%", minHeight: "34px", padding: "0 10px", lineHeight: 1.2 };
const buttonContent: CSSProperties = { display: "flex", alignItems: "center", justifyContent: "center", gap: "7px", fontSize: ".82em", overflow: "hidden" };
const ellipsis: CSSProperties = { overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" };

type Phase = { name: "idle" } | { name: "searching" } | { name: "picking"; candidates: SoundtrackCandidate[] } | { name: "none" };

/** The Quick Access entry, shown only while a game runs. */
export function GameSoundtrackEntry() {
  const t = getTranslations("soundtrack");
  const game = useRunningGame();
  const [phase, setPhase] = useState<Phase>({ name: "idle" });
  const [busy, setBusy] = useState(false);
  const [pickVersion, setPickVersion] = useState(0);
  const gameKeyRef = useRef("");

  useEffect(() => {
    if (gameKeyRef.current !== (game?.key ?? "")) {
      gameKeyRef.current = game?.key ?? "";
      setPhase({ name: "idle" });
    }
  }, [game?.key]);

  if (!game) return null;
  const store = storage();
  const pick = store ? recallPick(store, game.key) : null;
  void pickVersion;

  const play = async (chosen: Omit<SoundtrackPick, "at">) => {
    setBusy(true);
    try {
      if (await playPick(game, chosen)) { setPhase({ name: "idle" }); setPickVersion((v) => v + 1); }
    } finally { setBusy(false); }
  };

  const search = async (offerChoice: boolean) => {
    const key = game.key;
    setPhase({ name: "searching" });
    let result: python.SoundtrackSearch | undefined;
    try {
      const answer = await python.youtubeMusicFindSoundtrack(game.title, game.platform);
      result = answer?.ok ? answer.data : undefined;
    } catch { result = undefined; }
    if (gameKeyRef.current !== key) return;
    const candidates = (result?.candidates ?? []).slice(0, 3);
    if (!candidates.length) { setPhase({ name: "none" }); return; }
    if (result?.confident && !offerChoice) { await play(asPick(candidates[0])); return; }
    setPhase({ name: "picking", candidates });
  };

  const typeLabel = (candidate: SoundtrackCandidate) => {
    const parts = [candidate.type === "album" ? t.album : t.playlist, candidateArtist(candidate), candidate.release_date ?? ""];
    return parts.filter(Boolean).join(" · ");
  };

  return (
    <div className="npGameSoundtrack" style={card}>
      <div style={label}>{t.title}</div>
      <div style={gameLine}>{formatTranslation(t.forGame, { game: game.platform ? `${game.title} · ${game.platform}` : game.title })}</div>
      {phase.name === "picking" ? (
        <Focusable flow-children="vertical" style={{ display: "flex", flexDirection: "column", gap: "4px" }}>
          <div style={hint}>{t.pickTitle}</div>
          {phase.candidates.map((candidate, index) => (
            <MediaRow
              key={`${candidate.type}-${candidate.id}`}
              item={{ ...candidate, images: candidate.images ?? [] }}
              subtitle={typeLabel(candidate)}
              preferredFocus={index === 0}
              onActivate={() => { if (!busy) void play(asPick(candidate)); }}
            />
          ))}
          <DialogButton style={button} onClick={() => setPhase({ name: "idle" })}><span style={buttonContent}>{t.cancel}</span></DialogButton>
        </Focusable>
      ) : (
        <Focusable flow-children="vertical" style={{ display: "flex", flexDirection: "column", gap: "6px" }}>
          {phase.name === "none" ? <div style={hint}>{formatTranslation(t.noneFound, { game: game.title })}</div> : null}
          {phase.name === "searching" ? <div style={hint}>{t.searching}</div> : null}
          <DialogButton
            style={button}
            disabled={busy || phase.name === "searching"}
            onClick={() => void (pick ? play(pick) : search(false))}
          >
            <span style={buttonContent}>
              {phase.name === "none" ? <FaRedo /> : <FaCompactDisc />}
              <span style={ellipsis}>{pick ? formatTranslation(t.playPick, { album: pick.name }) : phase.name === "none" ? t.retry : t.play}</span>
            </span>
          </DialogButton>
          {pick ? (
            <DialogButton style={button} disabled={busy || phase.name === "searching"} onClick={() => void search(true)}>
              <span style={buttonContent}><FaListUl /><span style={ellipsis}>{t.choose}</span></span>
            </DialogButton>
          ) : null}
        </Focusable>
      )}
    </div>
  );
}
