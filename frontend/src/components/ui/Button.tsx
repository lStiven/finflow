import type { ButtonHTMLAttributes } from "react";
import { cn } from "@/lib/cn";

type Props = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "primary" | "ghost" | "quiet";
  full?: boolean;
};

const variants = {
  primary:
    "bg-accent text-accent-ink hover:brightness-108 active:brightness-95 font-semibold",
  ghost:
    "border border-line bg-surface text-text hover:bg-surface-raised active:bg-surface",
  quiet: "text-muted hover:text-text",
} as const;

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
      className={cn(
        "inline-flex items-center justify-center gap-2 rounded-xl px-4 py-3",
        "text-sm transition-all duration-150",
        "disabled:cursor-not-allowed disabled:opacity-45",
        variants[variant],
        full && "w-full",
        className,
      )}
      {...rest}
    />
  );
}
