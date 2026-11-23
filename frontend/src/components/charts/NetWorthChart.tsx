/**
 * Net worth over the last months, as one line you can run a finger along.
 *
 * The scale always includes zero: a line zoomed onto its own band turns a
 * 2% wobble into a cliff, and this is the one chart on the screen people
 * read as "am I better off". The month still being lived is drawn dashed,
 * because its point moves every time something arrives.
 *
 * Scrubbing reports an index; the figure it changes lives with the caller,
 * so the big number above the chart and the marker on it never disagree.
 */

import { type KeyboardEvent, type PointerEvent, useId, useRef } from "react";
import { cn } from "@/lib/cn";

export type NetWorthPoint = {
  key: string;
  /** Short month name for the axis, e.g. «nov». */
  label: string;
  value: number;
  partial: boolean;
};

const WIDTH = 600;
const HEIGHT = 160;
const PAD_TOP = 10;
const PAD_BOTTOM = 4;

const compact = (currency: string) => {
  try {
    return new Intl.NumberFormat("es-CO", {
      style: "currency",
      currency,
      notation: "compact",
      maximumFractionDigits: 1,
    });
  } catch {
    return new Intl.NumberFormat("es-CO", { notation: "compact" });
  }
};

export function NetWorthChart({
  points,
  selected,
  onSelect,
  describe,
  currency,
  className,
}: {
  /** Oldest first. Fewer than two draws nothing. */
  points: NetWorthPoint[];
  /** The index the marker sits on. */
  selected: number;
  /** An index while scrubbing, null when the finger or pointer lets go. */
  onSelect: (index: number | null) => void;
  /** What a screen reader hears for one point. */
  describe: (index: number) => string;
  currency: string;
  className?: string;
}) {
  const gradient = useId();
  const frame = useRef<HTMLDivElement>(null);

  if (points.length < 2) return null;

  const values = points.map((point) => point.value);
  const low = Math.min(0, ...values);
  const high = Math.max(0, ...values);
  const span = high - low || 1;
  const last = points.length - 1;

  const x = (index: number) => (index / last) * WIDTH;
  const y = (value: number) =>
    PAD_TOP + ((high - value) / span) * (HEIGHT - PAD_TOP - PAD_BOTTOM);

  const coords = points.map((point, index) => `${x(index)},${y(point.value)}`);
  const lastIsPartial = points[last]?.partial ?? false;
  // The settled run, solid; the month in progress hangs off its end, dashed.
  const settled = lastIsPartial ? coords.slice(0, -1) : coords;
  const area = `0,${y(low)} ${coords.join(" ")} ${WIDTH},${y(low)}`;
  const zero = y(0);

  const indexAt = (clientX: number) => {
    const box = frame.current?.getBoundingClientRect();
    if (!box || box.width === 0) return selected;
    const ratio = Math.min(1, Math.max(0, (clientX - box.left) / box.width));
    return Math.round(ratio * last);
  };

  const onPointer = (event: PointerEvent<HTMLDivElement>) => {
    if (
      event.pointerType === "mouse" ||
      event.buttons > 0 ||
      event.type === "pointerdown"
    ) {
      onSelect(indexAt(event.clientX));
    }
  };

  const release = (event: PointerEvent<HTMLDivElement>) => {
    // A mouse keeps reading while it hovers; a finger lifted goes back to now.
    if (event.type === "pointerleave" || event.pointerType !== "mouse") onSelect(null);
  };

  const onKey = (event: KeyboardEvent<HTMLDivElement>) => {
    const moves: Record<string, number> = {
      ArrowLeft: selected - 1,
      ArrowRight: selected + 1,
      Home: 0,
      End: last,
    };
    if (event.key === "Escape") {
      onSelect(null);
      return;
    }
    const next = moves[event.key];
    if (next === undefined) return;
    event.preventDefault();
    onSelect(Math.min(last, Math.max(0, next)));
  };

  const markerX = (selected / last) * 100;
  const markerY = (y(points[selected]?.value ?? 0) / HEIGHT) * 100;
  const scale = compact(currency);

  return (
    <div className={cn("flex flex-col gap-2", className)}>
      <div
        ref={frame}
        role="slider"
        tabIndex={0}
        aria-label="Patrimonio mes a mes"
        aria-valuemin={0}
        aria-valuemax={last}
        aria-valuenow={selected}
        aria-valuetext={describe(selected)}
        onPointerDown={onPointer}
        onPointerMove={onPointer}
        onPointerUp={release}
        onPointerCancel={release}
        onPointerLeave={release}
        onKeyDown={onKey}
        onBlur={() => onSelect(null)}
        className="relative h-36 cursor-crosshair touch-pan-y rounded-lg sm:h-44"
      >
        <svg
          viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
          preserveAspectRatio="none"
          aria-hidden
          className="absolute inset-0 size-full overflow-visible text-cyan"
        >
          <defs>
            <linearGradient id={gradient} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="currentColor" stopOpacity="0.2" />
              <stop offset="100%" stopColor="currentColor" stopOpacity="0" />
            </linearGradient>
          </defs>

          {[PAD_TOP, (PAD_TOP + y(low)) / 2, y(low)].map((line) => (
            <line
              key={line}
              x1="0"
              x2={WIDTH}
              y1={line}
              y2={line}
              className="stroke-line"
              strokeWidth="1"
              vectorEffect="non-scaling-stroke"
            />
          ))}
          {low < 0 ? (
            <line
              x1="0"
              x2={WIDTH}
              y1={zero}
              y2={zero}
              className="stroke-faint"
              strokeWidth="1"
              vectorEffect="non-scaling-stroke"
            />
          ) : null}

          <polygon
            points={area}
            fill={`url(#${gradient})`}
            style={{
              animation: "fade-in 700ms cubic-bezier(0.16, 1, 0.3, 1) 250ms both",
            }}
          />
          <polyline
            points={settled.join(" ")}
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
            vectorEffect="non-scaling-stroke"
            pathLength={1}
            strokeDasharray="1"
            style={{ animation: "trace 1100ms cubic-bezier(0.16, 1, 0.3, 1) both" }}
          />
          {lastIsPartial ? (
            <polyline
              points={coords.slice(-2).join(" ")}
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeDasharray="4 5"
              vectorEffect="non-scaling-stroke"
              style={{ animation: "fade-in 400ms ease-out 900ms both" }}
            />
          ) : null}
        </svg>

        {/* The scale's two ends, so the line has a size without an axis. */}
        <span className="pointer-events-none absolute top-0 left-0 -translate-y-1/2 bg-surface pr-1.5 text-faint text-xs tabular">
          {scale.format(high)}
        </span>
        <span className="pointer-events-none absolute bottom-1 left-0 bg-surface pr-1.5 text-faint text-xs tabular">
          {scale.format(low)}
        </span>

        {/*
         * The marker is HTML over the SVG: the SVG stretches to fit, which
         * would squash a dot into an oval. Each axis moves by transform on a
         * full-size layer, so a percentage of the layer is a percentage of the
         * chart and nothing animates `left` or `top`.
         */}
        {/* Clipped, or the shifted layer itself would widen the page: at the
            last month it sits a whole chart-width to the right. The clip is
            wider than the chart so the dot on either end stays whole. */}
        <div
          aria-hidden
          className="pointer-events-none absolute -inset-x-3 inset-y-0 overflow-x-clip"
        >
          <div className="absolute inset-x-3 inset-y-0">
            <div
              className="absolute inset-0 transition-transform duration-200 ease-out motion-reduce:transition-none"
              style={{ transform: `translateX(${markerX}%)` }}
            >
              <span className="absolute inset-y-0 left-0 w-px bg-cyan/35" />
              <div
                className="absolute inset-0 transition-transform duration-200 ease-out motion-reduce:transition-none"
                style={{ transform: `translateY(${markerY}%)` }}
              >
                <span className="absolute top-0 left-0 size-3 -translate-x-1/2 -translate-y-1/2 rounded-full bg-cyan ring-4 ring-cyan/20" />
              </div>
            </div>
          </div>
        </div>
      </div>

      <div aria-hidden className="relative h-4 text-faint text-xs">
        {points.map((point, index) => {
          // On a phone, every third month counted back from now, so the
          // labels never collide; from `sm` up, all of them.
          const sparse = (last - index) % 3 !== 0;
          return (
            <span
              key={point.key}
              className={cn(
                "absolute top-0 whitespace-nowrap transition-colors duration-150",
                index === 0
                  ? "translate-x-0"
                  : index === last
                    ? "-translate-x-full"
                    : "-translate-x-1/2",
                index === selected ? "text-text" : null,
                sparse && index !== selected ? "hidden sm:block" : null,
              )}
              style={{ left: `${(index / last) * 100}%` }}
            >
              {point.label}
            </span>
          );
        })}
      </div>
    </div>
  );
}
