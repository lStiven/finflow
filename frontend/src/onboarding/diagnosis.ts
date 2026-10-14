/**
 * What a screen with no movements says about why, and where it sends you.
 *
 * Transacciones and Resumen used to answer an empty list the same way
 * whatever the reason: «cuando tu banco te avise, aparecerá aquí». That is
 * true when everything works and a lie of omission when it does not — a bank
 * nobody approved, mail being thrown away, alerts nothing could read. The
 * connection screen already tells those apart with evidence; this picks the
 * one that applies and points there.
 *
 * What cannot be observed is said as such. Nothing outside Gmail can see a
 * filter, so «no ha llegado nada» never becomes «tu filtro está mal».
 */

import type { InboxSetup } from "@/api/queries";
import type { Health } from "@/onboarding/activity";

export type Diagnosis = {
  tone: "info" | "warn";
  title: string;
  body: string;
  /** Where the fix is: the connection's own screen, at the right step. */
  action: { label: string; paso?: number };
};

function done(setup: InboxSetup, key: string): boolean {
  return setup.steps.find((step) => step.key === key)?.done ?? false;
}

/**
 * Null while either answer is still on its way: guessing would show the
 * wrong reason for a moment and then swap it, which reads as a flicker of
 * alarm on a screen that is merely empty.
 */
export function diagnoseEmpty(
  setup: InboxSetup | undefined,
  health: Health | null,
): Diagnosis | null {
  if (!setup || !health) return null;

  if (!done(setup, "senders_approved")) {
    return {
      tone: "info",
      title: "Tu banco todavía no está conectado",
      body: "Elige tus bancos y reenvía sus alertas: los movimientos llegarán solos.",
      action: { label: "Conectar mi banco", paso: 1 },
    };
  }

  if (health.kind === "discarding") {
    return {
      tone: "warn",
      title: "Llegaron correos de un remitente que no aprobaste",
      body: `Finflow no los leyó (${health.senders[0]}${health.senders.length > 1 ? " y otros" : ""}). Si son de tu banco, apruébalo.`,
      action: { label: "Revisar la conexión" },
    };
  }

  if (health.kind === "unreadable") {
    return {
      tone: "warn",
      title: "Llegaron alertas, pero ninguna se pudo leer",
      body: "Puede ser un formato que Finflow todavía no conoce. Mira qué llegó.",
      action: { label: "Ver qué llegó" },
    };
  }

  if (health.kind === "quiet") {
    return done(setup, "forwarding_confirmed")
      ? {
          tone: "info",
          title: "Todavía no llega ningún correo de tu banco",
          body: "Tu dirección está confirmada. El filtro de Gmail no se puede ver desde aquí: se comprueba cuando llegue la primera alerta.",
          action: { label: "Ver el estado de la conexión" },
        }
      : {
          tone: "info",
          title: "Falta que Gmail confirme tu dirección",
          body: "Hasta entonces Gmail no reenvía nada a Finflow.",
          action: { label: "Seguir conectando", paso: 2 },
        };
  }

  if (health.kind === "stale") {
    return {
      tone: "warn",
      title: "Hace días que no llega nada de tu banco",
      body: "Si sigues usando tus cuentas, revisa que el reenvío siga activo.",
      action: { label: "Revisar la conexión" },
    };
  }

  // Mail arrives and is read; it simply has not been a movement yet.
  return null;
}
