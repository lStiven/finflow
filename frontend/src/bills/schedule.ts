/**
 * The rules the bills screen cannot be trusted to get right inline.
 *
 * Same split as `alerts/channels.ts`: what the screen *looks* like is checked
 * by looking at it, and what has an answer worth pinning lives here. That is
 * mostly the two totals — which are two and not one on purpose, and which a
 * screen showing the wrong one would turn into a number nobody can trust.
 *
 * Nothing here computes a date. The calendar is the server's, and a second
 * implementation in the browser would be a second answer to "when is the
 * next charge", drifting from the first the moment one of them was fixed.
 */

import type { Bill, BillCadence, BillOccurrence, BillTotal } from "@/api/queries";

/** What each cadence is called where somebody reads it. */
const CADENCE_LABEL: Record<BillCadence, string> = {
  weekly: "Cada semana",
  biweekly: "Cada dos semanas",
  monthly: "Cada mes",
  bimonthly: "Cada dos meses",
  quarterly: "Cada tres meses",
  annual: "Cada año",
};

export function cadenceLabel(cadence: BillCadence): string {
  return CADENCE_LABEL[cadence];
}

/** Every cadence, in the order a picker should offer them. */
export const CADENCES: readonly BillCadence[] = [
  "monthly",
  "biweekly",
  "weekly",
  "bimonthly",
  "quarterly",
  "annual",
];

export type BillState = "paused" | "frozen" | "overdue" | "active";

/**
 * What the card should be saying about this bill, in one word.
 *
 * The order is the whole rule and it is not alphabetical. A paused bill
 * outranks everything: it is predicting nothing, so "overdue" about it would
 * be a charge nobody is expecting. Frozen outranks overdue for the same
 * reason — a bill whose account is closed is not late, it is stopped.
 */
export function billState(bill: Bill): BillState {
  if (bill.status === "paused") return "paused";
  if (bill.frozen) return "frozen";
  if (bill.next_occurrence?.state === "overdue") return "overdue";
  return "active";
}

/**
 * Whether this bill takes part in the totals on screen.
 *
 * Income is declared — a salary is a real recurring series and the next
 * deliverable wants it — but it is never netted off what the month costs. A
 * total that subtracted it would report a month costing less than it costs.
 */
export function countsTowardsSpending(bill: Bill): boolean {
  return bill.direction === "outgoing" && bill.status !== "paused";
}

/**
 * The one currency a header can show, when there is exactly one.
 *
 * With two or more the header has to show them apart, because adding pesos
 * to dollars needs a rate this app does not have. Returning null is how the
 * screen finds out it has to list rather than headline.
 */
export function singleTotal(totals: readonly BillTotal[]): BillTotal | null {
  return totals.length === 1 ? (totals[0] ?? null) : null;
}

/** The charges of one day, for a list that groups by date. */
export type DayGroup = {
  day: string;
  occurrences: BillOccurrence[];
};

/**
 * Occurrences grouped by the day they fall on, keeping the server's order.
 *
 * The server already sorts by date across every bill, so this only has to
 * preserve that — sorting again here would be a second opinion about order,
 * and the two would disagree the day one of them learned about ties.
 */
export function groupByDay(occurrences: readonly BillOccurrence[]): DayGroup[] {
  const groups: DayGroup[] = [];

  for (const occurrence of occurrences) {
    const last = groups.at(-1);

    if (last && last.day === occurrence.due_on) {
      last.occurrences.push(occurrence);
    } else {
      groups.push({ day: occurrence.due_on, occurrences: [occurrence] });
    }
  }

  return groups;
}

/**
 * An amount as somebody typed it, turned into what the API takes.
 *
 * Lifted from the alerts card rather than shared with it: that one parses a
 * floor, which may legitimately be empty, and this one parses a price, which
 * may not. Folding them together would mean one of the two callers reading
 * "" as a real answer.
 */
export function parseAmount(raw: string): string | null {
  const trimmed = raw.trim();
  if (trimmed === "" || !/^[\d.,\s]*$/.test(trimmed)) return null;

  const decimal = trimmed.match(/,(\d{1,2})$/);
  const whole = (decimal ? trimmed.slice(0, -decimal[0].length) : trimmed).replace(
    /[.,\s]/g,
    "",
  );

  if (whole === "") return null;

  const amount = decimal ? `${whole}.${decimal[1]}` : whole;
  if (!/^\d+(\.\d+)?$/.test(amount)) return null;

  // A bill for nothing is refused by the server too; refusing it here is what
  // keeps the form from asking twice.
  return Number(amount) > 0 ? amount : null;
}

/** The stored amount, back in the shape a field shows. */
export function formatAmountInput(amount: string): string {
  const dot = amount.indexOf(".");
  const whole = dot === -1 ? amount : amount.slice(0, dot);
  const cents = dot === -1 ? "" : amount.slice(dot + 1);
  const grouped = whole.replace(/\B(?=(\d{3})+(?!\d))/g, ".");

  return cents !== "" && Number(cents) > 0 ? `${grouped},${cents}` : grouped;
}

/**
 * How far away a charge is, in words somebody reads without doing arithmetic.
 *
 * "El 4 de octubre" is a date; "en 3 días" is an answer. The card shows both
 * because they do different work: the first is what you check against your
 * bank, the second is what tells you whether to care today.
 *
 * Both dates are plain `YYYY-MM-DD` — calendar days, never instants. Building
 * a `Date` from one and reading it back in another zone is how a charge due on
 * the 1st starts showing up on the 31st.
 */
export function daysUntil(due: string, today: string): number {
  return Math.round(
    (Date.parse(`${due}T00:00:00Z`) - Date.parse(`${today}T00:00:00Z`)) / 86_400_000,
  );
}

export function whenLabel(due: string, today: string): string {
  const days = daysUntil(due, today);

  if (days === 0) return "hoy";
  if (days === 1) return "mañana";
  if (days === -1) return "ayer";
  if (days > 1) return `en ${days} días`;

  return `hace ${Math.abs(days)} días`;
}

/**
 * How much of the window has already fallen due, from 0 to 1.
 *
 * What the bar under the total draws. Returns 0 rather than dividing by zero
 * when nothing is expected, because a month with no bills has no proportion
 * to show and an empty bar says that correctly.
 */
export function dueShare(expected: string, upcoming: string): number {
  const total = Number(expected);
  if (!Number.isFinite(total) || total <= 0) return 0;

  const left = Number(upcoming);
  const share = (total - (Number.isFinite(left) ? left : 0)) / total;

  return Math.min(1, Math.max(0, share));
}

/** The letter a bill is recognised by when there is no icon for it. */
export function initialOf(name: string): string {
  return [...name.trim()][0]?.toUpperCase() ?? "·";
}
