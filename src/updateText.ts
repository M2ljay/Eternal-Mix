// Pure helpers for the self-update panel (kept free of imports so tests can load it).

/** Decky Loader's PluginInstallType.UPDATE (backend/decky_loader/browser.py); its prompt words itself from it. */
export const DECKY_INSTALL_TYPE_UPDATE = 2;

/** Backend refusal reason (mix_update.py) -> key in locales.json's "update" section. */
const REASON_KEYS = {
  network: "errorNetwork",
  manifest_invalid: "errorManifest",
  up_to_date: "upToDate",
  size_mismatch: "errorSize",
  hash_mismatch: "errorHash",
  layout_invalid: "errorLayout",
  download_failed: "errorSave",
  busy: "errorBusy",
} as const;

export type UpdateReasonKey = (typeof REASON_KEYS)[keyof typeof REASON_KEYS] | "errorUnknown";

export function updateReasonKey(reason: unknown): UpdateReasonKey {
  const key = String(reason ?? "");
  return Object.prototype.hasOwnProperty.call(REASON_KEYS, key) ? REASON_KEYS[key as keyof typeof REASON_KEYS] : "errorUnknown";
}

/** Megabytes with one decimal, as Windows Explorer would round them ("37.4"). */
export function formatMegabytes(bytes: unknown): string {
  const value = Number(bytes);
  return Number.isFinite(value) && value > 0 ? (value / 1_000_000).toFixed(1) : "?";
}
