import { useSyncExternalStore, type CSSProperties } from "react";
import { CORE_ROOT_CLASS, CORE_VARS } from "./corePalette";

// Theme of the plugin's views:
// - "core": the Core palette (black, gunmetal, steel, one crimson accent) in
//   the quick access panel and in every full-screen view. Default.
// - "steam": "Match Steam theme". The panel leaves buttons, focus highlights
//   and text to the active Steam/CSS Loader theme and derives its accent from
//   it; full-screen views use the service colours.
// - "service": each service's own colour as the accent.

export type MixTheme = "core" | "steam" | "service";

const THEME_STORAGE_KEY = "nowPlaying:theme:v1";
// Before themes, a single "Match Steam theme" switch ("1" on, "0" off). It is
// only written when the user changed the switch, so it keeps that choice.
const LEGACY_QAM_THEME_STORAGE_KEY = "nowPlaying:qamMatchTheme:v1";
const QAM_THEME_CHANGED_EVENT = "nowPlaying:qam-theme-changed";

// CapyMenu (QAM) publishes its colours as variables; any other theme falls
// back to the panel's own text colour, which every theme sets.
export const THEME_ACCENT = "var(--chromahonQAM__main-fg-hl, var(--chromahonQAM__main-fg, currentColor))";
export const THEME_CARD_BG = "color-mix(in srgb, currentColor 6%, transparent)";
export const THEME_CARD_BORDER = "1px solid color-mix(in srgb, currentColor 14%, transparent)";
export const THEME_DIVIDER = "linear-gradient(90deg,transparent,color-mix(in srgb,currentColor 45%,transparent),transparent)";

function isMixTheme(value: unknown): value is MixTheme {
  return value === "core" || value === "steam" || value === "service";
}

export function getMixTheme(): MixTheme {
  try {
    const stored = window.localStorage?.getItem(THEME_STORAGE_KEY);
    if (isMixTheme(stored)) return stored;
    const legacy = window.localStorage?.getItem(LEGACY_QAM_THEME_STORAGE_KEY);
    if (legacy === "1") return "steam";
    if (legacy === "0") return "service";
  } catch {
    // Storage can be unavailable in restricted Steam CEF contexts.
  }
  return "core";
}

export function setMixTheme(theme: MixTheme) {
  try {
    window.localStorage?.setItem(THEME_STORAGE_KEY, theme);
  } catch {
    // Storage can be unavailable in restricted Steam CEF contexts.
  }
  window.dispatchEvent(new Event(QAM_THEME_CHANGED_EVENT));
}

function subscribe(listener: () => void) {
  window.addEventListener(QAM_THEME_CHANGED_EVENT, listener);
  return () => window.removeEventListener(QAM_THEME_CHANGED_EVENT, listener);
}

export function useMixTheme(): MixTheme {
  return useSyncExternalStore(subscribe, getMixTheme, getMixTheme);
}

export function useQamThemeMatch(): boolean {
  return useMixTheme() === "steam";
}

export function useCoreTheme(): boolean {
  return useMixTheme() === "core";
}

const NO_VARS: CSSProperties = {};

// Spread on a view's root: the Core variables (or nothing) and its class.
export function useCoreRoot(): { coreVars: CSSProperties; coreClass: string } {
  const core = useCoreTheme();
  return { coreVars: core ? CORE_VARS : NO_VARS, coreClass: core ? ` ${CORE_ROOT_CLASS}` : "" };
}
