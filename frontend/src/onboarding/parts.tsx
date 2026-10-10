/**
 * The small pieces only the connect guide uses: one way to title a step, to
 * leave for Gmail, and to say "on its way". The ones other screens needed too
 * — `Notice`, `SuccessMark` — moved to `components/ui/`.
 */

import { ExternalLink } from "lucide-react";
import type { ReactNode } from "react";
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
