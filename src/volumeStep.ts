/** One press of left/right on a volume control moves this many percent. */
export const VOLUME_STEP = 5;

/**
 * The volume after one press in `direction` (-1 or 1), snapped to the step grid so
 * that 37% goes up to 40% and down to 35%, then moves in whole steps.
 */
export function steppedVolume(current: number, direction: number, step = VOLUME_STEP): number {
  const value = Math.max(0, Math.min(100, Math.round(Number(current) || 0)));
  if (!direction) return value;
  const next = direction > 0
    ? Math.floor(value / step) * step + step
    : Math.ceil(value / step) * step - step;
  return Math.max(0, Math.min(100, next));
}
