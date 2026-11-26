// Shared list rows, cards and track rows for the music browsers (YouTube Music).
// (Formerly part of the Spotify module, removed in Awesome Mix.)
import { DialogButton, Focusable } from "@decky/ui";
import type { CSSProperties, ReactNode } from "react";
import { FaChevronRight, FaMusic, FaPlay } from "react-icons/fa";
import { getTranslations } from "./i18n";

const controlHeight = 34;

const fullButtonStyle: CSSProperties = {
  width: "100%",
  minWidth: "100%",
  maxWidth: "100%",
  minHeight: `${controlHeight}px`,
  padding: 0,
  lineHeight: 1,
};

const iconButtonStyle: CSSProperties = {
  width: `${controlHeight}px`,
  minWidth: `${controlHeight}px`,
  maxWidth: `${controlHeight}px`,
  height: `${controlHeight}px`,
  minHeight: `${controlHeight}px`,
  padding: 0,
};

function imageUrl(item: any): string {
  const images = item?.images ?? item?.album?.images ?? [];
  if (!Array.isArray(images) || !images.length) return "";
  const medium = images.find((entry: any) => Number(entry?.width || 0) >= 160) ?? images[0];
  return String(medium?.url ?? "");
}

function artistText(item: any): string {
  const artists = item?.artists;
  if (Array.isArray(artists) && artists.length) {
    return artists.map((artist: any) => artist?.name).filter(Boolean).join(", ");
  }
  const singleArtist = typeof item?.artist === "string"
    ? item.artist
    : String(item?.artist?.name ?? "");
  if (singleArtist.trim()) return singleArtist.trim();
  if (String(item?.type ?? "").toLowerCase() === "album") return "";
  return String(item?.owner?.display_name ?? item?.publisher ?? "");
}

function normalizeTrack(entry: any): any {
  return entry?.track ?? entry?.item ?? entry;
}

function itemType(item: any): "track" | "album" | "artist" | "playlist" | "unknown" {
  const type = String(item?.type ?? "").toLowerCase();
  if (type === "track" || type === "album" || type === "artist" || type === "playlist") return type;
  return "unknown";
}

function formatTrackDuration(value: any): string {
  const totalSeconds = Math.max(0, Math.floor(Number(value || 0) / 1000));
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return `${minutes}:${String(seconds).padStart(2, "0")}`;
}

function releaseYear(item: any): string {
  const value = String(item?.release_date ?? "").trim();
  const match = value.match(/^\d{4}/);
  return match?.[0] ?? "";
}

export function MediaArtwork({ url, size = 42, round = false }: { url?: string; size?: number; round?: boolean }) {
  const radius = round ? "50%" : "5px";
  return (
    <div
      style={{
        width: `${size}px`,
        height: `${size}px`,
        minWidth: `${size}px`,
        borderRadius: radius,
        overflow: "hidden",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        background: "var(--mix-surface, rgba(255,255,255,0.08))",
        color: "rgba(255,255,255,0.42)",
      }}
    >
      {url ? (
        <img src={url} style={{ width: "100%", height: "100%", objectFit: "cover" }} />
      ) : (
        <FaMusic size={Math.max(14, Math.round(size * 0.38))} />
      )}
    </div>
  );
}

