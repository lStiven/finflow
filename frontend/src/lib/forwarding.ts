/**
 * The approved senders as one Gmail `From` criterion: a domain becomes
 * `@domain`, which matches any address there, and `OR` joins them, so one
 * filter forwards every bank. Built from the same list the intake accepts;
 * whatever extra Gmail's matching lets through, that list still discards.
 */
export function gmailFromFilter(domains: string[], addresses: string[]): string {
  return [...domains.map((domain) => `@${domain}`), ...addresses].join(" OR ");
}
