import { ChevronRight, Landmark, Mail } from "lucide-react";
import type { ReactNode } from "react";
import { Logo } from "@/components/Logo";
import { cn } from "@/lib/cn";

/**
 * Bank → Gmail → Finflow, the whole route in one picture.
 *
 * It replaces the paragraphs that used to explain it: an alert leaves the
 * bank, lands in the person's own Gmail, and only then travels on to
 * Finflow — which is also the answer to "do you read my mail?", drawn rather
 * than argued. `live` is the same picture while a first alert is awaited,
 * with Finflow's end listening.
 *
 * The motion is decoration and nothing depends on seeing it: with motion
 * reduced the envelopes rest out of sight and the chevrons still say which
 * way the mail goes. The caption is what a screen reader gets.
 */
export function FlowAnimation({
  size = "lg",
  live = false,
  className,
}: {
  size?: "lg" | "sm";
  live?: boolean;
  className?: string;
}) {
  const large = size === "lg";

  return (
    <figure className={cn("w-full", large ? "max-w-md" : "max-w-xs", className)}>
      <div aria-hidden className="flex items-start">
        <Node label="Tu banco" large={large} tone="cyan">
          <Landmark className={large ? "size-6" : "size-4.5"} />
        </Node>
        <Leg large={large} />
        <Node label="Tu Gmail" large={large} tone="violet">
          <Mail className={large ? "size-6" : "size-4.5"} />
        </Node>
        <Leg large={large} late />
        <Node label="Finflow" large={large} tone="accent" live={live}>
          <Logo className={large ? "size-9" : "size-6"} />
        </Node>
      </div>
      <figcaption className="sr-only">
        Tu banco te envía la alerta a tu Gmail, y Gmail se la reenvía a Finflow.
      </figcaption>
    </figure>
  );
}

const NODE_TONES = {
  cyan: "border-cyan/30 bg-cyan/10 text-cyan",
  violet: "border-violet/30 bg-violet/10 text-violet",
  accent: "border-accent/35 bg-accent/10 text-accent",
} as const;

function Node({
  label,
  large,
  tone,
  live = false,
  children,
}: {
  label: string;
  large: boolean;
  tone: keyof typeof NODE_TONES;
  live?: boolean;
  children: ReactNode;
}) {
  return (
    <div className="flex shrink-0 flex-col items-center gap-2">
      <span className="relative grid place-items-center">
        {live ? (
          <span className="absolute inset-0 animate-ping rounded-2xl bg-accent/25" />
        ) : null}
        <span
          className={cn(
            "relative grid place-items-center rounded-2xl border",
            large ? "size-14" : "size-10",
            NODE_TONES[tone],
          )}
        >
          {children}
        </span>
      </span>
      <span className={cn("text-muted", large ? "text-xs" : "text-[0.6875rem]")}>
        {label}
      </span>
    </div>
  );
}

/** One leg of the route: a dashed line, a chevron, and an alert crossing it. */
function Leg({ large, late = false }: { large: boolean; late?: boolean }) {
  return (
    <div
      className={cn(
        "@container relative mx-1.5 min-w-6 flex-1",
        large ? "h-14" : "h-10",
      )}
    >
      <span className="absolute inset-x-0 top-1/2 border-line border-t border-dashed" />
      <ChevronRight className="-translate-y-1/2 absolute top-1/2 right-0 size-3.5 text-faint" />
      <span
        className={cn(
          "hop absolute inset-y-0 left-0 my-auto grid place-items-center rounded-md bg-text text-ink shadow-[0_0_14px_-2px] shadow-cyan/60",
          large ? "h-4 w-5" : "h-3 w-4",
          late && "hop-late",
        )}
      >
        <Mail className={large ? "size-3" : "size-2.5"} />
      </span>
    </div>
  );
}
