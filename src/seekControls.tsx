import type { CSSProperties } from "react";
import { DialogButton, Focusable, GamepadButton } from "@decky/ui";
import { FaRedoAlt } from "react-icons/fa";
import { getTranslations } from "./i18n";
import { isPodcastEpisode, localAudioPlayer, useLocalAudioState } from "./localAudio";
import { SmoothProgressFill, SmoothProgressTime } from "./smoothProgress";

// Seek bar and skip/speed buttons for the in-plugin player (Your Music and
// YouTube Music). Windows media sessions (Apple Music) keep their read-only bar.

const SEEK_CSS = ".npSeekBar .npSeekTrack{transition:box-shadow 120ms ease}.npSeekBarFocused .npSeekTrack{box-shadow:0 0 0 2px rgba(255,255,255,.6)}";

function formatClock(value: number) {
  const total = Math.max(0, Math.floor(Number(value || 0) / 1000));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const seconds = String(total % 60).padStart(2, "0");
  return hours ? `${hours}:${String(minutes).padStart(2, "0")}:${seconds}` : `${minutes}:${seconds}`;
}

// One D-pad press moves 1% of the track, between 10 s and 60 s.
function seekStepMs(length: number) {
  return Math.max(10000, Math.min(60000, Math.round(length / 100)));
}

function speedLabel(rate: number) {
  return `${Number(Number(rate || 1).toFixed(2))}×`;
}

function directionOf(event: any) {
  const key = String(event?.key ?? "");
  const button = event?.detail?.button;
  if (key === "ArrowLeft" || key === "Left" || button === GamepadButton.DIR_LEFT) return -1;
  if (key === "ArrowRight" || key === "Right" || button === GamepadButton.DIR_RIGHT) return 1;
  return 0;
}

export function LocalSeekBar({ accent, trackHeight = 6, timeStyle }: { accent: string; trackHeight?: number; timeStyle?: CSSProperties }) {
  const state = useLocalAudioState();
  const core = getTranslations("core");
  const length = Math.max(0, Number(state.length || state.track?.duration_ms || 0));
  const hasTrack = Boolean(state.track) && length > 0;
  const playing = state.status === "Playing";
  const sampledAt = Date.now();
  const nudge = (event: any) => {
    const direction = directionOf(event);
    if (!direction || !hasTrack) return;
    event.preventDefault?.();
    event.stopPropagation?.();
    localAudioPlayer.seekBy(direction * seekStepMs(length));
  };
  const jump = (event: any) => {
    if (!hasTrack) return;
    const rect = event.currentTarget.getBoundingClientRect();
    if (!rect.width) return;
    const ratio = Math.max(0, Math.min(1, (Number(event.clientX) - rect.left) / rect.width));
    localAudioPlayer.seek(ratio * length);
  };
  return (
    <Focusable
      className="npSeekBar"
      focusClassName="npSeekBarFocused"
      noFocusRing
      onActivate={() => undefined}
      onButtonDown={nudge}
      onKeyDown={nudge}
      role="slider"
      tabIndex={0}
      {...({ focusable: true } as any)}
      aria-label={core.playbackPositionLabel}
      aria-valuemin={0}
      aria-valuemax={Math.round(length / 1000)}
      aria-valuenow={Math.round(Number(state.position || 0) / 1000)}
      style={{ width: "100%", padding: "6px 0", boxSizing: "border-box" }}
    >
      <style>{SEEK_CSS}</style>
      <div className="npSeekTrack" onPointerDown={jump} style={{ width: "100%", height: `${trackHeight}px`, borderRadius: "999px", background: "rgba(255,255,255,0.18)", overflow: "hidden", cursor: "pointer" }}>
        <SmoothProgressFill position={state.position} duration={Math.max(1, length)} playing={playing} sampledAt={sampledAt} rate={state.playbackRate} style={{ height: "100%", borderRadius: "999px", background: accent }} />
      </div>
      <div style={{ display: "flex", justifyContent: "space-between", marginTop: "6px", fontVariantNumeric: "tabular-nums", ...timeStyle }}>
        <SmoothProgressTime position={state.position} duration={length} playing={playing} sampledAt={sampledAt} rate={state.playbackRate} format={formatClock} />
        <span>{formatClock(length)}</span>
      </div>
    </Focusable>
  );
}

export function LocalSeekButtons({ buttonStyle, gap = 8 }: { buttonStyle: CSSProperties; gap?: number }) {
  const state = useLocalAudioState();
  const core = getTranslations("core");
  const episode = isPodcastEpisode(state.track);
  const back = episode ? 15 : 10;
  const forward = episode ? 30 : 10;
  const disabled = !state.track;
  const seconds = (text: string, value: number) => String(text).replace("{seconds}", String(value));
  const content: CSSProperties = { display: "flex", alignItems: "center", justifyContent: "center", gap: "6px", fontVariantNumeric: "tabular-nums" };
  return (
    <Focusable flow-children="horizontal" style={{ display: "flex", gap: `${gap}px`, width: "100%" }}>
      <DialogButton style={buttonStyle} disabled={disabled} aria-label={seconds(core.seekBackLabel, back)} onClick={() => localAudioPlayer.seekBy(-back * 1000)}>
        <span style={content}><FaRedoAlt style={{ transform: "scaleX(-1)" }} />{back}</span>
      </DialogButton>
      {episode ? (
        <DialogButton style={buttonStyle} aria-label={core.playbackSpeedLabel} onClick={() => localAudioPlayer.cyclePodcastSpeed()}>
          <span style={{ ...content, fontWeight: 700 }}>{speedLabel(state.playbackRate)}</span>
        </DialogButton>
      ) : null}
      <DialogButton style={buttonStyle} disabled={disabled} aria-label={seconds(core.seekForwardLabel, forward)} onClick={() => localAudioPlayer.seekBy(forward * 1000)}>
        <span style={content}>{forward}<FaRedoAlt /></span>
      </DialogButton>
    </Focusable>
  );
}
