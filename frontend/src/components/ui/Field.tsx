import { type InputHTMLAttributes, useId } from "react";
import { cn } from "@/lib/cn";

type Props = InputHTMLAttributes<HTMLInputElement> & {
  label: string;
  hint?: string;
};

export function Field({ label, hint, className, ...rest }: Props) {
  const id = useId();
  const hintId = `${id}-hint`;

  return (
    <div className="flex flex-col gap-2">
      <label htmlFor={id} className="text-sm text-muted">
        {label}
      </label>
      <input
        id={id}
        aria-describedby={hint ? hintId : undefined}
        className={cn(
          "rounded-xl border border-line bg-ink px-4 py-3 text-base text-text",
          "placeholder:text-faint",
          "focus:border-accent focus:outline-none",
          className,
        )}
        {...rest}
      />
      {hint ? (
        <p id={hintId} className="text-xs text-faint">
          {hint}
        </p>
      ) : null}
    </div>
  );
}
