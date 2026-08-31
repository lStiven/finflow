/**
 * The session, which is just a token and its expiry.
 *
 * There is no refresh token and no logout endpoint — the backend issues a
 * stateless JWT and forgets about it. So signing out is deleting this, and
 * "renewing" is sending the user back to the login form. Expiry is checked
 * before each request rather than waited for: a 401 arriving mid-screen is a
 * worse experience than a login form arriving before the screen does.
 */

const STORAGE_KEY = "finflow.session";

/** Treat a token as spent slightly early, so a request cannot expire in flight. */
const EXPIRY_MARGIN_SECONDS = 30;

export type Session = {
  userId: string;
  accessToken: string;
  /** Epoch seconds, straight from the API. */
  expiresAt: number;
};

/**
 * What was in storage, and whether it was thrown away for having expired.
 *
 * The distinction is the whole point: "no session" and "the session you had
 * ran out" look identical once the token is gone, and only the second one is
 * worth telling somebody about.
 */
export type StoredSession = {
  session: Session | null;
  expired: boolean;
};

function isSession(value: unknown): value is Session {
  if (typeof value !== "object" || value === null) return false;
  const candidate = value as Record<string, unknown>;
  return (
    typeof candidate.userId === "string" &&
    typeof candidate.accessToken === "string" &&
    typeof candidate.expiresAt === "number"
  );
}

export function isExpired(session: Session, now = Date.now() / 1000): boolean {
  return session.expiresAt - EXPIRY_MARGIN_SECONDS <= now;
}

/** Milliseconds until `session` is due to be treated as spent. Never negative. */
export function millisecondsUntilExpiry(
  session: Session,
  now = Date.now() / 1000,
): number {
  return Math.max(0, (session.expiresAt - EXPIRY_MARGIN_SECONDS - now) * 1000);
}

export function readStoredSession(): StoredSession {
  let raw: string | null;
  try {
    raw = window.localStorage.getItem(STORAGE_KEY);
  } catch {
    // Private windows and "block site data" both throw rather than return
    // null. No stored session is a valid state, so this is not an error.
    return { session: null, expired: false };
  }
  if (!raw) return { session: null, expired: false };

  try {
    const parsed: unknown = JSON.parse(raw);
    // Unreadable is not expired: a corrupt entry never was a usable session,
    // and saying "tu sesión expiró" for one would be a lie.
    if (!isSession(parsed)) {
      clearSession();
      return { session: null, expired: false };
    }
    if (isExpired(parsed)) {
      clearSession();
      return { session: null, expired: true };
    }
    return { session: parsed, expired: false };
  } catch {
    clearSession();
    return { session: null, expired: false };
  }
}

export function loadSession(): Session | null {
  return readStoredSession().session;
}

export function saveSession(session: Session): void {
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(session));
  } catch {
    // Nothing to do: the session still lives in memory for this tab, and the
    // only cost is having to log in again on the next visit.
  }
}

export function clearSession(): void {
  try {
    window.localStorage.removeItem(STORAGE_KEY);
  } catch {
    // Same as above.
  }
}
