import { type TextareaHTMLAttributes, useId } from "react";
import { cn } from "@/lib/cn";

type Props = TextareaHTMLAttributes<HTMLTextAreaElement> & {
  label: string;
  hint?: string;
};

export function TextArea({ label, hint, className, ...rest }: Props) {
  const id = useId();
  const hintId = `${id}-hint`;

  return (
    <div className="flex flex-col gap-2">
      <label htmlFor={id} className="text-muted text-sm">
        {label}
      </label>
      <textarea
        id={id}
        aria-describedby={hint ? hintId : undefined}
        rows={3}
        className={cn(
          "resize-y rounded-xl border border-line bg-ink px-4 py-3 text-base text-text",
          "placeholder:text-faint focus:border-accent focus:outline-none",
          className,
        )}
        {...rest}
      />
      {hint ? (
        <p id={hintId} className="text-faint text-xs">
          {hint}
        </p>
      ) : null}
    </div>
  );
}
