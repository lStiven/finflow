import { describe, expect, it } from "vitest";
import type { Account } from "@/api/queries";
import { OUTSIDE, resolveSides, sameBank, UNNAMED_OUTSIDE } from "@/transfers/sides";

function account(id: string, bank: string | null): Account {
  return { id, bank } as Account;
}

describe("resolveSides", () => {
  it("writes both sides when both are accounts, leaving from the first", () => {
    expect(resolveSides("savings", "card", "")).toEqual({
      ok: true,
      body: { role: "source", account_id: "savings", counterpart_account_id: "card" },
    });
  });

  it("pays a card from outside Finflow as the side arriving on it", () => {
    // The card's debt is what falls: the destination, never the source.
    expect(resolveSides(OUTSIDE, "card", "Nequi")).toEqual({
      ok: true,
      body: { role: "destination", account_id: "card", counterparty: "Nequi" },
    });
  });

  it("moves money out to somewhere outside as the side leaving", () => {
    expect(resolveSides("savings", OUTSIDE, "  Davivienda ")).toEqual({
      ok: true,
      body: { role: "source", account_id: "savings", counterparty: "Davivienda" },
    });
  });

  it("names an outside side it was not told the name of", () => {
    const sides = resolveSides(OUTSIDE, "card", "   ");

    expect(sides.ok && sides.body.counterparty).toBe(UNNAMED_OUTSIDE);
  });

  it("never sends a name when both sides are accounts", () => {
    const sides = resolveSides("savings", "card", "Nequi");

    expect(sides.ok && "counterparty" in sides.body).toBe(false);
  });

  it("refuses «Otro» on both sides, which would move no balance", () => {
    expect(resolveSides(OUTSIDE, OUTSIDE, "").ok).toBe(false);
  });

  it("refuses one account on both sides", () => {
    expect(resolveSides("card", "card", "").ok).toBe(false);
  });

  it("waits for both sides to be chosen", () => {
    expect(resolveSides("", "card", "").ok).toBe(false);
    expect(resolveSides("savings", "", "").ok).toBe(false);
  });
});

describe("sameBank", () => {
  const accounts = [
    account("savings", "bancolombia"),
    account("card", "Bancolombia "),
    account("nu", "nu"),
    account("cash", null),
  ];

  it("spots two accounts at one bank, however the name was typed", () => {
    expect(sameBank("savings", "card", accounts)).toBe(true);
  });

  it("is false across banks", () => {
    expect(sameBank("savings", "nu", accounts)).toBe(false);
  });

  it("is false when a side is outside or names no bank", () => {
    expect(sameBank("savings", OUTSIDE, accounts)).toBe(false);
    expect(sameBank("cash", "cash", [account("cash", null)])).toBe(false);
  });
});
