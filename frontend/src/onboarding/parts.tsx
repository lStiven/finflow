/**
 * The small pieces every screen of the connect guide is built from, so the
 * four steps, the summary and the dialogs share one vocabulary: one way to
 * title a step, to warn, to leave for Gmail, and to say "done".
 */

import { ExternalLink } from "lucide-react";
import type { ComponentType, CSSProperties, ReactNode } from "react";
import { buttonClass } from "@/components/ui/Button";
import { cn } from "@/lib/cn";

/** The step heading's id: moving between steps puts focus on it. */
export const STEP_TITLE_ID = "connect-step-title";

export function StepHeading({
  title,
  lead,
  center = false,
}: {
  title: string;
  lead?: ReactNode;
  center?: boolean;
}) {
  return (
    <div className={cn("mb-6", center && "text-center")}>
      {/* Focusable but not in the tab order: it is where a screen reader
          lands after "Continuar", so the new step is announced by name. */}
      <h2
        id={STEP_TITLE_ID}
        tabIndex={-1}
        className="text-balance font-semibold text-xl tracking-tight focus:outline-none sm:text-2xl"
      >
        {title}
      </h2>
      {lead ? (
        <p
          className={cn(
            "mt-2 max-w-xl text-pretty text-muted text-sm leading-relaxed sm:text-base",
            center && "mx-auto",
          )}
        >
          {lead}
        </p>
      ) : null}
    </div>
  );
}

type Tone = "info" | "warn" | "success";

const TONES: Record<Tone, { box: string; icon: string; title: string }> = {
  info: {
    box: "border-cyan/25 bg-cyan/6",
    icon: "text-cyan",
    title: "text-text",
  },
  warn: {
    box: "border-warn/30 bg-warn/7",
    icon: "text-warn",
    title: "text-warn",
  },
  success: {
    box: "border-incoming/30 bg-incoming/7",
    icon: "text-incoming",
    title: "text-incoming",
  },
};

/** Something to know right here — never a paragraph somebody has to find. */
export function Notice({
  tone = "info",
  icon: Icon,
  title,
  children,
  className,
}: {
  tone?: Tone;
  icon: ComponentType<{ className?: string }>;
  title: ReactNode;
  children?: ReactNode;
  className?: string;
}) {
  const look = TONES[tone];

  return (
    <div
      className={cn(
        "flex items-start gap-3 rounded-2xl border p-4",
        look.box,
        className,
      )}
    >
      <span aria-hidden className={cn("mt-0.5 shrink-0", look.icon)}>
        <Icon className="size-4.5" />
      </span>
      <div className="min-w-0 flex-1">
        <p className={cn("font-medium text-sm", look.title)}>{title}</p>
        {children ? (
          <div className="mt-1 text-muted text-sm leading-relaxed">{children}</div>
        ) : null}
      </div>
    </div>
  );
}

/** A way out to Gmail. An `<a>`, so it can open in a tab and be copied. */
export function ExternalButton({
  href,
  children,
  variant = "ghost",
  className,
}: {
  href: string;
  children: ReactNode;
  variant?: "primary" | "ghost" | "quiet";
  className?: string;
}) {
  return (
    <a
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      className={cn(buttonClass(variant), className)}
    >
      {children}
      <ExternalLink aria-hidden className="size-3.5 opacity-70" />
      <span className="sr-only">(se abre en una pestaña nueva)</span>
    </a>
  );
}

/** A pulse that means "on its way", for a state nobody has to act on. */
export function WaitingDot({ className }: { className?: string }) {
  return (
    <span aria-hidden className={cn("relative inline-flex size-2", className)}>
      <span className="absolute inset-0 animate-ping rounded-full bg-cyan/70" />
      <span className="relative inline-flex size-full rounded-full bg-cyan" />
    </span>
  );
}

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
