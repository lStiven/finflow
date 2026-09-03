/**
 * The form's own arithmetic, which is one conversion and a list of refusals.
 *
 * The conversion is the dangerous half. Nobody writes `0.1956` on a form —
 * they write `19.56`, because that is what the bank printed — and the API
 * takes the fraction. Getting that wrong by a factor of a hundred is the
 * difference between a mortgage and something that doubles every four months,
 * and the API's own guard only catches it in one direction.
 */

import { describe, expect, it } from "vitest";
import {
  emptyCharge,
  emptyInvestmentDraft,
  emptyLoanDraft,
  investmentPayload,
  isFinanceable,
  type LoanDraft,
  loanPayload,
  toFraction,
  toPercent,
  validateInvestmentDraft,
  validateLoanDraft,
} from "@/accounts/financing";

function loan(overrides: Partial<LoanDraft> = {}): LoanDraft {
  return {
    ...emptyLoanDraft(),
    ratePercent: "19.56",
    disbursedOn: "2026-01-15",
    termMonths: "60",
    statementDay: "15",
    ...overrides,
  };
}

describe("toFraction", () => {
  it("moves the point two places without ever seeing a float", () => {
    // `19.56 / 100` is 0.19560000000000002 in IEEE 754, and a rate is the
    // slope of somebody's debt.
    expect(toFraction("19.56")).toBe("0.1956");
    expect(toFraction("1.5")).toBe("0.015");
    expect(toFraction("0.0345")).toBe("0.000345");
    expect(toFraction("100")).toBe("1.00");
    expect(toFraction("0")).toBe("0.00");
  });

  it("is empty for an empty field, so an optional rate stays optional", () => {
    expect(toFraction("")).toBe("");
    expect(toFraction("   ")).toBe("");
  });

  it("round-trips the rates the API sends back", () => {
    expect(toPercent("0.1956")).toBe("19.56");
    expect(toPercent("0.000345")).toBe("0.0345");
    expect(toPercent("0.015")).toBe("1.5");
    expect(toPercent("")).toBe("");
  });
});

describe("validateLoanDraft", () => {
  it("accepts a mortgage with its insurance", () => {
    const draft = loan({
      charges: [
        {
          ...emptyCharge(),
          name: "Seguro de vida deudores",
          basis: "outstanding_balance",
          rate: "0.0345",
        },
        {
          ...emptyCharge(),
          name: "Seguro de incendio",
          basis: "insured_value",
          rate: "0.029",
          base: "350000000",
        },
      ],
    });

    expect(validateLoanDraft(draft)).toEqual([]);
  });

  it("catches a fraction typed where a percentage goes, before the API does", () => {
    const issues = validateLoanDraft(loan({ ratePercent: "1956" }));

    expect(issues.map((issue) => issue.field)).toContain("ratePercent");
  });

  it("refuses a cut day that is not a day of the month", () => {
    expect(validateLoanDraft(loan({ statementDay: "45" }))).toHaveLength(1);
    expect(validateLoanDraft(loan({ statementDay: "" }))).toHaveLength(1);
  });

  it("refuses two charges under one name", () => {
    // They would be one key on the server, so the second would never be
    // charged at all — and nothing on screen would say so.
    const issues = validateLoanDraft(
      loan({
        charges: [
          {
            ...emptyCharge(),
            name: "Seguro",
            basis: "outstanding_balance",
            rate: "0.03",
          },
          { ...emptyCharge(), name: "seguro", basis: "fixed", amount: "1000" },
        ],
      }),
    );

    expect(issues.map((issue) => issue.field)).toContain("charges.1.name");
  });

  it("asks for the insured value only when the charge is quoted on one", () => {
    const onValue = validateLoanDraft(
      loan({
        charges: [
          { ...emptyCharge(), name: "Incendio", basis: "insured_value", rate: "0.029" },
        ],
      }),
    );
    const onBalance = validateLoanDraft(
      loan({
        charges: [
          {
            ...emptyCharge(),
            name: "Vida",
            basis: "outstanding_balance",
            rate: "0.0345",
          },
        ],
      }),
    );

    expect(onValue.map((issue) => issue.field)).toContain("charges.0.base");
    expect(onBalance).toEqual([]);
  });

  it("refuses a term nobody could schedule", () => {
    expect(validateLoanDraft(loan({ termMonths: "0" }))).toHaveLength(1);
    expect(validateLoanDraft(loan({ termMonths: "1200" }))).toHaveLength(1);
  });
});

describe("loanPayload", () => {
  it("sends the fraction, the bare amounts and nothing it was not given", () => {
    const payload = loanPayload(
      loan({
        paymentDay: "20",
        principal: "60000000",
        installment: "2000000",
        charges: [
          {
            ...emptyCharge(),
            name: "Seguro de vida deudores",
            basis: "outstanding_balance",
            rate: "0.0345",
          },
        ],
      }),
    );

    expect(payload.rate).toEqual({ value: "0.1956", basis: "effective_annual" });
    expect(payload.payment_day).toBe(20);
    expect(payload.principal).toBe("60000000");
    expect(payload.charges?.[0]).toEqual({
      name: "Seguro de vida deudores",
      basis: "outstanding_balance",
      rate: "0.000345",
      amount: null,
      base: null,
      charged_to_balance: true,
    });
  });

  it("leaves accrue_from out, so the arithmetic starts today", () => {
    // The trap: a balance somebody just read off their bank already contains
    // every month of interest so far. Charging those again doubles the debt.
    expect(loanPayload(loan()).accrue_from).toBeNull();
    expect(loanPayload(loan({ accrueFrom: "2026-01-15" })).accrue_from).toBe(
      "2026-01-15",
    );
  });
});

describe("validateInvestmentDraft", () => {
  it("accepts a position with no rate, which is how renta variable works", () => {
    const draft = {
      ...emptyInvestmentDraft(),
      openedOn: "2026-01-10",
      statementDay: "10",
    };

    expect(validateInvestmentDraft(draft)).toEqual([]);
    expect(investmentPayload(draft).rate).toBeNull();
  });

  it("refuses withholding on a return nobody computes", () => {
    const issues = validateInvestmentDraft({
      ...emptyInvestmentDraft(),
      openedOn: "2026-01-10",
      statementDay: "10",
      charges: [{ ...emptyCharge(), name: "Retención", basis: "earnings", rate: "4" }],
    });

    expect(issues.map((issue) => issue.field)).toContain("ratePercent");
  });

  it("refuses a maturity before the opening", () => {
    const issues = validateInvestmentDraft({
      ...emptyInvestmentDraft(),
      openedOn: "2026-01-10",
      statementDay: "10",
      maturesOn: "2025-12-10",
    });

    expect(issues.map((issue) => issue.field)).toContain("maturesOn");
  });
});

describe("isFinanceable", () => {
  it("knows which kinds have a cost worth declaring", () => {
    expect(isFinanceable("mortgage")).toBe("loan");
    expect(isFinanceable("loan")).toBe("loan");
    expect(isFinanceable("investment")).toBe("investment");
    // A credit card charges interest too, and is deliberately left out: it is
    // charged on whatever part of the statement went unpaid, which nothing
    // here knows.
    expect(isFinanceable("credit_card")).toBeNull();
    expect(isFinanceable("savings")).toBeNull();
  });
});