export function MediaRow({
  item,
  subtitle,
  onActivate,
  leadingImage,
  roundImage,
  sideAction,
  buttonRef,
  preferredFocus,
  onFocus,
}: {
  item: any;
  subtitle?: string;
  onActivate: () => void;
  leadingImage?: string;
  roundImage?: boolean;
  sideAction?: { icon: ReactNode; label: string; onActivate: () => void };
  buttonRef?: any;
  preferredFocus?: boolean;
  onFocus?: () => void;
}) {
  const t = getTranslations("youtubeMusic");
  const mainButton = (
    <DialogButton
      ref={buttonRef}
      preferredFocus={preferredFocus}
      className="npMediaResultButton"
      {...({ onFocus: (event: any) => {
        event?.currentTarget?.scrollIntoView?.({ block: "nearest", inline: "nearest", behavior: "smooth" });
        onFocus?.();
      } } as any)}
      style={{
        ...fullButtonStyle,
        width: sideAction ? "auto" : "100%",
        minWidth: 0,
        maxWidth: sideAction ? "none" : "100%",
        flex: sideAction ? 1 : undefined,
        height: "54px",
        minHeight: "54px",
        marginBottom: sideAction ? 0 : "6px",
        overflow: "hidden",
      }}
      onClick={onActivate}
    >
      <span
        style={{
          width: "100%",
          minWidth: 0,
          boxSizing: "border-box",
          display: "flex",
          alignItems: "center",
          gap: "9px",
          padding: "5px 8px",
          textAlign: "left",
        }}
      >
        <MediaArtwork url={leadingImage ?? imageUrl(item)} size={42} round={roundImage} />
        <span style={{ minWidth: 0, flex: 1 }}>
          <strong
            style={{
              display: "block",
              overflow: "hidden",
              textOverflow: "ellipsis",
              whiteSpace: "nowrap",
              fontSize: "0.88em",
              lineHeight: 1.15,
            }}
          >
            {String(item?.name ?? t.untitled)}
          </strong>
          <span
            style={{
              display: "block",
              overflow: "hidden",
              textOverflow: "ellipsis",
              whiteSpace: "nowrap",
              fontSize: "0.72em",
              lineHeight: 1.25,
              minHeight: "1.25em",
              paddingBottom: "2px",
              opacity: 0.62,
              marginTop: "3px",
            }}
          >
            {subtitle ?? artistText(item)}
          </span>
        </span>
        {!sideAction ? <span style={{ display: "inline-flex", alignItems: "center", opacity: 0.68 }}><FaChevronRight size={12} /></span> : null}
      </span>
    </DialogButton>
  );

  if (!sideAction) return mainButton;

  return (
    <Focusable style={{ display: "flex", alignItems: "stretch", gap: "6px", width: "100%", marginBottom: "6px" }} flow-children="horizontal">
      {mainButton}
      <DialogButton
        style={{ ...iconButtonStyle, height: "54px", minHeight: "54px", width: "38px", minWidth: "38px", maxWidth: "38px" }}
        aria-label={sideAction.label}
        onClick={sideAction.onActivate}
      >
        {sideAction.icon}
      </DialogButton>
    </Focusable>
  );
}

