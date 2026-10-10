/**
 * The half of onboarding progress that nothing on the server can observe.
 *
 * `GET /ingestion/setup` already answers the facts that are checkable — the
 * address exists, somebody is approved, Google confirmed the forwarding, an
 * alert arrived — and it derives them on every call so they are the same in
 * every browser. Deliberately, it has no field a client writes.
 *
 * What is left over is what somebody *did*, which only they can report:
 * starting the setup, saying they added the address in Gmail, saying they
 * made the filter, and having been told once that everything is connected.
 * Those live here, per browser, because inventing a server field for "I did
 * this in Gmail" would put unverifiable claims in the same record as verified
 * ones.
 *
 * The cost is stated rather than hidden: signing in somewhere else replays
 * the claims, not the work. Every real step stays green, because those come
 * from the API.
 */

export type OnboardingAcks = {
  /** Was welcomed, once, on the first visit after the account existed. */
  welcomeSeen: boolean;
  /** Left the introduction and started the setup. */
  introSeen: boolean;
  /** Took the forwarding address. */
  addressCopied: boolean;
  /**
   * When they said the address was added in Gmail, in epoch seconds. Google's
   * confirmation is what settles that step; this only dates the wait, so the
   * screen can tell "a minute ago" from "half an hour ago".
   */
  forwardingRequestedAt: number | null;
  /** Said the Gmail filter is in place. An arriving alert is what proves it. */
  gmailSubmitted: boolean;
  /**
   * The filter's terms when they said it was made — what Gmail is matching,
   * as far as this browser knows. A bank approved afterwards is missing from
   * it, and comparing the two is the only way the screen can say so.
   */
  filterSenders: string[] | null;
  /** Was told, once, that expenses are arriving on their own now. */
  readyCelebrated: boolean;
};

/** The acknowledgements that are a plain yes or no. */
export type FlagAck = {
  [K in keyof OnboardingAcks]: OnboardingAcks[K] extends boolean ? K : never;
}[keyof OnboardingAcks];

export const NO_ACKS: OnboardingAcks = {
  welcomeSeen: false,
  introSeen: false,
  addressCopied: false,
  forwardingRequestedAt: null,
  gmailSubmitted: false,
  filterSenders: null,
  readyCelebrated: false,
};

/**
 * Per account, not per browser: two people sharing a laptop must not inherit
 * each other's progress, and signing out and back in as somebody else is the
 * ordinary way that happens.
 */
function keyFor(userId: string): string {
  return `finflow.onboarding.${userId}`;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function asInstant(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) && value > 0
    ? value
    : null;
}

function asTerms(value: unknown): string[] | null {
  if (!Array.isArray(value)) return null;
  return value.every((term) => typeof term === "string") ? value : null;
}

export function readAcks(userId: string): OnboardingAcks {
  let raw: string | null;
  try {
    raw = window.localStorage.getItem(keyFor(userId));
  } catch {
    // Private windows and "block site data" throw rather than return null.
    // Nothing acknowledged is a valid state, so this is not an error.
    return NO_ACKS;
  }
  if (!raw) return NO_ACKS;

  try {
    const parsed: unknown = JSON.parse(raw);
    if (!isRecord(parsed)) return NO_ACKS;

    // Field by field, so an entry written by an older build — or hand-edited
    // to something absurd — degrades to "not done" instead of putting a
    // string where the screen expects a boolean.
    return {
      welcomeSeen: parsed.welcomeSeen === true,
      introSeen: parsed.introSeen === true,
      addressCopied: parsed.addressCopied === true,
      forwardingRequestedAt: asInstant(parsed.forwardingRequestedAt),
      gmailSubmitted: parsed.gmailSubmitted === true,
      filterSenders: asTerms(parsed.filterSenders),
      readyCelebrated: parsed.readyCelebrated === true,
    };
  } catch {
    return NO_ACKS;
  }
}

/** Records several acknowledgements at once and returns the whole set. */
export function writeAcks(
  userId: string,
  patch: Partial<OnboardingAcks>,
): OnboardingAcks {
  const next = { ...readAcks(userId), ...patch };
  try {
    window.localStorage.setItem(keyFor(userId), JSON.stringify(next));
  } catch {
    // The screen still moves on with what it holds in memory; the only cost
    // is being walked through it again on the next visit.
  }
  return next;
}

/** Records one yes-or-no acknowledgement and returns the whole set. */
export function writeAck(userId: string, key: FlagAck, value = true): OnboardingAcks {
  return writeAcks(userId, { [key]: value });
}
