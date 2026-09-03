/**
 * Columns over time — two series side by side, or many stacked.
 *
 * Laid out in ordinary boxes rather than SVG, unlike the ring and the
 * sparkline beside it. Those are geometry; this is a row of rectangles with a
 * baseline, and boxes give it the things that matter here for free: hit
 * targets big enough to hover, an axis band that grows with its labels rather
 * than being clipped by a fixed viewBox, and a 2px gap in the surface colour
 * doing the separating instead of a stroke drawn round every mark.
 *
 * Every value is reachable three ways — the axis, the tooltip, and a table
 * for a screen reader — because a tooltip that is the *only* way to read a
 * number gates the chart on a pointer.
 */

import { useId, useState } from "react";
import { Money } from "@/components/Money";
import { cn } from "@/lib/cn";
import { formatMoney } from "@/lib/money";

export type ColumnPoint = {
  /** Geometry only — a float, never shown. */
  value: number;
  /** The decimal string the tooltip and the table render. */
  amount: string;
};

export type ColumnSeries = {
  key: string;
  label: string;
  /** A `bg-*` token. Identity, so it follows the entity and never the rank. */
  tone: string;
  /** One per bucket, in bucket order. Dense: a gap would misalign the run. */
  points: ColumnPoint[];
};

export type ColumnBucket = {
  key: string;
  /** The short form under the column. */
  label: string;
  /** The long form the tooltip names. */
  full: string;
  /** The period still being lived. Drawn lighter and footnoted. */
  partial?: boolean;
};

const PLOT_HEIGHT = "h-52 sm:h-60";
/** Bars stay thin: the leftover of each slot is air, not more ink. */
const SINGLE_WIDTH = "max-w-[22px]";
const PAIRED_WIDTH = "max-w-[13px]";
/** Enough x labels to orient, few enough that none collide. */
const MAX_TICKS = 8;

