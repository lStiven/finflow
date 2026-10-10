import type { ButtonHTMLAttributes } from "react";
import { cn } from "@/lib/cn";

type Variant = "primary" | "ghost" | "quiet";

type Props = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: Variant;
  full?: boolean;
};

const variants = {
  primary:
    "bg-accent text-accent-ink hover:brightness-108 active:brightness-95 font-semibold",
  ghost:
    "border border-line bg-surface text-text hover:bg-surface-raised active:bg-surface",
  quiet: "text-muted hover:text-text",
} as const;

/**
 * The button's look, for the few things that must look like one and are not
 * one — a link that leaves the app, which has to stay an `<a>` to be opened
 * in a new tab or copied.
 */
export function buttonClass(variant: Variant = "primary", full = false): string {
  return cn(
    "inline-flex items-center justify-center gap-2 rounded-xl px-4 py-3",
    "text-sm transition-all duration-150",
    "disabled:cursor-not-allowed disabled:opacity-45",
    variants[variant],
    full && "w-full",
  );
}

export function Button({
  variant = "primary",
  full = false,
  className,
  disabled,
  ...rest
}: Props) {
  return (
    <button
      type="button"
      disabled={disabled}
      className={cn(buttonClass(variant, full), className)}
      {...rest}
    />
  );
}
