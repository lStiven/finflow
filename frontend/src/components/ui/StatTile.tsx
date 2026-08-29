import type { ComponentType, ReactNode } from "react";
import { cn } from "@/lib/cn";

/*
 * Tint written out per tile rather than assembled: Tailwind only generates
 * class names it can find whole in the source.
 */
const TINTS = {
  accent: "bg-accent-soft text-accent",
  incoming: "bg-chart-4/15 text-incoming",
  outgoing: "bg-chart-1/15 text-outgoing",
  neutral: "bg-surface-raised text-muted",
} as const;

export function StatTile({
  label,
  icon: Icon,
  tint = "neutral",
  children,
  caption,
}: {
  label: string;
  icon: ComponentType<{ className?: string }>;
  tint?: keyof typeof TINTS;
  /** The figure. Always a `<Money>`, never a pre-formatted string. */
  children: ReactNode;
  caption?: ReactNode;
}) {
  return (
    <div className="flex flex-col gap-3 rounded-2xl border border-line bg-surface p-4 sm:p-5">
      <div className="flex items-start justify-between gap-3">
        <p className="text-muted text-sm">{label}</p>
        <span
          className={cn(
            "grid size-8 shrink-0 place-items-center rounded-lg",
            TINTS[tint],
          )}
        >
          <Icon className="size-4" />
        </span>
      </div>
      <div className="min-w-0">{children}</div>
      {caption ? <div className="text-faint text-xs">{caption}</div> : null}
    </div>
  );
}
