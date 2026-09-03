/**
 * What the "¿cuánto cuesta este crédito?" form holds, said in Spanish.
 *
 * The backend refuses every one of these mistakes on its own. The point of
 * repeating them here is *where*: a rate typed as `19.56` instead of `0.1956`
 * is the difference between a mortgage and a loan shark, and finding that out
 * from a 400 after pressing guardar is finding it out in the wrong place.
 *
 * Percentages are the one thing this file translates rather than passes
 * through. Nobody writes `0.1956` on a form — they write `19.56`, because
 * that is what the bank printed — so the field is a percentage and the
 * payload is a fraction, converted in exactly one function so the two can
 * never disagree.
 *
 * Money stays a string the whole way, like everywhere else here: the fields
 * are text and the payload takes decimal strings, and nothing between them
 * ever parses a peso as a float.
 */

import type {
  ChargeBasis,
  InvestmentTermsPayload,
  LoanTermsPayload,
  RateBasis,
} from "@/api/queries";
import type { components } from "@/api/schema";

type ChargePayload = components["schemas"]["ChargePayload"];

/** How a bank quotes a rate, and the difference is not cosmetic. */
export const RATE_BASIS_COPY: Record<string, { label: string; hint: string }> = {
  effective_annual: {
    label: "Efectivo anual (% E.A.)",
    hint: "El número grande que sale en el contrato. Es el más común.",
  },
  nominal_annual: {
    label: "Nominal anual mes vencido (% N.A. M.V.)",
    hint: "Se divide entre 12 para sacar el mes. Da un poco más que el E.A.",
  },
  monthly: {
    label: "Mensual (% M.V.)",
    hint: "La tasa del mes, tal cual aparece en el extracto.",
  },
};

/** What a recurring charge is a percentage *of*. */
export const CHARGE_BASIS_COPY: Record<
  string,
  {
    label: string;
    /**
     * The same thing without the leading `%`, for reading back beside a
     * figure that already carries one: "0.0345 % sobre el saldo".
     */
    short: string;
    hint: string;
    needs: "amount" | "rate";
    base: boolean;
  }
> = {
  outstanding_balance: {
    label: "% sobre el saldo de la deuda",
    short: "mensual sobre el saldo de la deuda",
    hint: "El seguro de vida deudores va aquí: baja a medida que pagas.",
    needs: "rate",
    base: false,
  },
  insured_value: {
    label: "% sobre el valor asegurado",
    short: "mensual sobre el valor asegurado",
    hint: "El seguro de incendio y terremoto: se cobra sobre el avalúo, no sobre la deuda.",
    needs: "rate",
    base: true,
  },
  fixed: {
    label: "Un valor fijo cada mes",
    short: "fijos cada mes",
    hint: "Cuotas de manejo y administración.",
    needs: "amount",
    base: false,
  },
  original_principal: {
    label: "% sobre el monto desembolsado",
    short: "mensual sobre el monto desembolsado",
    hint: "Sobre lo que te prestaron al principio, que no cambia.",
    needs: "rate",
    base: false,
  },
  earnings: {
    label: "% sobre el rendimiento",
    short: "de lo que rinda cada corte",
    hint: "La retención en la fuente de un CDT: 4 % de lo que ganó, no del saldo.",
    needs: "rate",
    base: false,
  },
};

export const AMORTIZATION_COPY: Record<string, { label: string; hint: string }> = {
  french: {
    label: "Cuota fija",
    hint: "La cuota es siempre la misma; al principio casi todo es interés.",
  },
  constant_principal: {
    label: "Abono fijo a capital",
    hint: "Abonas lo mismo al capital cada mes y la cuota va bajando.",
  },
  interest_only: {
    label: "Solo intereses",
    hint: "Pagas el interés y la deuda no baja. Periodos de gracia.",
  },
};

