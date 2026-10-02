/**
 * The rules the budgets screen and the dashboard card both have to get right.
 *
 * Same split as `plan/allowance.ts` and `bills/schedule.ts`: how a card looks
 * is checked by looking at it, and what has a right answer lives here under
 * vitest.
 *
 * **Nothing here decides whether a cap has been passed.** `state` comes from
 * the server already decided, because two implementations of «¿me pasé?» would
 * be two answers the day either one moved — and they would disagree on the
 * same category on the same screen. What is here is geometry (how wide the bar
 * is drawn) and copy (what the row says), neither of which the server owes a
 * client.
 */

import type {
  BudgetProgress,
  BudgetScope,
  BudgetState,
  BudgetTotal,
} from "@/api/queries";

/**
 * How much of a cap is used, from 0 to 1.
 *
 * What the bar draws, and geometry rather than a figure: it goes through
 * `Number` the way every chart value in this app does, while the amounts
 * beside it stay the API's decimal strings. Clamped at 1 because a bar cannot
 * be more than full — how far past the ceiling somebody went is what
 * `remaining` says in words, and a bar that overflowed its track would say it
 * worse.
 */
export function usedShare(budget: BudgetProgress): number {
  const limit = Number(budget.limit);

  if (!Number.isFinite(limit) || limit <= 0) return 1;

  return Math.min(1, Math.max(0, Number(budget.spent) / limit));
}

/**
 * Where the amber mark sits on the track, from 0 to 1.
 *
 * Drawn as a notch so the bar says what it is measuring against rather than
 * just filling up: the point somebody chose is part of the answer, and a bar
 * that turns amber with no visible reason reads as the app deciding for them.
 */
export function warningMark(budget: BudgetProgress): number {
  return Math.min(1, Math.max(0, budget.warn_at / 100));
}

/** What is left of a cap, or null once there is nothing left to say. */
export function leftOf(budget: BudgetProgress): string | null {
  return Number(budget.remaining) > 0 ? budget.remaining : null;
}

/** How far past the ceiling, or null while it has not been passed. */
export function overBy(budget: BudgetProgress): string | null {
  const remaining = Number(budget.remaining);

  return remaining < 0 ? String(-remaining) : null;
}

/**
 * What the row says about itself, in one short line.
 *
 * The plan this came from is explicit that the traffic light **informs, it
 * does not scold** — two thirds of the people who abandon one of these apps
 * say it made them feel bad — so «te pasaste» states the fact and stops. No
 * exclamation, no advice, and nothing red where there is nothing to do about
 * it.
 */
export function captionOf(budget: BudgetProgress): string {
  if (budget.state === "over") return "Te pasaste del tope";
  if (budget.state === "warning") return `Vas por el ${percentUsed(budget)}%`;

  return `Te queda el ${100 - percentUsed(budget)}%`;
}

/** The share used as a whole number, for copy rather than for geometry. */
export function percentUsed(budget: BudgetProgress): number {
  return Math.round(usedShare(budget) * 100);
}

/**
 * How many of a currency's caps are still green, and out of how many.
 *
 * What the dashboard card says out loud. The counts come from the server so
 * this cannot disagree with the screen it links to — all this does is pick
 * which currency leads when somebody has caps in two, which is the same rule
 * the rest of the dashboard follows: the first one, and say there are others
 * rather than adding them together.
 */
export function tallyOf(total: BudgetTotal): { ok: number; total: number } {
  return { ok: total.ok, total: total.ok + total.warning + total.over };
}

/**
 * The worst thing happening in a currency's caps, for the dashboard card.
 *
 * `over` beats `warning` beats `ok`: a card that reported the majority state
 * would say «todo bien» while one category was a hundred thousand pesos past
 * its ceiling, which is the one thing that card exists to surface.
 */
export function worstOf(total: BudgetTotal): BudgetState {
  if (total.over > 0) return "over";
  if (total.warning > 0) return "warning";

  return "ok";
}

/**
 * Whether a set of caps is worth a card at all.
 *
 * Nothing declared means no card, not a card saying zero — the same rule the
 * allowance follows, and for the same reason: «no has puesto ningún tope» and
 * «no te queda nada» are different things and only one is somebody's
 * situation.
 */
export function hasCaps(totals: readonly BudgetTotal[]): boolean {
  return totals.length > 0;
}

/** What a month key looks like on screen, in Spanish. */
export function monthLabel(month: string, locale = "es-CO"): string {
  const [year, index] = month.split("-").map(Number);

  if (!year || !index) return month;

  return new Date(Date.UTC(year, index - 1, 1)).toLocaleDateString(locale, {
    month: "long",
    year: "numeric",
    timeZone: "UTC",
  });
}

/**
 * The month key `steps` months away from this one.
 *
 * Built from the two integers rather than from a `Date`, because a `Date`
 * stepped by a month from the 31st lands in the month after next — and a month
 * bar that skips October is a month bar somebody cannot get back to.
 */
export function shiftMonth(month: string, steps: number): string {
  const [year, index] = month.split("-").map(Number);

  if (!year || !index) return month;

  const zeroBased = year * 12 + (index - 1) + steps;

  return `${String(Math.floor(zeroBased / 12)).padStart(4, "0")}-${String(
    (zeroBased % 12) + 1,
  ).padStart(2, "0")}`;
}

/**
 * What a budget watches, in the words the card shows under its name.
 *
 * Three cases, and the first is the one that matters: an empty scope is «todo
 * el mes», never an empty string. A card whose subtitle is blank reads as a
 * budget that watches nothing, which is the opposite of what it means.
 *
 * `labels` maps a category value to its name, because half of somebody's
 * vocabulary is whatever they wrote and only that list knows it. A value with
 * no label falls back to itself rather than disappearing: a category the
 * server still reports and this map has not heard of is a real budget, and a
 * gap in the subtitle would hide it.
 */
export function scopeLabel(scope: BudgetScope, labels: Record<string, string>): string {
  const what = scope.total
    ? "Todo el mes"
    : scope.categories.map((value) => labels[value] ?? value).join(" · ");

  // A budget over every category *in one account* is not «todo el mes», and
  // saying so beside a bar that measures one card is the kind of subtitle that
  // makes somebody stop believing the bar.
  if (!isNarrowed(scope)) return what;

  const many =
    scope.accounts.length === 1 ? "una cuenta" : `${scope.accounts.length} cuentas`;

  return `${what} · ${many}`;
}

/**
 * Whether a budget is narrowed to particular accounts.
 *
 * Its own helper rather than a check inlined in the card, because the card
 * draws a badge for it and the budgets screen draws a line: two spellings of
 * «solo estas cuentas» is how the two end up disagreeing.
 */
export function isNarrowed(scope: BudgetScope): boolean {
  return !scope.every_account;
}
