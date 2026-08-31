/**
 * How a transfer between two of your own accounts reads on screen.
 *
 * The API gives each side a structured `transfer` block rather than prose,
 * and the counterparty text on the row itself is machine-shaped
 * (`credit_card *1234`) because it is part of the movement's identity and can
 * never be reworded. So the words live here, and every screen says the same
 * ones.
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

export function counterpartName(leg: TransferLeg): string {
  return OWN_INSTRUMENT[leg.counterpart_instrument_kind] ?? "otra cuenta tuya";
}

/**
 * The line that replaces the merchant on a transfer leg.
 *
 * `source` is the side money left, so it reads as a payment *to* the other
 * one; `destination` is the side it arrived at — on a credit card, its debt
 * going down.
 */
export function transferTitle(leg: TransferLeg): string {
  const other = `${counterpartName(leg)} ···· ${leg.counterpart_last_four}`;

  return leg.role === "source" ? `Pago a ${other}` : `Pago desde ${other}`;
}

/** One line for why this movement is not an expense. */
export function transferBlurb(leg: TransferLeg): string {
  return leg.role === "source"
    ? "Salió de esta cuenta y llegó a otra tuya, así que no cuenta como gasto."
    : "Llegó desde otra cuenta tuya, así que no cuenta como ingreso.";
}
