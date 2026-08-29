import { describe, expect, it } from "vitest";
import {
  describeBalance,
  formatMoney,
  formatSignedMoney,
  isZero,
  signOf,
} from "@/lib/money";

/**
 * These are the rules `docs/frontend-integration.md` says the interface may
 * not break, tested where they are decided. The Python half has its own tests
 * for the same rules; this is the half that decides what a person sees.
 */

/**
 * `Intl` separates the symbol from the digits with a no-break space (U+00A0),
 * not an ordinary one. That is its business, not a rule of this app, so the
 * assertions below normalise it and stay readable.
 */
function plain(text: string): string {
  return text.replace(/[\u00A0\u202F]/g, " ");
}

function normalised(reading: ReturnType<typeof describeBalance>) {
  return { ...reading, text: plain(reading.text) };
}

describe("formatMoney", () => {
  it("formats COP without decimals, which is what its smallest unit is", () => {
    expect(plain(formatMoney("919500", "COP"))).toBe("$ 919.500");
  });

  it("keeps two decimals for a currency that has cents", () => {
    expect(plain(formatMoney("45000.50", "USD"))).toBe("US$ 45.000,50");
  });

  it("never loses precision, because it never goes through a float", () => {
    // Number("9007199254740993.99") rounds to ...994. A ledger cannot.
    expect(plain(formatMoney("9007199254740993.99", "USD"))).toBe(
      "US$ 9.007.199.254.740.993,99",
    );
  });

  it("shows what the server said rather than throwing on a bad amount", () => {
    expect(plain(formatMoney("N/A", "COP"))).toBe("N/A COP");
  });

  it("degrades to the raw code rather than throwing on a bad currency", () => {
    // Intl.NumberFormat raises a RangeError here; one odd field must not take
    // a whole screen of balances down with it. Two decimals is the fallback:
    // an unknown currency is far more likely to have cents than not.
    expect(plain(formatMoney("919500", "NOT-A-CURRENCY"))).toBe(
      "919.500,00 NOT-A-CURRENCY",
    );
  });
});

describe("formatSignedMoney", () => {
  it("marks a positive net explicitly", () => {
    expect(plain(formatSignedMoney("2354700", "COP"))).toBe("+$ 2.354.700");
  });

  it("marks a month that spent more than it took in", () => {
    expect(plain(formatSignedMoney("-412000", "COP"))).toBe("-$ 412.000");
  });

  it("leaves zero unsigned", () => {
    expect(plain(formatSignedMoney("0", "COP"))).toBe("$ 0");
  });
});

describe("signOf", () => {
  it.each([
    ["158800", 1],
    ["-158800", -1],
    ["0", 0],
    ["0.00", 0],
    ["-0", 0],
    ["-0.00", 0],
    ["0.01", 1],
    ["-0.01", -1],
  ])("reads %s as %i", (amount, expected) => {
    expect(signOf(amount)).toBe(expected);
  });

  it("agrees with isZero", () => {
    expect(isZero("-0.00")).toBe(true);
    expect(isZero("0.01")).toBe(false);
  });
});

describe("describeBalance", () => {
  it("reads a credit card's positive balance as money owed, sign unflipped", () => {
    // Rule 4: never show a liability with the sign changed. "158800" on a
    // credit card means you owe 158.800.
    expect(normalised(describeBalance("158800", "COP", "liability"))).toEqual({
      text: "$ 158.800",
      tone: "negative",
      label: "Debes",
    });
  });

  it("reads a paid-off card as settled, not as a debt of zero", () => {
    expect(normalised(describeBalance("0", "COP", "liability"))).toEqual({
      text: "$ 0",
      tone: "neutral",
      label: "Al día",
    });
  });

  it("reads an overpaid card as money in your favour", () => {
    expect(normalised(describeBalance("-50000", "COP", "liability"))).toEqual({
      text: "-$ 50.000",
      tone: "positive",
      label: "A favor",
    });
  });

  it("reads an asset balance as available", () => {
    expect(normalised(describeBalance("919500", "COP", "asset"))).toEqual({
      text: "$ 919.500",
      tone: "positive",
      label: "Disponible",
    });
  });

  it("explains a negative asset instead of calling it an error", () => {
    // Legitimate when no opening balance was declared: the history is
    // incomplete, not wrong.
    expect(normalised(describeBalance("-30000", "COP", "asset"))).toEqual({
      text: "-$ 30.000",
      tone: "negative",
      label: "Sin saldo inicial",
    });
  });
});
