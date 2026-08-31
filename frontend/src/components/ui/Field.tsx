import type { ComponentType, InputHTMLAttributes } from "react";
import { useId } from "react";
import { cn } from "@/lib/cn";

type Props = InputHTMLAttributes<HTMLInputElement> & {
  label: string;
  hint?: string;
  /** Optional leading adornment. Decorative — the label is what names the field. */
  icon?: ComponentType<{ className?: string }>;
};

export function Field({ label, hint, icon: Icon, className, ...rest }: Props) {
  const id = useId();
  const hintId = `${id}-hint`;

  return (
    <div className="flex flex-col gap-2">
      <label htmlFor={id} className="text-sm text-muted">
        {label}
      </label>
      <div className="relative">
        {Icon ? (
          <Icon className="-translate-y-1/2 pointer-events-none absolute top-1/2 left-4 size-4 text-faint" />
        ) : null}
        <input
          id={id}
          aria-describedby={hint ? hintId : undefined}
          className={cn(
            "w-full rounded-xl border border-line bg-ink px-4 py-3 text-base text-text",
            "placeholder:text-faint",
            "transition-colors duration-150",
            "focus:border-accent focus:outline-none",
            Icon && "pl-11",
            className,
          )}
          {...rest}
        />
      </div>
      {hint ? (
        <p id={hintId} className="text-xs text-faint">
          {hint}
        </p>
      ) : null}
    </div>
  );
}
