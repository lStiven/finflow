import { describe, expect, it } from "vitest";
import { attentionOf, watchedCount } from "@/accounts/attention";
import type { Account } from "@/api/queries";

const SAVINGS = {
  id: "acc-savings",
  name: "Ahorros",
  kind: "savings",
  category: "asset",
  informational: false,
  currency: "COP",
  balance: "1000000",
  closed_at: null,
  instruments: [],
  loan: null,
  investment: null,
} as unknown as Account;

const account = (patch: Partial<Account>) => ({ ...SAVINGS, ...patch }) as Account;

describe("what an account still needs", () => {
  it("asks for a card when an account that emails has none linked", () => {
    expect(attentionOf(SAVINGS)).toEqual({ kind: "link-alerts" });
  });

  it("asks nothing once something is linked", () => {
    expect(
      attentionOf(account({ instruments: ["11:bancolombia|7:account|4:0530|"] })),
    ).toBeNull();
  });

  /* Cash and a mortgage never email: asking to link them is asking the impossible. */
  it("never asks to link what never emails", () => {
    expect(attentionOf(account({ kind: "cash" }))).toBeNull();
  });

  /* A loan with no rate is wrong every month; that comes before anything else. */
  it("asks for the terms of a loan or an investment first", () => {
    expect(attentionOf(account({ kind: "mortgage", category: "liability" }))).toEqual({
      kind: "terms",
      shape: "loan",
    });
    expect(attentionOf(account({ kind: "investment" }))).toEqual({
      kind: "terms",
      shape: "investment",
    });
  });

  it("asks nothing of a loan whose terms exist and that never emails", () => {
    expect(
      attentionOf(
        account({ kind: "loan", category: "liability", loan: {} as Account["loan"] }),
      ),
    ).toBeNull();
  });

  it("asks nothing of a closed account", () => {
    expect(attentionOf(account({ closed_at: 1_700_000_000 }))).toBeNull();
  });
});

describe("watched accounts", () => {
  it("counts the open ones kept outside the totals", () => {
    expect(
      watchedCount([
        SAVINGS,
        account({ id: "l", kind: "loan", informational: true }),
        account({ id: "m", kind: "mortgage", informational: true, closed_at: 1 }),
      ]),
    ).toBe(1);
  });
});
