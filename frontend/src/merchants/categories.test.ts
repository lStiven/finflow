import { describe, expect, it } from "vitest";
import type { SummaryGroup } from "@/api/queries";
import {
  categoryGroupLabel,
  categoryLabel,
  isUncategorized,
} from "@/merchants/categories";

function group(key: string | null, label: string): SummaryGroup {
  return { key, label, movements: 1, totals: [] };
}

describe("categoryLabel", () => {
  it("says the category in Spanish, from its stable value", () => {
    expect(categoryLabel("groceries", "Groceries")).toBe("Mercado");
  });

  /*
   * The point of the fallback: a category added on the server must still
   * render in a filter rather than disappear from it, and the catalogue's own
   * label is a real word where the raw value is not.
   */
  it("falls back to the catalogue's label for a category it has never heard of", () => {
    expect(categoryLabel("pets", "Pets")).toBe("Pets");
  });

  it("falls back to the value when there is no label either", () => {
    expect(categoryLabel("pets")).toBe("pets");
  });
});

describe("categoryGroupLabel", () => {
  /*
   * The bucket `/financial/summary` fills with everything it could not
   * attribute is the one whose label arrives as English prose. Reading the
   * key rather than the label is what keeps that off the dashboard.
   */
  it("names the unattributed bucket in Spanish", () => {
    expect(categoryGroupLabel(group(null, "Unattributed"))).toBe("Sin comercio");
  });

  it("reads the category out of the key, never out of the label", () => {
    expect(categoryGroupLabel(group("restaurants", "restaurants"))).toBe(
      "Restaurantes",
    );
  });
});

describe("isUncategorized", () => {
  it("reconoce la categoría por defecto", () => {
    expect(isUncategorized("uncategorized")).toBe(true);
  });

  it("trata la ausencia de categoría igual que la categoría por defecto", () => {
    expect(isUncategorized(null)).toBe(true);
    expect(isUncategorized(undefined)).toBe(true);
  });

  it("no se lleva por delante una categoría de verdad", () => {
    expect(isUncategorized("groceries")).toBe(false);
    expect(isUncategorized("other")).toBe(false);
  });
});
