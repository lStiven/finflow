/**
 * The approved senders as the terms of one Gmail `From` criterion: a domain
 * becomes `@domain`, which matches any address there, and an address stays
 * as it is. Built from the same list the intake accepts; whatever extra
 * Gmail's matching lets through, that list still discards.
 */
export function gmailFromTerms(domains: string[], addresses: string[]): string[] {
  return [...domains.map((domain) => `@${domain}`), ...addresses];
}

/** The same terms joined with `OR`, so one filter forwards every bank. */
export function gmailFromFilter(domains: string[], addresses: string[]): string {
  return gmailFromTerms(domains, addresses).join(" OR ");
}
