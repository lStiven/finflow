/**
 * What the "connect your bank" screen shows, resolved in one place.
 *
 * Two sources have to be merged and neither one is enough on its own: the API
 * knows the four verifiable facts and nothing about what the person read, the
 * browser knows what they read and nothing about what the mailbox did. This
 * is the function that puts them together, so no component has to reason
 * about the seam — and so the rules that matter can be tested without a DOM.
 *
 * The server always wins where the two could disagree. `ready` in particular
 * closes the whole thing regardless of what was acknowledged: somebody whose
 * expenses are already arriving does not need to be walked through how to
 * make them arrive.
 */

import type { InboxSetup } from "@/api/queries";
import type { OnboardingAcks } from "@/onboarding/progress";

/**
 * The five stages a person walks through, in the order they are shown.
 *
 * More than the API's four steps because two of these are *reading*, not
 * doing: the API has no business tracking those, and a screen that skipped
 * them would hand somebody an address with no explanation of what it is.
 */
export const STAGES = [
  "intro",
  "address",
  "senders",
  "forwarding",
  "first-alert",
] as const;

export type StageId = (typeof STAGES)[number];

/** Who says a stage is done, which is what the screen labels it with. */
export type Proof = "you" | "verified";

export type Stage = {
  id: StageId;
  done: boolean;
  proof: Proof;
};

/** The three states of the address itself, which is its own indicator. */
export type AddressStatus = "unverified" | "confirmed" | "receiving";

export type OnboardingState = {
  stages: Stage[];
  /** The stage to point at, or null once there is nothing left to do. */
  current: StageId | null;
  /** Expenses are arriving on their own. The API's `ready`, unmodified. */
  complete: boolean;
  doneCount: number;
  total: number;
  /** Whether to show the "everything is connected" message — once, ever. */
  celebrate: boolean;
  address: string;
  addressStatus: AddressStatus;
  /** Senders whose mail arrived and was discarded for not being approved. */
  unapprovedSenders: string[];
};

function isDone(setup: InboxSetup, key: string): boolean {
  return setup.steps.some((step) => step.key === key && step.done);
}

export function resolveOnboarding(
  setup: InboxSetup,
  acks: OnboardingAcks,
): OnboardingState {
  const sendersApproved = isDone(setup, "senders_approved");
  const forwardingConfirmed = isDone(setup, "forwarding_confirmed");
  const firstAlert = isDone(setup, "first_alert");
  const complete = setup.ready;

  const stages: Stage[] = (
    [
      { id: "intro", done: acks.introSeen, proof: "you" },
      { id: "address", done: acks.addressCopied, proof: "you" },
      { id: "senders", done: sendersApproved, proof: "verified" },
      {
        id: "forwarding",
        // A confirmed rule is the usual way mail gets here, not the only
        // one: somebody forwarding each alert by hand never gets a
        // confirmation, and their first alert is the better proof that the
        // route works. Without this they would sit on this stage forever
        // with movements already on screen.
        done: forwardingConfirmed || firstAlert,
        proof: "verified",
      },
      { id: "first-alert", done: firstAlert, proof: "verified" },
    ] satisfies Stage[]
  ).map((stage) =>
    // Arriving expenses settle every stage behind them. A recap that still
    // showed "copy your address" as pending, for an account already
    // receiving, would be reporting on the reading rather than the setup.
    complete ? { ...stage, done: true } : stage,
  );

  return {
    stages,
    current: complete ? null : (stages.find((stage) => !stage.done)?.id ?? null),
    complete,
    doneCount: stages.filter((stage) => stage.done).length,
    total: stages.length,
    celebrate: complete && !acks.readyCelebrated,
    address: setup.address,
    addressStatus: addressStatusOf({ complete, forwardingConfirmed }),
    unapprovedSenders: setup.unapproved_senders,
  };
}

function addressStatusOf({
  complete,
  forwardingConfirmed,
}: {
  complete: boolean;
  forwardingConfirmed: boolean;
}): AddressStatus {
  if (complete) return "receiving";
  if (forwardingConfirmed) return "confirmed";
  return "unverified";
}
