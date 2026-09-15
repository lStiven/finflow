/**
 * The rules behind the one number a screen must not get wrong.
 *
 * Same split as `bills/schedule.ts`: how the card looks is checked by looking
 * at it, and what has a right answer lives here. On this card that is more
 * than usual, because the figure is the most dangerous one in the app — it is
 * the only place where four separate answers are put together, and **the
 * server has already done the subtraction**. Nothing here recomputes
 * `available`: a second implementation in the browser would be a second answer
 * to «cuánto me queda», drifting from the first the day either was fixed.
 *
 * What is here is what the *card* has to decide: how to spell the number out,
 * what to say when it runs out, and — the only real arithmetic — turning a
 * detected salary into a figure a form can prefill.
 */

import type { Allowance, BillCadence, RecurringSeries } from "@/api/queries";
import { parseAmount } from "@/bills/schedule";

/**
 * What somebody typed into the «quieres guardar» field, as the API takes it.
 *
 * Its own parser because zero is a **real answer** here and nowhere else: the
 * shared one refuses it on purpose — a bill for nothing is a mistake — and
 * most people are not setting anything aside, so «0» has to be accepted as
 * readily as an empty field. Anything that is not a number is still refused.
 */
export function parseKept(raw: string): string | null {
  const trimmed = raw.trim();

  if (trimmed === "") return "0";

  const parsed = parseAmount(trimmed);

  if (parsed !== null) return parsed;

  // The one thing `parseAmount` refuses that this field allows.
  return /^[0.,\s]+$/.test(trimmed) ? "0" : null;
}

/** What the card is saying, in one word. */
export type AllowanceTone = "healthy" | "tight" | "over";

/**
 * How the figure should read.
 *
 * Three states and not two, because «te queda poco» and «te pasaste» are
 * different situations and only one of them is still a budget. The boundary
 * is a tenth of what the month had to live on: below that, what is left is
 * closer to noise than to a plan.
 */
export function toneOf(allowance: Allowance): AllowanceTone {
  const available = Number(allowance.available);

  if (available < 0) return "over";

  const spendable =
    Number(allowance.expected_income) - Number(allowance.savings_target);

  return spendable > 0 && available / spendable < 0.1 ? "tight" : "healthy";
}

/**
 * What is left per day, or null when that is not a thing worth saying.
 *
 * Null rather than zero in the two cases where the division answers nothing:
 * a month already over, and money already spent. Telling somebody they can
 * spend «-$40.000 por día» is arithmetic, not an answer.
 *
 * Rounded down to the peso. Rounding up would hand back a figure that, spent
 * every day, ends the month over — which is the one direction this number
 * must never be wrong in.
 */
export function perDay(allowance: Allowance): string | null {
  const available = Number(allowance.available);

  if (!Number.isFinite(available) || available <= 0) return null;
  if (allowance.days_left <= 0) return null;

  return String(Math.floor(available / allowance.days_left));
}

/** One line of the subtraction, as the card shows it. */
export type BreakdownRow = {
  label: string;
  amount: string;
  /** Whether this line is taken away from the one above it. */
  subtracted: boolean;
};

/**
 * The figure taken apart, in the order it is built.
 *
 * The whole point of the card: a number somebody cannot check is a number
 * they stop believing the first time it disagrees with their own arithmetic.
 * The lines that are zero are dropped — «$0 guardados» is a row that pushes
 * the ones that matter off a phone.
 */
export function breakdownOf(allowance: Allowance): BreakdownRow[] {
  const rows: BreakdownRow[] = [
    {
      label: "Esperas que entren",
      amount: allowance.expected_income,
      subtracted: false,
    },
  ];

  if (Number(allowance.savings_target) > 0) {
    rows.push({
      label: "Quieres guardar",
      amount: allowance.savings_target,
      subtracted: true,
    });
  }

  if (Number(allowance.spent) > 0) {
    rows.push({ label: "Ya gastaste", amount: allowance.spent, subtracted: true });
  }

  if (Number(allowance.committed) > 0) {
    rows.push({
      label: "Facturas sin pagar",
      amount: allowance.committed,
      subtracted: true,
    });
  }

  return rows;
}

/**
 * How much of the month's money is already accounted for, from 0 to 1.
 *
 * What the bar draws. Spent *and* committed, because a charge you have not
 * paid yet is money that is not yours to spend twice — the bar is about what
 * is left to decide, not about what has left the account.
 */
export function usedShare(allowance: Allowance): number {
  const spendable =
    Number(allowance.expected_income) - Number(allowance.savings_target);

  if (!Number.isFinite(spendable) || spendable <= 0) return 1;

  const used = Number(allowance.spent) + Number(allowance.committed);

  return Math.min(1, Math.max(0, used / spendable));
}

/**
 * How many charges of this cadence land in an average month.
 *
 * The one piece of real arithmetic on this screen, and it only ever feeds a
 * **suggestion in a form** — never a stored figure and never the allowance
 * itself. A fortnightly salary is not «la mitad de un mensual»: it lands
 * twenty-six times a year, so 2.17 months' worth of it, and a form that
 * prefilled 2 would understate the year by a fortnight's pay.
 *
 * Whatever it fills in stays editable, which is what makes an approximation
 * acceptable here and unacceptable anywhere the number is read as an answer.
 */
const PER_MONTH: Record<BillCadence, number> = {
  weekly: 52 / 12,
  biweekly: 26 / 12,
  monthly: 1,
  bimonthly: 1 / 2,
  quarterly: 1 / 3,
  annual: 1 / 12,
};

/**
 * What a detected incoming series is worth in a month, as a whole figure.
 *
 * The link the plan predicted: a salary is a recurring series like any other,
 * so the detector already knows it and the form can offer it instead of
 * asking somebody to add up their own payslips.
 */
export function monthlyEquivalent(series: RecurringSeries): string {
  return String(Math.round(Number(series.amount) * PER_MONTH[series.cadence]));
}

/**
 * The detected income worth offering as a starting figure, surest first.
 *
 * Outgoing series are not income, and neither is a series in another currency
 * than the one being declared — adding those would need a rate this app does
 * not have.
 */
export function incomeSuggestions(
  series: readonly RecurringSeries[],
  currency: string,
): RecurringSeries[] {
  return series
    .filter((found) => found.direction === "incoming" && found.currency === currency)
    .sort((a, b) => Number(b.confidence) - Number(a.confidence));
}
