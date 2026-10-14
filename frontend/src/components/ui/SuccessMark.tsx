import type { CSSProperties } from "react";
import { cn } from "@/lib/cn";

/** Where each particle of the success burst lands, and in which hue. */
const PARTICLES: { x: number; y: number; color: string }[] = [
  { x: -52, y: -36, color: "bg-incoming" },
  { x: 48, y: -44, color: "bg-cyan" },
  { x: 62, y: 6, color: "bg-accent" },
  { x: -64, y: 12, color: "bg-violet" },
  { x: -32, y: 50, color: "bg-warn" },
  { x: 36, y: 46, color: "bg-incoming" },
  { x: 2, y: -64, color: "bg-accent" },
  { x: 8, y: 62, color: "bg-cyan" },
];

/**
 * The tick that says a step is proven. `celebrate` adds the one burst the
 * guide allows itself, for the moment a real movement arrives.
 */
export function SuccessMark({
  celebrate = false,
  className,
}: {
  celebrate?: boolean;
  className?: string;
}) {
  return (
    <span className={cn("relative grid place-items-center", className)}>
      {celebrate ? (
        <span
          aria-hidden
          className="pointer-events-none absolute inset-0 grid place-items-center"
        >
          {PARTICLES.map(({ x, y, color }, index) => (
            <span
              key={`${x}:${y}`}
              className={cn("burst absolute size-1.5 rounded-full", color)}
              style={
                {
                  "--burst-x": `${x}px`,
                  "--burst-y": `${y}px`,
                  animationDelay: `${index * 30}ms`,
                } as CSSProperties
              }
            />
          ))}
        </span>
      ) : null}
      <span className="pop grid size-16 place-items-center rounded-full bg-incoming/15 text-incoming ring-1 ring-incoming/40">
        <svg
          viewBox="0 0 24 24"
          className="size-8"
          fill="none"
          stroke="currentColor"
          strokeWidth={2.5}
          strokeLinecap="round"
          strokeLinejoin="round"
          aria-hidden="true"
        >
          <path className="draw-check" pathLength={1} d="M5 12.5l4.5 4.5L19 7.5" />
        </svg>
      </span>
    </span>
  );
}