export type ChargeDraft = {
  /**
   * Identity for the form row, never sent anywhere.
   *
   * A charge's name is what identifies it to the API, and it is also the
   * field somebody is halfway through typing — so keying the rows by it, or
   * by their position, makes React reuse the wrong input the moment a row is
   * removed from the middle.
   */
  id: string;
  name: string;
  basis: ChargeBasis | "";
  /** A percentage as a person writes it: `0.0345`, meaning 0.0345 % al mes. */
  rate: string;
  amount: string;
  base: string;
  chargedToBalance: boolean;
};

export type LoanDraft = {
  ratePercent: string;
  rateBasis: RateBasis;
  disbursedOn: string;
  termMonths: string;
  statementDay: string;
  paymentDay: string;
  style: string;
  principal: string;
  installment: string;
  installmentCoversCharges: boolean;
  charges: ChargeDraft[];
  /** Empty means "desde hoy", which is the safe default. See `loanPayload`. */
  accrueFrom: string;
};

export type InvestmentDraft = {
  /** Empty when the value is stated rather than computed — renta variable. */
  ratePercent: string;
  rateBasis: RateBasis;
  openedOn: string;
  statementDay: string;
  maturesOn: string;
  charges: ChargeDraft[];
  accrueFrom: string;
};

export type FinancingField =
  | keyof LoanDraft
  | keyof InvestmentDraft
  | `charges.${number}.${keyof ChargeDraft}`;

export type FinancingIssue = { field: FinancingField; message: string };

const AMOUNT = /^\d+(\.\d+)?$/;
const DATE = /^\d{4}-\d{2}-\d{2}$/;

/** Half a century of monthly periods, the same cap the API keeps. */
export const MAX_TERM_MONTHS = 600;

let nextChargeId = 0;

export function emptyCharge(): ChargeDraft {
  nextChargeId += 1;

  return {
    id: `charge-${nextChargeId}`,
    name: "",
    basis: "outstanding_balance",
    rate: "",
    amount: "",
    base: "",
    chargedToBalance: true,
  };
}

export function emptyLoanDraft(): LoanDraft {
  return {
    ratePercent: "",
    rateBasis: "effective_annual",
    disbursedOn: "",
    termMonths: "",
    statementDay: "",
    paymentDay: "",
    style: "french",
    principal: "",
    installment: "",
    installmentCoversCharges: true,
    charges: [],
    accrueFrom: "",
  };
}

export function emptyInvestmentDraft(): InvestmentDraft {
  return {
    ratePercent: "",
    rateBasis: "effective_annual",
    openedOn: "",
    statementDay: "",
    maturesOn: "",
    charges: [],
    accrueFrom: "",
  };
}

/**
 * A percentage as somebody writes it, as the fraction the API takes.
 *
 * The single place the two units meet. `19.56` on a form is `0.1956` in a
 * payload, and the API refuses anything above 10 precisely because that is
 * what a percentage sent unconverted looks like.
 *
 * Done as text rather than through a float: `19.56 / 100` is
 * `0.19560000000000002`, and a rate is money's slope.
 */
export function toFraction(percent: string): string {
  const trimmed = percent.trim();
  if (trimmed === "") return "";

  const negative = trimmed.startsWith("-");
  const digits = negative ? trimmed.slice(1) : trimmed;
  const [whole, decimals = ""] = digits.split(".");
  const shifted = `${whole}${decimals}`.padStart(decimals.length + 3, "0");
  const cut = shifted.length - decimals.length - 2;
  const result = `${shifted.slice(0, cut)}.${shifted.slice(cut)}`.replace(
    /^0+(?=\d)/,
    "",
  );

  return negative ? `-${result}` : result;
}

/** The inverse, for filling the form from terms the API already holds. */
export function toPercent(fraction: string): string {
  const trimmed = fraction.trim();
  if (trimmed === "") return "";

  const negative = trimmed.startsWith("-");
  const digits = negative ? trimmed.slice(1) : trimmed;
  const [whole, decimals = ""] = digits.split(".");
  const padded = `${whole}${decimals.padEnd(2, "0")}`;
  const cut = padded.length - Math.max(decimals.length - 2, 0);
  const shifted = `${padded.slice(0, cut)}.${padded.slice(cut)}`
    .replace(/^0+(?=\d)/, "")
    .replace(/\.$/, "")
    .replace(/(\.\d*?)0+$/, "$1")
    .replace(/\.$/, "");

  return negative ? `-${shifted}` : shifted;
}

