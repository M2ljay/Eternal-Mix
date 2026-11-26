// Awesome Mix steps aside for games: while a game runs, the music fades down to a
// quarter of its volume, and when the last game closes it fades back to exactly where it
// was. Only the live volume moves; the saved per-source volumes are never touched, so a
// crash mid-game leaves nothing to undo. Self-contained so the tests can load it alone.

export const DUCK_STORAGE_KEY = "nowPlaying:duckForGames:v1";
export const DUCK_FACTOR = 0.25;
const FADE_STEPS = 6;
const FADE_STEP_MS = 150;

export type Volumes = Record<string, number>;
export type DuckDeps = {
  subscribe: (callback: (appId: number, running: boolean) => void) => () => void;
  read: () => Promise<Volumes>;
  write: (volumes: Volumes) => Promise<void>;
  enabled: () => boolean;
  ignore?: (appId: number) => boolean;
  wait?: (ms: number) => Promise<void>;
};

export function duckedVolumes(volumes: Volumes): Volumes {
  return Object.fromEntries(Object.entries(volumes).map(([key, value]) => [key, Math.round(value * DUCK_FACTOR)]));
}

export function fadeFrames(from: Volumes, to: Volumes, steps = FADE_STEPS): Volumes[] {
  const frames: Volumes[] = [];
  for (let step = 1; step <= steps; step++) {
    frames.push(Object.fromEntries(Object.keys(to).map(key => {
      const start = from[key] ?? to[key];
      return [key, Math.round(start + (to[key] - start) * step / steps)];
    })));
  }
  return frames;
}

export function installGameDucking(deps: DuckDeps): () => void {
  const running = new Set<number>();
  const wait = deps.wait ?? ((ms: number) => new Promise<void>(resolve => setTimeout(resolve, ms)));
  let saved: Volumes | null = null;
  let current: Volumes = {};
  let generation = 0;
  let disposed = false;

  async function fade(target: Volumes, id: number) {
    for (const frame of fadeFrames(current, target)) {
      if (disposed || id !== generation) return;
      current = frame;
      try { await deps.write(frame); } catch { /* A source that is not running ignores this. */ }
      await wait(FADE_STEP_MS);
    }
  }

  async function update() {
    const id = ++generation;
    if (running.size > 0 && saved === null) {
      if (!deps.enabled()) return;
      let volumes: Volumes;
      try { volumes = await deps.read(); } catch { return; }
      if (id !== generation || disposed || Object.keys(volumes).length === 0) return;
      saved = volumes;
      current = { ...volumes };
      await fade(duckedVolumes(volumes), id);
    } else if (running.size === 0 && saved !== null) {
      const target = saved;
      saved = null;
      await fade(target, id);
    }
  }

  const unsubscribe = deps.subscribe((appId, isRunning) => {
    if (!appId || deps.ignore?.(appId)) return;
    if (isRunning) running.add(appId); else running.delete(appId);
    void update();
  });

  return () => {
    disposed = true;
    unsubscribe();
    // Leaving mid-game (plugin reload): put the music back at once.
    if (saved !== null) { void deps.write(saved).catch(() => undefined); saved = null; }
  };
}
