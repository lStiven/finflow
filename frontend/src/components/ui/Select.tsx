import { type SelectHTMLAttributes, useId } from "react";
import { cn } from "@/lib/cn";

export type Option = { value: string; label: string };

type Props = Omit<SelectHTMLAttributes<HTMLSelectElement>, "children"> & {
  label: string;
  options: Option[];
  /** Shown as the first entry, for "no filter" — omit to force a choice. */
  placeholder?: string;
  hint?: string;
};

export function Select({
  label,
  options,
  placeholder,
  hint,
  className,
  ...rest
}: Props) {
  const id = useId();
  const hintId = `${id}-hint`;

  return (
    <div className="flex min-w-0 flex-col gap-2">
      <label htmlFor={id} className="text-muted text-sm">
        {label}
      </label>
      <select
        id={id}
        aria-describedby={hint ? hintId : undefined}
        className={cn(
          "rounded-xl border border-line bg-ink px-4 py-3 text-base text-text",
          "focus:border-accent focus:outline-none",
          className,
        )}
        {...rest}
      >
        {placeholder ? <option value="">{placeholder}</option> : null}
        {options.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
      {hint ? (
        <p id={hintId} className="text-faint text-xs">
          {hint}
        </p>
      ) : null}
    </div>
  );
}
