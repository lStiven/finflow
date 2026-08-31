/**
 * What the small forms on an account card will let through.
 *
 * Same arrangement as `@/accounts/draft`, for the same reason: every rule here
 * is one the backend also enforces, said in Spanish before the round trip. The
 * difference is the sign — a *restated* balance may be negative, because an
 * overdrawn account and an overpaid card are both real, while a credit limit
 * is what may be owed and can only be positive.
 *
 * Money stays a string throughout. Nothing here parses an amount as a number.
 */

import { MAX_NAME_LENGTH } from "@/accounts/draft";

/** A decimal, optionally negative, written the way a person writes one. */
const SIGNED_AMOUNT = /^-?\d+(\.\d+)?$/;
const AMOUNT = /^\d+(\.\d+)?$/;

const DIGITS_ONLY = "Escribe solo números, sin puntos de miles.";

/** What is wrong with a restated balance, or nothing. Empty is not valid. */
export function balanceIssue(value: string): string | undefined {
  const trimmed = value.trim();
  if (trimmed === "") return "Escribe el saldo que muestra tu banco hoy.";
  if (!SIGNED_AMOUNT.test(trimmed)) return DIGITS_ONLY;
  return undefined;
}

/** Same for a credit limit, where empty is valid: it clears the limit. */
export function creditLimitIssue(value: string): string | undefined {
  const trimmed = value.trim();
  if (trimmed === "") return undefined;
  if (!AMOUNT.test(trimmed)) return `${DIGITS_ONLY} El cupo nunca es negativo.`;
  return undefined;
}

export function nameIssue(value: string): string | undefined {
  const trimmed = value.trim();
  if (trimmed === "") return "Ponle un nombre para reconocerla.";
  if (trimmed.length > MAX_NAME_LENGTH) {
    return `El nombre no puede pasar de ${MAX_NAME_LENGTH} caracteres.`;
  }
  return undefined;
}
