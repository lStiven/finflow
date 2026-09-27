/**
 * The words for declaring, after the fact, that a movement was a transfer.
 *
 * The case it exists for: Bancolombia emails "Pagaste $3.625.733 a BANCO
 * COMERCIAL AV VILLAS", the card's own bank emails nothing Finflow can read,
 * and the payment sits there counted as spending while the card still shows
 * the debt. Only its owner knows that bank holds their card.
 *
 * Kept apart from the screen so the sentences that promise what a balance
 * will do can be tested — a promise about money that is wrong on screen is
 * worse than no promise.
 */

import type { Account, Transaction } from "@/api/queries";
import { formatMoney } from "@/lib/money";

/** Why a movement cannot be declared, as the API codes it. */
export function refusalMessage(code: string): string {
  switch (code) {
    case "already_transfer":
      return "Este movimiento ya es un traslado.";
    case "self_written":
      return "Finflow escribió este movimiento a partir de una factura o de un crédito, así que no puede ser un traslado.";
    case "unplaceable":
      return "Este movimiento no está en ninguna cuenta; asígnalo primero a la cuenta de donde salió o a la que llegó.";
    case "linked_to_bill":
      return "Este movimiento está vinculado al cobro de una factura. Desvincúlalo en Facturas si en realidad fue un traslado.";
    default:
      return "Este movimiento no se puede marcar como traslado.";
  }
}

/**
 * What writing the other side on `account` does to it, in one sentence.
 *
 * An outgoing movement's other side *arrives*: on a card that is its debt
 * falling, on a savings account its balance rising. An incoming movement's
 * other side left from somewhere. Direction alone gets a liability backwards,
 * which is why the category is read here and not guessed.
 */
export function writtenSideEffect(movement: Transaction, account: Account): string {
  const money = formatMoney(movement.amount, movement.currency);
  const arrives = movement.direction === "outgoing";

  if (account.category === "liability") {
    return arrives
      ? `La deuda de ${account.name} baja ${money}.`
      : `La deuda de ${account.name} sube ${money}.`;
  }

  return arrives
    ? `${account.name} sube ${money}.`
    : `A ${account.name} se le restan ${money}.`;
}

/** What stops happening to this movement once it is a transfer. */
export function stopsCounting(movement: Transaction): string {
  return movement.direction === "outgoing"
    ? "Deja de contar como gasto del mes."
    : "Deja de contar como ingreso del mes.";
}

/**
 * What undoing a declared transfer does, before it is done.
 *
 * `writtenAccount` is where Finflow wrote the other side, null when it wrote
 * none, and `undefined` when the other side could not be read — then the
 * sentence covers both cases rather than promising no balance moves.
 */
export function undoConsequence(
  movement: Transaction,
  writtenAccount: string | null | undefined,
): string {
  const counts =
    movement.direction === "outgoing"
      ? "vuelve a contar como gasto"
      : "vuelve a contar como ingreso";

  if (movement.transfer?.basis === "counterpart") {
    return `Se borra este movimiento, que Finflow escribió al marcar el traslado, y su saldo vuelve a como estaba. El otro lado ${
      movement.direction === "outgoing"
        ? "vuelve a contar como ingreso"
        : "vuelve a contar como gasto"
    }.`;
  }

  if (writtenAccount === undefined && movement.transfer?.external === false) {
    return `Este movimiento ${counts}. Si Finflow escribió la otra mitad, se borra y el saldo de esa cuenta vuelve a como estaba; si era un movimiento de tu banco, vuelve a ser lo que era.`;
  }

  if (writtenAccount) {
    return `Este movimiento ${counts}, y se borra el lado que Finflow escribió en ${writtenAccount}: su saldo vuelve a como estaba.`;
  }

  return movement.transfer?.external === false
    ? `Este movimiento ${counts} y el otro también vuelve a ser lo que era. Ningún saldo cambia.`
    : `Este movimiento ${counts}. Ningún saldo cambia.`;
}
