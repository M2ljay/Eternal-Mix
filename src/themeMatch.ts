import { useSyncExternalStore } from "react";

// "Match Steam theme" for the quick access panel. When on, the panel leaves
// buttons, focus highlights and text to the active Steam/CSS Loader theme and
// derives its accent from the theme instead of the service's brand colour.
// Full-screen views keep their brand colours either way.

const QAM_THEME_STORAGE_KEY = "nowPlaying:qamMatchTheme:v1";
const QAM_THEME_CHANGED_EVENT = "nowPlaying:qam-theme-changed";

// CapyMenu (QAM) publishes its colours as variables; any other theme falls
// back to the panel's own text colour, which every theme sets.
export const THEME_ACCENT = "var(--chromahonQAM__main-fg-hl, var(--chromahonQAM__main-fg, currentColor))";
export const THEME_CARD_BG = "color-mix(in srgb, currentColor 6%, transparent)";
export const THEME_CARD_BORDER = "1px solid color-mix(in srgb, currentColor 14%, transparent)";
export const THEME_DIVIDER = "linear-gradient(90deg,transparent,color-mix(in srgb,currentColor 45%,transparent),transparent)";

export function getQamThemeMatch(): boolean {
  try {
    return window.localStorage?.getItem(QAM_THEME_STORAGE_KEY) !== "0";
  } catch {
    return true;
  }
}

export function setQamThemeMatch(enabled: boolean) {
  try {
    window.localStorage?.setItem(QAM_THEME_STORAGE_KEY, enabled ? "1" : "0");
  } catch {
    // Storage can be unavailable in restricted Steam CEF contexts.
  }
  window.dispatchEvent(new Event(QAM_THEME_CHANGED_EVENT));
}

function subscribe(listener: () => void) {
  window.addEventListener(QAM_THEME_CHANGED_EVENT, listener);
  return () => window.removeEventListener(QAM_THEME_CHANGED_EVENT, listener);
}

export function useQamThemeMatch(): boolean {
  return useSyncExternalStore(subscribe, getQamThemeMatch, getQamThemeMatch);
}
