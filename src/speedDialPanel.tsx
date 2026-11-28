// YouTube Music's "Speed dial" grid: 2 rows of 4 at the top of the Quick Access page and
// up to 3 rows of 9 in the full view. A press plays the tile (see playPlan in
// src/speedDial.ts); X, a long press or a right click opens "More". Hidden when empty.
import { Focusable, Menu, MenuItem, showContextMenu } from "@decky/ui";
import { toaster } from "@decky/api";
import { useEffect, useRef, useState } from "react";
import type { CSSProperties } from "react";
import { FaThumbtack } from "react-icons/fa";
import * as python from "./python";
import { CORE } from "./corePalette";
import { getTranslations, localizeRuntimeMessage } from "./i18n";
import { podcastProgress } from "./localAudio";
import { playYouTubeMusicCollection, playYouTubeMusicTracks } from "./youtubeMusic";
import {
  COMPACT_COLUMNS,
  FULL_COLUMNS,
  SPEED_DIAL_EVENT,
  compactItems,
  fullItems,
  panelDiagnostic,
  pickEpisodeIndex,
  playPlan,
  sanitizeResult,
  sourceHintKey,
  type SpeedDialItem,
  type SpeedDialResult,
} from "./speedDial";

const HOLD_MS = 550;

// Last answer for this session: the grid shows at once when Quick Access reopens, then
// the backend (30-minute cache) confirms it.
let current: SpeedDialResult | null = null;
let inflight: Promise<SpeedDialResult> | null = null;
let focusFullViewOnOpen = false;

function publish(next: SpeedDialResult) {
  // A failed refresh keeps the tiles already shown.
  if (next.reason === "error" && !next.items.length && current?.items.length) return;
  current = next;
  try { window.dispatchEvent(new Event(SPEED_DIAL_EVENT)); } catch { /* window gone */ }
}

// One line per event per session in NowPlaying.log (through report_diagnostic_event).
const reported = new Set<string>();

export function reportSpeedDialOnce(event: string, details: Record<string, string | number | boolean>) {
  if (reported.has(event)) return;
  reported.add(event);
  try {
    void python.reportDiagnosticEvent("speedDial", event, details).catch(() => { /* log only */ });
  } catch { /* log only */ }
}

function errorText(error: unknown): string {
  return String((error as any)?.message ?? error ?? "unknown").replace(/\s+/g, " ").slice(0, 120);
}

export function loadSpeedDial(refresh = false): Promise<SpeedDialResult> {
  if (inflight && !refresh) return inflight;
  const request = python.youtubeMusicGetSpeedDial(refresh)
    .then((result) => {
      if (!result?.ok) reportSpeedDialOnce("call_failed", { error: errorText(result?.error ?? "no answer") });
      return sanitizeResult(result?.ok ? result.data : { reason: "error" });
    })
    .catch((error) => {
      reportSpeedDialOnce("call_failed", { error: errorText(error) });
      return sanitizeResult({ reason: "error" });
    });
  inflight = request;
  void request.then(publish).finally(() => { if (inflight === request) inflight = null; });
  return request;
}

function useSpeedDial(): { value: SpeedDialResult | null; answered: boolean } {
  const [value, setValue] = useState<SpeedDialResult | null>(current);
  const [answered, setAnswered] = useState(false);
  useEffect(() => {
    let alive = true;
    const changed = () => setValue(current);
    window.addEventListener(SPEED_DIAL_EVENT, changed);
    void loadSpeedDial(false).then(() => { if (alive) { setValue(current); setAnswered(true); } });
    return () => { alive = false; window.removeEventListener(SPEED_DIAL_EVENT, changed); };
  }, []);
  return { value, answered };
}

function notify(body: string) {
  toaster.toast({ title: getTranslations("speedDial").title, body, duration: 3500 });
}

/** One press: play the tile through Awesome Mix's existing YouTube Music paths. */
export async function playSpeedDialItem(item: SpeedDialItem): Promise<boolean> {
  const t = getTranslations("speedDial");
  const plan = playPlan(item);
  if (!plan) return false;
  try {
    // playYouTubeMusicCollection reports its own failures.
    if (plan.kind === "collection") return await playYouTubeMusicCollection(plan.collection, plan.id);
    let played = false;
    if (plan.kind === "episode") {
      played = await playYouTubeMusicTracks([plan.track], 0);
    } else if (plan.kind === "podcast") {
      const result = await python.youtubeMusicGetDetail("playlist", plan.id);
      if (!result?.ok) throw new Error(String(result?.error ?? "YouTube Music request failed"));
      const episodes = Array.isArray(result.data?.tracks) ? result.data.tracks : [];
      const index = pickEpisodeIndex(episodes, podcastProgress);
      played = index >= 0 && await playYouTubeMusicTracks(episodes, index);
    } else {
      const result = await python.youtubeMusicGetSpeedDialQueue(item);
      if (!result?.ok) throw new Error(String(result?.error ?? "YouTube Music request failed"));
      played = await playYouTubeMusicTracks(result.data?.tracks ?? [], 0);
    }
    if (!played) notify(t.nothingToPlay);
    return played;
  } catch (error) {
    notify(localizeRuntimeMessage(error, getTranslations("youtubeMusic").genericError));
    return false;
  }
}

