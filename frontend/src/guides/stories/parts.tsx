/**
 * The pieces every flow in the guides is drawn with, so the four of them —
 * and the connect guide's bank → Gmail → Finflow — read as one language:
 * a place is a tile, a trip is a dashed leg with something crossing it, a
 * fact that appears pops in, and a balance that moves counts to its new
 * figure.
 *
 * All of it is decoration over words that already say the same thing: the
 * slide's title and caption carry the meaning, so with motion reduced — or
 * with no sight of it at all — nothing is lost.
 */

import { ChevronRight, Coins, Mail } from "lucide-react";
import type { ComponentType, CSSProperties, ReactNode } from "react";
import { CountUpMoney } from "@/components/CountUpMoney";
import { cn } from "@/lib/cn";

type Icon = ComponentType<{ className?: string }>;

const TONES = {
  cyan: "border-cyan/30 bg-cyan/10 text-cyan",
  violet: "border-violet/30 bg-violet/10 text-violet",
  accent: "border-accent/35 bg-accent/10 text-accent",
  green: "border-incoming/30 bg-incoming/10 text-incoming",
} as const;

export type Tone = keyof typeof TONES;

const delay = (ms: number): CSSProperties => ({ animationDelay: `${ms}ms` });

/** A place money or mail passes through. */
export function Tile({
  icon: Glyph,
  label,
  tone,
  at = 0,
  children,
}: {
  icon?: Icon;
  label: string;
  tone: Tone;
  /** When it pops in, in milliseconds after the slide arrives. */
  at?: number;
  children?: ReactNode;
}) {
  return (
    <div className="pop flex shrink-0 flex-col items-center gap-2" style={delay(at)}>
      <span
        className={cn(
          "grid size-14 place-items-center rounded-2xl border",
          TONES[tone],
        )}
      >
        {Glyph ? <Glyph className="size-6" /> : children}
      </span>
      <span className="max-w-20 text-center text-muted text-xs leading-tight">
        {label}
      </span>
    </div>
  );
}

/** The trip between two tiles, with what travels it crossing on a loop. */
export function Leg({ carries = "mail" }: { carries?: "mail" | "money" }) {
  const Glyph = carries === "mail" ? Mail : Coins;
  return (
    <div className="@container relative mx-1.5 h-14 min-w-6 flex-1">
      <span className="absolute inset-x-0 top-1/2 border-line border-t border-dashed" />
      <ChevronRight className="-translate-y-1/2 absolute top-1/2 right-0 size-3.5 text-faint" />
      <span
        className={cn(
          "hop absolute inset-y-0 left-0 my-auto grid h-4 w-5 place-items-center rounded-md shadow-[0_0_14px_-2px]",
          carries === "mail"
            ? "bg-text text-ink shadow-cyan/60"
            : "bg-warn text-ink shadow-warn/60",
        )}
      >
        <Glyph className="size-3" />
      </span>
    </div>
  );
}

/** Two or three tiles joined by legs, as one row. */
export function Route({ children }: { children: ReactNode }) {
  return <div className="flex items-start">{children}</div>;
}

/** A fact that appears, with a beat between one and the next. */
export function Chip({
  label,
  value,
  tone = "cyan",
  at = 0,
}: {
  label: string;
  value: ReactNode;
  tone?: Tone;
  at?: number;
}) {
  return (
    <span
      className={cn(
        "pop inline-flex items-baseline gap-1.5 rounded-full border px-3 py-1 text-xs",
        TONES[tone],
      )}
      style={delay(at)}
    >
      <span className="opacity-75">{label}</span>
      <span className="font-medium text-text">{value}</span>
    </span>
  );
}

/** A card that slides into place: an email, a movement, a message. */
export function Slip({
  children,
  at = 0,
  className,
}: {
  children: ReactNode;
  at?: number;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "rise rounded-xl border border-line bg-surface p-3 text-sm",
        className,
      )}
      style={delay(at)}
    >
      {children}
    </div>
  );
}

/** A balance seen moving from what it was to what it is now. */
export function Balance({
  label,
  caption,
  from,
  to,
  tone = "plain",
  at = 0,
}: {
  label: string;
  caption: string;
  from: string;
  to: string;
  tone?: "positive" | "negative" | "plain";
  at?: number;
}) {
  return (
    <Slip at={at} className="flex items-center justify-between gap-3">
      <span className="min-w-0">
        <span className="block truncate">{label}</span>
        <span className="block text-faint text-xs">{caption}</span>
      </span>
      <CountUpMoney amount={to} from={from} currency="COP" size="sm" tone={tone} />
    </Slip>
  );
}

/** A segment of a whole, filling in after the ones before it. */
export function Share({
  label,
  amount,
  share,
  tone,
  at = 0,
}: {
  label: string;
  amount: ReactNode;
  /** 0 to 1 of the whole bar. */
  share: number;
  tone: "accent" | "cyan";
  at?: number;
}) {
  return (
    <div className="flex flex-col gap-1.5" style={{ width: `${share * 100}%` }}>
      <span
        className={cn(
          "grow-x block h-3 rounded-full",
          tone === "accent" ? "bg-accent/80" : "bg-cyan/80",
        )}
        style={delay(at)}
      />
      <span className="rise text-xs" style={delay(at + 300)}>
        <span className="block text-faint">{label}</span>
        <span className="block font-medium">{amount}</span>
      </span>
    </div>
  );
}

/** Centres a slide's picture and spaces what is stacked in it. */
export function Stage({ children }: { children: ReactNode }) {
  return <div className="flex min-h-48 flex-col justify-center gap-4">{children}</div>;
}
