/**
 * Banks, as the person choosing them thinks of them, over the two lists the
 * server actually stores.
 *
 * The API knows approved domains and approved addresses and nothing else — no
 * bank names. A known bank is the set of domains its parser recognises; any
 * other approval is shown as what it is, the address or domain somebody
 * typed. Whatever is drawn here, the server's list is still the only thing
 * that decides what is read: this module never claims a bank is approved
 * that the list does not approve.
 */

import { gmailFromTerms } from "@/lib/forwarding";

export type Senders = { domains: string[]; addresses: string[] };

export type KnownBank = {
  id: string;
  name: string;
  /** What the parser writes into a movement's `bank`, lowercased. */
  parserName: string;
  domains: readonly string[];
};

/**
 * The banks with a deterministic parser today, and the domains each one
 * actually sends from — the same ones the backend's parser registry lists.
 *
 * Bancolombia runs several alert subdomains, plus `bancolombia.com.co`, which
 * is the one a transfer between the owner's own accounts arrives from.
 * Approving only the alert domains accepts card purchases while silently
 * dropping every transfer, which is why a bank reads as chosen only when all
 * of its domains are approved.
 *
 * Lulo's message ids come from Amazon SES, which is shared with every other
 * SES customer — the From domain is what identifies the bank.
 */
export const KNOWN_BANKS: readonly KnownBank[] = [
  {
    id: "bancolombia",
    name: "Bancolombia",
    parserName: "bancolombia",
    domains: [
      "an.notificacionesbancolombia.com",
      "notificacionesbancolombia.com",
      "ayn.notificacionesbancolombia.com",
      "bancolombia.com.co",
    ],
  },
  {
    id: "lulo",
    name: "Lulo Bank",
    parserName: "lulo bank",
    domains: ["lulobank.com"],
  },
];

/** `on` approves every domain the bank sends from; `partial` only some. */
export type BankState = "on" | "partial" | "off";

export function bankState(bank: KnownBank, senders: Senders): BankState {
  const approved = bank.domains.filter((domain) => senders.domains.includes(domain));
  if (approved.length === bank.domains.length) return "on";
  return approved.length === 0 ? "off" : "partial";
}

export type CustomSender = { type: "domain" | "address"; value: string };

/** Every approval no known bank accounts for, domains first. */
export function customSenders(senders: Senders): CustomSender[] {
  const known = new Set(KNOWN_BANKS.flatMap((bank) => bank.domains));

  return [
    ...senders.domains
      .filter((domain) => !known.has(domain))
      .map((value) => ({ type: "domain" as const, value })),
    ...senders.addresses.map((value) => ({ type: "address" as const, value })),
  ];
}

/** A chip per approved bank, for the screens that only need to name them. */
export type ApprovedBank = { name: string; partial: boolean };

export function approvedBanks(senders: Senders): ApprovedBank[] {
  const known = KNOWN_BANKS.map((bank) => ({
    name: bank.name,
    state: bankState(bank, senders),
  }))
    .filter(({ state }) => state !== "off")
    .map(({ name, state }) => ({ name, partial: state === "partial" }));

  return [
    ...known,
    ...customSenders(senders).map(({ value }) => ({ name: value, partial: false })),
  ];
}

export function hasSenders(senders: Senders): boolean {
  return senders.domains.length > 0 || senders.addresses.length > 0;
}

export function withBank(senders: Senders, bank: KnownBank): Senders {
  return {
    domains: [...new Set([...senders.domains, ...bank.domains])],
    addresses: senders.addresses,
  };
}

export function withoutBank(senders: Senders, bank: KnownBank): Senders {
  return {
    domains: senders.domains.filter((domain) => !bank.domains.includes(domain)),
    addresses: senders.addresses,
  };
}

export function withSender(senders: Senders, sender: CustomSender): Senders {
  return sender.type === "domain"
    ? {
        domains: [...new Set([...senders.domains, sender.value])],
        addresses: senders.addresses,
      }
    : {
        domains: senders.domains,
        addresses: [...new Set([...senders.addresses, sender.value])],
      };
}

export function withoutSender(senders: Senders, sender: CustomSender): Senders {
  return sender.type === "domain"
    ? {
        domains: senders.domains.filter((value) => value !== sender.value),
        addresses: senders.addresses,
      }
    : {
        domains: senders.domains,
        addresses: senders.addresses.filter((value) => value !== sender.value),
      };
}

function domainOf(address: string): string {
  return address.slice(address.lastIndexOf("@") + 1);
}

/** The same rule the intake applies: the exact address, or its whole domain. */
export function isApproved(sender: string, senders: Senders): boolean {
  const address = sender.trim().toLowerCase();
  return (
    senders.addresses.includes(address) || senders.domains.includes(domainOf(address))
  );
}

