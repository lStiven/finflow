/**
 * Spending by category: one stacked bar for the proportions, one ranked list
 * for the reading.
 *
 * Replaces the ring. A ring asks the eye to compare angles and needs a legend
 * beside it, which on a half-width card left the category names truncated to
 * three letters. Here the list *is* the legend, each name gets the full width,
 * and pointing at a row lights its segment so the two read as one.
 */

import { Link } from "@tanstack/react-router";
import { type ReactNode, useState } from "react";
import { Money } from "@/components/Money";
import { cn } from "@/lib/cn";

export type Part = {
  key: string;
  label: string;
  /** The original decimal string, which is what the list renders. */
  amount: string;
  /** Its share of the total, 0 to 1. Drives the drawing only. */
  share: number;
  /** The same figure at this point last month, when there was one. */
  previous?: string;
};

/* Whole class names, so Tailwind's scanner finds them. */
const FILLS = [
  "bg-chart-1",
  "bg-chart-2",
  "bg-chart-3",
  "bg-chart-4",
  "bg-chart-5",
  "bg-chart-6",
] as const;

export function CategoryBar({
  parts,
  currency,
  movementsOf,
  compare,
  hint,
}: {
  parts: Part[];
  currency: string;
  /** What changed for one part against last month; nothing when it cannot say. */
  compare?: (part: Part) => ReactNode;
  /** Shown while no part is picked. */
  hint?: string;
  /** The Transacciones filters behind one part, or null when no list can show it. */
  movementsOf: (part: Part) => Record<string, unknown> | null;
}) {
  const [active, setActive] = useState<string | null>(null);
  const focused = parts.find((part) => part.key === active) ?? null;

  return (
    <div className="flex flex-col gap-4">
      <div
        className="flex h-6 origin-left gap-0.5 overflow-hidden rounded-full"
        style={{ animation: "grow-from-zero 700ms cubic-bezier(0.16, 1, 0.3, 1) both" }}
      >
        {parts.map((part, index) => (
          <button
            key={part.key}
            type="button"
            aria-label={`${part.label}, ${Math.round(part.share * 100)}%`}
            aria-pressed={active === part.key}
            className={cn(
              "h-full min-w-1 cursor-pointer transition-opacity duration-200 focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2",
              FILLS[index % FILLS.length],
              active !== null && active !== part.key ? "opacity-25" : null,
            )}
            style={{ flex: `${part.share} 1 0` }}
            onPointerEnter={(event) => {
              if (event.pointerType === "mouse") setActive(part.key);
            }}
            onPointerLeave={(event) => {
              if (event.pointerType === "mouse") setActive(null);
            }}
            // A finger has no hover: tapping picks, tapping again lets go.
            onClick={() => setActive(active === part.key ? null : part.key)}
          />
        ))}
      </div>

      <div className="flex min-h-10 flex-col gap-0.5 text-sm" aria-live="polite">
        {focused ? (
          <>
            <span className="text-muted">
              {focused.label}, {Math.round(focused.share * 100)}% de lo gastado
            </span>
            <span className="text-xs">{compare?.(focused)}</span>
          </>
        ) : hint ? (
          <span className="text-faint text-xs">{hint}</span>
        ) : null}
      </div>

      <ul className="-mx-2 flex flex-col">
        {parts.map((part, index) => {
          const row: ReactNode = (
            <>
              <span
                aria-hidden
                className={cn(
                  "size-2.5 shrink-0 rounded-sm",
                  FILLS[index % FILLS.length],
                )}
              />
              <span className="min-w-0 flex-1 break-words">{part.label}</span>
              <span className="w-10 shrink-0 text-right text-faint text-xs tabular">
                {Math.round(part.share * 100)}%
              </span>
              <Money
                amount={part.amount}
                currency={currency}
                size="sm"
                className="shrink-0 text-right"
              />
            </>
          );
          const search = movementsOf(part);
          const shared = cn(
            "flex min-h-11 items-center gap-3 rounded-lg px-2 py-1.5 text-sm transition-colors duration-150",
            active === part.key ? "bg-surface-raised" : null,
          );
          const hover = {
            onPointerEnter: () => setActive(part.key),
            onPointerLeave: () => setActive(null),
            onFocus: () => setActive(part.key),
            onBlur: () => setActive(null),
          };

          return (
            <li key={part.key}>
              {search ? (
                <Link to="/transacciones" search={search} className={shared} {...hover}>
                  {row}
                </Link>
              ) : (
                <span className={shared} {...hover}>
                  {row}
                </span>
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );
}
