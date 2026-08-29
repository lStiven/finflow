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

export function loadSession(): Session | null {
  let raw: string | null;
  try {
    raw = window.localStorage.getItem(STORAGE_KEY);
  } catch {
    // Private windows and "block site data" both throw rather than return
    // null. No stored session is a valid state, so this is not an error.
    return null;
  }
  if (!raw) return null;

  try {
    const parsed: unknown = JSON.parse(raw);
    if (!isSession(parsed)) return null;
    if (isExpired(parsed)) {
      clearSession();
      return null;
    }
    return parsed;
  } catch {
    clearSession();
    return null;
  }
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
