/**
 * The half of onboarding progress that nothing on the server can observe.
 *
 * `GET /ingestion/setup` already answers the four facts that are checkable —
 * the address exists, somebody is approved, Google confirmed the forwarding,
 * an alert arrived — and it derives them on every call so they are the same
 * in every browser. Deliberately, it has no field a client writes.
 *
 * What is left over is what somebody *did*, which only they can report:
 * reading the explanation, copying their address, saying they set the Gmail
 * rule up, and having been told once that everything is connected. Those live
 * here, per browser, because inventing a server field for "I read this" would
 * put unverifiable claims in the same record as verified ones.
 *
 * The cost is stated rather than hidden: signing in somewhere else replays
 * the reading, not the work. Every real step stays green, because those come
 * from the API.
 */

export type OnboardingAcks = {
  /** Was welcomed, once, on the first visit after the account existed. */
  welcomeSeen: boolean;
  /** Read "cómo funciona". */
  introSeen: boolean;
  /** Took the forwarding address. */
  addressCopied: boolean;
  /** Said the Gmail forwarding rule is in place; Google's confirmation is
   * what actually settles it, and that comes from the API. */
  gmailSubmitted: boolean;
  /** Was told, once, that expenses are arriving on their own now. */
  readyCelebrated: boolean;
};

export const NO_ACKS: OnboardingAcks = {
  welcomeSeen: false,
  introSeen: false,
  addressCopied: false,
  gmailSubmitted: false,
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
      gmailSubmitted: parsed.gmailSubmitted === true,
      readyCelebrated: parsed.readyCelebrated === true,
    };
  } catch {
    return NO_ACKS;
  }
}

/** Records one acknowledgement and returns the whole set as it now stands. */
export function writeAck(
  userId: string,
  key: keyof OnboardingAcks,
  value = true,
): OnboardingAcks {
  const next = { ...readAcks(userId), [key]: value };
  try {
    window.localStorage.setItem(keyFor(userId), JSON.stringify(next));
  } catch {
    // The screen still moves on with what it holds in memory; the only cost
    // is being walked through it again on the next visit.
  }
  return next;
}
