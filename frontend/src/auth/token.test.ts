import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  millisecondsUntilExpiry,
  readStoredSession,
  type Session,
  saveSession,
} from "@/auth/token";

const KEY = "finflow.session";

/**
 * The tests run in Node, where there is no `window`. Only `localStorage` is
 * stubbed because only `localStorage` is touched — a full DOM would be a
 * dependency to install for two methods.
 */
function stubStorage(initial: Record<string, string> = {}) {
  const store = new Map(Object.entries(initial));
  const localStorage = {
    getItem: (key: string) => store.get(key) ?? null,
    setItem: (key: string, value: string) => void store.set(key, value),
    removeItem: (key: string) => void store.delete(key),
  };
  vi.stubGlobal("window", { localStorage });
  return store;
}

function session(overrides: Partial<Session> = {}): Session {
  return {
    userId: "11111111-1111-1111-1111-111111111111",
    accessToken: "a-token",
    expiresAt: Math.floor(Date.now() / 1000) + 3600,
    ...overrides,
  };
}

describe("stored session", () => {
  beforeEach(() => {
    stubStorage();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("returns a live session and reports nothing expired", () => {
    const live = session();
    saveSession(live);

    expect(readStoredSession()).toEqual({ session: live, expired: false });
  });

  it("reports an expired session as expired, and throws it away", () => {
    // The whole reason the flag exists: this is what the login screen turns
    // into "tu sesión expiró" rather than an unexplained login form.
    const store = stubStorage();
    saveSession(session({ expiresAt: Math.floor(Date.now() / 1000) - 1 }));

    expect(readStoredSession()).toEqual({ session: null, expired: true });
    expect(store.has(KEY)).toBe(false);
  });

  it("counts a token inside the safety margin as already spent", () => {
    // A token with seconds left cannot survive a request in flight, so it is
    // reported the same as one that already lapsed.
    saveSession(session({ expiresAt: Math.floor(Date.now() / 1000) + 5 }));

    expect(readStoredSession().expired).toBe(true);
  });

  it("does not call a corrupt entry expired", () => {
    // It never was a usable session, so saying it ran out would be a lie.
    const store = stubStorage({ [KEY]: "{not json" });

    expect(readStoredSession()).toEqual({ session: null, expired: false });
    expect(store.has(KEY)).toBe(false);
  });

  it("does not call a wrongly shaped entry expired either", () => {
    stubStorage({ [KEY]: JSON.stringify({ userId: 7 }) });

    expect(readStoredSession()).toEqual({ session: null, expired: false });
  });

  it("reports nothing at all when there is no session", () => {
    expect(readStoredSession()).toEqual({ session: null, expired: false });
  });
});

describe("time until expiry", () => {
  it("counts down to the margin, not to the stated instant", () => {
    const now = 1_000_000;
    const value = millisecondsUntilExpiry(session({ expiresAt: now + 120 }), now);

    expect(value).toBe(90_000);
  });

  it("never goes negative, so a lapsed token fires the notice at once", () => {
    const now = 1_000_000;

    expect(millisecondsUntilExpiry(session({ expiresAt: now - 500 }), now)).toBe(0);
  });
});
