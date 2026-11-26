import type { CSSProperties } from "react";

// The Core palette (Core by Eternal), shared by every view of the plugin.
// Colours are applied as CSS variables on each view's root while the Core
// theme is active; every themed rule reads `var(--mix-*, <legacy value>)`, so
// the other themes keep their own look when the variables are absent.
export const CORE = {
  black: "#000000",
  gunmetal: "#1A1D21",
  gunmetalRaised: "#2A2F36",
  steel: "#C9D1D9",
  hint: "#9AA4AE",
  white: "#FFFFFF",
  text: "#E6EAEE",
  crimson: "#D7263D",
  // Crimson words: #D7263D as text on black/gunmetal is under 4.5:1. Keep
  // `crimson` for fills, bars, dots and rings.
  crimsonText: "#EA5A68",
  green: "#6FA37F",
  amber: "#C98B4A",
} as const;

export const CORE_VARS = {
  "--mix-accent": CORE.crimson,
  "--mix-on-accent": CORE.white,
  "--mix-accent-text": CORE.crimsonText,
  "--mix-bg": CORE.black,
  "--mix-surface": CORE.gunmetal,
  "--mix-surface-2": CORE.gunmetalRaised,
  "--mix-line": CORE.gunmetalRaised,
  "--mix-text": CORE.text,
  "--mix-steel": CORE.steel,
  "--mix-hint": CORE.hint,
  "--mix-track": CORE.gunmetalRaised,
  // Focus: gunmetal surface plus a crimson ring, no glow.
  "--mix-focus": `0 0 0 2px ${CORE.crimson}`,
  "--mix-focus-bg": CORE.gunmetalRaised,
  "--mix-shadow": "none",
  "--mix-divider": CORE.gunmetalRaised,
  "--mix-cover-focus": `0 0 0 3px ${CORE.crimson}`,
  "--mix-filter-glow": "none",
  "--mix-backdrop": "none",
  "--mix-glow-warm": CORE.crimson,
  "--mix-glow-cool": CORE.steel,
  "--mix-error": CORE.crimson,
  // Problems: crimson text in its readable tint, beside a crimson bar.
  "--mix-error-text": CORE.crimsonText,
  "--mix-error-bar": `inset 3px 0 0 ${CORE.crimson}`,
  "--mix-error-pad": "9px",
  "--mix-ok": CORE.green,
  "--mix-warn": CORE.amber,
  // Update available: amber bar beside white text.
  "--mix-warn-bar": `inset 3px 0 0 ${CORE.amber}`,
  "--mix-warn-pad": "9px",
} as unknown as CSSProperties;

export const CORE_ROOT_CLASS = "npCore";

// Buttons without a view-specific rule: gunmetal at rest, raised gunmetal and
// a crimson ring when focused (Core's QAM style). Cover-art glows and blurred
// backdrops (`npCoreNoGlow`) are left out: Core has no glow backgrounds.
export const CORE_ROOT_CSS = [
  `.${CORE_ROOT_CLASS} button.DialogButton{background:${CORE.gunmetal};color:${CORE.text};border-color:${CORE.gunmetalRaised}}`,
  `.${CORE_ROOT_CLASS} button.DialogButton:hover,.${CORE_ROOT_CLASS} button.DialogButton:focus,.${CORE_ROOT_CLASS} button.DialogButton.gpfocus{background:${CORE.gunmetalRaised};color:${CORE.white};box-shadow:0 0 0 2px ${CORE.crimson}}`,
  `.${CORE_ROOT_CLASS} input[type=range]{accent-color:${CORE.crimson}}`,
  `.${CORE_ROOT_CLASS} .npCoreNoGlow{display:none!important}`,
].join("\n");

export const CORE_SERVICE_ACCENT = CORE.crimson;
