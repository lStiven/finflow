import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { NO_ACKS, readAcks, writeAck } from "@/onboarding/progress";

const USER = "11111111-1111-1111-1111-111111111111";
const OTHER = "22222222-2222-2222-2222-222222222222";

/** Node has no `window`; only `localStorage` is touched, so only it is stubbed. */
function stubStorage(
  initial: Record<string, string> = {},
  options?: { throws?: boolean },
) {
  const store = new Map(Object.entries(initial));
  const localStorage = {
    getItem: (key: string) => {
      if (options?.throws) throw new Error("blocked");
      return store.get(key) ?? null;
    },
    setItem: (key: string, value: string) => {
      if (options?.throws) throw new Error("blocked");
      store.set(key, value);
    },
    removeItem: (key: string) => void store.delete(key),
  };
  vi.stubGlobal("window", { localStorage });
  return store;
}

describe("onboarding acknowledgements", () => {
  beforeEach(() => {
    stubStorage();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("starts with nothing acknowledged", () => {
    expect(readAcks(USER)).toEqual(NO_ACKS);
  });

  it("keeps what was acknowledged, and returns the whole set", () => {
    const after = writeAck(USER, "introSeen");

    expect(after.introSeen).toBe(true);
    expect(readAcks(USER)).toEqual({ ...NO_ACKS, introSeen: true });
  });

  it("adds to what is already there rather than replacing it", () => {
    writeAck(USER, "introSeen");
    writeAck(USER, "addressCopied");

    expect(readAcks(USER)).toEqual({
      ...NO_ACKS,
      introSeen: true,
      addressCopied: true,
    });
  });

  it("can take an acknowledgement back", () => {
    writeAck(USER, "gmailSubmitted");

    expect(writeAck(USER, "gmailSubmitted", false).gmailSubmitted).toBe(false);
  });

  it("keeps one account's progress out of another's", () => {
    // Two people on one laptop, or signing out and back in as somebody else.
    writeAck(USER, "readyCelebrated");

    expect(readAcks(OTHER)).toEqual(NO_ACKS);
  });

  it("treats an unreadable entry as nothing acknowledged", () => {
    stubStorage({ [`finflow.onboarding.${USER}`]: "{not json" });

    expect(readAcks(USER)).toEqual(NO_ACKS);
  });

  it("treats a non-object entry as nothing acknowledged", () => {
    stubStorage({ [`finflow.onboarding.${USER}`]: '["introSeen"]' });

    expect(readAcks(USER)).toEqual(NO_ACKS);
  });

  it("reads each flag as a boolean, whatever was stored there", () => {
    // An older build, or a hand-edited entry: a truthy string must not reach
    // a screen that branches on `=== true`.
    stubStorage({
      [`finflow.onboarding.${USER}`]: JSON.stringify({
        introSeen: "yes",
        addressCopied: 1,
        gmailSubmitted: true,
      }),
    });

    expect(readAcks(USER)).toEqual({ ...NO_ACKS, gmailSubmitted: true });
  });

  it("survives storage being blocked entirely", () => {
    // A private window throws on both reads and writes. Being walked through
    // the guide again is the whole cost; nothing may crash.
    stubStorage({}, { throws: true });

    expect(readAcks(USER)).toEqual(NO_ACKS);
    expect(() => writeAck(USER, "introSeen")).not.toThrow();
    expect(writeAck(USER, "introSeen").introSeen).toBe(true);
  });
});
