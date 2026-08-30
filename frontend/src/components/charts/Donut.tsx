/**
 * Spending by category, as a ring.
 *
 * Hand-drawn in SVG rather than pulled from a charting library: this is one
 * ring with at most six wedges, and every library that draws it also brings a
 * layout engine, a tooltip system and a tick formatter that this never uses.
 */

import type { CSSProperties } from "react";
import { Money } from "@/components/Money";
import { cn } from "@/lib/cn";

export type Slice = {
  key: string;
  label: string;
  /** The original decimal string — what the legend renders. */
  amount: string;
  /** Its share of the total, 0 to 1. */
  share: number;
};

/*
 * Written out rather than built with a template literal: Tailwind scans the
 * source for whole class names, and one assembled at runtime is never
 * generated.
 */
const SLICE_COLOURS = [
  "stroke-chart-1",
  "stroke-chart-2",
  "stroke-chart-3",
  "stroke-chart-4",
  "stroke-chart-5",
  "stroke-chart-6",
] as const;

const DOT_COLOURS = [
  "bg-chart-1",
  "bg-chart-2",
  "bg-chart-3",
  "bg-chart-4",
  "bg-chart-5",
  "bg-chart-6",
] as const;

const RADIUS = 62;
const CIRCUMFERENCE = 2 * Math.PI * RADIUS;

export function Donut({
  slices,
  total,
  currency,
  className,
}: {
  slices: Slice[];
  total: string;
  currency: string;
  className?: string;
}) {
  let travelled = 0;

  return (
    <div className={cn("flex flex-col items-center gap-6 sm:flex-row", className)}>
      <div className="relative shrink-0">
        <svg
          viewBox="0 0 160 160"
          className="size-44 sm:size-48"
          role="img"
          aria-label={`Gastos por categoría, total ${total} ${currency}`}
        >
          <g transform="rotate(-90 80 80)" fill="none" strokeWidth="17">
            <circle cx="80" cy="80" r={RADIUS} className="stroke-surface-raised" />
            {slices.map((slice, index) => {
              const dash = slice.share * CIRCUMFERENCE;
              const offset = -travelled * CIRCUMFERENCE;
              travelled += slice.share;
              return (
                <circle
                  key={slice.key}
                  cx="80"
                  cy="80"
                  r={RADIUS}
                  className={SLICE_COLOURS[index % SLICE_COLOURS.length]}
                  strokeDasharray={`${dash} ${CIRCUMFERENCE - dash}`}
                  strokeDashoffset={offset}
                  style={
                    {
                      // Each wedge grows from nothing, once, in ring order.
                      "--sweep-circumference": CIRCUMFERENCE,
                      animation: `sweep 620ms cubic-bezier(0.16, 1, 0.3, 1) ${index * 70}ms both`,
                    } as CSSProperties
                  }
                />
              );
            })}
          </g>
        </svg>

        <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center">
          <Money amount={total} currency={currency} size="sm" />
          <span className="text-faint text-xs">Total</span>
        </div>
      </div>

      <ul className="flex w-full min-w-0 flex-col gap-2.5">
        {slices.map((slice, index) => (
          <li key={slice.key} className="flex items-center gap-3 text-sm">
            <span
              aria-hidden
              className={cn(
                "size-2.5 shrink-0 rounded-full",
                DOT_COLOURS[index % DOT_COLOURS.length],
              )}
            />
            <span className="min-w-0 flex-1 truncate text-muted">{slice.label}</span>
            <span className="tabular text-faint text-xs">
              {Math.round(slice.share * 100)}%
            </span>
            <Money
              amount={slice.amount}
              currency={currency}
              size="sm"
              className="w-24 text-right text-sm"
            />
          </li>
        ))}
      </ul>
    </div>
  );
}
