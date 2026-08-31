import mark from "@/assets/mark.png";
import { cn } from "@/lib/cn";

/**
 * The app's mark, in one place so the three screens that show it cannot drift.
 *
 * Imported rather than pointed at `/public`: Vite hashes the filename, so a
 * new artwork invalidates itself instead of living behind whatever cache the
 * old one earned. The tile already carries its own rounded corners and
 * transparent background, so it needs no frame — anything drawn behind it
 * would read as a badge around a badge.
 */
export function Logo({
  className,
  label,
}: {
  className?: string;
  /**
   * Only when the mark stands alone. Beside the word "Finflow" it is
   * decoration, and a screen reader announcing the name twice is noise.
   */
  label?: string;
}) {
  return (
    <img
      src={mark}
      alt={label ?? ""}
      aria-hidden={label === undefined}
      width={128}
      height={128}
      className={cn("block shrink-0 select-none", className)}
      draggable={false}
    />
  );
}
