import { describe, expect, it } from "vitest";
import {
  lastAliasBlocker,
  MAX_NAME_LENGTH,
  merchantNameIssue,
  newMerchantNameIssue,
} from "@/merchants/edits";

describe("merchantNameIssue", () => {
  it("accepts a name", () => {
    expect(merchantNameIssue("Éxito")).toBeUndefined();
  });

  it("refuses an empty one, and whitespace is empty", () => {
    expect(merchantNameIssue("")).toBeDefined();
    expect(merchantNameIssue("   ")).toBeDefined();
  });

  // The backend's ceiling, said before the round trip rather than after a 422.
  it("refuses a name past the API's ceiling", () => {
    expect(merchantNameIssue("a".repeat(MAX_NAME_LENGTH))).toBeUndefined();
    expect(merchantNameIssue("a".repeat(MAX_NAME_LENGTH + 1))).toBeDefined();
  });
});

describe("newMerchantNameIssue", () => {
  /*
   * The one difference from the rule above: `display_name` is optional on a
   * split, and leaving it out is how you ask the backend to name the new
   * merchant after the spelling itself.
   */
  it("accepts an empty name, because the backend names it from the spelling", () => {
    expect(newMerchantNameIssue("")).toBeUndefined();
    expect(newMerchantNameIssue("  ")).toBeUndefined();
  });

  it("still refuses one past the ceiling", () => {
    expect(newMerchantNameIssue("a".repeat(MAX_NAME_LENGTH + 1))).toBeDefined();
  });
});

describe("lastAliasBlocker", () => {
  /*
   * A merchant with no spellings does not exist, so the API answers 409 —
   * for moving the last one *and* for splitting it, which is the half that
   * is easy to miss. The screen has to refuse first: an error after the click
   * reads like a bug, and its message talks about aliases, a word the screen
   * never uses.
   */
  it("blocks taking away the only spelling a merchant has", () => {
    expect(lastAliasBlocker(1)).toBeDefined();
    expect(lastAliasBlocker(0)).toBeDefined();
  });

  it("allows it as soon as something would be left behind", () => {
    expect(lastAliasBlocker(2)).toBeUndefined();
  });

  // Both ways out are named, because both are refused by the same rule.
  it("points at the two things that do work instead", () => {
    const blocker = lastAliasBlocker(1) ?? "";
    expect(blocker).toContain("nombre");
    expect(blocker).toContain("fusión");
  });
});
