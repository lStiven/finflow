/**
 * What the forms on a merchant will let through, and what they refuse to
 * offer at all.
 *
 * Same arrangement as `@/accounts/edits`: every rule here is one the backend
 * also enforces, said in Spanish before the round trip. The one that is not a
 * validation but a *refusal to offer* is `lastAliasBlocker` — taking the last
 * spelling off a merchant is a 409, because a merchant with no spelling does
 * not exist, and an explanation in place beats an error message after the
 * fact.
 */

/** The API's own ceiling for a merchant name (`MAX_NAME_LENGTH`). */
export const MAX_NAME_LENGTH = 120;

/** What is wrong with a merchant's name, or nothing. Empty is not valid. */
export function merchantNameIssue(value: string): string | undefined {
  const trimmed = value.trim();
  if (trimmed === "") return "Ponle un nombre para reconocerlo.";
  if (trimmed.length > MAX_NAME_LENGTH) {
    return `El nombre no puede pasar de ${MAX_NAME_LENGTH} caracteres.`;
  }
  return undefined;
}

/**
 * Same, for the optional name of a merchant being split off. Empty is valid
 * here: the backend names the new merchant after the spelling itself.
 */
export function newMerchantNameIssue(value: string): string | undefined {
  const trimmed = value.trim();
  if (trimmed === "") return undefined;
  if (trimmed.length > MAX_NAME_LENGTH) {
    return `El nombre no puede pasar de ${MAX_NAME_LENGTH} caracteres.`;
  }
  return undefined;
}

/**
 * Why this spelling cannot be taken off this merchant at all, or nothing.
 *
 * It covers **both** ways of taking one away, because the backend refuses
 * both for the same reason: moving the last spelling elsewhere empties the
 * merchant exactly as splitting it out does, and a merchant with no spelling
 * does not exist. Confirmed against the running API — each answers 409 with
 * "is the only way to reach this merchant".
 *
 * Said here first, in the screen's own words: that message names aliases, and
 * nothing the user reads ever calls them that. And said as a way forward,
 * since there are two — the merchant can be renamed, or merged into the one
 * it should have been all along.
 */
export function lastAliasBlocker(aliasCount: number): string | undefined {
  if (aliasCount <= 1) {
    return "Es la única forma en que este comercio se escribe, y un comercio sin ninguna no existe: por eso esta no se puede mover ni separar. Si lo que está mal es el nombre, cámbialo arriba; si este negocio y otro que ya tienes son el mismo, fusiónalos abajo.";
  }
  return undefined;
}
