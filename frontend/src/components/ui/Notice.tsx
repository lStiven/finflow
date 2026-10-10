import type { ComponentType, ReactNode } from "react";
import { cn } from "@/lib/cn";

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
