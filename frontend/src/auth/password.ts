/**
 * What the password forms will let through, said in Spanish before the round
 * trip.
 *
 * Same arrangement as `@/accounts/edits`: every rule here is one the backend
 * also enforces, so this only saves a request and a worse error message — it
 * is never the only thing standing between a bad value and storage.
 *
 * One rule is *not* the backend's: the confirmation box. The API takes a
 * single `new_password` and could not check a second one if it wanted to.
 * It exists because a password nobody can read back is the one field where a
 * typo is invisible until it locks somebody out of their own account.
 */

/** The backend's only rule, and deliberately its only one. */
export const MIN_PASSWORD_LENGTH = 8;

export const LENGTH_MESSAGE = `Mínimo ${MIN_PASSWORD_LENGTH} caracteres. Una frase larga sirve.`;

/** What is wrong with a new password, or nothing. */
export function newPasswordIssue(value: string): string | undefined {
  // Not trimmed: leading and trailing spaces are part of a password, and the
  // backend counts them too. Trimming here would accept something it refuses.
  if (value.length < MIN_PASSWORD_LENGTH) return LENGTH_MESSAGE;
  return undefined;
}

/**
 * What is wrong with a password change as a whole, or nothing.
 *
 * The order matters: length first, because "no puede ser la misma" on a
 * four-character attempt is answering a question nobody asked.
 */
export function passwordChangeIssue({
  current,
  next,
  confirmation,
}: {
  current: string;
  next: string;
  confirmation: string;
}): string | undefined {
  if (current === "") return "Escribe tu contraseña actual.";

  const issue = newPasswordIssue(next);
  if (issue) return issue;

  // The backend refuses this too (422), but it is worth catching here: it
  // would otherwise cost a round trip to be told something the form already
  // knows, and the change would have ended every session for nothing.
  if (next === current) return "La contraseña nueva es igual a la actual.";

  if (confirmation !== next) return "Las dos contraseñas no coinciden.";

  return undefined;
}

/**
 * The six digits, as they should be sent.
 *
 * People paste codes out of a mail client with the spacing it decided on, and
 * some clients hyphenate. The backend normalizes the same way; doing it here
 * as well is what lets the form say "faltan dígitos" about what was actually
 * typed rather than about the spaces around it.
 */
export function normalizeCode(value: string): string {
  return value.replace(/[\s-]/g, "");
}

export const CODE_LENGTH = 6;

/** Whether the code is worth sending yet. Never says it is *correct*. */
export function codeLooksComplete(value: string): boolean {
  const code = normalizeCode(value);
  return code.length === CODE_LENGTH && /^\d+$/.test(code);
}
