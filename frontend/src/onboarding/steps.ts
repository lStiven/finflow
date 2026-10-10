/**
 * What the "connect your bank" screen shows, resolved in one place.
 *
 * Two sources have to be merged and neither one is enough on its own: the API
 * knows the verifiable facts and nothing about what the person did in Gmail,
 * the browser knows what they said they did and nothing about what the
 * mailbox saw. This is the function that puts them together, so no component
 * has to reason about the seam — and so the rules that matter can be tested
 * without a DOM.
 *
 * The server always wins where the two could disagree. `ready` in particular
 * closes the whole thing regardless of what was acknowledged: somebody whose
 * expenses are already arriving does not need to be walked through how to
 * make them arrive.
 */

import type { InboxSetup } from "@/api/queries";
import type { OnboardingAcks } from "@/onboarding/progress";

/**
 * The four stages, in the order they are shown and the order Gmail allows:
 * the filter's «Reenviarlo a» only lists an address Google already verified,
 * and the filter's terms come from the banks chosen first.
 */
export const STAGES = ["banks", "address", "filter", "first-alert"] as const;

export type StageId = (typeof STAGES)[number];

/** Who says a stage is done, which is what the screen labels it with. */
export type Proof = "you" | "verified";

/** `waiting` is a stage whose part is done and whose proof is on its way. */
export type StageStatus = "done" | "waiting" | "todo";

export type Stage = {
  id: StageId;
  done: boolean;
  status: StageStatus;
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
  /** Whether to say "everything is connected" — once, ever. */
  celebrate: boolean;
  /**
   * Whether to welcome somebody and point them at the first step — once,
   * ever. Never at the same time as `celebrate`: one needs `complete` false
   * and the other needs it true, so the shell can mount both without ever
   * stacking two dialogs.
   */
  welcome: boolean;
  /**
   * Whether the guide opens on its introduction. Only for somebody with
   * nothing done anywhere: progress made in another browser is on the
   * server, and somebody who has already started is not shown the door.
   */
  intro: boolean;
  address: string;
  addressStatus: AddressStatus;
  /** Senders whose mail arrived and was discarded for not being approved. */
  unapprovedSenders: string[];
  sendersApproved: boolean;
  forwardingConfirmedAt: number | null;
  firstAlertAt: number | null;
  /** When they said the address was added in Gmail, if they did. */
  forwardingRequestedAt: number | null;
  /** The filter's terms when they said it was made, if this browser saw it. */
  filterSenders: string[] | null;
};

function stepOf(setup: InboxSetup, key: string) {
  return setup.steps.find((step) => step.key === key);
}

export function resolveOnboarding(
  setup: InboxSetup,
  acks: OnboardingAcks,
): OnboardingState {
  const sendersApproved = Boolean(stepOf(setup, "senders_approved")?.done);
  const forwarding = stepOf(setup, "forwarding_confirmed");
  const forwardingConfirmed = Boolean(forwarding?.done);
  const alert = stepOf(setup, "first_alert");
  const firstAlert = Boolean(alert?.done);
  const complete = setup.ready;

  // An alert that arrived proves the address works with or without Google's
  // confirmation: somebody forwarding by hand never gets one, and must not be
  // told their address is unverified while their movements appear.
  const addressDone = forwardingConfirmed || firstAlert;
  // Google's confirmation is half of the filter stage's precondition, and the
  // person's word is the other half: nothing outside Gmail can see a filter.
  // That word alone never closes it — a green tick over a filter that cannot
  // exist yet (the address is not even verified) is the lie this avoids. The
  // first alert settles it either way.
  const filterDone = (forwardingConfirmed && acks.gmailSubmitted) || firstAlert;

  const stages: Stage[] = (
    [
      {
        id: "banks",
        done: sendersApproved,
        status: sendersApproved ? "done" : "todo",
        proof: "verified",
      },
      {
        id: "address",
        done: addressDone,
        status: addressDone
          ? "done"
          : acks.forwardingRequestedAt !== null
            ? "waiting"
            : "todo",
        proof: "verified",
      },
      {
        id: "filter",
        done: filterDone,
        status: filterDone ? "done" : "todo",
        proof: firstAlert ? "verified" : "you",
      },
      {
        id: "first-alert",
        done: firstAlert,
        status: firstAlert ? "done" : filterDone ? "waiting" : "todo",
        proof: "verified",
      },
    ] satisfies Stage[]
  ).map((stage) =>
    // Arriving expenses settle every stage behind them. A recap that still
    // showed a stage as pending, for an account already receiving, would be
    // reporting on the clicks rather than on the setup.
    complete ? { ...stage, done: true, status: "done" } : stage,
  );

  const nothingStarted =
    !sendersApproved &&
    !forwardingConfirmed &&
    !firstAlert &&
    acks.forwardingRequestedAt === null &&
    !acks.gmailSubmitted;

  return {
    stages,
    current: complete ? null : (stages.find((stage) => !stage.done)?.id ?? null),
    complete,
    doneCount: stages.filter((stage) => stage.done).length,
    total: stages.length,
    celebrate: complete && !acks.readyCelebrated,
    // Guarded on `complete` for the same reason `celebrate` is, the other way
    // round: somebody signing in on a new browser with everything already
    // connected must not be walked through connecting it.
    welcome: !complete && !acks.welcomeSeen,
    intro: !complete && !acks.introSeen && nothingStarted,
    address: setup.address,
    addressStatus: complete
      ? "receiving"
      : forwardingConfirmed
        ? "confirmed"
        : "unverified",
    unapprovedSenders: setup.unapproved_senders,
    sendersApproved,
    forwardingConfirmedAt: forwardingConfirmed ? (forwarding?.at ?? null) : null,
    firstAlertAt: firstAlert ? (alert?.at ?? null) : null,
    forwardingRequestedAt: acks.forwardingRequestedAt,
    filterSenders: acks.filterSenders,
  };
}

/** The 1-based position the URL carries, for a stage. */
export function stageNumber(id: StageId): number {
  return STAGES.indexOf(id) + 1;
}

/** The stage a 1-based position names, if it names one. */
export function stageAt(position: number): StageId | undefined {
  return STAGES[position - 1];
}
