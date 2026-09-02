/**
 * A ranked list of bars — where the money actually went, biggest first.
 *
 * A ring answers "what share"; this answers "how much, against what it was",
 * which is the question a report exists for. It is also the form that
 * survives close values and long names, both of which a donut loses.
 *
 * It needs no separate table view: every row already carries its label, its
 * amount and its share as text. The bar is the redundant channel here, not
 * the only one.
 */

import { Link } from "@tanstack/react-router";
import type { ReactNode } from "react";
import { Money } from "@/components/Money";
import { cn } from "@/lib/cn";
import { percentChange } from "@/lib/money";

export type RankedRow = {
  key: string;
  label: string;
  /** The decimal string. Never a float — this one is read, not drawn. */
  amount: string;
  /** Geometry: its share of the largest row, 0 to 1. */
  fill: number;
  /** A `bg-*` token. */
  tone: string;
  /**
   * The same row in the window before. `null` when it was compared and there
   * was nothing; `undefined` when nothing was compared at all — which is not
   * the same thing, and must not read as a fall to zero.
   */
  previous?: string | null;
  /** Where the row opens as the movements behind it. Absent = not drillable. */
  to?: { to: "/transacciones"; search: Record<string, unknown> };
};

export function RankedBars({
  rows,
  currency,
  empty,
}: {
  rows: RankedRow[];
  currency: string;
  empty: ReactNode;
}) {
  if (rows.length === 0) {
    return <p className="py-6 text-center text-muted text-sm">{empty}</p>;
  }

  return (
    <ul className="flex flex-col gap-1">
      {rows.map((row) => (
        <li key={row.key}>
          <Row row={row} currency={currency} />
        </li>
      ))}
    </ul>
  );
}

function Row({ row, currency }: { row: RankedRow; currency: string }) {
  const body = (
    <>
      <div className="flex items-baseline justify-between gap-3">
        <span className="min-w-0 flex-1 truncate text-sm">{row.label}</span>
        <Delta previous={row.previous} current={row.amount} />
        <Money amount={row.amount} currency={currency} size="sm" />
      </div>
      {/*
       * The track is the surface, the fill is the hue. No stroke round
       * either: the gap between rows is what separates them.
       */}
      <div className="mt-1.5 h-1.5 w-full overflow-hidden rounded-full bg-surface-raised">
        <div
          className={cn("h-full rounded-full", row.tone)}
          style={{ width: `${Math.max(row.fill * 100, 1.5)}%` }}
        />
      </div>
    </>
  );

  const shell =
    "block rounded-xl px-2 py-2 transition-colors duration-150 hover:bg-surface-raised";

  if (!row.to) return <div className={cn(shell, "hover:bg-transparent")}>{body}</div>;

  return (
    <Link
      to={row.to.to}
      search={row.to.search}
      className={cn(shell, "focus-visible:bg-surface-raised")}
    >
      {body}
    </Link>
  );
}

/**
 * Change against the same row in the previous window.
 *
 * Silent in the two cases where a badge would be a fabrication: nothing was
 * compared, or the baseline was zero — "up 100%" from nothing is a number
 * people quote back at you.
 *
 * Spending more is never good news, so the colour is fixed rather than taking
 * a `lowerIsBetter`: every row this renders is money going out.
 */
function Delta({ previous, current }: { previous?: string | null; current: string }) {
  if (previous === undefined || previous === null) return null;
  const change = percentChange(previous, current);
  if (change === null) return null;

  const rounded = Math.round(change);
  if (rounded === 0) {
    return <span className="shrink-0 text-[11px] text-faint">igual</span>;
  }

  return (
    <span
      className={cn(
        "shrink-0 tabular text-[11px]",
        rounded > 0 ? "text-outgoing" : "text-incoming",
      )}
    >
      {rounded > 0 ? "↑" : "↓"}
      {Math.abs(rounded)}%
    </span>
  );
}
