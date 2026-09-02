/**
 * The categorical ramp, and the one gray that is not part of it.
 *
 * Written out rather than assembled: Tailwind scans the source for whole
 * class names, and one built at runtime is never generated.
 *
 * Assigned in fixed order and never cycled. Past the sixth band the answer is
 * to fold the tail into the remainder — which is what the API's `top` and
 * `series` parameters already do — never to invent a seventh hue, because a
 * generated one is indistinguishable from an existing slot under colour-blind
 * vision.
 *
 * `REMAINDER` is deliberately outside the ramp: it is the de-emphasis gray for
 * "Otros", which is not an identity but the absence of one.
 */

export const BAND_FILL = [
  "bg-chart-1",
  "bg-chart-2",
  "bg-chart-3",
  "bg-chart-4",
  "bg-chart-5",
] as const;

export const REMAINDER_FILL = "bg-chart-6";

/** The gray the remainder and anything unplaced wears. */
export const REMAINDER_KEY = "__otros__";

/**
 * Which hue a bucket wears, by its position in the ranking it was read from.
 *
 * Built once per screen and shared by every chart on it, so a category is the
 * same colour in the bar list and in the stacked run beside it — colour
 * follows the entity, never its row number in whichever chart is drawing.
 */
export function bandPalette(keys: (string | null)[]): Map<string | null, string> {
  const hues = new Map<string | null, string>();
  let slot = 0;
  for (const key of keys) {
    if (hues.has(key)) continue;
    hues.set(key, BAND_FILL[slot] ?? REMAINDER_FILL);
    slot += 1;
  }
  return hues;
}
