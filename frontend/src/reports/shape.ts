/**
 * Turning what the reports endpoints answer into what the charts draw.
 *
 * Kept out of the route for the usual reason this codebase keeps things out
 * of routes: it is the part with rules in it, and rules deserve tests. The
 * rules here are mostly about *nulls that do not mean the same thing* — the
 * API has three of them on this surface and confusing any two puts a wrong
 * word or a wrong colour on somebody's spending.
 */

import type {
  Currency,
  SpendingTotals,
  Summary,
  SummaryGroup,
  Trend,
  TrendBucket,
  TrendInterval,
} from "@/api/queries";
import type { ColumnBucket, ColumnSeries } from "@/components/charts/Columns";
import { REMAINDER_FILL } from "@/components/charts/palette";
import { DISPLAY_TIMEZONE, dayStringOf, formatDate, formatMonthKey } from "@/lib/dates";
import { toChartValue } from "@/lib/money";
import type { Range } from "@/lib/periods";

const DAY = 86_400;

export type View = {
  range: Range;
  currency: Currency;
  accountId?: string;
  interval: TrendInterval;
};

export const WEEKDAYS = [
  { key: "1", short: "Lun", full: "Lunes" },
  { key: "2", short: "Mar", full: "Martes" },
  { key: "3", short: "Mié", full: "Miércoles" },
  { key: "4", short: "Jue", full: "Jueves" },
  { key: "5", short: "Vie", full: "Viernes" },
  { key: "6", short: "Sáb", full: "Sábado" },
  { key: "7", short: "Dom", full: "Domingo" },
] as const;

/** The one currency's figures, or null when the period matched nothing. */
export function figures(
  totals: SpendingTotals[] | null | undefined,
  currency: string,
): SpendingTotals | null {
  return totals?.find((entry) => entry.currency === currency) ?? null;
}

export function netOf(totals: SpendingTotals): number {
  return Number(totals.net) || 0;
}

export function elapsedDays({ from, to }: Range): number {
  return Math.max(1, Math.round((to - from) / DAY));
}

/**
 * Spending per day, as a chart figure rather than a ledger one.
 *
 * Through a float on purpose and only here: an average is derived, not a sum
 * of movements, and every exact figure beside it still comes from the string
 * the API sent.
 */
export function perDay(outgoing: string, range: Range): string {
  const total = Number(outgoing);
  if (!Number.isFinite(total)) return "0";
  return (total / elapsedDays(range)).toFixed(2);
}

/** What the comparison window was, named so a caption can say it. */
export function describePrevious(summary: Summary): string {
  const from = summary.previous_starts_at;
  const to = summary.previous_ends_at;
  if (from === null || from === undefined) return "el periodo anterior";
  return `${formatDate(from)} — ${formatDate((to ?? from) - 1)}`;
}

/** The filters that reproduce this window as a list of movements. */
export function drilldown(view: View, extra: Record<string, unknown> = {}) {
  return {
    from: dayStringOf(view.range.from),
    // The window's upper bound is exclusive and the list's is an inclusive
    // day, so the day named here is the one the last second falls in.
    to: dayStringOf(view.range.to - 1),
    account: view.accountId,
    transfers: "exclude",
    ...extra,
  };
}

/**
 * What to call one step of the axis.
 *
 * Read off the bucket's own `starts_at` rather than parsed out of its key: a
 * week's key is an ISO year and week, and `2026-W53` opens in December, so
 * turning the key back into a date is work the API already did correctly.
 */
export function describeBucket(
  bucket: TrendBucket,
  interval: TrendInterval,
): ColumnBucket {
  const at = new Date(bucket.starts_at * 1000);
  const zone = { timeZone: DISPLAY_TIMEZONE };

  if (interval === "month") {
    return {
      key: bucket.key,
      label: new Intl.DateTimeFormat("es-CO", { ...zone, month: "short" }).format(at),
      full: formatMonthKey(bucket.key),
      partial: bucket.partial,
    };
  }

  return {
    key: bucket.key,
    label: new Intl.DateTimeFormat("es-CO", {
      ...zone,
      day: "numeric",
      month: interval === "week" ? "short" : undefined,
    }).format(at),
    full:
      interval === "week"
        ? `Semana del ${formatDate(bucket.starts_at)}`
        : formatDate(bucket.starts_at),
    partial: bucket.partial,
  };
}

export function bucketsOf(trend: Trend, interval: TrendInterval): ColumnBucket[] {
  return trend.buckets.map((bucket) => describeBucket(bucket, interval));
}

function pointsOf(
  points: Trend["series"][number]["points"],
  pick: (totals: SpendingTotals) => string,
) {
  return points.map((point) => {
    const amount = point.totals[0] ? pick(point.totals[0]) : "0";
    return { value: toChartValue(amount), amount };
  });
}

/**
 * The cashflow chart: one undivided band read as two.
 *
 * `dimension=none` is asked for rather than a split, because every point
 * already carries what came in and what went out — the two series here are
 * the two halves of one band, not two queries.
 */
