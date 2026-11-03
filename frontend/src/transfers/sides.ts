/**
 * A transfer typed by hand, as its owner thinks of it: where the money left
 * and where it arrived.
 *
 * The API speaks in a different shape — the side on `account_id`, its `role`,
 * and the other side named either as another account of theirs or as text —
 * and this is the one place that translates. The cases it covers:
 *
 * - **Two accounts.** Both sides are written and both balances move: paying
 *   the card from savings lowers the savings *and* the debt.
 * - **One account and «Otro».** Only that account moves; the other side is
 *   outside Finflow — another bank, Nequi, cash — and is named, if at all, by
 *   what its owner types.
 * - **«Otro» on both sides** moves no balance at all, so it is refused here
 *   rather than sent.
 */

import type { Account } from "@/api/queries";

/** The «Otro» choice: a side Finflow does not hold. */
export const OUTSIDE = "outside";

/** What an unnamed outside side reads as. Part of the movement's identity. */
export const UNNAMED_OUTSIDE = "Otra cuenta";

/** The part of `POST /financial/transactions/transfer` that says which sides. */
export type SidesBody = {
  role: "source" | "destination";
  account_id: string;
  counterpart_account_id?: string;
  counterparty?: string;
};

export type Sides = { ok: true; body: SidesBody } | { ok: false; error: string };

/**
 * `from` and `to` are an account id, `OUTSIDE`, or empty while unchosen.
 * `name` is what the owner called the outside side, and is optional.
 */
export function resolveSides(from: string, to: string, name: string): Sides {
  if (!from || !to) {
    return { ok: false, error: "Elige de dónde salió la plata y a dónde llegó." };
  }
  if (from === OUTSIDE && to === OUTSIDE) {
    return {
      ok: false,
      error:
        "Al menos un lado tiene que ser una cuenta tuya: si no, no se mueve ningún saldo.",
    };
  }
  if (from === to) {
    return { ok: false, error: "Elige dos cuentas distintas." };
  }

  const counterparty = name.trim() || UNNAMED_OUTSIDE;
  const body: SidesBody =
    to === OUTSIDE
      ? { role: "source", account_id: from, counterparty }
      : from === OUTSIDE
        ? { role: "destination", account_id: to, counterparty }
        : { role: "source", account_id: from, counterpart_account_id: to };

  return { ok: true, body };
}

/**
 * Whether both sides are accounts at the same bank — the case where that
 * bank already emails one alert naming both, and Finflow writes the pair on
 * its own. Typed here as well, it would be counted twice.
 */
export function sameBank(from: string, to: string, accounts: Account[]): boolean {
  const bankOf = (id: string) =>
    accounts
      .find((account) => account.id === id)
      ?.bank?.trim()
      .toLowerCase() || null;
  const origin = bankOf(from);

  return origin !== null && origin === bankOf(to);
}
