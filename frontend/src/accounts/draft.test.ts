import { describe, expect, it } from "vitest";
import {
  type AccountDraft,
  emptyDraft,
  issueFor,
  toPayload,
  validateDraft,
} from "@/accounts/draft";

function draft(overrides: Partial<AccountDraft> = {}): AccountDraft {
  return {
    ...emptyDraft(),
    kind: "savings",
    category: "asset",
    name: "Cuenta de ahorros",
    ...overrides,
  };
}

describe("validateDraft", () => {
  it("accepts the shortest valid declaration: a kind and a name", () => {
    expect(validateDraft(draft())).toEqual([]);
  });

  it("refuses a name that is only spaces", () => {
    const issues = validateDraft(draft({ name: "   " }));
    expect(issueFor(issues, "name")).toBeDefined();
  });

  it("refuses a kind nobody chose", () => {
    const issues = validateDraft(draft({ kind: "", category: "" }));
    expect(issueFor(issues, "kind")).toBeDefined();
  });

  /*
   * The rule that costs the most when it is wrong: matching on one half of an
   * instrument would merge two real accounts, so the API refuses it and this
   * says so before the round trip.
   */
  it("refuses an instrument kind without its digits", () => {
    const issues = validateDraft(
      draft({ bank: "Bancolombia", instrumentKind: "debit_card" }),
    );
    expect(issueFor(issues, "lastFour")).toBeDefined();
  });

  it("refuses digits without an instrument kind", () => {
    const issues = validateDraft(draft({ bank: "Bancolombia", lastFour: "0530" }));
    expect(issueFor(issues, "instrumentKind")).toBeDefined();
  });

  it("refuses an instrument with no bank behind it", () => {
    const issues = validateDraft(
      draft({ instrumentKind: "debit_card", lastFour: "0530" }),
    );
    expect(issueFor(issues, "bank")).toBeDefined();
  });

  it("takes a bank on its own — a mortgage has one and no card", () => {
    expect(validateDraft(draft({ kind: "mortgage", bank: "Davivienda" }))).toEqual([]);
  });

  it("refuses last four that are not digits", () => {
    const issues = validateDraft(
      draft({ bank: "Bancolombia", instrumentKind: "debit_card", lastFour: "05e0" }),
    );
    expect(issueFor(issues, "lastFour")).toBeDefined();
  });

  it("takes more than four digits: the key normalizes to the trailing ones", () => {
    expect(
      validateDraft(
        draft({
          bank: "Bancolombia",
          instrumentKind: "account",
          lastFour: "12345678",
        }),
      ),
    ).toEqual([]);
  });

  it("refuses a credit limit on an account that owes nothing", () => {
    const issues = validateDraft(draft({ creditLimit: "12000000" }));
    expect(issueFor(issues, "creditLimit")).toBeDefined();
  });

  it("takes a credit limit on a liability", () => {
    expect(
      validateDraft(
        draft({
          kind: "credit_card",
          category: "liability",
          creditLimit: "12000000",
          openingBalance: "200000",
        }),
      ),
    ).toEqual([]);
  });

  it.each(["-1000", "1.000.000", "12,5", "abc", "+50"])(
    "refuses %s as an amount",
    (amount) => {
      const issues = validateDraft(draft({ openingBalance: amount }));
      expect(issueFor(issues, "openingBalance")).toBeDefined();
    },
  );

  it("takes an amount with decimals", () => {
    expect(validateDraft(draft({ openingBalance: "45000.50" }))).toEqual([]);
  });
});

describe("toPayload", () => {
  it("sends null rather than empty strings for what was left blank", () => {
    expect(toPayload(draft())).toEqual({
      name: "Cuenta de ahorros",
      kind: "savings",
      currency: "COP",
      bank: null,
      instrument_kind: null,
      last_four: null,
      opening_balance: null,
      credit_limit: null,
    });
  });

  it("trims what a person typed", () => {
    const payload = toPayload(draft({ name: "  Ahorros  ", bank: "  Bancolombia  " }));
    expect(payload.name).toBe("Ahorros");
    expect(payload.bank).toBe("Bancolombia");
  });

  it("keeps money as the string it was typed as", () => {
    const payload = toPayload(
      draft({ kind: "credit_card", category: "liability", openingBalance: "200000" }),
    );
    expect(payload.opening_balance).toBe("200000");
  });

  it("carries the instrument when both halves are there", () => {
    const payload = toPayload(
      draft({
        bank: "Bancolombia",
        instrumentKind: "account",
        lastFour: "5261",
      }),
    );
    expect(payload.instrument_kind).toBe("account");
    expect(payload.last_four).toBe("5261");
  });

  /*
   * Reachable by choosing a card, typing a limit and going back to change the
   * kind: the field is hidden again but the draft still holds what was typed.
   */
  it("drops a credit limit left over from a kind that was changed", () => {
    const payload = toPayload(
      draft({ kind: "savings", category: "asset", creditLimit: "12000000" }),
    );
    expect(payload.credit_limit).toBeNull();
  });
});
