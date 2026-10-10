import { describe, expect, it } from "vitest";
import type { Account, Transaction } from "@/api/queries";
import {
  assignableAccounts,
  buildCorrection,
  type CorrectionForm,
  DETACH,
  isEmpty,
} from "@/lib/correction";

const WHEN = Date.UTC(2026, 7, 20, 17, 0, 0) / 1000;
const RENDERED = "2026-08-20T12:00";

const MOVEMENT = {
  id: "m1",
  amount: "50000",
  counterparty: "TIENDAS ARA",
  currency: "COP",
  direction: "outgoing",
  occurred_at: WHEN,
  account_id: "acc1",
  note: "almuerzo",
  bank: "Bancolombia",
  origin: "manual",
  status: "assigned",
  merchant: null,
  stated: null,
} as unknown as Transaction;

const UNCHANGED: CorrectionForm = {
  amount: "50000",
  counterparty: "TIENDAS ARA",
  currency: "COP",
  occurredAt: RENDERED,
  account: "acc1",
  note: "almuerzo",
};

function build(patch: Partial<CorrectionForm>, at = WHEN) {
  return buildCorrection(MOVEMENT, { ...UNCHANGED, ...patch }, RENDERED, at);
}

describe("buildCorrection", () => {
  it("sends nothing when nothing changed", () => {
    expect(isEmpty(build({}))).toBe(true);
  });

  /**
   * The endpoint refuses an amount without its currency, and treats a
   * currency on its own as no change at all — either alone is a 422, which
   * made correcting just the amount impossible.
   */
  it("sends the currency alongside a changed amount", () => {
    expect(build({ amount: "60000" })).toMatchObject({
      amount: "60000",
      currency: "COP",
    });
  });

  it("sends the amount alongside a changed currency", () => {
    expect(build({ currency: "USD" })).toMatchObject({
      amount: "50000",
      currency: "USD",
    });
  });

  /** `null` means "leave it"; the empty string is the only way to clear one. */
  it("clears a note with an empty string, not null", () => {
    expect(build({ note: "" }).note).toBe("");
  });

  it("leaves the note alone when it did not change", () => {
    expect(build({}).note).toBeUndefined();
    expect(build({ note: "  almuerzo  " }).note).toBeUndefined();
  });

  /**
   * The field holds minutes. Comparing instants would send a seconds-wide
   * "correction" every time the form was opened for something else.
   */
  it("ignores a timestamp the field rounded rather than the user changed", () => {
    expect(build({}, WHEN + 45).occurred_at).toBeUndefined();
  });

  it("sends the timestamp when the field itself changed", () => {
    const moved = Date.UTC(2026, 7, 21, 17, 0, 0) / 1000;
    expect(build({ occurredAt: "2026-08-21T12:00" }, moved).occurred_at).toBe(moved);
  });

  it("detaches instead of naming an account, never both", () => {
    const body = build({ account: DETACH });
    expect(body.detach).toBe(true);
    expect(body.account_id).toBeUndefined();
  });

  it("assigns an account without detaching", () => {
    const body = build({ account: "acc2" });
    expect(body.account_id).toBe("acc2");
    expect(body.detach).toBe(false);
  });
});

describe("assignableAccounts", () => {
  const account = (id: string, currency: string, closed_at: number | null = null) =>
    ({ id, name: id, currency, closed_at }) as unknown as Account;

  it("offers the open accounts in the movement's currency", () => {
    const offered = assignableAccounts({ currency: "COP" }, [
      account("ahorros", "COP"),
      account("dolares", "USD"),
      account("vieja", "COP", 1_700_000_000),
    ]);

    expect(offered.map((each) => each.id)).toEqual(["ahorros"]);
  });

  it("offers nothing when no account could take it", () => {
    expect(
      assignableAccounts({ currency: "USD" }, [account("ahorros", "COP")]),
    ).toEqual([]);
  });
});
