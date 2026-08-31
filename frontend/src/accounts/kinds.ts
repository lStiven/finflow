/**
 * How each account kind is presented, in Spanish and in the owner's words.
 *
 * The API publishes the vocabulary (`GET /financial/catalog`) and its labels
 * are English by contract — `value` is the stable half, and a client showing
 * anything else builds its own labels from it. So this file is that build:
 * keyed by `value`, and every lookup falls back to whatever the catalogue
 * sent, so a kind added on the server still renders instead of disappearing.
 *
 * It also holds the one thing a dropdown cannot say on its own: which
 * instrument a kind's alerts actually arrive as. A savings account is
 * declared `savings` and emails as `account` — spelling that key with the
 * account kind produces something no alert can ever match, and the owner
 * finds out only by noticing that nothing was ever assigned.
 */

import {
  Banknote,
  CreditCard,
  HandCoins,
  Home,
  Landmark,
  PiggyBank,
  TrendingUp,
  Wallet,
} from "lucide-react";
import type { ComponentType } from "react";

export type KindCopy = {
  label: string;
  /** One line, in the second person: what this kind is, not what it means. */
  blurb: string;
  /** Prefilled so somebody who has one of each can just keep going. */
  suggestedName: string;
  icon: ComponentType<{ className?: string }>;
  /**
   * The instrument its alerts arrive under, or null for what never emails.
   * A suggestion, never a lock: the form still offers the whole list.
   */
  instrument: string | null;
  /** What the opening figure is called for this kind — spent, held or owed. */
  openingLabel: string;
  openingHint: string;
};

const FALLBACK_ICON = Wallet;

export const KIND_COPY: Record<string, KindCopy> = {
  savings: {
    label: "Cuenta de ahorros",
    blurb: "Donde guardas la plata y desde donde transfieres.",
    suggestedName: "Cuenta de ahorros",
    icon: PiggyBank,
    instrument: "account",
    openingLabel: "¿Cuánto tiene hoy?",
    openingHint: "Opcional. El saldo que muestra tu banco ahora mismo.",
  },
  checking: {
    label: "Cuenta corriente",
    blurb: "La del día a día, normalmente con chequera o sobregiro.",
    suggestedName: "Cuenta corriente",
    icon: Landmark,
    instrument: "account",
    openingLabel: "¿Cuánto tiene hoy?",
    openingHint: "Opcional. El saldo que muestra tu banco ahora mismo.",
  },
  cash: {
    label: "Efectivo",
    blurb: "La plata que cargas encima. Nunca te llega un correo por ella.",
    suggestedName: "Efectivo",
    icon: Banknote,
    instrument: null,
    openingLabel: "¿Cuánto tienes ahora?",
    openingHint: "Opcional. Lo que hay en tu bolsillo hoy.",
  },
  investment: {
    label: "Inversión",
    blurb: "Un CDT, un fondo, lo que tengas puesto a rendir.",
    suggestedName: "Inversión",
    icon: TrendingUp,
    instrument: "account",
    openingLabel: "¿Cuánto tiene hoy?",
    openingHint: "Opcional. Lo que vale hoy, según tu entidad.",
  },
  credit_card: {
    label: "Tarjeta de crédito",
    blurb: "Lo que gastas con ella es deuda hasta que la pagues.",
    suggestedName: "Tarjeta de crédito",
    icon: CreditCard,
    instrument: "credit_card",
    openingLabel: "¿Cuánto llevas gastado?",
    openingHint: "Opcional. Lo que debes hoy, no el cupo.",
  },
  loan: {
    label: "Préstamo",
    blurb: "Un crédito de libre inversión, de vehículo, de estudio.",
    suggestedName: "Préstamo",
    icon: HandCoins,
    instrument: null,
    openingLabel: "¿Cuánto debes hoy?",
    openingHint: "Opcional. El saldo que falta por pagar.",
  },
  mortgage: {
    label: "Hipoteca",
    blurb: "El crédito de vivienda. Casi nunca avisa por correo.",
    suggestedName: "Hipoteca",
    icon: Home,
    instrument: null,
    openingLabel: "¿Cuánto debes hoy?",
    openingHint: "Opcional. El saldo que falta por pagar.",
  },
};

/**
 * The copy for a kind, or something usable for one this build never heard of.
 *
 * `catalogLabel` is what the API sent, which is a real word in English rather
 * than a raw enum value — a better last resort than the value itself.
 */
export function kindCopy(value: string, catalogLabel: string): KindCopy {
  return (
    KIND_COPY[value] ?? {
      label: catalogLabel,
      blurb: "",
      suggestedName: catalogLabel,
      icon: FALLBACK_ICON,
      instrument: null,
      openingLabel: "Saldo de hoy",
      openingHint: "Opcional.",
    }
  );
}

/**
 * The names a bank gives the thing money moved through, said in Spanish.
 *
 * These are the only words that ever appear in an alert, so the blurbs
 * describe the *movement* somebody would recognise rather than the account
 * they think they have.
 */
export const INSTRUMENT_COPY: Record<string, { label: string; blurb: string }> = {
  account: {
    label: "La cuenta",
    blurb: "Transferencias, QR y pagos hechos desde la cuenta.",
  },
  debit_card: {
    label: "Tarjeta débito",
    blurb: "Compras con la tarjeta que saca de esta cuenta.",
  },
  credit_card: {
    label: "Tarjeta de crédito",
    blurb: "Compras y avances con la tarjeta de crédito.",
  },
  savings_account: {
    label: "Cuenta de ahorros",
    blurb: "Si tus alertas la nombran así, tal cual.",
  },
  checking_account: {
    label: "Cuenta corriente",
    blurb: "Si tus alertas la nombran así, tal cual.",
  },
};

export function instrumentLabel(value: string, catalogLabel = value): string {
  return INSTRUMENT_COPY[value]?.label ?? catalogLabel;
}

/**
 * Whether to offer linking this kind's alerts at all.
 *
 * Only the kinds known to never email — cash, a mortgage — are answered
 * `false`. A kind this build has no copy for gets `true`: hiding the fields
 * would leave an account the server accepts and nothing can ever link, which
 * is worse than four fields somebody skips.
 */
export function canLinkAlerts(kind: string): boolean {
  const copy = KIND_COPY[kind];
  return copy === undefined || copy.instrument !== null;
}

/**
 * Where a movement came from, said in Spanish.
 *
 * The catalogue's own labels are English (`Bank alert`, `Manual`), and this is
 * the filter somebody reads before choosing one.
 */
const ORIGIN_COPY: Record<string, string> = {
  bank_alert: "Alerta del banco",
  manual: "Registrado a mano",
};

export function originLabel(value: string, catalogLabel = value): string {
  return ORIGIN_COPY[value] ?? catalogLabel;
}
