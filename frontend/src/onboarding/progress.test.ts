import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { NO_ACKS, readAcks, writeAck, writeAcks } from "@/onboarding/progress";

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

  /*
   * An entry written before `welcomeSeen` existed reads as false, so somebody
   * who left the setup pending under an older build is welcomed once. That is
   * the behaviour we want rather than a migration to write: they are exactly
   * the people the dialog is for.
   */
  it("reads an entry from a build with no welcomeSeen as not welcomed", () => {
    stubStorage({
      [`finflow.onboarding.${USER}`]: JSON.stringify({
        introSeen: true,
        addressCopied: true,
      }),
    });

    expect(readAcks(USER)).toEqual({
      ...NO_ACKS,
      introSeen: true,
      addressCopied: true,
    });
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

  it("keeps when the address was added and what the filter matched", () => {
    writeAcks(USER, { forwardingRequestedAt: 1_760_000_000 });
    writeAcks(USER, {
      gmailSubmitted: true,
      filterSenders: ["@lulobank.com", "alertas@banco.com"],
    });

    expect(readAcks(USER)).toEqual({
      ...NO_ACKS,
      forwardingRequestedAt: 1_760_000_000,
      gmailSubmitted: true,
      filterSenders: ["@lulobank.com", "alertas@banco.com"],
    });
  });

  /*
   * The e2e suites and `just shot` write the entry by hand, with only the
   * flags that existed before these two fields. They must keep reading as
   * "nothing claimed" rather than as a broken entry.
   */
  it("reads an entry with none of the newer fields as not claimed", () => {
    stubStorage({
      [`finflow.onboarding.${USER}`]: JSON.stringify({
        welcomeSeen: true,
        introSeen: true,
        addressCopied: true,
        gmailSubmitted: true,
        readyCelebrated: true,
      }),
    });

    const acks = readAcks(USER);

    expect(acks.forwardingRequestedAt).toBeNull();
    expect(acks.filterSenders).toBeNull();
    expect(acks.gmailSubmitted).toBe(true);
  });

  it("drops a wait time or a filter that is not what it should be", () => {
    stubStorage({
      [`finflow.onboarding.${USER}`]: JSON.stringify({
        forwardingRequestedAt: "ayer",
        filterSenders: ["@lulobank.com", 7],
      }),
    });

    expect(readAcks(USER)).toEqual(NO_ACKS);
  });
});
