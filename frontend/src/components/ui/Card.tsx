import type { HTMLAttributes } from "react";
import { cn } from "@/lib/cn";

/** Which hue the card lights up in on hover. `none` stays neutral. */
export type Glow = "accent" | "cyan" | "green" | "violet" | "none";

const GLOWS: Record<Glow, string> = {
  accent: "glow-accent",
  cyan: "glow-cyan",
  green: "glow-green",
  violet: "glow-violet",
  none: "",
};

type Props = HTMLAttributes<HTMLDivElement> & {
  glow?: Glow;
  /**
   * `false` keeps the card flat on hover — for a container that holds other
   * hover targets, where lifting the whole panel under the pointer fights
   * whatever is being pointed at.
   */
  lift?: boolean;
};

export function Card({ className, glow = "none", lift = true, ...rest }: Props) {
  return (
    <div
      className={cn(
        "surface rounded-card border border-line bg-surface p-5",
        GLOWS[glow],
        !lift && "surface-static",
        className,
      )}
      {...rest}
    />
  );
}
