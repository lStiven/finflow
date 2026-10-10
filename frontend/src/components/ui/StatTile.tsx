import { Link } from "@tanstack/react-router";
import type { ComponentType, ReactNode } from "react";
import type { Glow } from "@/components/ui/Card";
import { cn } from "@/lib/cn";

const TINTS = {
  accent: "bg-accent/12 text-accent",
  cyan: "bg-cyan/12 text-cyan",
  green: "bg-incoming/12 text-incoming",
  violet: "bg-violet/12 text-violet",
  none: "bg-surface-raised text-muted",
} as const;

const GLOWS: Record<Glow, string> = {
  accent: "glow-accent",
  cyan: "glow-cyan",
  green: "glow-green",
  violet: "glow-violet",
  none: "",
};

type Props = {
  label: string;
  icon: ComponentType<{ className?: string }>;
  hue?: Glow;
  /** The figure. Always a `<Money>`, never a pre-formatted string. */
  children: ReactNode;
  /** The change against the same figure last month. */
  caption?: ReactNode;
  /** The shape of the run behind the figure. */
  chart?: ReactNode;
  /**
   * Makes the whole tile a link — "Gastos" opens the movements behind it,
   * "Patrimonio" the accounts it is made of.
   */
  to?: { to: "/transacciones"; search: Record<string, unknown> } | { to: "/cuentas" };
};

export function StatTile({
  label,
  icon: Icon,
  hue = "none",
  children,
  caption,
  chart,
  to,
}: Props) {
  const body = (
    <>
      <div className="flex items-start justify-between gap-3">
        <p className="text-muted text-sm">{label}</p>
        <span
          className={cn(
            "grid size-8 shrink-0 place-items-center rounded-lg transition-transform duration-200 group-hover:scale-110",
            TINTS[hue],
          )}
        >
          <Icon className="size-4" />
        </span>
      </div>

      <div className="min-w-0">{children}</div>
      {caption ? <div className="text-xs">{caption}</div> : null}
      {chart ? <div className="-mx-1 -mb-1 mt-1">{chart}</div> : null}
    </>
  );

  const shell = cn(
    "surface group flex flex-col gap-2 rounded-card border border-line bg-surface p-4 sm:p-5",
    GLOWS[hue],
  );

  if (to?.to === "/transacciones") {
    return (
      <Link to={to.to} search={to.search} className={shell}>
        {body}
      </Link>
    );
  }

  if (to?.to === "/cuentas") {
    return (
      <Link to="/cuentas" className={shell}>
        {body}
      </Link>
    );
  }

  return <div className={cn(shell, "surface-static")}>{body}</div>;
}
