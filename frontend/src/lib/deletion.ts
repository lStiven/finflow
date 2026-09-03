/**
 * What actually happens when a movement is deleted, said out loud.
 *
 * Deleting is the one action here whose consequence is not guessable from the
 * button, and it differs by *what kind of movement it is* — four independent
 * facts decide it, and getting any of them wrong on screen would be worse than
 * saying nothing:
 *
 * - **Whether it sits on an account.** Nothing does with no accounts declared,
 *   which is a complete way to use Finflow — and then no balance moves at all.
 * - **What kind of account.** On a savings account an erased expense is money
 *   back; on a credit card an erased purchase is *debt going down*, because
 *   spending on a card raises what it holds. Reading the direction alone gets
 *   this backwards half the time.
 * - **Whether it is a transfer.** A transfer never counted as spending or
 *   income, so erasing it changes no monthly total — while erasing an ordinary
 *   movement changes exactly that.
 * - **Whether its other half is here.** A pair is two rows stating one
 *   movement of money and the API removes both; a leg paid from outside this
 *   app has no second row and goes alone.
 *
 * The words live here rather than in the screen so they can be tested, and so
 * the confirmation and any future list action cannot drift apart.
 */

import type { Account, Transaction, TransferLeg } from "@/api/queries";
import { formatMoney } from "@/lib/money";

export type DeletionConsequence = {
  /** How many rows leave the ledger. Two only when a transfer's pair is here. */
  rows: 1 | 2;
  /** What happens to the balance this movement sits on, or that none does. */
  balance: string;
  /** What happens to the month's totals. */
  totals: string;
  /** Where the other half goes. Null on everything that is not a transfer. */
  transfer: string | null;
  /** What the button that carries it out should say. */
  confirm: string;
  /** What to do instead, when the movement is real and only wrong. */
  instead: string;
};

/**
 * `account` is the one this movement sits on, when the caller has it. Undefined
 * covers two different things and the copy has to survive both: the movement is
 * on no account, and the account exists but was not loaded — so the amount and
 * the direction are never claimed without the category that gives them meaning.
 */
export function describeDeletion(
  movement: Transaction,
  account: Account | undefined,
): DeletionConsequence {
  // Nullish, not just null: the field carries a default on the server, so the
  // generated type has it optional as well as nullable.
  const leg = movement.transfer ?? null;
  const paired = leg !== null && leg.external === false;

  return {
    rows: paired ? 2 : 1,
    balance: balanceEffect(movement, account),
    totals:
      leg === null
        ? `Deja de contar como ${movement.direction === "incoming" ? "ingreso" : "gasto"} en tus totales.`
        : "Un traslado no cuenta como gasto ni como ingreso, así que tus totales del mes no cambian.",
    transfer: leg === null ? null : otherHalf(paired),
    confirm: paired ? "Sí, eliminar las dos mitades" : "Sí, eliminarlo",
    instead: instead(movement, leg),
  };
}

/**
 * The alternative to offer, which is not the same for every movement.
 *
 * Correcting is always available. *Taking it off its account* is not: a
 * movement on no account has none to leave, and a leg paid from outside this
 * app is refused with a 409 — it states that a balance moved and names no
 * instrument, so nothing could ever adopt it back. Offering it there would
 * send somebody to a control the edit form does not even show.
 */
function instead(movement: Transaction, leg: TransferLeg | null): string {
  const base =
    "Esto no se puede deshacer. Si el movimiento sí ocurrió y solo está mal, usa Corregir";

  return movement.account_id !== null && leg?.external !== true
    ? `${base}, que también permite sacarlo de la cuenta sin borrarlo.`
    : `${base}.`;
}

function balanceEffect(movement: Transaction, account: Account | undefined): string {
  if (movement.account_id === null) {
    return "No está en ninguna cuenta, así que no se mueve ningún saldo.";
  }

  const money = formatMoney(movement.amount, movement.currency);

  if (account === undefined) {
    // The account is real — the movement names it — but this screen does not
    // have it, so the figure is stated without claiming which way it goes.
    return `Se ajusta el saldo de su cuenta en ${money}.`;
  }

  /*
   * A liability's balance is what is owed, so an outgoing movement *raises*
   * it. Erasing one therefore lowers the debt, and erasing a payment to the
   * card puts the debt back. This is the pair of sentences that reading
   * `direction` alone gets backwards.
   */
  if (account.category === "liability") {
    return movement.direction === "outgoing"
      ? `La deuda de ${account.name} baja ${money}.`
      : `La deuda de ${account.name} vuelve a subir ${money}, porque este pago deja de existir.`;
  }

  return movement.direction === "outgoing"
    ? `Vuelven ${money} a ${account.name}.`
    : `Se le restan ${money} a ${account.name}.`;
}

function otherHalf(paired: boolean): string {
  return paired
    ? "Se borra también la otra mitad del traslado: las dos dicen el mismo movimiento, y dejar una sola sería un pago apuntando a algo que ya no existe. Cada lado le devuelve a su cuenta lo que movió."
    : "La otra mitad no está en Finflow, así que solo se borra este lado.";
}
