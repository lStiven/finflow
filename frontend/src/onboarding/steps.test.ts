import { describe, expect, it } from "vitest";
import type { InboxSetup } from "@/api/queries";
import { NO_ACKS, type OnboardingAcks } from "@/onboarding/progress";
import { resolveOnboarding, STAGES, stageAt, stageNumber } from "@/onboarding/steps";

const ADDRESS = "finflowingest+abc@gmail.com";
const CONFIRMED_AT = 1_760_000_000;
const ALERT_AT = 1_760_000_600;

/**
 * The API's four steps, as they actually come back. Named individually so a
 * test says which fact it is about rather than indexing into an array.
 */
function setup(
  overrides: {
    sendersApproved?: boolean;
    forwardingConfirmed?: boolean;
    firstAlert?: boolean;
    ready?: boolean;
    unapproved?: string[];
  } = {},
): InboxSetup {
  const {
    sendersApproved = false,
    forwardingConfirmed = false,
    firstAlert = false,
    ready = false,
    unapproved = [],
  } = overrides;

  return {
    address: ADDRESS,
    steps: [
      { key: "address_assigned", done: true, at: null },
      { key: "senders_approved", done: sendersApproved, at: null },
      {
        key: "forwarding_confirmed",
        done: forwardingConfirmed,
        at: forwardingConfirmed ? CONFIRMED_AT : null,
      },
      { key: "first_alert", done: firstAlert, at: firstAlert ? ALERT_AT : null },
    ],
    current: null,
    ready,
    unapproved_senders: unapproved,
  };
}

function acks(overrides: Partial<OnboardingAcks> = {}): OnboardingAcks {
  return { ...NO_ACKS, ...overrides };
}

function doneIds(state: ReturnType<typeof resolveOnboarding>): string[] {
  return state.stages.filter((stage) => stage.done).map((stage) => stage.id);
}

function statusOf(state: ReturnType<typeof resolveOnboarding>, id: string) {
  return state.stages.find((stage) => stage.id === id)?.status;
}

