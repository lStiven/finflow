/**
 * What to send when correcting a movement.
 *
 * `PATCH /financial/transactions/{id}` takes every field as optional and
 * applies only what arrives, so the body has to be the difference — sending
 * the whole form back would rewrite fields nobody touched. Three of its rules
 * are not guessable from the shape of the payload, and each one has already
 * produced a bug:
 *
 * - **Amount and currency travel together.** The endpoint refuses an amount
 *   without its currency (a figure whose unit is in doubt is exactly the
 *   correction not to accept), and a currency on its own does not count as a
 *   change at all. Either alone is a 422.
 * - **The empty string clears a note; `null` means "leave it".** Sending
 *   `null` for a field somebody emptied quietly keeps the old text.
 * - **`account_id` and `detach` contradict each other** and are refused
 *   together.
 */

import type { Transaction } from "@/api/queries";

/** The account picker's value for "take this off its account". */
export const DETACH = "__detach__";

export type CorrectionForm = {
  amount: string;
  counterparty: string;
  currency: string;
  /** The `datetime-local` string, compared as rendered — see `occurredAt` below. */
  occurredAt: string;
  /** An account id, `DETACH`, or "" for none. */
  account: string;
  note: string;
};

export type Correction = {
  amount?: string;
  counterparty?: string;
  currency?: "COP" | "USD";
  occurred_at?: number;
  account_id?: string | null;
  note?: string;
  detach: boolean;
};

export function buildCorrection(
  movement: Transaction,
  form: CorrectionForm,
  /**
   * The movement's own timestamp rendered the way the field renders it. The
   * input holds minutes, so a movement recorded at 12:30:45 reads back as
   * 12:30 — comparing instants would send a 45-second "correction" every
   * time the form was opened to change something else, permanently splitting
   * the movement from what the bank said.
   */
  renderedOccurredAt: string,
  /** The form's timestamp as an instant, already parsed. */
  occurredAt: number,
): Correction {
  const body: Correction = { detach: false };

  if (form.counterparty !== movement.counterparty) {
    body.counterparty = form.counterparty.trim();
  }

  if (form.amount !== movement.amount || form.currency !== movement.currency) {
    body.amount = form.amount;
    body.currency = form.currency as "COP" | "USD";
  }

  if (form.occurredAt !== renderedOccurredAt) body.occurred_at = occurredAt;

  if (form.note.trim() !== (movement.note ?? "")) body.note = form.note.trim();

  const held = movement.account_id ?? "";
  if (form.account !== held) {
    if (form.account === DETACH) body.detach = true;
    else body.account_id = form.account || null;
  }

  return body;
}

/** True when the body would change nothing — `detach: false` is the only key. */
export function isEmpty(body: Correction): boolean {
  return Object.keys(body).length === 1 && !body.detach;
}