export function Columns({
  buckets,
  series,
  mode,
  currency,
  className,
  caption,
}: {
  buckets: ColumnBucket[];
  series: ColumnSeries[];
  mode: "grouped" | "stacked";
  currency: string;
  className?: string;
  /** What the chart is, for the reader who cannot see it. */
  caption: string;
}) {
  const tableId = useId();
  const [active, setActive] = useState<number | null>(null);

  // Stacked measures the tallest total; grouped the tallest single bar. Both
  // fall back to 1 so an all-zero run draws a flat baseline instead of
  // dividing by nothing.
  const ceiling =
    Math.max(
      ...buckets.map((_, index) =>
        mode === "stacked"
          ? series.reduce((sum, band) => sum + (band.points[index]?.value ?? 0), 0)
          : Math.max(...series.map((band) => band.points[index]?.value ?? 0), 0),
      ),
      0,
    ) || 1;

  const step = Math.ceil(buckets.length / MAX_TICKS);
  const anyPartial = buckets.some((bucket) => bucket.partial);

  return (
    <figure className={cn("flex flex-col gap-3", className)}>
      <div className="flex gap-3">
        <YAxis ceiling={ceiling} />

        <div className="relative min-w-0 flex-1">
          <Gridlines />

          <div className={cn("relative flex items-end gap-[2px]", PLOT_HEIGHT)}>
            {buckets.map((bucket, index) => (
              <div
                key={bucket.key}
                className="relative flex h-full min-w-0 flex-1 items-end"
              >
                {mode === "stacked" ? (
                  <Stack
                    series={series}
                    index={index}
                    ceiling={ceiling}
                    dimmed={bucket.partial === true}
                  />
                ) : (
                  <Pair
                    series={series}
                    index={index}
                    ceiling={ceiling}
                    dimmed={bucket.partial === true}
                  />
                )}

                {/*
                 * The hit target is the whole slot, not the painted pixels: a
                 * 6px column on a phone is a pinpoint nobody lands on. Focus
                 * shows the same readout as hover, so the chart is as usable
                 * from a keyboard as from a pointer.
                 */}
                {/*
                 * The label carries the figures rather than pointing at the
                 * table: `aria-describedby` on each of thirty columns would
                 * read the whole table out thirty times. Said here, a
                 * keyboard reader gets the same numbers the tooltip shows.
                 */}
                <button
                  type="button"
                  aria-label={describeSlot(bucket, series, index, currency)}
                  className={cn(
                    "absolute inset-0 rounded-md transition-colors duration-100",
                    active === index && "bg-text/5",
                  )}
                  onPointerEnter={() => setActive(index)}
                  onPointerLeave={() => setActive((at) => (at === index ? null : at))}
                  onFocus={() => setActive(index)}
                  onBlur={() => setActive((at) => (at === index ? null : at))}
                />
              </div>
            ))}
          </div>

          {active !== null && buckets[active] ? (
            <Readout
              bucket={buckets[active]}
              series={series}
              index={active}
              currency={currency}
              position={(active + 0.5) / buckets.length}
            />
          ) : null}

          <div className="mt-2 flex gap-[2px]">
            {buckets.map((bucket, index) => (
              // One slot wide but allowed to spill into the blank slots
              // beside it: truncating here clipped "15" to "1" on a phone,
              // where a slot is eight pixels and the axis then read as a row
              // of loose digits. `min-w-0` keeps the row itself from growing.
              <span
                key={bucket.key}
                className="min-w-0 flex-1 overflow-visible whitespace-nowrap text-center text-[10px] text-faint"
              >
                {index % step === 0 ? bucket.label : " "}
              </span>
            ))}
          </div>
        </div>
      </div>

      {anyPartial ? (
        <p className="text-faint text-xs">
          La última barra cubre un periodo que aún no termina, por eso se ve más corta.
        </p>
      ) : null}

      {/*
       * The table twin. Not a fallback — the same numbers, reachable without a
       * pointer and without colour.
       */}
      <table id={tableId} className="sr-only">
        <caption>{caption}</caption>
        <thead>
          <tr>
            <th>Periodo</th>
            {series.map((band) => (
              <th key={band.key}>{band.label}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {buckets.map((bucket, index) => (
            <tr key={bucket.key}>
              <th scope="row">{bucket.full}</th>
              {series.map((band) => (
                <td key={band.key}>
                  {formatMoney(band.points[index]?.amount ?? "0", currency)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </figure>
  );
}

function Stack({
  series,
  index,
  ceiling,
  dimmed,
}: {
  series: ColumnSeries[];
  index: number;
  ceiling: number;
  dimmed: boolean;
}) {
  // `flex-col-reverse` so the first band sits on the baseline, which is where
  // a reader expects the first legend entry to be.
  return (
    <div
      className={cn(
        "flex h-full w-full flex-col-reverse gap-[2px]",
        SINGLE_WIDTH,
        "mx-auto",
        dimmed && "opacity-60",
      )}
    >
      {series.map((band, depth) => {
        const point = band.points[index];
        if (!point || point.value <= 0) return null;
        // Only the topmost segment gets the rounded data-end; the rest meet
        // their neighbours square across the 2px gap.
        const highest = series.every(
          (other, at) => at <= depth || (other.points[index]?.value ?? 0) <= 0,
        );
        return (
          <div
            key={band.key}
            className={cn(
              "min-h-[2px] w-full",
              band.tone,
              highest && "rounded-t-[4px]",
            )}
            style={{ height: `${(point.value / ceiling) * 100}%` }}
          />
        );
      })}
    </div>
  );
}

function Pair({
  series,
  index,
  ceiling,
  dimmed,
}: {
  series: ColumnSeries[];
  index: number;
  ceiling: number;
  dimmed: boolean;
}) {
  // One series is not a pair: splitting the slot in half for a bar that has
  // no neighbour leaves a thread of ink and half a slot of nothing.
  const width = series.length > 1 ? PAIRED_WIDTH : SINGLE_WIDTH;

  return (
    <div
      className={cn(
        "flex h-full w-full items-end justify-center gap-[2px]",
        dimmed && "opacity-60",
      )}
    >
      {series.map((band) => {
        const point = band.points[index];
        const value = point?.value ?? 0;
        return (
          <div
            key={band.key}
            className={cn(
              "w-full rounded-t-[4px]",
              width,
              band.tone,
              value > 0 && "min-h-[2px]",
            )}
            style={{ height: `${(value / ceiling) * 100}%` }}
          />
        );
      })}
    </div>
  );
}

/**
 * One column, said in words: the period and what each band holds in it.
 *
 * This is what makes the chart readable without a pointer, so it carries the
 * figures themselves rather than deferring to the tooltip.
 */
function describeSlot(
  bucket: ColumnBucket,
  series: ColumnSeries[],
  index: number,
  currency: string,
): string {
  const moved = series.filter((band) => (band.points[index]?.value ?? 0) > 0);
  const period = `${bucket.full}${bucket.partial ? " (en curso)" : ""}`;

  if (moved.length === 0) return `${period}: sin movimientos`;

  const parts = moved.map(
    (band) =>
      `${band.label} ${formatMoney(band.points[index]?.amount ?? "0", currency)}`,
  );
  return `${period}: ${parts.join(", ")}`;
}

/** Three ticks: nothing, half, and the ceiling. More is chrome. */
function YAxis({ ceiling }: { ceiling: number }) {
  return (
    <div
      aria-hidden
      className={cn(
        "flex w-11 shrink-0 flex-col justify-between text-right text-[10px] text-faint tabular",
        PLOT_HEIGHT,
      )}
    >
      <span>{compact(ceiling)}</span>
      <span>{compact(ceiling / 2)}</span>
      <span>0</span>
    </div>
  );
}

function Gridlines() {
  return (
    <div aria-hidden className={cn("absolute inset-x-0 top-0", PLOT_HEIGHT)}>
      {[0, 50, 100].map((at) => (
        <span
          key={at}
          className="absolute inset-x-0 h-px bg-line"
          style={{ top: `${at}%` }}
        />
      ))}
    </div>
  );
}

/**
 * The hover readout. Values lead and the series name follows — the legend's
 * hierarchy inverted, because by now the reader has the series and wants the
 * number.
 */
function Readout({
  bucket,
  series,
  index,
  currency,
  position,
}: {
  bucket: ColumnBucket;
  series: ColumnSeries[];
  index: number;
  currency: string;
  position: number;
}) {
  const shown = series.filter((band) => (band.points[index]?.value ?? 0) > 0);

  return (
    <div
      aria-hidden
      className="pointer-events-none absolute bottom-full z-10 mb-1 w-max max-w-[13rem] rounded-xl border border-line bg-surface-raised p-2.5 shadow-lg"
      style={{
        // Clamped so a readout on the first or last column stays on screen
        // instead of hanging off the card.
        left: `${Math.min(88, Math.max(12, position * 100))}%`,
        transform: "translateX(-50%)",
      }}
    >
      <p className="mb-1.5 text-faint text-xs">
        {bucket.full}
        {bucket.partial ? " · en curso" : null}
      </p>
      {shown.length === 0 ? (
        <p className="text-muted text-xs">Sin movimientos</p>
      ) : (
        <ul className="flex flex-col gap-1">
          {shown.map((band) => (
            <li key={band.key} className="flex items-center gap-2 text-xs">
              <span
                className={cn("h-0.5 w-3 shrink-0 rounded-full", band.tone)}
                aria-hidden
              />
              <Money
                amount={band.points[index]?.amount ?? "0"}
                currency={currency}
                size="sm"
                className="text-xs"
              />
              <span className="min-w-0 truncate text-faint">{band.label}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

/** An axis tick: rounded hard, because it labels a gridline and not a figure. */
function compact(value: number): string {
  if (value <= 0) return "0";
  return new Intl.NumberFormat("es-CO", {
    notation: "compact",
    maximumFractionDigits: 1,
  }).format(value);
}