describe("resolving onboarding", () => {
  it("opens a brand new account on the introduction, pointing at the banks", () => {
    const state = resolveOnboarding(setup(), acks());

    expect(state.intro).toBe(true);
    expect(state.current).toBe("banks");
    expect(state.doneCount).toBe(0);
    expect(state.total).toBe(4);
    expect(state.complete).toBe(false);
  });

  it("leaves the introduction once it is left, or once anything is done", () => {
    expect(resolveOnboarding(setup(), acks({ introSeen: true })).intro).toBe(false);
    // Progress made in another browser is on the server: no door to walk
    // through again.
    expect(resolveOnboarding(setup({ sendersApproved: true }), acks()).intro).toBe(
      false,
    );
    expect(
      resolveOnboarding(setup(), acks({ forwardingRequestedAt: CONFIRMED_AT })).intro,
    ).toBe(false);
  });

  it("takes the bank step from the API, not from the browser", () => {
    const state = resolveOnboarding(setup({ sendersApproved: true }), acks());

    expect(doneIds(state)).toEqual(["banks"]);
    expect(state.current).toBe("address");
  });

  it("waits on Google once the address was added, and not before", () => {
    const before = resolveOnboarding(setup({ sendersApproved: true }), acks());
    const after = resolveOnboarding(
      setup({ sendersApproved: true }),
      acks({ forwardingRequestedAt: CONFIRMED_AT - 60 }),
    );

    expect(statusOf(before, "address")).toBe("todo");
    expect(statusOf(after, "address")).toBe("waiting");
    // Saying so does not close it: only Google's confirmation does.
    expect(after.current).toBe("address");
  });

  it("closes the address step on Google's confirmation, with its time", () => {
    const state = resolveOnboarding(
      setup({ sendersApproved: true, forwardingConfirmed: true }),
      acks(),
    );

    expect(doneIds(state)).toEqual(["banks", "address"]);
    expect(state.forwardingConfirmedAt).toBe(CONFIRMED_AT);
    expect(state.addressStatus).toBe("confirmed");
    expect(state.current).toBe("filter");
  });

  it("closes the filter step on the person's word, once Google confirmed", () => {
    const state = resolveOnboarding(
      setup({ sendersApproved: true, forwardingConfirmed: true }),
      acks({ gmailSubmitted: true }),
    );

    expect(state.current).toBe("first-alert");
    // The filter is the person's word until an alert proves it.
    expect(state.stages.find((stage) => stage.id === "filter")?.proof).toBe("you");
    expect(statusOf(state, "first-alert")).toBe("waiting");
  });

  it("does not believe a filter made before the address was verified", () => {
    // Gmail does not offer an unverified address in «Reenviarlo a», so that
    // filter cannot exist yet. A tick over it would hide the real step.
    const state = resolveOnboarding(
      setup({ sendersApproved: true }),
      acks({ gmailSubmitted: true }),
    );

    expect(doneIds(state)).toEqual(["banks"]);
    expect(state.addressStatus).toBe("unverified");
  });

  it("counts a first alert as proof of the address and the filter", () => {
    // Somebody forwarding each alert by hand never gets a confirmation. They
    // are connected, and must not sit on those steps with movements on screen.
    const state = resolveOnboarding(
      setup({ sendersApproved: true, firstAlert: true, ready: true }),
      acks(),
    );

    expect(state.complete).toBe(true);
    expect(state.current).toBeNull();
    expect(state.firstAlertAt).toBe(ALERT_AT);
  });

  it("settles every stage once expenses are arriving", () => {
    const state = resolveOnboarding(
      setup({
        sendersApproved: true,
        forwardingConfirmed: true,
        firstAlert: true,
        ready: true,
      }),
      acks(),
    );

    expect(state.doneCount).toBe(state.total);
    expect(doneIds(state)).toEqual([...STAGES]);
    expect(state.stages.every((stage) => stage.status === "done")).toBe(true);
    expect(state.addressStatus).toBe("receiving");
  });

  it("reopens the guide when the allow-list is emptied after a first alert", () => {
    // `ready` is "arriving right now", not "once arrived": emptying the
    // senders stops the next alert cold, and the screen has to say so.
    const state = resolveOnboarding(
      setup({ sendersApproved: false, forwardingConfirmed: true, firstAlert: true }),
      acks({ introSeen: true }),
    );

    expect(state.complete).toBe(false);
    expect(state.current).toBe("banks");
    expect(state.intro).toBe(false);
  });

  it("celebrates once, and then never again", () => {
    const connected = setup({ sendersApproved: true, firstAlert: true, ready: true });

    expect(resolveOnboarding(connected, acks()).celebrate).toBe(true);
    expect(
      resolveOnboarding(connected, acks({ readyCelebrated: true })).celebrate,
    ).toBe(false);
  });

  it("welcomes once, and then never again", () => {
    expect(resolveOnboarding(setup(), acks()).welcome).toBe(true);
    expect(resolveOnboarding(setup(), acks({ welcomeSeen: true })).welcome).toBe(false);
  });

  /*
   * Somebody signing in on a second browser with everything already
   * connected: the acks are empty there, so only the server half can tell
   * this apart from a brand-new account.
   */
  it("never welcomes or introduces an account already receiving", () => {
    const state = resolveOnboarding(setup({ ready: true }), acks());

    expect(state.welcome).toBe(false);
    expect(state.intro).toBe(false);
  });

  /*
   * Both dialogs are mounted from the shell at once, so the guarantee that
   * keeps them from stacking has to be the state itself, not their order.
   */
  it("never welcomes and celebrates at the same time", () => {
    for (const ready of [false, true]) {
      const state = resolveOnboarding(setup({ ready }), acks());
      expect(state.welcome && state.celebrate).toBe(false);
    }
  });

  it("never celebrates a setup that is not finished", () => {
    expect(resolveOnboarding(setup({ sendersApproved: true }), acks()).celebrate).toBe(
      false,
    );
  });

  it("carries the address, who is being discarded and what was claimed", () => {
    const state = resolveOnboarding(
      setup({ unapproved: ["alertas@banco.com"] }),
      acks({ forwardingRequestedAt: 42, filterSenders: ["@lulobank.com"] }),
    );

    expect(state.address).toBe(ADDRESS);
    expect(state.unapprovedSenders).toEqual(["alertas@banco.com"]);
    expect(state.forwardingRequestedAt).toBe(42);
    expect(state.filterSenders).toEqual(["@lulobank.com"]);
  });

  it("labels who vouches for each stage", () => {
    const state = resolveOnboarding(setup(), acks());
    const proofs = Object.fromEntries(
      state.stages.map((stage) => [stage.id, stage.proof]),
    );

    expect(proofs).toEqual({
      banks: "verified",
      address: "verified",
      filter: "you",
      "first-alert": "verified",
    });
  });

  it("numbers the stages the way the URL does", () => {
    expect(stageNumber("banks")).toBe(1);
    expect(stageNumber("first-alert")).toBe(4);
    expect(stageAt(3)).toBe("filter");
    expect(stageAt(0)).toBeUndefined();
    expect(stageAt(5)).toBeUndefined();
  });
});
