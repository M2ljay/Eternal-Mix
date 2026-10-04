import { useEffect, useRef } from "react";
import type { CSSProperties } from "react";

type SmoothProgressFillProps = {
  position: number;
  duration: number;
  playing: boolean;
  sampledAt: number;
  style?: CSSProperties;
  // Playback speed, so the bar keeps pace between samples at 1.5x or 2x.
  rate?: number;
};

type SmoothProgressTimeProps = {
  position: number;
  duration: number;
  playing: boolean;
  sampledAt: number;
  format: (value: number) => string;
  rate?: number;
};

export function SmoothProgressFill({ position, duration, playing, sampledAt, style, rate = 1 }: SmoothProgressFillProps) {
  const fillRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let frame = 0;
    const base = Math.max(0, Number(position || 0));
    const total = Math.max(1, Number(duration || 1));
    const receivedAt = Math.max(0, Number(sampledAt || Date.now()));

    const draw = () => {
      const elapsed = playing ? Math.max(0, Date.now() - receivedAt) * (rate > 0 ? rate : 1) : 0;
      const ratio = Math.max(0, Math.min(1, (base + elapsed) / total));
      if (fillRef.current) fillRef.current.style.transform = `scaleX(${ratio})`;
      if (playing && ratio < 1) frame = window.requestAnimationFrame(draw);
    };

    draw();
    return () => {
      if (frame) window.cancelAnimationFrame(frame);
    };
  }, [duration, playing, position, rate, sampledAt]);

  return (
    <div
      ref={fillRef}
      aria-hidden="true"
      style={{ width: "100%", transform: "scaleX(0)", transformOrigin: "left center", willChange: "transform", ...style }}
    />
  );
}

export function SmoothProgressTime({ position, duration, playing, sampledAt, format, rate = 1 }: SmoothProgressTimeProps) {
  const labelRef = useRef<HTMLSpanElement>(null);

  useEffect(() => {
    let timer = 0;
    const base = Math.max(0, Number(position || 0));
    const total = Math.max(0, Number(duration || 0));
    const receivedAt = Math.max(0, Number(sampledAt || Date.now()));

    const draw = () => {
      const elapsed = playing ? Math.max(0, Date.now() - receivedAt) * (rate > 0 ? rate : 1) : 0;
      const current = Math.min(total || Number.MAX_SAFE_INTEGER, base + elapsed);
      const label = format(current);
      if (labelRef.current && labelRef.current.textContent !== label) labelRef.current.textContent = label;
      if (playing && (!total || current < total)) timer = window.setTimeout(draw, 200);
    };

    draw();
    return () => {
      if (timer) window.clearTimeout(timer);
    };
  }, [duration, format, playing, position, rate, sampledAt]);

  return <span ref={labelRef}>{format(Math.max(0, Number(position || 0)))}</span>;
}