export function cashflowSeries(trend: Trend): ColumnSeries[] {
  const band = trend.series[0];
  const empty = trend.buckets.map(() => ({ value: 0, amount: "0" }));

  return [
    {
      key: "incoming",
      label: "Ingresos",
      tone: "bg-incoming",
      points: band ? pointsOf(band.points, (t) => t.incoming) : empty,
    },
    {
      key: "outgoing",
      label: "Gastos",
      tone: "bg-accent",
      points: band ? pointsOf(band.points, (t) => t.outgoing) : empty,
    },
  ];
}

/**
 * The stacked run's bands, wearing the colours the ranking already assigned.
 *
 * Two different things arrive with a null key and they must not be conflated:
 * a band inside `series` is spending the dimension **could not place** — a
 * counterparty no merchant owns yet — which is a real bucket with a real hue,
 * while `others` is the tail that `series` **folded** and is not an identity
 * at all. Calling the first one "Otros" would quietly rename somebody's
 * unattributed spending into a remainder that is already on the chart.
 */
export function bandsOf(
  trend: Trend,
  hues: Map<string | null, string>,
  /*
   * The chart is stacked by category, and a category band's label arrives as
   * its own value — `groceries`, or `custom:mascotas` for one this person
   * wrote. Neither is a word anybody reads, so the legend is built from the
   * same copy every other category on the screen uses.
   */
  label: (value: string) => string = (value) => value,
): ColumnSeries[] {
  const bands: ColumnSeries[] = trend.series.map((band, index) => ({
    key: band.key ?? `__unplaced-${index}`,
    label: band.key === null ? "Sin comercio" : label(band.key),
    // Looked up by key, null included: the ranked list gave that bucket a hue
    // too, and the two charts have to agree on it.
    tone: hues.get(band.key) ?? REMAINDER_FILL,
    points: pointsOf(band.points, (t) => t.outgoing),
  }));

  if (trend.others) {
    bands.push({
      key: "__otros__",
      label: `Otros (${trend.folded})`,
      // The one band deliberately outside the ramp: a remainder is the
      // absence of an identity, not one more of them.
      tone: REMAINDER_FILL,
      points: pointsOf(trend.others.points, (t) => t.outgoing),
    });
  }

  return bands;
}

/**
 * Seven columns, always.
 *
 * The API only returns the weekdays that saw movement, and a week missing its
 * Sunday would read as a six-day week. Here the gap *is* the answer — "you
 * never spend on Sundays" — so the zeros are filled in rather than left out.
 */
export function weekdaySeries(summary: Summary, currency: string): ColumnSeries[] {
  const byKey = new Map(summary.groups.map((group) => [group.key, group]));

  return [
    {
      key: "outgoing",
      label: "Gastos",
      tone: "bg-accent",
      points: WEEKDAYS.map((day) => {
        const amount =
          byKey.get(day.key)?.totals.find((t) => t.currency === currency)?.outgoing ??
          "0";
        return { value: toChartValue(amount), amount };
      }),
    },
  ];
}

export type RankedRowInput = {
  view: View;
  /** Null when the rows carry no identity elsewhere and one hue is enough. */
  hues: Map<string | null, string> | null;
  label: (group: SummaryGroup) => string;
  /** The filters that open this row as a list, or undefined if it cannot be. */
  link: (group: SummaryGroup) => Record<string, unknown> | undefined;
};

export type ShapedRow = {
  key: string;
  label: string;
  amount: string;
  fill: number;
  tone: string;
  previous?: string;
  to?: { to: "/transacciones"; search: Record<string, unknown> };
};

/**
 * A summary's buckets as ranked bars, with the folded remainder on the end.
 *
 * `others` arrives as its own field rather than inside `groups`, and keeps no
 * key: unlike a real bucket it cannot be reopened as the list behind it, so
 * it gets no link. `previous_totals` has the same three-way null as
 * everywhere else — absent means *not compared*, empty means *compared and
 * there was nothing*, and only the second is a fall to zero.
 */
export function rankedRows(summary: Summary, options: RankedRowInput): ShapedRow[] {
  const { view, hues, label, link } = options;

  const amountOf = (group: SummaryGroup) =>
    group.totals.find((entry) => entry.currency === view.currency)?.outgoing ?? "0";
  const previousOf = (group: SummaryGroup) =>
    group.previous_totals === null || group.previous_totals === undefined
      ? undefined
      : (group.previous_totals.find((entry) => entry.currency === view.currency)
          ?.outgoing ?? "0");

  const shown = [...summary.groups, ...(summary.others ? [summary.others] : [])];
  const peak = Math.max(...shown.map((group) => toChartValue(amountOf(group))), 0) || 1;

  return shown
    .filter((group) => toChartValue(amountOf(group)) > 0)
    .map((group, index) => {
      const remainder = group === summary.others;
      const target = remainder ? undefined : link(group);

      return {
        key: group.key ?? `__row-${index}`,
        label: remainder ? `Otros (${summary.folded})` : label(group),
        amount: amountOf(group),
        fill: toChartValue(amountOf(group)) / peak,
        tone: remainder ? REMAINDER_FILL : (hues?.get(group.key) ?? "bg-chart-2"),
        previous: previousOf(group),
        to: target
          ? { to: "/transacciones" as const, search: drilldown(view, target) }
          : undefined,
      };
    });
}
