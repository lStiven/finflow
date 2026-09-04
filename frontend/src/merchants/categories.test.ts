import { describe, expect, it } from "vitest";
import { ApiError } from "@/api/errors";
import type { CategoryOption, SummaryGroup } from "@/api/queries";
import {
  categoryGroupLabel,
  categoryLabel,
  categoryLabels,
  createCategoryError,
  isNameTaken,
  isUncategorized,
  labelFrom,
} from "@/merchants/categories";

/** What `GET /merchants/categories` answers, in the two shapes it comes in. */
const VOCABULARY: CategoryOption[] = [
  { value: "groceries", label: "Groceries", custom: false },
  { value: "custom:mascotas", label: "Mascotas", custom: true },
];

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
  it("falls back to the label that came with it for one it never heard of", () => {
    expect(categoryLabel("pets", "Pets")).toBe("Pets");
  });

  /*
   * The user's own categories are exactly this case, and always will be: no
   * table in this bundle can know what somebody typed yesterday.
   */
  it("shows a category the user wrote under their own name for it", () => {
    expect(categoryLabel("custom:mascotas", "Mascotas")).toBe("Mascotas");
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

  /*
   * A summary bucket's label *is* its key, so a category somebody wrote
   * arrives as `custom:mascotas` and there is nowhere else to read the name
   * from.
   */
  it("names a bucket of a category the user wrote", () => {
    expect(
      categoryGroupLabel(
        group("custom:mascotas", "custom:mascotas"),
        categoryLabels(VOCABULARY),
      ),
    ).toBe("Mascotas");
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

describe("labelFrom", () => {
  const labels = categoryLabels(VOCABULARY);

  /*
   * The two-argument `categoryLabel` needs whoever calls it to already hold
   * the right label. A row that holds nothing but a value — a chip beside a
   * merchant, a slice of a donut — has only this.
   */
  it("names a category the user wrote, given the list the API answered", () => {
    expect(labelFrom(labels, "custom:mascotas")).toBe("Mascotas");
  });

  it("still prefers this app's Spanish over the API's English", () => {
    expect(labelFrom(labels, "groceries")).toBe("Mercado");
  });

  /*
   * Reachable while the vocabulary is still loading, and for a merchant filed
   * under a category that has since gone. A raw slug on screen is bad; a
   * crash or a blank is worse.
   */
  it("falls back to the value for one that is in neither place", () => {
    expect(labelFrom(labels, "custom:bici")).toBe("custom:bici");
  });
});

describe("isNameTaken", () => {
  /*
   * The case the server cannot see. Its uniqueness rule is about keys, and
   * the shipped categories carry English labels on the wire — so "Mercado"
   * keys as `custom:mercado`, collides with nothing, and lands in the
   * dropdown directly under the shipped `groceries`, which this app also
   * draws as "Mercado".
   */
  it("refuses a name this app already shows, even in the other language", () => {
    expect(isNameTaken("Mercado", VOCABULARY)).toBe(true);
    expect(isNameTaken("Groceries", VOCABULARY)).toBe(true);
  });

  it("folds case, accents and stray spaces the way the server does", () => {
    expect(isNameTaken("  mascotas ", VOCABULARY)).toBe(true);
    expect(isNameTaken("MASCÓTAS", VOCABULARY)).toBe(true);
  });

  it("lets a name nothing on screen answers to through", () => {
    expect(isNameTaken("Bici", VOCABULARY)).toBe(false);
  });

  it("says nothing about an empty name, which the form refuses anyway", () => {
    expect(isNameTaken("   ", VOCABULARY)).toBe(false);
  });

  /*
   * Renaming "mascotas" to "Mascotas" is a correction, not a collision. The
   * rename form leaves the category being edited out of the list for exactly
   * this, so a category is never refused by itself.
   */
  it("does not stand in the way of a category correcting its own spelling", () => {
    const others = VOCABULARY.filter((c) => c.value !== "custom:mascotas");

    expect(isNameTaken("Mascotas", others)).toBe(false);
  });
});

describe("createCategoryError", () => {
  /*
   * 409 is not a failure of the form: the name they want is already taken.
   * Saying "no se pudo guardar" would send somebody looking for a bug.
   */
  it("says a conflict is a name already in use", () => {
    expect(createCategoryError(new ApiError(409, { detail: "already exists" }))).toBe(
      "Ya tienes una categoría con ese nombre.",
    );
  });

  it("falls back to a plain apology for anything else", () => {
    expect(createCategoryError(new ApiError(500, { detail: "boom" }))).toBe(
      "No se pudo guardar la categoría.",
    );
    expect(createCategoryError("something that is not an Error")).toBe(
      "No se pudo guardar la categoría.",
    );
  });

  /*
   * The status is the only place the code lives: `ApiError.message` is the
   * API's own `detail` string, so a check against the text would look right
   * and never fire.
   */
  it("does not go looking for the code in the message", () => {
    expect(createCategoryError(new Error("409"))).toBe(
      "No se pudo guardar la categoría.",
    );
  });
});
