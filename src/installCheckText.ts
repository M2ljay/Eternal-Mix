// Pure helpers for the install check banner (kept free of imports so tests can load it).

type CheckLike = { status?: string; phase?: string; safeMode?: boolean } | null | undefined;

/** Show the banner when files are missing/damaged or the backend runs in safe mode. */
export function shouldShowInstallBanner(check: CheckLike): boolean {
  return Boolean(check && (check.safeMode || check.status === "broken"));
}

/** Milliseconds until the next poll, or null once the background hash check has finished. */
export function installCheckPollDelay(check: CheckLike, polls: number): number | null {
  if (!check || polls >= 60) return null;
  return check.phase === "sizes" || check.phase === "hashing" ? 4000 : null;
}