/** Which known bank an address writes from, if any. */
export function bankOfSender(sender: string): KnownBank | undefined {
  const domain = domainOf(sender.trim().toLowerCase());
  return KNOWN_BANKS.find((bank) => bank.domains.includes(domain));
}

/** A movement's `bank`, spelled the way the bank spells itself. */
export function bankDisplayName(raw: string): string {
  const normalized = raw.trim().toLowerCase();
  return KNOWN_BANKS.find((bank) => bank.parserName === normalized)?.name ?? raw;
}

/* ------------------------------------------------------------ what is typed */

/**
 * Webmail providers. Approving one of these as a whole domain would accept
 * mail from anybody with an account there — an exact address on them is
 * fine, the domain never is.
 */
const PERSONAL_DOMAINS = new Set([
  "gmail.com",
  "googlemail.com",
  "hotmail.com",
  "hotmail.es",
  "outlook.com",
  "outlook.es",
  "live.com",
  "msn.com",
  "yahoo.com",
  "yahoo.es",
  "icloud.com",
  "me.com",
  "aol.com",
  "proton.me",
  "protonmail.com",
]);

const DOMAIN = /^(?=.{4,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$/;
const LOCAL_PART = /^[^\s@<>()",;:]+$/;

export type SenderError =
  | { code: "empty" }
  | { code: "invalid" }
  | { code: "personal-domain" }
  | { code: "duplicate" }
  | { code: "covered"; domain: string };

export type SenderParse =
  | { ok: true; sender: CustomSender }
  | { ok: false; error: SenderError };

/**
 * What somebody pasted, cleaned the way people actually paste it: Gmail
 * copies a sender as `Bancolombia <alertas@…>`, and a leading `@` or a stray
 * `mailto:` is what a domain looks like when copied from anywhere else.
 */
function clean(raw: string): string {
  let value = raw.trim();
  const bracketed = /<([^<>]+)>/.exec(value);
  if (bracketed?.[1]) value = bracketed[1];

  return value
    .replace(/^mailto:/i, "")
    .replace(/^["'`\s]+|["'`\s.,;:]+$/g, "")
    .toLowerCase();
}

/**
 * Reads one sender to approve, or says why it cannot be.
 *
 * A leading `@` is a domain, not an address: sent as an address it would be
 * stored as `@banco.com` and never match anything. The checks here are for
 * the person typing — the server validates again, and its answer is the one
 * the screen shows.
 */
export function parseSender(raw: string, senders: Senders): SenderParse {
  const value = clean(raw);
  if (!value) return { ok: false, error: { code: "empty" } };

  if (value.startsWith("@") || !value.includes("@")) {
    const domain = value.replace(/^@/, "");
    if (!DOMAIN.test(domain)) return { ok: false, error: { code: "invalid" } };
    if (PERSONAL_DOMAINS.has(domain)) {
      return { ok: false, error: { code: "personal-domain" } };
    }
    if (senders.domains.includes(domain)) {
      return { ok: false, error: { code: "duplicate" } };
    }
    return { ok: true, sender: { type: "domain", value: domain } };
  }

  const at = value.lastIndexOf("@");
  const local = value.slice(0, at);
  const domain = value.slice(at + 1);
  if (!LOCAL_PART.test(local) || !DOMAIN.test(domain)) {
    return { ok: false, error: { code: "invalid" } };
  }
  if (senders.addresses.includes(value)) {
    return { ok: false, error: { code: "duplicate" } };
  }
  if (senders.domains.includes(domain)) {
    return { ok: false, error: { code: "covered", domain } };
  }
  return { ok: true, sender: { type: "address", value } };
}

export function senderErrorMessage(error: SenderError): string {
  switch (error.code) {
    case "empty":
      return "Escribe el correo desde el que te escribe tu banco.";
    case "invalid":
      return "Eso no parece un correo. Debe verse como alertas@tubanco.com.";
    case "personal-domain":
      return "Ese dominio es de correo personal: aprobarlo aceptaría correos de cualquiera. Escribe la dirección completa de tu banco.";
    case "duplicate":
      return "Ya está en tu lista.";
    case "covered":
      return `Ya aceptas todos los correos de @${error.domain}.`;
  }
}

/* ------------------------------------------------------------ Gmail filter */

/** The filter's terms: what Gmail is told to match, one per approval. */
export function filterTerms(senders: Senders): string[] {
  return gmailFromTerms(senders.domains, senders.addresses);
}

/**
 * What changed between the filter somebody made and the approvals now.
 *
 * Null when this browser never saw the filter being made, which is not the
 * same as "nothing changed": nobody can know, so nothing is claimed.
 */
export function filterDrift(
  saved: string[] | null,
  current: string[],
): { added: string[]; removed: string[] } | null {
  if (saved === null) return null;

  return {
    added: current.filter((term) => !saved.includes(term)),
    removed: saved.filter((term) => !current.includes(term)),
  };
}
