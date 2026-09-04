/**
 * What each merchant category is called on screen, in Spanish.
 *
 * The vocabulary has two halves and they are named differently on purpose.
 * The ones the app ships arrive from `GET /merchants/categories` with English
 * labels by contract — `value` is the stable half, so a client that shows
 * anything the reader reads builds its own words from it, which is the table
 * below. Same arrangement as `@/accounts/kinds`, and for the same reason: the
 * values are storage, the labels are copy, and only one of the two is allowed
 * to change.
 *
 * The other half is whatever this person wrote for themselves. Their label is
 * their own text, nobody gets to restate it, and no table here could know it —
 * so it is carried through from the API, which is what every `fallback` and
 * every `labels` map in this module is for.
 *
 * Two labels come from neither half. `/financial/summary` buckets everything
 * it could not attribute under a null key, and the label it sends with it is
 * English prose (`Unattributed`, `Unassigned`) rather than a value — so those
 * are translated here too, by the one helper that reads a summary group.
 */

import { ApiError } from "@/api/errors";
import type { CategoryOption, SummaryGroup } from "@/api/queries";

/**
 * The category a merchant has when nobody has chosen one.
 *
 * Worth naming because a list can then leave it out: on a screen where almost
 * nothing is categorised yet, "Sin categoría" beside every single row is the
 * chip that pushes the useful ones off the edge of a phone.
 */
export const UNCATEGORIZED = "uncategorized";

/**
 * How long a name of one's own may be, mirroring the server's own rule so the
 * input stops at the same place the API would refuse.
 *
 * It is read in a dropdown, in a chip beside a movement and in a chart legend
 * on a phone, and a long one makes all three unreadable — by pushing the
 * useful ones off the edge rather than by being wrong.
 */
export const MAX_CATEGORY_LABEL_LENGTH = 24;

/** Whether this category is the default rather than somebody's decision. */
export function isUncategorized(category: string | null | undefined): boolean {
  return category === null || category === undefined || category === UNCATEGORIZED;
}

const CATEGORY_COPY: Record<string, string> = {
  uncategorized: "Sin categoría",
  groceries: "Mercado",
  restaurants: "Restaurantes",
  transport: "Transporte",
  fuel: "Combustible",
  shopping: "Compras",
  entertainment: "Entretenimiento",
  subscriptions: "Suscripciones",
  utilities: "Servicios",
  health: "Salud",
  education: "Educación",
  travel: "Viajes",
  fees: "Comisiones",
  transfers: "Transferencias",
  income: "Ingresos",
  other: "Otros",
};

/**
 * A category as it reads, or the label that came with it for one this build
 * has never heard of — which is every category the user wrote, and also a
 * shipped one added on the server after this bundle was built. Either way it
 * renders instead of disappearing from a filter as a raw slug.
 */
export function categoryLabel(value: string, fallback = value): string {
  return CATEGORY_COPY[value] ?? fallback;
}

/** Their own names, keyed by value, for the screens that only hold a value. */
export function categoryLabels(
  categories: readonly CategoryOption[],
): Record<string, string> {
  return Object.fromEntries(
    categories.map((category) => [category.value, category.label]),
  );
}

/**
 * A category as it reads, given the list the API answered.
 *
 * The two-argument form above needs whoever calls it to already hold the right
 * label. This is for a row that holds nothing but a value — a chip beside a
 * merchant, a slice of a donut — where the map is the only way to know that
 * `custom:mascotas` is called "Mascotas".
 */
export function labelFrom(labels: Record<string, string>, value: string): string {
  return categoryLabel(value, labels[value] ?? value);
}

/**
 * What to call a bucket of `/financial/summary`, grouped by category.
 *
 * `key` is null for everything with no merchant behind it, and that is the
 * bucket whose label arrives in English. With a key, the key *is* the category
 * value: the copy above answers for the shipped ones, and `labels` — when the
 * screen has it — for the ones this person wrote.
 */
export function categoryGroupLabel(
  group: SummaryGroup,
  labels: Record<string, string> = {},
): string {
  return group.key === null ? "Sin comercio" : labelFrom(labels, group.key);
}

/**
 * Whether this name is already on screen, under any of its spellings.
 *
 * The server's uniqueness rule is about *keys*, and it cannot see this case:
 * the shipped categories carry English labels on the wire, so a category
 * somebody names "Mercado" keys as `custom:mercado`, collides with nothing,
 * and lands in the dropdown directly under the shipped `groceries` — which
 * this app also draws as "Mercado". Two identical entries, and no way to tell
 * which one their spending went to.
 *
 * Only this side knows the Spanish, so only this side can refuse it. Folded
 * the way the server folds a name into a key, so "mascotas" and "Mascotas"
 * are the same answer here too.
 */
export function isNameTaken(
  name: string,
  categories: readonly CategoryOption[],
): boolean {
  const wanted = foldName(name);

  if (wanted === "") return false;

  return categories.some(
    (category) =>
      foldName(category.label) === wanted ||
      foldName(categoryLabel(category.value, category.label)) === wanted,
  );
}

function foldName(value: string): string {
  return value
    .normalize("NFD")
    .replace(/\p{Diacritic}/gu, "")
    .toLowerCase()
    .split(/\s+/)
    .filter(Boolean)
    .join(" ");
}

/**
 * What went wrong creating or renaming a category, in the reader's words.
 *
 * 409 is the interesting one and it is not a failure of the form: the name
 * they want is already one of theirs. Read off `status` rather than out of
 * the message — `ApiError.message` is the API's own `detail` string and never
 * carries the code, so matching on the text would silently never fire.
 */
export function createCategoryError(cause: unknown): string {
  if (cause instanceof ApiError && cause.status === 409) {
    return "Ya tienes una categoría con ese nombre.";
  }

  return "No se pudo guardar la categoría.";
}
