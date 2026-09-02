/**
 * How a transfer between two of your own accounts reads on screen.
 *
 * The API gives each side a structured `transfer` block rather than prose, and
 * the counterparty text on the row itself is machine-shaped
 * (`credit_card *1234`) because it is part of the movement's identity and can
 * never be reworded. So the words live here, and every screen says the same
 * ones.
 *
 * Two shapes arrive, and `external` is the one field that tells them apart —
 * read it rather than null-checking the three `counterpart_*` fields, which
 * are null exactly when it is true:
 *
 * - **A pair.** Both sides are movements here, because one alert named both
 *   instruments. The other side is named by its instrument, and there is a row
 *   to send the reader to.
 * - **A lone leg.** The card was paid from another bank, a wallet or cash, so
 *   only this side exists. Nothing names the other one except what its owner
 *   typed, which is the movement's own `counterparty` — and unlike a pair's,
 *   that text *is* meant to be read.
 */

import type { TransferLeg } from "@/api/queries";

/** What the other side is, in the second person: it is the reader's own. */
const OWN_INSTRUMENT: Record<string, string> = {
  credit_card: "tu tarjeta de crédito",
  debit_card: "tu tarjeta débito",
  savings_account: "tu cuenta de ahorros",
  checking_account: "tu cuenta corriente",
  account: "tu cuenta",
};

/**
 * The other side, named. Null on a lone leg, where there is no instrument to
 * name and the caller has the owner's own words instead.
 */
export function counterpartName(leg: TransferLeg): string | null {
  if (leg.counterpart_instrument_kind === null) return null;

  return OWN_INSTRUMENT[leg.counterpart_instrument_kind] ?? "otra cuenta tuya";
}

/**
 * The line that replaces the merchant on a transfer leg.
 *
 * `source` is the side money left, so it reads as a payment *to* the other
 * one; `destination` is the side it arrived at — on a credit card, its debt
 * going down.
 *
 * `counterparty` is the movement's own text, used only on a lone leg. Passed
 * in rather than read off the leg because the leg does not carry it, and
 * because a pair's copy of that field is machine text nobody should see.
 */
export function transferTitle(leg: TransferLeg, counterparty: string): string {
  const named = counterpartName(leg);
  const other =
    named === null ? counterparty : `${named} ···· ${leg.counterpart_last_four}`;

  return leg.role === "source" ? `Pago a ${other}` : `Pago desde ${other}`;
}

/** One line for why this movement is not an expense. */
export function transferBlurb(leg: TransferLeg): string {
  if (leg.external) {
    return leg.role === "source"
      ? "Salió de esta cuenta para pagar una deuda tuya que no está en Finflow, así que no cuenta como gasto."
      : "Pagaste esta cuenta desde fuera de Finflow, así que no cuenta como ingreso.";
  }

  return leg.role === "source"
    ? "Salió de esta cuenta y llegó a otra tuya, así que no cuenta como gasto."
    : "Llegó desde otra cuenta tuya, así que no cuenta como ingreso.";
}
