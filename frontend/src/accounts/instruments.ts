/**
 * Reading the keys an account's alerts arrive under.
 *
 * `AccountResponse.instruments` is the account's own matching keys, and the
 * API publishes them as the strings it stores: length-prefixed triples,
 * `11:bancolombia|10:debit_card|4:0530|`. Length-prefixed rather than merely
 * delimited because `bank` comes out of an untrusted email and a crafted
 * separator inside it would otherwise let two different cards canonicalize to
 * the same key.
 *
 * Decoding it here is a coupling to a storage format, and it is deliberate
 * and bounded: nothing decides anything from the result — it is shown, and a
 * string this reader cannot parse is shown whole rather than mangled. The
 * better fix lives on the other side (publishing bank, kind and last four as
 * fields), and until then this is the only place that knows the shape.
 */

import { instrumentLabel } from "@/accounts/kinds";

export type Instrument = {
  bank: string;
  /** The bank's word for the thing, e.g. `debit_card`. Free text by design. */
  kind: string;
  lastFour: string;
};

/** The three parts, or null for anything that is not one of these keys. */
export function parseInstrument(value: string): Instrument | null {
  const parts: string[] = [];
  let cursor = 0;

  while (cursor < value.length) {
    const colon = value.indexOf(":", cursor);
    if (colon === -1) return null;

    const length = Number(value.slice(cursor, colon));
    // `Number("")` is 0 and `Number(" 1")` is 1, so the digits are checked as
    // text rather than trusted to the conversion.
    if (!/^\d+$/.test(value.slice(cursor, colon))) return null;

    const start = colon + 1;
    const end = start + length;
    if (value[end] !== "|") return null;

    parts.push(value.slice(start, end));
    cursor = end + 1;
  }

  if (parts.length !== 3) return null;

  const [bank, kind, lastFour] = parts;
  // The length check above settles this; indexing is checked here rather than
  // inferred, and a cast to say so would be the same claim with less safety.
  if (bank === undefined || kind === undefined || lastFour === undefined) return null;

  return { bank, kind, lastFour };
}

/** One instrument as it reads on a card: `Bancolombia · Tarjeta débito ···· 0530`. */
export function describeInstrument(value: string): string {
  const parsed = parseInstrument(value);
  if (parsed === null) return value;

  const bank = parsed.bank.charAt(0).toUpperCase() + parsed.bank.slice(1);
  return `${bank} · ${instrumentLabel(parsed.kind)} ···· ${parsed.lastFour}`;
}
