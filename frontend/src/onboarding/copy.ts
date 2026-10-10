import type { StageId } from "@/onboarding/steps";

/**
 * What each stage is called, wherever it is named.
 *
 * Kept apart from the screen so the nudge in the shell, the stepper and the
 * dialogs all say the same thing — a step called one thing in the sidebar and
 * another in the guide reads as two different steps. `short` is the stepper's
 * label on a phone, where four titles do not fit in a row.
 */
export const STAGE_COPY: Record<StageId, { title: string; short: string }> = {
  banks: { title: "Elige tus bancos", short: "Bancos" },
  address: { title: "Autoriza tu dirección en Gmail", short: "Dirección" },
  filter: { title: "Reenvía solo tus alertas", short: "Filtro" },
  "first-alert": { title: "Recibe tu primera alerta", short: "Prueba" },
};
