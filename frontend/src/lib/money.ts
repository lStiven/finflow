/**
 * Money, as the API hands it over: a decimal string, never a number.
 *
 * `Intl.NumberFormat.format` accepts a string and formats it at full
 * precision, so nothing here ever needs `Number()` or `parseFloat`. That is
 * the whole point — a float loses cents, and this is a ledger.
 *
 * There is deliberately no `add`/`subtract`: the API already returns period
 * totals pre-summed in `GET /financial/summary`, so the client has nothing to
 * add up. If that ever changes, reach for a decimal library rather than
 * teaching this module arithmetic it cannot do safely.
 */

const LOCALE = "es-CO";

/** Currencies whose smallest unit makes decimals noise rather than precision. */
const WHOLE_UNIT_CURRENCIES = new Set(["COP", "CLP", "JPY", "KRW", "PYG", "VND"]);

function fractionDigits(currency: string): number {
  return WHOLE_UNIT_CURRENCIES.has(currency.toUpperCase()) ? 0 : 2;
}

/**
 * The decimal string, narrowed to what `Intl.NumberFormat` accepts.
 *
 * TypeScript types that overload as a template literal (`${number}`), which no
 * plain `string` satisfies, so a cast is unavoidable. It is guarded rather
 * than blind: `format` throws a RangeError on anything non-numeric, and one
 * odd field from the API should not take a whole screen of balances down with
 * it.
 */
function asNumeric(amount: string): Intl.StringNumericLiteral | null {
  const trimmed = amount.trim();
  return /^[+-]?(\d+(\.\d*)?|\.\d+)$/.test(trimmed)
    ? (trimmed as Intl.StringNumericLiteral)
    : null;
}

/**
 * Format, or say what the server said.
 *
 * Both inputs are untrusted in the same way: `amount` and `currency` are
 * plain strings in the OpenAPI contract, and `Intl.NumberFormat` throws a
 * RangeError on a malformed currency code just as it does on a non-numeric
 * amount. Constructing the formatter inside the guard is what makes the
 * promise above true for both — a bad currency degrades to "919500 XYZ"
 * rather than unmounting the dashboard.
 */
function format(
  amount: string,
  currency: string,
  options: Intl.NumberFormatOptions,
): string {
  const numeric = asNumeric(amount);
  if (numeric === null) return currency ? `${amount} ${currency}` : amount;

  const digits = fractionDigits(currency);
  try {
    return new Intl.NumberFormat(LOCALE, {
      style: "currency",
      currency,
      minimumFractionDigits: digits,
      maximumFractionDigits: digits,
      ...options,
    }).format(numeric);
  } catch {
    // A currency code the runtime rejects. The figure is still worth showing,
    // and showing it with the raw code is more honest than hiding it.
    const plain = new Intl.NumberFormat(LOCALE, {
      minimumFractionDigits: digits,
      maximumFractionDigits: digits,
      ...options,
      style: "decimal",
    }).format(numeric);
    return currency ? `${plain} ${currency}` : plain;
  }
}

export function formatMoney(amount: string, currency: string): string {
  return format(amount, currency, {});
}

/** Same, with an explicit `+` on positives — for a net that can go either way. */
export function formatSignedMoney(amount: string, currency: string): string {
  return format(amount, currency, { signDisplay: "exceptZero" });
}

/** Sign of a decimal string, read from the text. No parsing, no rounding. */
export function signOf(amount: string): -1 | 0 | 1 {
  const trimmed = amount.trim();
  if (trimmed.startsWith("-")) return /[1-9]/.test(trimmed) ? -1 : 0;
  return /[1-9]/.test(trimmed) ? 1 : 0;
}

export function isZero(amount: string): boolean {
  return signOf(amount) === 0;
}

export type BalanceReading = {
  /** The figure, formatted. */
  text: string;
  tone: "positive" | "negative" | "neutral";
  /** What the figure means for this account, e.g. "Debes" or "Disponible". */
  label: string;
};

/**
 * How a balance should read for the account holding it.
 *
 * A liability's balance is positive when money is owed — `"158800"` on a
 * credit card means *you owe 158.800*, not that you have it. Showing that
 * with the sign flipped is the fastest way to make a finance app lie, so the
 * decision lives here and only here: the caption travels with the tone
 * because a screen that derives its own label from `category` will sooner or
 * later caption a paid-off card "Debes" beside a zero.
 */
export function describeBalance(
  balance: string,
  currency: string,
  category: "asset" | "liability" | string,
): BalanceReading {
  const text = formatMoney(balance, currency);
  const sign = signOf(balance);

  if (category === "liability") {
    if (sign === 0) return { text, tone: "neutral", label: "Al día" };
    // Negative on a liability means the account is overpaid — real, and worth
    // reading as money in your favour rather than as a mistake.
    return sign > 0
      ? { text, tone: "negative", label: "Debes" }
      : { text, tone: "positive", label: "A favor" };
  }

  if (sign < 0) {
    // An asset can go negative legitimately when no opening balance was
    // declared: the history is incomplete, not wrong.
    return { text, tone: "negative", label: "Sin saldo inicial" };
  }
  return {
    text,
    tone: sign === 0 ? "neutral" : "positive",
    label: "Disponible",
  };
}
