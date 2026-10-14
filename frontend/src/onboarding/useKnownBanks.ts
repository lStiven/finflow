import { useSuspenseQuery } from "@tanstack/react-query";
import { ingestionCatalogQuery, type KnownBank } from "@/api/queries";

/**
 * The banks Finflow reads with a parser of its own, as the backend lists them.
 *
 * Suspends rather than falling back: if the catalogue cannot be read, the
 * route's error screen offers a retry. A list kept here as a backup is the
 * second copy this hook exists to remove.
 */
export function useKnownBanks(): readonly KnownBank[] {
  return useSuspenseQuery(ingestionCatalogQuery).data.known_banks;
}
