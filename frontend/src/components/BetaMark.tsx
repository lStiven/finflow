import { cn } from "@/lib/cn";

/**
 * The one word on screen in handwriting.
 *
 * It says what the app is without dressing it as a feature: pencilled into a
 * corner, small, unclickable, and never in the way of anything. The script
 * face is loaded for this and nothing else.
 */
export function BetaMark({ className }: { className?: string }) {
  return (
    <span className={cn("beta-mark select-none", className)}>
      beta
      <span className="sr-only"> — versión en pruebas</span>
    </span>
  );
}
