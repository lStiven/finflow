import { describe, expect, it } from "vitest";
import type { TransferLeg } from "@/api/queries";
import { counterpartName, transferBlurb, transferTitle } from "@/lib/transfers";

function leg(overrides: Partial<TransferLeg> = {}): TransferLeg {
  return {
    id: "abc",
    role: "source",
    counterpart_movement_id: "def",
    counterpart_instrument_kind: "credit_card",
    counterpart_last_four: "1234",
    ...overrides,
  };
}

describe("transferTitle", () => {
  it("says where the money went, from the side it left", () => {
    expect(transferTitle(leg())).toBe("Pago a tu tarjeta de crédito ···· 1234");
  });

  it("says where it came from, on the side it arrived at", () => {
    expect(
      transferTitle(
        leg({
          role: "destination",
          counterpart_instrument_kind: "account",
          counterpart_last_four: "5261",
        }),
      ),
    ).toBe("Pago desde tu cuenta ···· 5261");
  });

  /*
   * The instrument vocabulary lives on the server and can grow. An unknown
   * word still has to read as somebody's own account rather than as a shop.
   */
  it("falls back to something true for an instrument it has no words for", () => {
    expect(counterpartName(leg({ counterpart_instrument_kind: "wallet" }))).toBe(
      "otra cuenta tuya",
    );
  });
});

describe("transferBlurb", () => {
  it("explains the side that left as not being spending", () => {
    expect(transferBlurb(leg())).toContain("no cuenta como gasto");
  });

  it("explains the side that arrived as not being income", () => {
    expect(transferBlurb(leg({ role: "destination" }))).toContain(
      "no cuenta como ingreso",
    );
  });
});
