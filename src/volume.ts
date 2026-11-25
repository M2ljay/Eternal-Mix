// Versions are shown as volumes: 1.x.y is "Vol. 1". package.json holds the
// version; this file only formats it (kept free of imports so tests can load it).
export function volumeLabel(version: string): string {
  const match = /^(\d+)\.(\d+)\.(\d+)(?:[-+].*)?$/.exec(String(version ?? "").trim());
  return match ? `Vol. ${Number(match[1])}` : "Vol. ?";
}
