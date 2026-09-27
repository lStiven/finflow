import { describe, expect, it } from "vitest";
import type { TransferLeg } from "@/api/queries";
import { counterpartName, transferBlurb, transferTitle } from "@/lib/transfers";

/** One side of a transfer whose other side is a row here too. */
function leg(overrides: Partial<TransferLeg> = {}): TransferLeg {
  return {
    id: "abc",
    role: "source",
    external: false,
    counterpart_movement_id: "def",
    counterpart_instrument_kind: "credit_card",
    counterpart_last_four: "1234",
    basis: "stated",
    ...overrides,
  };
}

/**
 * A card paid from another bank, a wallet or cash. Every `counterpart_*`
 * field is null, which is exactly what `external` announces — the shape that
 * used to render `···· null` on three screens.
 */
function loneLeg(overrides: Partial<TransferLeg> = {}): TransferLeg {
  return {
    id: "abc",
    role: "destination",
    external: true,
    counterpart_movement_id: null,
    counterpart_instrument_kind: null,
    counterpart_last_four: null,
    basis: "stated",
    ...overrides,
  };
}

describe("transferTitle", () => {
  it("says where the money went, from the side it left", () => {
    expect(transferTitle(leg(), "credit_card *1234")).toBe(
      "Pago a tu tarjeta de crédito ···· 1234",
    );
  });

  it("says where it came from, on the side it arrived at", () => {
    expect(
      transferTitle(
        leg({
          role: "destination",
          counterpart_instrument_kind: "account",
          counterpart_last_four: "5261",
        }),
        "account *5261",
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

  it("uses the owner's own words when the other side is not here", () => {
    expect(transferTitle(loneLeg(), "Nequi")).toBe("Pago desde Nequi");
  });

  it("names what was paid, on a lone leg that is the side money left", () => {
    expect(transferTitle(loneLeg({ role: "source" }), "Tarjeta Nu")).toBe(
      "Pago a Tarjeta Nu",
    );
  });

  /* The bug this replaced: `···· null` on the dashboard, the list and the detail. */
  it("never renders a placeholder for digits it does not have", () => {
    expect(transferTitle(loneLeg(), "Nequi")).not.toContain("null");
    expect(counterpartName(loneLeg())).toBeNull();
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

  it("says the other side is outside Finflow when it is", () => {
    expect(transferBlurb(loneLeg())).toContain("fuera de Finflow");
    expect(transferBlurb(loneLeg())).toContain("no cuenta como ingreso");
  });

  it("keeps a lone outgoing leg out of spending too", () => {
    expect(transferBlurb(loneLeg({ role: "source" }))).toContain(
      "no cuenta como gasto",
    );
  });
});
