import { describe, expect, it } from "vitest";
import { balanceIssue, creditLimitIssue, nameIssue } from "@/accounts/edits";

describe("balanceIssue", () => {
  it.each(["0", "158800", "1234.56"])("accepts %s", (value) => {
    expect(balanceIssue(value)).toBeUndefined();
  });

  /*
   * The one difference from the opening balance on the "declare an account"
   * form: an overdrawn account and an overpaid card are both real, and the
   * endpoint takes a signed figure precisely so they can be stated.
   */
  it("accepts a negative balance", () => {
    expect(balanceIssue("-42000")).toBeUndefined();
  });

  it("refuses an empty figure: restating means saying a number", () => {
    expect(balanceIssue("  ")).toBeDefined();
  });

  it.each(["1.234.567", "1,5", "abc", "1e5"])("refuses %s", (value) => {
    expect(balanceIssue(value)).toBeDefined();
  });
});

describe("creditLimitIssue", () => {
  it("accepts an empty limit, which is how a limit is cleared", () => {
    expect(creditLimitIssue("")).toBeUndefined();
  });

  it("accepts a positive limit", () => {
    expect(creditLimitIssue("5000000")).toBeUndefined();
  });

  it("refuses a negative one: a ceiling on what may be owed is never below zero", () => {
    expect(creditLimitIssue("-1")).toBeDefined();
  });
});

describe("nameIssue", () => {
  it("accepts a name", () => {
    expect(nameIssue("Tarjeta Bancolombia")).toBeUndefined();
  });

  it("refuses one that is only spaces, which is what the backend refuses", () => {
    expect(nameIssue("   ")).toBeDefined();
  });

  it("refuses one past the length the API takes", () => {
    expect(nameIssue("a".repeat(121))).toBeDefined();
  });
});
