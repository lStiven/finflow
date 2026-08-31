import { describe, expect, it } from "vitest";
import type { InboxSetup } from "@/api/queries";
import { NO_ACKS, type OnboardingAcks } from "@/onboarding/progress";
import { resolveOnboarding } from "@/onboarding/steps";

const ADDRESS = "finflowingest+abc@gmail.com";

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
      { key: "forwarding_confirmed", done: forwardingConfirmed, at: null },
      { key: "first_alert", done: firstAlert, at: null },
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

describe("resolving onboarding", () => {
  it("points a brand new account at the explanation", () => {
    const state = resolveOnboarding(setup(), acks());

    expect(state.current).toBe("intro");
    expect(state.doneCount).toBe(0);
    expect(state.total).toBe(5);
    expect(state.complete).toBe(false);
  });

  it("advances through the reading as it is acknowledged", () => {
    expect(resolveOnboarding(setup(), acks({ introSeen: true })).current).toBe(
      "address",
    );
    expect(
      resolveOnboarding(setup(), acks({ introSeen: true, addressCopied: true }))
        .current,
    ).toBe("senders");
  });

  it("takes the sender step from the API, not from the browser", () => {
    // Nothing acknowledged locally, but the account already approves
    // somebody: the screen must not ask them to do it again.
    const state = resolveOnboarding(setup({ sendersApproved: true }), acks());

    expect(doneIds(state)).toEqual(["senders"]);
  });

  it("closes the forwarding step when Google confirms", () => {
    const state = resolveOnboarding(
      setup({ sendersApproved: true, forwardingConfirmed: true }),
      acks({ introSeen: true, addressCopied: true }),
    );

    expect(state.current).toBe("first-alert");
    expect(state.addressStatus).toBe("confirmed");
  });

  it("saying the Gmail rule is set up does not close the step by itself", () => {
    // Only Google's confirmation — or an alert actually arriving — settles
    // it. Believing the claim would show a green tick over a broken route.
    const state = resolveOnboarding(
      setup({ sendersApproved: true }),
      acks({ introSeen: true, addressCopied: true, gmailSubmitted: true }),
    );

    expect(state.current).toBe("forwarding");
    expect(state.addressStatus).toBe("unverified");
  });

  it("counts a first alert as proof the route works, with no confirmation", () => {
    // Somebody forwarding each alert by hand never gets a confirmation. They
    // are connected, and must not sit on that step with movements on screen.
    const state = resolveOnboarding(
      setup({ sendersApproved: true, firstAlert: true, ready: true }),
      acks(),
    );

    expect(state.complete).toBe(true);
    expect(state.current).toBeNull();
  });

  it("settles every stage once expenses are arriving", () => {
    // Including the reading nobody acknowledged: the recap reports on the
    // setup, not on which paragraphs were opened.
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
    expect(doneIds(state)).toEqual([
      "intro",
      "address",
      "senders",
      "forwarding",
      "first-alert",
    ]);
    expect(state.addressStatus).toBe("receiving");
  });

  it("reopens the guide when the allow-list is emptied after a first alert", () => {
    // `ready` is "arriving right now", not "once arrived": emptying the
    // senders stops the next alert cold, and the screen has to say so.
    const state = resolveOnboarding(
      setup({ sendersApproved: false, forwardingConfirmed: true, firstAlert: true }),
      acks({ introSeen: true, addressCopied: true }),
    );

    expect(state.complete).toBe(false);
    expect(state.current).toBe("senders");
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
  it("never welcomes an account whose expenses are already arriving", () => {
    expect(resolveOnboarding(setup({ ready: true }), acks()).welcome).toBe(false);
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

  it("carries the address and who is being discarded", () => {
    const state = resolveOnboarding(
      setup({ unapproved: ["alertas@banco.com"] }),
      acks(),
    );

    expect(state.address).toBe(ADDRESS);
    expect(state.unapprovedSenders).toEqual(["alertas@banco.com"]);
  });

  it("labels who vouches for each stage", () => {
    // The two reading stages are the user's word; the other three are the
    // API's. The screen says which, so a tick means the same thing twice.
    const state = resolveOnboarding(setup(), acks());
    const proofs = Object.fromEntries(
      state.stages.map((stage) => [stage.id, stage.proof]),
    );

    expect(proofs).toEqual({
      intro: "you",
      address: "you",
      senders: "verified",
      forwarding: "verified",
      "first-alert": "verified",
    });
  });
});
