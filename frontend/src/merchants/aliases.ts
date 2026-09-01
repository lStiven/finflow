/**
 * What a merchant's aliases are, said in words somebody can act on.
 *
 * An alias is one of the spellings a merchant's name arrives under. The API
 * publishes *why* each one hangs off its merchant (`origin`), and that is the
 * single most useful thing on the review screen: `suggested` is the only one
 * that is a guess, and therefore the only one worth checking. The rest are
 * either a rule that cannot be wrong about the name it matched, or the user's
 * own decision.
 *
 * Same arrangement as `@/merchants/categories`: the enum value is storage and
 * the words are copy, so the labels the API sends are never rendered.
 */

/** A badge and the sentence that explains it. */
export type OriginCopy = {
  label: string;
  hint: string;
  /** `true` for the one origin the user is being asked to check. */
  guess: boolean;
};

const ORIGIN_COPY: Record<string, OriginCopy> = {
  seed: {
    label: "La primera",
    hint: "Es el nombre con el que este comercio apareció por primera vez. De aquí salió todo lo demás.",
    guess: false,
  },
  derived: {
    label: "Misma marca",
    hint: "El mismo nombre con otro ruido alrededor —otra sede, otro número—. Se agrupó por regla, no por conjetura.",
    guess: false,
  },
  suggested: {
    label: "Conjetura",
    hint: "Se parece a una variante de este comercio, pero nadie lo ha confirmado. Si no es el mismo negocio, sácala de aquí.",
    guess: true,
  },
  manual: {
    label: "La pusiste tú",
    hint: "La moviste o la separaste tú. Ninguna regla vuelve a decidir por ella.",
    guess: false,
  },
};

const UNKNOWN_ORIGIN: OriginCopy = {
  label: "Otra",
  hint: "Esta grafía llegó por un camino que esta versión de la app no conoce.",
  guess: false,
};

/**
 * How to describe one alias, or a neutral description for an origin this
 * build has never heard of — a new origin on the server still renders.
 */
export function originCopy(origin: string): OriginCopy {
  return ORIGIN_COPY[origin] ?? UNKNOWN_ORIGIN;
}

/** How many of these the user is actually being asked to look at. */
export function countGuesses(aliases: { origin: string }[]): number {
  return aliases.filter((alias) => originCopy(alias.origin).guess).length;
}

/**
 * `automatic` | `confirmed`, as a person reads them.
 *
 * The API calls the unreviewed state "automatic", which describes how it got
 * there rather than what it means to the reader — and what it means is that
 * nobody has looked at it yet.
 */
export function statusLabel(status: string): string {
  if (status === "confirmed") return "Revisado";
  if (status === "automatic") return "Sin revisar";
  return status;
}

const SORT_COPY: Record<string, string> = {
  last_seen: "Más recientes",
  times_seen: "Más frecuentes",
  name: "Por nombre",
};

export function sortLabel(value: string, catalogLabel = value): string {
  return SORT_COPY[value] ?? catalogLabel;
}

/**
 * "3 grafías", said without the word — nobody outside this codebase calls
 * them that. What the count means to a user is how many different ways this
 * one business writes its name in their alerts.
 */
export function aliasCountLabel(count: number): string {
  return count === 1 ? "1 forma de escribirse" : `${count} formas de escribirse`;
}

/** Sightings of the name. Never money: what was spent is Financial's answer. */
export function timesSeenLabel(times: number): string {
  return times === 1 ? "visto 1 vez" : `visto ${times} veces`;
}
