import { cn } from "@/lib/cn";
import { formatMoney, formatSignedMoney } from "@/lib/money";

type Props = {
  amount: string;
  currency: string;
  /** `signed` shows an explicit `+` — for a net that can go either way. */
  signed?: boolean;
  tone?: "positive" | "negative" | "neutral" | "plain";
  size?: "lg" | "md" | "sm";
  className?: string;
};

const tones = {
  positive: "text-incoming",
  negative: "text-outgoing",
  neutral: "text-muted",
  plain: "text-text",
} as const;

const sizes = {
  lg: "text-numeral",
  md: "text-numeral-sm",
  sm: "text-base font-medium",
} as const;

export function Money({
  amount,
  currency,
  signed = false,
  tone = "plain",
  size = "md",
  className,
}: Props) {
  const text = signed
    ? formatSignedMoney(amount, currency)
    : formatMoney(amount, currency);

  return (
    <span
      className={cn("tabular whitespace-nowrap", tones[tone], sizes[size], className)}
    >
      {text}
    </span>
  );
}
