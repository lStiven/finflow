/**
 * The window a report covers, and how finely to slice it.
 *
 * One decision drives the whole screen, so it lives in one place: the range
 * picks the interval the time charts step by, the previous window every delta
 * is measured against, and the days a drill-down link lands on.
 *
 * The subtle part is where a running period stops. "Este mes" ends at *now*,
 * not at the end of the month — because `compare` on the API takes the window
 * of equal length immediately before, and a whole month against three elapsed
 * days reports spending down by most of it, every month, and is right about
 * nothing. Ending the window at now makes the comparison the same stretch of
 * the previous month automatically.
 */

import type { TrendInterval } from "@/api/queries";
import {
  currentMonthKey,
  fromLocalInput,
  monthKeyMinus,
  monthRange,
  nowInSeconds,
  previousMonthKey,
} from "@/lib/dates";

export type PresetId = "mes" | "mes-pasado" | "3-meses" | "6-meses" | "ano" | "custom";

export type Range = { from: number; to: number };

export const PRESETS: { id: PresetId; label: string }[] = [
  { id: "mes", label: "Este mes" },
  { id: "mes-pasado", label: "Mes pasado" },
  { id: "3-meses", label: "3 meses" },
  { id: "6-meses", label: "6 meses" },
  { id: "ano", label: "Este año" },
  { id: "custom", label: "Personalizado" },
];

const HOUR = 3600;
const DAY = 86_400;

/**
 * Now, rounded down to the hour.
 *
 * Taken to the second this lands in a react-query key, so every render would
 * be a different query: the loader's prefetch would never be the component's
 * cache hit, and the cache would grow an entry per second the tab stayed
 * open. The dashboard rounds for the same reason.
 */
export function reportingNow(): number {
  return Math.floor(nowInSeconds() / HOUR) * HOUR;
}

/**
 * The half-open epoch range a preset covers.
 *
 * Null when a custom range is asked for without usable dates — the caller
 * falls back rather than charting an invented window.
 */
export function resolveRange(
  preset: PresetId,
  custom?: { from?: string; to?: string },
): Range | null {
  const now = reportingNow();
  const month = currentMonthKey();

  if (preset === "custom") {
    const from = custom?.from ? fromLocalInput(`${custom.from}T00:00`) : null;
    const until = custom?.to ? fromLocalInput(`${custom.to}T00:00`) : null;
    if (from === null || until === null) return null;
    // The chosen end day is included, so the exclusive bound is the midnight
    // after it — the same rule the movement list applies to its own inputs.
    const to = until + DAY;
    return to > from ? { from, to } : null;
  }

  if (preset === "mes-pasado") {
    // The one finished period on the list: both ends are real month bounds,
    // so its comparison is a whole month against a whole month.
    return monthRange(previousMonthKey(month));
  }

  if (preset === "ano") {
    const january = monthRange(`${month.slice(0, 4)}-01`);
    return january ? { from: january.from, to: now } : null;
  }

  const back = preset === "mes" ? 0 : preset === "3-meses" ? 2 : 5;
  const start = monthRange(monthKeyMinus(month, back));
  return start ? { from: start.from, to: now } : null;
}

/**
 * How wide one step of a time chart should be for a range this long.
 *
 * Daily columns over a year is 365 marks nobody can read, and monthly columns
 * over three weeks is one. The thresholds are where a chart stops being
 * legible rather than anything the data knows about.
 */
export function intervalFor({ from, to }: Range): TrendInterval {
  const days = (to - from) / DAY;
  if (days <= 32) return "day";
  if (days <= 130) return "week";
  return "month";
}

/**
 * The window `compare` measures against: the same length, immediately before.
 *
 * The API computes this itself and reports it back; this mirrors it so the
 * screen can *name* the period it is comparing with before the answer lands.
 */
export function previousRange({ from, to }: Range): Range {
  return { from: from - (to - from), to: from };
}
