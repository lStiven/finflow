import { CircleDashed, Tag, Utensils } from "lucide-react";
import { describe, expect, it } from "vitest";
import { categoryIcon } from "./categoryIcons";

describe("categoryIcon", () => {
  it("gives a shipped category its own icon", () => {
    expect(categoryIcon("restaurants")).toBe(Utensils);
  });

  it("gives one this person wrote the plain tag", () => {
    expect(categoryIcon("custom:2c5f0d26")).toBe(Tag);
  });

  it("treats a missing category as uncategorized", () => {
    expect(categoryIcon(null)).toBe(CircleDashed);
    expect(categoryIcon("uncategorized")).toBe(CircleDashed);
  });
});
