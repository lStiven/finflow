/**
 * What each merchant category is called on screen, in Spanish.
 *
 * The API publishes the vocabulary (`GET /merchants/catalog`) and its labels
 * are English by contract — `value` is the stable half, so a client that shows
 * anything the reader reads builds its own words from it. Same arrangement as
 * `@/accounts/kinds`, and for the same reason: the enum values are storage,
 * the labels are copy, and only one of the two is allowed to change.
 *
 * Two of these do not come from the enum at all. `/financial/summary` buckets
 * everything it could not attribute under a null key, and the label it sends
 * with it is English prose (`Unattributed`, `Unassigned`) rather than a value
 * — so those are translated here too, by the one helper that reads a summary
 * group.
 */

import type { SummaryGroup } from "@/api/queries";

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
 * A category as it reads, or the API's own label for one this build has never
 * heard of — a category added on the server still renders instead of
 * disappearing from a filter.
 */
export function categoryLabel(value: string, catalogLabel = value): string {
  return CATEGORY_COPY[value] ?? catalogLabel;
}

/**
 * What to call a bucket of `/financial/summary`, grouped by category.
 *
 * `key` is null for everything with no merchant behind it, and that is the
 * bucket whose label arrives in English. With a key, the key *is* the category
 * value, so the copy above answers it and the server's label is never read.
 */
export function categoryGroupLabel(group: SummaryGroup): string {
  return group.key === null ? "Sin comercio" : categoryLabel(group.key);
}
