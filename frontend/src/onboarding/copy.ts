import type { StageId } from "@/onboarding/steps";

/**
 * What each stage is called, wherever it is named.
 *
 * Kept apart from the screen so the nudge in the shell, the stepper and the
 * recap all say the same thing — a step called one thing in the sidebar and
 * another in the guide reads as two different steps.
 */
export const STAGE_COPY: Record<
  StageId,
  { title: string; blurb: string; waiting?: string }
> = {
  intro: {
    title: "Cómo funciona Finflow",
    blurb: "Un minuto de lectura para entender qué vas a conectar y por qué.",
  },
  address: {
    title: "Tu dirección de Finflow",
    blurb: "La dirección a la que vas a reenviar los correos de tu banco.",
  },
  senders: {
    title: "Aprueba a tu banco",
    blurb: "Mientras no apruebes a nadie, tu dirección no acepta nada.",
  },
  forwarding: {
    title: "Activa el reenvío en tu correo",
    blurb:
      "Dos partes en Gmail, desde un computador: autorizar tu dirección y crear un filtro.",
    waiting: "Esperando la confirmación de Google…",
  },
  "first-alert": {
    title: "Tu primer movimiento",
    blurb: "La prueba de que todo el camino funciona de punta a punta.",
    waiting: "Esperando la primera alerta de tu banco…",
  },
};