function checkRate(
  percent: string,
  field: FinancingField,
  issues: FinancingIssue[],
  { required }: { required: boolean },
): void {
  const value = percent.trim();

  if (value === "") {
    if (required) {
      issues.push({ field, message: "Escribe la tasa que te cobran." });
    }
    return;
  }

  if (!AMOUNT.test(value)) {
    issues.push({ field, message: "Escribe solo números, por ejemplo 19.56." });
    return;
  }

  // The API's own guard, said before the round trip: 1000 % is what a
  // fraction typed as a percentage looks like on the other side.
  if (Number(value) > 1000) {
    issues.push({ field, message: "Esa tasa es demasiado alta. ¿Sobran ceros?" });
  }
}

function checkDay(
  value: string,
  field: FinancingField,
  issues: FinancingIssue[],
): void {
  const day = Number(value.trim());

  if (value.trim() === "" || !Number.isInteger(day) || day < 1 || day > 31) {
    issues.push({ field, message: "Es un día del mes, del 1 al 31." });
  }
}

function checkCharges(charges: ChargeDraft[], issues: FinancingIssue[]): void {
  const seen = new Set<string>();

  charges.forEach((charge, index) => {
    const name = charge.name.trim();

    if (name === "") {
      issues.push({
        field: `charges.${index}.name`,
        message: "Ponle el nombre con el que aparece en tu extracto.",
      });
    } else if (seen.has(name.toLowerCase())) {
      // Not tidiness: each charge is posted once per month under its own
      // name, so two sharing one would be a single row and the second would
      // never be charged at all.
      issues.push({
        field: `charges.${index}.name`,
        message: "Ya hay otro cobro con ese nombre. Cámbiale uno.",
      });
    } else {
      seen.add(name.toLowerCase());
    }

    const copy = CHARGE_BASIS_COPY[charge.basis];

    if (charge.basis === "" || copy === undefined) {
      issues.push({
        field: `charges.${index}.basis`,
        message: "Elige sobre qué se calcula.",
      });
      return;
    }

    if (copy.needs === "rate") {
      checkRate(charge.rate, `charges.${index}.rate`, issues, { required: true });
    } else if (!AMOUNT.test(charge.amount.trim())) {
      issues.push({
        field: `charges.${index}.amount`,
        message: "Escribe cuánto te cobran cada mes.",
      });
    }

    if (copy.base && !AMOUNT.test(charge.base.trim())) {
      issues.push({
        field: `charges.${index}.base`,
        message: "Escribe el valor asegurado sobre el que lo calculan.",
      });
    }
  });
}

export function validateLoanDraft(draft: LoanDraft): FinancingIssue[] {
  const issues: FinancingIssue[] = [];

  checkRate(draft.ratePercent, "ratePercent", issues, { required: true });

  if (!DATE.test(draft.disbursedOn)) {
    issues.push({
      field: "disbursedOn",
      message: "¿Cuándo te desembolsaron el crédito?",
    });
  }

  const term = Number(draft.termMonths.trim());
  if (!Number.isInteger(term) || term < 1 || term > MAX_TERM_MONTHS) {
    issues.push({
      field: "termMonths",
      message: `El plazo va de 1 a ${MAX_TERM_MONTHS} meses.`,
    });
  }

  checkDay(draft.statementDay, "statementDay", issues);

  if (draft.paymentDay.trim() !== "") {
    checkDay(draft.paymentDay, "paymentDay", issues);
  }

  for (const [field, value] of [
    ["principal", draft.principal],
    ["installment", draft.installment],
  ] as const) {
    if (value.trim() !== "" && !AMOUNT.test(value.trim())) {
      issues.push({ field, message: "Escribe solo números, sin puntos ni comas." });
    }
  }

  checkCharges(draft.charges, issues);

  return issues;
}

