import { describe, expect, it } from "vitest";
import { describeInstrument, parseInstrument } from "@/accounts/instruments";

describe("parseInstrument", () => {
  it("reads the three parts of a key", () => {
    expect(parseInstrument("11:bancolombia|10:debit_card|4:0530|")).toEqual({
      bank: "bancolombia",
      kind: "debit_card",
      lastFour: "0530",
    });
  });

  /*
   * The whole reason the format is length-prefixed: a bank name out of an
   * untrusted email may contain the separator, and the length says how far to
   * read rather than the delimiter.
   */
  it("reads a part that contains the separator", () => {
    expect(parseInstrument("6:a|b|c:|7:account|4:1234|")).toEqual({
      bank: "a|b|c:",
      kind: "account",
      lastFour: "1234",
    });
  });

  it.each([
    ["", "empty"],
    ["bancolombia", "no prefixes at all"],
    ["11:bancolombia|", "one part instead of three"],
    ["99:bancolombia|10:debit_card|4:0530|", "a length past the end"],
    ["3:bancolombia|10:debit_card|4:0530|", "a length that does not reach the bar"],
    ["11:bancolombia|10:debit_card|4:0530|4:9999|", "four parts"],
    [" 11:bancolombia|10:debit_card|4:0530|", "padding before the length"],
  ])("returns null for %s (%s)", (value) => {
    expect(parseInstrument(value)).toBeNull();
  });
});

describe("describeInstrument", () => {
  it("says it the way a person would read it", () => {
    expect(describeInstrument("11:bancolombia|10:debit_card|4:0530|")).toBe(
      "Bancolombia · Tarjeta débito ···· 0530",
    );
  });

  it("keeps the bank's own word for an instrument this build never heard of", () => {
    expect(describeInstrument("11:bancolombia|6:wallet|4:0530|")).toBe(
      "Bancolombia · wallet ···· 0530",
    );
  });

  it("shows anything it cannot read whole rather than mangled", () => {
    expect(describeInstrument("algo raro")).toBe("algo raro");
  });
});
