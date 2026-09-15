/**
 * What the suggestions section shows, and what it refuses to show.
 *
 * Same split as `schedule.ts`: how the cards look is checked by looking at
 * them, and the rules that have a right answer live here. Three of them are
 * worth pinning, because getting any one wrong turns a helpful list into a
 * list nobody reads:
 *
 * 1. **Already declared is marked, never re-offered as new.** A suggestion to
 *    declare what is already declared is what teaches somebody to ignore the
 *    whole section.
 * 2. **Income is detected but not shown here.** A salary is a real recurring
 *    series — the server finds it and the next deliverable wants it — but
 *    this section is about committing to charges, and "declarar" beside money
 *    somebody is waiting *for* asks them to do the wrong thing.
 * 3. **Accepting is declaring, with the detector's figures filled in.** The
 *    body built here is the same body the form sends, which is what keeps a
 *    guess from ever being a different kind of thing from a statement.
 */

import type { DeclareBillBody, RecurringSeries } from "@/api/queries";
import { whenLabel } from "@/bills/schedule";
import { UNCATEGORIZED } from "@/merchants/categories";

/**
 * The series worth putting in front of somebody, surest first.
 *
 * Outgoing only. The server answers with income too, marked with its
 * direction, and dropping it here rather than there is deliberate: E3 needs
 * the salary and this screen does not.
 *
 * The already-declared ones stay, at the bottom. They are the evidence that
 * the list knows what it is looking at — a section that silently hid them
 * would look like it had simply missed them.
 */
export function suggestions(series: readonly RecurringSeries[]): RecurringSeries[] {
  return series
    .filter((found) => found.direction === "outgoing")
    .sort((a, b) => {
      const declared = Number(a.bill_id !== null) - Number(b.bill_id !== null);

      return declared !== 0 ? declared : Number(b.confidence) - Number(a.confidence);
    });
}

/** Whether anything in this list is still worth acting on. */
export function hasProposals(series: readonly RecurringSeries[]): boolean {
  return suggestions(series).some((found) => found.bill_id === null);
}

export type Certainty = "high" | "medium" | "low";

/**
 * How sure the detector is, in a word rather than a percentage.
 *
 * "87 %" invites arithmetic nobody can check: the number is a weighted
 * judgement over how complete and how punctual a series is, and showing two
 * decimals of it would claim a precision it does not have. Three bands is
 * what a reader actually does with it — act, glance, ignore.
 */
export function certaintyOf(series: RecurringSeries): Certainty {
  const confidence = Number(series.confidence);

  if (confidence >= 0.85) return "high";

  return confidence >= 0.6 ? "medium" : "low";
}

/**
 * The evidence behind a suggestion, in one line.
 *
 * What makes the difference between a guess somebody trusts and one they
 * dismiss: "4 cobros, ninguno faltó" is checkable against their own bank, and
 * a percentage is not.
 */
export function evidenceLabel(series: RecurringSeries): string {
  const charges = `${series.sightings} ${series.sightings === 1 ? "cobro" : "cobros"}`;

  if (series.missed === 0) return `${charges}, ninguno faltó`;
  if (series.missed === 1) return `${charges}, faltó uno`;

  return `${charges}, faltaron ${series.missed}`;
}

/**
 * When the next charge of this series is due, in words that stay true.
 *
 * "Próximo 9 de sept, hace 6 días" is the sentence this exists to prevent.
 * A detected charge can be a few days past its day and still be a live
 * series — the grace is a fifth of the cadence — so the label has to be able
 * to say the charge was *expected* and has not shown up, which is a different
 * fact from the next one being on its way.
 */
export function nextChargeLabel(
  series: RecurringSeries,
  day: string,
  today: string,
): string {
  const when = whenLabel(series.next_due_on, today);

  return series.next_due_on < today
    ? `se esperaba el ${day}, ${when}`
    : `próximo ${day}, ${when}`;
}

/**
 * A suggestion as the body that declares it.
 *
 * `starts_on` is the next expected charge, which becomes the bill's anchor —
 * so the day every later charge lands on is the day this one has been landing
 * on, and not the day somebody happened to accept the suggestion.
 *
 * The category is carried over when there is one, because a bill without one
 * produces charges that sit outside every breakdown. `uncategorized` is not
 * one: it is the absence of an answer, and sending it would file the charge
 * under a category the picker itself hides.
 */
export function asDeclaration(series: RecurringSeries): DeclareBillBody {
  return {
    name: series.name,
    amount: series.amount,
    currency: series.currency,
    cadence: series.cadence,
    starts_on: series.next_due_on,
    direction: series.direction,
    account_id: series.account_id,
    category:
      series.category === null || series.category === UNCATEGORIZED
        ? null
        : series.category,
  };
}
