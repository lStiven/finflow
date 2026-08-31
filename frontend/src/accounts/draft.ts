/**
 * What the "declare an account" form holds, and what the API will take.
 *
 * Every rule here is one the backend also enforces — the point is not to
 * replace it but to say the same thing in Spanish, before a round trip, in
 * the place the person is looking. The 422s it prevents are the ones that
 * read as a bug: an instrument with a kind and no digits, a credit limit on a
 * savings account, a name that is only spaces.
 *
 * Money stays a string the whole way. The fields are text, the payload takes
 * a decimal string, and nothing in between ever parses it as a number.
 */

import type { components } from "@/api/schema";

export type OpenAccountPayload = components["schemas"]["OpenAccountPayload"];
export type Currency = components["schemas"]["Currency"];

export type AccountDraft = {
  kind: string;
  /** `asset` or `liability`, straight from the catalogue. Never chosen. */
  category: string;
  name: string;
  currency: Currency;
  bank: string;
  /** Empty means "no instrument": a valid account that simply matches no alert. */
  instrumentKind: string;
  lastFour: string;
  /** For an asset, what it holds today; for a liability, what is already owed. */
  openingBalance: string;
  creditLimit: string;
};

export type DraftField = keyof AccountDraft;
export type DraftIssue = { field: DraftField; message: string };

export const MAX_NAME_LENGTH = 120;

export function emptyDraft(): AccountDraft {
  return {
    kind: "",
    category: "",
    name: "",
    currency: "COP",
    bank: "",
    instrumentKind: "",
    lastFour: "",
    openingBalance: "",
    creditLimit: "",
  };
}

/** A non-negative decimal, written the way a person writes one. */
const AMOUNT = /^\d+(\.\d+)?$/;
/** The API's own rule: four digits or more, and only digits. */
const LAST_FOUR = /^\d{4,}$/;

export function isLiability(draft: AccountDraft): boolean {
  return draft.category === "liability";
}

/**
 * Everything wrong with the draft, in the order the fields are shown.
 *
 * Empty means the payload below is safe to build. Nothing here is a warning:
 * each entry is something the API would refuse.
 */
export function validateDraft(draft: AccountDraft): DraftIssue[] {
  const issues: DraftIssue[] = [];

  if (draft.kind === "") {
    issues.push({ field: "kind", message: "Elige qué tipo de cuenta es." });
  }

  const name = draft.name.trim();
  if (name === "") {
    issues.push({ field: "name", message: "Ponle un nombre para reconocerla." });
  } else if (name.length > MAX_NAME_LENGTH) {
    issues.push({
      field: "name",
      message: `El nombre no puede pasar de ${MAX_NAME_LENGTH} caracteres.`,
    });
  }

  const hasKind = draft.instrumentKind !== "";
  const digits = draft.lastFour.trim();

  if (hasKind && digits === "") {
    issues.push({
      field: "lastFour",
      message:
        "Faltan los últimos cuatro dígitos: sin ellos, esa tarjeta no identifica nada.",
    });
  }

  if (!hasKind && digits !== "") {
    issues.push({
      field: "instrumentKind",
      message: "Dinos también cómo llegan sus alertas: la cuenta o la tarjeta.",
    });
  }

  if (digits !== "" && !LAST_FOUR.test(digits)) {
    issues.push({
      field: "lastFour",
      message: "Son solo dígitos, mínimo cuatro.",
    });
  }

  if ((hasKind || digits !== "") && draft.bank.trim() === "") {
    issues.push({
      field: "bank",
      message: "Escribe el banco: “termina en 1234” sin banco no identifica nada.",
    });
  }

  if (draft.openingBalance.trim() !== "" && !AMOUNT.test(draft.openingBalance.trim())) {
    issues.push({
      field: "openingBalance",
      message: "Escribe solo números, sin puntos de miles ni signos.",
    });
  }

  if (draft.creditLimit.trim() !== "") {
    if (!isLiability(draft)) {
      issues.push({
        field: "creditLimit",
        message: "El cupo es de las cuentas que se deben, no de las que tienen saldo.",
      });
    } else if (!AMOUNT.test(draft.creditLimit.trim())) {
      issues.push({
        field: "creditLimit",
        message: "Escribe solo números, sin puntos de miles ni signos.",
      });
    }
  }

  return issues;
}

/** The first issue for one field, for the message under it. */
export function issueFor(issues: DraftIssue[], field: DraftField): string | undefined {
  return issues.find((issue) => issue.field === field)?.message;
}

/**
 * The draft as the API takes it.
 *
 * Optional fields travel as `null` rather than as an empty string: an absent
 * instrument is a real state — an account that matches no alert yet — and
 * `""` is not a valid bank, a valid amount, or a valid anything.
 *
 * Assumes `validateDraft` came back empty; it is the caller's job to check.
 */
export function toPayload(draft: AccountDraft): OpenAccountPayload {
  const digits = draft.lastFour.trim();
  const hasInstrument = draft.instrumentKind !== "" && digits !== "";
  const bank = draft.bank.trim();
  const opening = draft.openingBalance.trim();
  const limit = draft.creditLimit.trim();

  return {
    name: draft.name.trim(),
    kind: draft.kind as OpenAccountPayload["kind"],
    currency: draft.currency,
    bank: bank === "" ? null : bank,
    instrument_kind: hasInstrument
      ? (draft.instrumentKind as NonNullable<OpenAccountPayload["instrument_kind"]>)
      : null,
    last_four: hasInstrument ? digits : null,
    opening_balance: opening === "" ? null : opening,
    // Only a liability may carry one, and sending it on an asset is a 422
    // rather than a field the backend ignores.
    credit_limit: limit === "" || !isLiability(draft) ? null : limit,
  };
}