export function MediaTvCard({
  item,
  onActivate,
  round = false,
  preferredFocus = false,
  buttonRef,
  focusKey,
  gridIndex,
}: {
  item: any;
  onActivate: () => void;
  round?: boolean;
  preferredFocus?: boolean;
  buttonRef?: any;
  focusKey?: string;
  gridIndex?: number;
}) {
  const t = getTranslations("youtubeMusic");
  const title = String(item?.name ?? t.untitled);
  const subtitle = itemType(item) === "album" && releaseYear(item)
    ? `${artistText(item)} · ${releaseYear(item)}`
    : artistText(item);
  return (
    <DialogButton
      ref={buttonRef}
      preferredFocus={preferredFocus}
      className="npMediaTvCard"
      {...({
        "data-np-focus-key": focusKey || undefined,
        "data-np-grid-index": Number.isFinite(gridIndex) ? gridIndex : undefined,
        onFocus: (event: any) => event?.currentTarget?.scrollIntoView?.({ block: "nearest", inline: "nearest", behavior: "smooth" }),
      } as any)}
      onClick={onActivate}
      style={{
        width: "100%",
        minWidth: 0,
        height: "auto",
        minHeight: 0,
        padding: "10px",
        borderRadius: "12px",
        overflow: "hidden",
        textAlign: "left",
      }}
    >
      <span style={{ display: "flex", flexDirection: "column", width: "100%", height: "100%", minWidth: 0 }}>
        <span
          style={{
            width: "100%",
            aspectRatio: "1 / 1",
            borderRadius: round ? "50%" : "8px",
            overflow: "hidden",
            background: "var(--mix-surface, rgba(255,255,255,0.08))",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            boxShadow: "0 18px 42px rgba(0,0,0,0.3)",
          }}
        >
          {imageUrl(item) ? (
            <img src={imageUrl(item)} style={{ width: "100%", height: "100%", objectFit: "cover" }} />
          ) : (
            <FaMusic size={42} style={{ opacity: 0.45 }} />
          )}
        </span>
        <strong style={{ marginTop: "12px", fontSize: "16px", lineHeight: 1.15, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", fontWeight: 650 }}>
          {title}
        </strong>
        <span style={{ marginTop: "5px", fontSize: "13px", opacity: 0.58, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          {subtitle}
        </span>
      </span>
    </DialogButton>
  );
}

export function MediaTvTrack({
  track,
  index,
  onActivate,
  preferredFocus = false,
  buttonRef,
  showArtwork = true,
  onFocus,
  status,
  progress,
}: {
  track: any;
  index: number;
  onActivate: () => void;
  preferredFocus?: boolean;
  buttonRef?: any;
  showArtwork?: boolean;
  onFocus?: () => void;
  /** Replaces the duration, e.g. "Played" or "12 min left" for podcast episodes. */
  status?: string;
  /** Listened fraction (0–1), drawn as a thin bar under the subtitle. */
  progress?: number;
}) {
  const normalized = normalizeTrack(track);
  return (
    <DialogButton
      ref={buttonRef}
      preferredFocus={preferredFocus}
      className="npMediaTvTrack"
      {...({ onFocus: (event: any) => {
        event?.currentTarget?.scrollIntoView?.({ block: "nearest", inline: "nearest", behavior: "smooth" });
        onFocus?.();
      } } as any)}
      onClick={onActivate}
      style={{
        width: "100%",
        minWidth: "100%",
        height: "66px",
        minHeight: "66px",
        padding: "0 16px",
        borderRadius: "10px",
        marginBottom: "6px",
        textAlign: "left",
      }}
    >
      <span
        style={{
          display: "grid",
          gridTemplateColumns: showArtwork ? "32px 48px minmax(0,1fr) auto" : "32px minmax(0,1fr) auto",
          alignItems: "center",
          gap: showArtwork ? "13px" : "16px",
          width: "100%",
        }}
      >
        <span style={{ opacity: 0.45, fontVariantNumeric: "tabular-nums", textAlign: "right" }}>{index + 1}</span>
        {showArtwork ? <MediaArtwork url={imageUrl(normalized)} size={44} /> : null}
        <span style={{ minWidth: 0 }}>
          <strong style={{ display: "block", fontSize: "16px", fontWeight: 620, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
            {String(normalized?.name ?? "")}
          </strong>
          <span style={{ display: "block", marginTop: "4px", opacity: 0.56, fontSize: "13px", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
            {artistText(normalized)}
          </span>
          {progress && progress > 0 && progress < 1 ? <span className="npEpisodeProgress" style={{ display: "block", height: "3px", marginTop: "5px", width: "min(220px,60%)", borderRadius: "999px", background: "var(--mix-track, rgba(255,255,255,.14))", overflow: "hidden" }}><span style={{ display: "block", height: "100%", width: `${Math.round(progress * 100)}%`, background: "currentColor", opacity: 0.75 }} /></span> : null}
        </span>
        <span style={{ display: "flex", alignItems: "center", gap: "14px", opacity: 0.62 }}>
          {status ? <span className="npEpisodeStatus" style={{ fontSize: "13px", whiteSpace: "nowrap" }}>{status}</span> : normalized?.duration_ms ? <span style={{ fontVariantNumeric: "tabular-nums", fontSize: "13px" }}>{formatTrackDuration(normalized.duration_ms)}</span> : null}
          <FaPlay size={13} />
        </span>
      </span>
    </DialogButton>
  );
}
