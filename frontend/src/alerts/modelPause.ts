/**
 * What the screen says when the model stopped reading somebody's
 * unrecognised mail — the on-screen notice the owner approved on 2026-10-30
 * (`docs/model-strikes-copy.md`), and the same words the bell uses.
 *
 * Pure, like `@/alerts/inbox`: tested without the API client.
 */

import { DISPLAY_TIMEZONE } from "@/lib/dates";

/** The banks a template reads, as the copy names them. */
export const TEMPLATE_BANKS = "Bancolombia y Lulo";

/** `1 de noviembre a las 9:05 a. m.`, in Bogotá like every other moment here. */
export function pauseEnd(epochSeconds: number): string {
  const parts = new Intl.DateTimeFormat("es-CO", {
    day: "numeric",
    month: "long",
    hour: "numeric",
    minute: "2-digit",
    hour12: true,
    timeZone: DISPLAY_TIMEZONE,
  }).formatToParts(new Date(epochSeconds * 1000));
  const part = (type: Intl.DateTimeFormatPartTypes) =>
    parts.find((found) => found.type === type)?.value ?? "";
  // The locale writes the half of the day with non-breaking spaces; plain
  // ones read the same and compare the same in a test.
  const half = part("dayPeriod").replace(/\s/g, " ");

  return `${part("day")} de ${part("month")} a las ${part("hour")}:${part("minute")} ${half}`.trim();
}

export function pausedNotice(pausedUntil: number): string {
  return (
    `Pausamos hasta el ${pauseEnd(pausedUntil)} la lectura de los correos que ` +
    "Finflow no reconoce: llegaron correos que no eran de tu banco. Las alertas " +
    `de ${TEMPLATE_BANKS} se siguen registrando; lo demás puedes anotarlo a mano.`
  );
}

export const REVOKED_NOTICE =
  "Finflow ya no lee los correos que no reconoce por sí solo. Las alertas de " +
  `${TEMPLATE_BANKS} se siguen registrando y tu cuenta sigue activa. Para ` +
  "reactivar la lectura, comunícate con servicio al cliente (muy pronto).";

/** The bell's lines for one suspension, as Telegram says it. */
export function pauseLines(pause: { strike: number; paused_until: number | null }): {
  title: string;
  lines: string[];
} {
  if (pause.paused_until === null) {
    return { title: "Lectura desactivada", lines: [REVOKED_NOTICE] };
  }

  return {
    title: "Lectura pausada",
    lines: [
      pausedNotice(pause.paused_until),
      pause.strike === 1
        ? "Si vuelve a pasar, la pausa será de 3 días."
        : "Es el último aviso: si vuelve a pasar, Finflow dejará de leer esos correos de forma definitiva.",
    ],
  };
}
