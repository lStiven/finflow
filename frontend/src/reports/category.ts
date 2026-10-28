/**
 * One category, month by month: the figures the category screen is built on.
 *
 * Pure, and kept apart from the screen for the one decision worth testing —
 * what "on average" means. A plain mean of the last six months is wrong for
 * anybody who started using Finflow three months ago: the three months before
 * that read as zero, and the average comes out half of what they spend. So
 * the average starts at the first month this category had any spending, and
 * says how many months it is made of.
 */

import type { Trend } from "@/api/queries";
import { percentChange } from "@/lib/money";

export type MonthFigure = {
  /** The bucket's key, `2026-10`. */
  key: string;
  /** What this category spent that month, in the screen's currency. */
  outgoing: string;
  /** The month is not over yet. */
  partial: boolean;
};

/** A month-interval, no-dimension trend read as one figure per month. */
export function monthlyOutgoing(trend: Trend, currency: string): MonthFigure[] {
  const points = trend.series[0]?.points ?? [];

  return trend.buckets.map((bucket, index) => {
    const totals = points[index]?.totals ?? [];
    const figure = totals.find((entry) => entry.currency === currency);

    return {
      key: bucket.key,
      outgoing: figure?.outgoing ?? "0",
      partial: bucket.partial ?? false,
    };
  });
}

export type CategoryStats = {
  /** The chosen month — the last one in the run. */
  total: string;
  /** The month before it, or null when the run has only one. */
  previous: string | null;
  /** Percent against the month before; null when that month spent nothing. */
  vsPrevious: number | null;
  /** Mean of the months before the chosen one, from the first that spent. */
  average: string | null;
  /** How many months that average is made of. */
  averagedOver: number;
  /** Percent against that average; null without one. */
  vsAverage: number | null;
};

export function categoryStats(months: readonly MonthFigure[]): CategoryStats {
  const chosen = months.at(-1);
  const before = months.slice(0, -1);
  const total = chosen?.outgoing ?? "0";
  const previous = before.at(-1)?.outgoing ?? null;

  const firstSpent = before.findIndex((month) => Number(month.outgoing) > 0);
  const counted = firstSpent === -1 ? [] : before.slice(firstSpent);
  const sum = counted.reduce((running, month) => running + Number(month.outgoing), 0);
  const average = counted.length === 0 ? null : (sum / counted.length).toFixed(2);

  return {
    total,
    previous,
    vsPrevious: previous === null ? null : percentChange(previous, total),
    average,
    averagedOver: counted.length,
    vsAverage: average === null ? null : percentChange(average, total),
  };
}

/** What the change reads as, with its sign, or null when there is none. */
export function describeChange(change: number | null): string | null {
  if (change === null || !Number.isFinite(change)) return null;

  const rounded = Math.round(change);

  if (rounded === 0) return "igual";

  return rounded > 0 ? `${rounded}% más` : `${Math.abs(rounded)}% menos`;
}
