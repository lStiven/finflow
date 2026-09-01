/**
 * What an identity error means, in words somebody can act on.
 *
 * The API's `detail` is deliberately terse and in English ("That code is not
 * valid"), and for the credential endpoints it is also deliberately
 * uninformative: one answer covers a wrong code, an address with no challenge
 * and a malformed one, because saying which would say who is registered here.
 *
 * That leaves the *status* carrying the only thing worth telling apart, and
 * each of these has a different next move: ask for another code, wait, or fix
 * the digits. Shared by the three screens that call those endpoints so the
 * three cannot drift into saying different things about the same 429.
 */

import { ApiError } from "@/api/errors";

export function identityErrorMessage(cause: unknown): string {
  if (cause instanceof ApiError) {
    switch (cause.status) {
      case 410:
        return "El código venció. Pide uno nuevo.";
      case 429:
        return retryMessage(cause.retryAfterSeconds);
      case 403:
        // The ticket is gone, expired, or belongs to another address: the
        // code step has to start over.
        return "La verificación caducó. Pide un código nuevo.";
      case 502:
        return "No pudimos enviar el correo. Inténtalo en un momento.";
      default:
        break;
    }
  }
  return cause instanceof Error ? cause.message : "Algo salió mal";
}

/**
 * `Retry-After` is the only detail the 429 gives, and it gives it on purpose:
 * how many messages have already gone out, and to what, stays between the
 * backend and the address.
 */
export function retryMessage(seconds: number | undefined): string {
  if (seconds === undefined) return "Demasiados intentos. Espera un momento.";
  if (seconds <= 90) return `Espera ${seconds} segundos y vuelve a intentarlo.`;
  return `Espera ${Math.ceil(seconds / 60)} minutos y vuelve a intentarlo.`;
}
