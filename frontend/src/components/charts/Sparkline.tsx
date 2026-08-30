/**
 * The shape of a run of months, at a glance.
 *
 * Deliberately unlabelled and unreadable as a figure: it says "rising",
 * "falling" or "flat" and nothing else, which is all a KPI tile has room to
 * say. The exact numbers are one screen away.
 *
 * It traces itself once on arrival and then holds still.
 */

import { useId } from "react";
import { cn } from "@/lib/cn";

const WIDTH = 220;
const HEIGHT = 44;
const PAD = 3;

export function Sparkline({
  values,
  className,
}: {
  /** Oldest first. Fewer than two points draws nothing. */
  values: number[];
  /** A `text-*` class; the stroke and the fill both derive from it. */
  className?: string;
}) {
  const gradient = useId();

  if (values.length < 2) return null;

  const low = Math.min(...values);
  const high = Math.max(...values);
  // A flat run would divide by zero and, drawn at the top, would read as a
  // maximum. Centred is the honest picture of "no change".
  const span = high - low || 1;
  const step = (WIDTH - PAD * 2) / (values.length - 1);

  const points = values.map((value, index) => {
    const x = PAD + index * step;
    const y =
      high === low
        ? HEIGHT / 2
        : HEIGHT - PAD - ((value - low) / span) * (HEIGHT - PAD * 2);
    return [x, y] as const;
  });

  const line = points.map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join(" ");
  const area = `${PAD},${HEIGHT} ${line} ${(WIDTH - PAD).toFixed(1)},${HEIGHT}`;

  return (
    <svg
      viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
      preserveAspectRatio="none"
      aria-hidden
      className={cn("h-11 w-full", className)}
    >
      <defs>
        <linearGradient id={gradient} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="currentColor" stopOpacity="0.22" />
          <stop offset="100%" stopColor="currentColor" stopOpacity="0" />
        </linearGradient>
      </defs>
      <polygon points={area} fill={`url(#${gradient})`} />
      <polyline
        points={line}
        fill="none"
        stroke="currentColor"
        strokeWidth="1.75"
        strokeLinecap="round"
        strokeLinejoin="round"
        pathLength={1}
        strokeDasharray="1"
        style={{ animation: "trace 900ms cubic-bezier(0.16, 1, 0.3, 1) both" }}
      />
    </svg>
  );
}