export function validateInvestmentDraft(draft: InvestmentDraft): FinancingIssue[] {
  const issues: FinancingIssue[] = [];

  checkRate(draft.ratePercent, "ratePercent", issues, { required: false });

  if (!DATE.test(draft.openedOn)) {
    issues.push({ field: "openedOn", message: "¿Cuándo abriste la inversión?" });
  }

  checkDay(draft.statementDay, "statementDay", issues);

  if (draft.maturesOn.trim() !== "") {
    if (!DATE.test(draft.maturesOn)) {
      issues.push({ field: "maturesOn", message: "Escribe la fecha de vencimiento." });
    } else if (draft.maturesOn <= draft.openedOn) {
      issues.push({
        field: "maturesOn",
        message: "El vencimiento va después de la apertura.",
      });
    }
  }

  // The API refuses this too, and its reason is worth saying here: there is
  // nothing to withhold from a return nobody computes.
  if (
    draft.ratePercent.trim() === "" &&
    draft.charges.some((charge) => charge.basis === "earnings")
  ) {
    issues.push({
      field: "ratePercent",
      message:
        "Sin tasa no hay rendimiento del que retener. Pon la tasa, o registra el valor a mano.",
    });
  }

  checkCharges(draft.charges, issues);

  return issues;
}

function chargePayload(charge: ChargeDraft): ChargePayload {
  const copy = CHARGE_BASIS_COPY[charge.basis];

  return {
    name: charge.name.trim(),
    basis: charge.basis as ChargeBasis,
    rate: copy?.needs === "rate" ? toFraction(charge.rate) : null,
    amount: copy?.needs === "amount" ? charge.amount.trim() : null,
    base: copy?.base ? charge.base.trim() : null,
    charged_to_balance: charge.chargedToBalance,
  };
}

/**
 * The payload, from a draft this file has already accepted.
 *
 * `accrue_from` is sent only when the owner asked for it. Left out, the API
 * starts charging from today — which is what somebody declaring a mortgage
 * they have been paying for years needs, because the balance they just typed
 * already contains all that interest.
 */
export function loanPayload(draft: LoanDraft): LoanTermsPayload {
  return {
    rate: {
      value: toFraction(draft.ratePercent),
      basis: draft.rateBasis,
    },
    disbursed_on: draft.disbursedOn,
    term_months: Number(draft.termMonths.trim()),
    statement_day: Number(draft.statementDay.trim()),
    payment_day:
      draft.paymentDay.trim() === "" ? null : Number(draft.paymentDay.trim()),
    style: draft.style as LoanTermsPayload["style"],
    principal: draft.principal.trim() === "" ? null : draft.principal.trim(),
    installment: draft.installment.trim() === "" ? null : draft.installment.trim(),
    installment_covers_charges: draft.installmentCoversCharges,
    charges: draft.charges.map(chargePayload),
    accrue_from: draft.accrueFrom.trim() === "" ? null : draft.accrueFrom.trim(),
  };
}

export function investmentPayload(draft: InvestmentDraft): InvestmentTermsPayload {
  return {
    opened_on: draft.openedOn,
    statement_day: Number(draft.statementDay.trim()),
    rate:
      draft.ratePercent.trim() === ""
        ? null
        : { value: toFraction(draft.ratePercent), basis: draft.rateBasis },
    matures_on: draft.maturesOn.trim() === "" ? null : draft.maturesOn.trim(),
    charges: draft.charges.map(chargePayload),
    accrue_from: draft.accrueFrom.trim() === "" ? null : draft.accrueFrom.trim(),
  };
}

/** Whether this kind of account has a cost worth declaring at all. */
export function isFinanceable(kind: string): "loan" | "investment" | null {
  if (kind === "loan" || kind === "mortgage") return "loan";
  if (kind === "investment") return "investment";

  return null;
}