const TILE_CSS = [
  ".npSpeedDialTile{outline:none;border-radius:8px;cursor:pointer;min-width:0}",
  `.npSpeedDialArt{position:relative;width:100%;aspect-ratio:1/1;overflow:hidden;background:${CORE.black};border:1px solid ${CORE.gunmetalRaised};transition:box-shadow 120ms ease}`,
  ".npSpeedDialArt img{width:100%;height:100%;display:block;object-fit:cover}",
  // Focus: a crimson ring, no glow (Core).
  `.npSpeedDialTile:focus .npSpeedDialArt,.npSpeedDialTile:focus-visible .npSpeedDialArt,.npSpeedDialTile.gpfocus .npSpeedDialArt{box-shadow:0 0 0 2px ${CORE.crimson};border-color:${CORE.crimson}}`,
  `.npSpeedDialTile:focus .npSpeedDialName,.npSpeedDialTile.gpfocus .npSpeedDialName{color:${CORE.white}}`,
  ".npSpeedDialBusy .npSpeedDialArt{opacity:.5}",
].join("\n");

const ellipsis: CSSProperties = { overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" };

function SpeedDialTile({ item, full, busy, onPlay, onMore }: {
  item: SpeedDialItem;
  full: boolean;
  busy: boolean;
  onPlay: () => void;
  onMore: (anchor?: EventTarget) => void;
}) {
  const t = getTranslations("speedDial");
  const holdTimer = useRef(0);
  const held = useRef(false);
  const cancelHold = () => {
    if (holdTimer.current) window.clearTimeout(holdTimer.current);
    holdTimer.current = 0;
  };
  useEffect(() => cancelHold, []);
  const startHold = (event: any) => {
    held.current = false;
    cancelHold();
    const anchor = event?.currentTarget ?? undefined;
    holdTimer.current = window.setTimeout(() => { holdTimer.current = 0; held.current = true; onMore(anchor); }, HOLD_MS);
  };
  return (
    <Focusable
      className={`npSpeedDialTile${busy ? " npSpeedDialBusy" : ""}`}
      noFocusRing
      onActivate={() => {
        // The click that ends a long press is not a press.
        if (held.current) { held.current = false; return; }
        onPlay();
      }}
      onOKActionDescription={t.play}
      onSecondaryButton={(event: any) => onMore(event?.currentTarget ?? event?.target ?? undefined)}
      onSecondaryActionDescription={t.more}
      onFocus={(event: any) => event?.currentTarget?.scrollIntoView?.({ block: "nearest", inline: "nearest", behavior: "smooth" })}
      onPointerDown={startHold}
      onPointerUp={cancelHold}
      onPointerLeave={cancelHold}
      onPointerCancel={cancelHold}
      onContextMenu={(event: any) => { event?.preventDefault?.(); cancelHold(); onMore(event?.currentTarget ?? undefined); }}
      style={{ display: "flex", flexDirection: "column", gap: full ? "7px" : "4px", padding: full ? "4px" : "2px" }}
    >
      <div className="npSpeedDialArt" style={{ borderRadius: item.round ? "50%" : full ? "8px" : "5px" }}>
        {item.thumbnail ? <img src={item.thumbnail} alt="" loading="lazy" referrerPolicy="no-referrer" draggable={false} /> : null}
        {item.pinned ? (
          <span
            aria-label={t.pinned}
            style={{ position: "absolute", top: full ? 6 : 3, right: full ? 6 : 3, display: "flex", padding: full ? 4 : 2, borderRadius: 999, background: CORE.gunmetal, color: CORE.steel }}
          >
            <FaThumbtack size={full ? 11 : 7} />
          </span>
        ) : null}
      </div>
      <div className="npSpeedDialName" style={{ ...ellipsis, textAlign: full ? "left" : "center", fontSize: full ? "15px" : ".62em", fontWeight: 600, lineHeight: 1.2, color: CORE.steel }}>
        {item.title}
      </div>
      {full && item.subtitle ? <div style={{ ...ellipsis, fontSize: "13px", lineHeight: 1.2, color: CORE.hint }}>{item.subtitle}</div> : null}
    </Focusable>
  );
}

/**
 * The Speed dial grid. `compact` is the Quick Access block (2 by 4), `full` the
 * full-view section (up to 3 by 9). Renders nothing while there are no tiles.
 */
export function SpeedDialGrid({ variant, onOpenFull }: { variant: "compact" | "full"; onOpenFull?: () => void }) {
  const t = getTranslations("speedDial");
  const { value: result, answered } = useSpeedDial();
  const [busyKey, setBusyKey] = useState("");
  const gridRef = useRef<HTMLDivElement>(null);
  const full = variant === "full";
  const items = result ? (full ? fullItems(result.items) : compactItems(result.items)) : [];

  // Once per session and view, after the backend's first answer: shown or why hidden.
  useEffect(() => {
    if (answered) reportSpeedDialOnce(`panel_${variant}`, panelDiagnostic(result, items.length));
  }, [answered]);

  // "Open in full view" lands on the grid instead of the Home tab.
  useEffect(() => {
    if (!full || !items.length || !focusFullViewOnOpen) return;
    focusFullViewOnOpen = false;
    const timer = window.setTimeout(() => {
      try { (gridRef.current?.querySelector(".npSpeedDialTile") as HTMLElement | null)?.focus?.(); } catch { /* keep Steam's focus */ }
    }, 160);
    return () => window.clearTimeout(timer);
  }, [full, items.length]);

  if (!items.length) return null;

  const play = async (item: SpeedDialItem) => {
    const key = `${item.type}:${item.id}`;
    if (busyKey) return;
    setBusyKey(key);
    try { await playSpeedDialItem(item); } finally { setBusyKey(""); }
  };

  const more = (item: SpeedDialItem, anchor?: EventTarget) => {
    const refresh = () => {
      void loadSpeedDial(true).then((next) => { if (next.reason !== "error") notify(t.refreshed); });
    };
    const openFull = () => {
      focusFullViewOnOpen = true;
      onOpenFull?.();
    };
    try {
      showContextMenu(
        <Menu label={item.title}>
          <MenuItem onSelected={refresh}>{t.refresh}</MenuItem>
          {!full && onOpenFull ? <MenuItem onSelected={openFull}>{t.openFull}</MenuItem> : null}
        </Menu>,
        anchor,
      );
    } catch {
      refresh();
    }
  };

  const columns = full ? FULL_COLUMNS : COMPACT_COLUMNS;
  const grid = (
    <Focusable
      ref={gridRef}
      flow-children="grid"
      style={{ display: "grid", gridTemplateColumns: `repeat(${columns}, minmax(0, 1fr))`, gap: full ? "12px" : "5px", width: "100%" }}
    >
      {items.map((item) => (
        <SpeedDialTile
          key={`${item.type}:${item.id}`}
          item={item}
          full={full}
          busy={busyKey === `${item.type}:${item.id}`}
          onPlay={() => void play(item)}
          onMore={(anchor) => more(item, anchor)}
        />
      ))}
    </Focusable>
  );

  if (full) {
    const hintKey = result ? sourceHintKey(result.source) : "";
    const hint = hintKey ? t[hintKey] : "";
    return (
      <section className="npSpeedDial npSpeedDialFull" style={{ marginTop: "28px", background: "transparent" }}>
        <style>{TILE_CSS}</style>
        <h2 style={{ margin: "0 0 13px", fontSize: "25px", fontWeight: 650, color: CORE.text }}>
          {t.title}
          {hint ? <span className="npSpeedDialHint" style={{ marginLeft: "12px", fontSize: "14px", fontWeight: 500, color: CORE.hint }}>{hint}</span> : null}
        </h2>
        {grid}
      </section>
    );
  }
  return (
    <div
      className="npSpeedDial npSpeedDialCompact"
      style={{ width: "100%", boxSizing: "border-box", marginBottom: "10px", borderRadius: "10px", border: `1px solid ${CORE.gunmetalRaised}`, background: CORE.gunmetal, padding: "8px", display: "flex", flexDirection: "column", gap: "6px" }}
    >
      <style>{TILE_CSS}</style>
      <div style={{ fontSize: ".72em", fontWeight: 800, letterSpacing: ".035em", textTransform: "uppercase", color: CORE.crimsonText }}>{t.title}</div>
      {grid}
    </div>
  );
}
