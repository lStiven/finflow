/**
 * What can be said about the mail that reached somebody's address, from the
 * records alone.
 *
 * The setup endpoint answers "did an email get past the sender filter?" — a
 * fact about the route, not about the money. Whether a movement came out of
 * it is a second fact, read from the notification's status and from the
 * movements themselves. The screens keep the two apart on purpose: "your
 * first alert arrived" and "Finflow is working" are different claims, and the
 * second one needs a movement to point at.
 */

import type { Notification, Transaction } from "@/api/queries";
import { isApproved, type Senders } from "@/onboarding/banks";

/** A notification's status, as the person reading the screen would put it. */
export type Outcome = "registered" | "reading" | "unreadable" | "discarded";

export function outcomeOf(status: string): Outcome {
  switch (status) {
    case "processed":
      return "registered";
    case "pending_fallback":
    case "failed":
      return "unreadable";
    case "ignored":
      return "discarded";
    default:
      // `received`, `queued`, `processing` — and any status a newer backend
      // adds, which must not read as a success nobody verified.
      return "reading";
  }
}

export type Evidence =
  /** Nothing has got past the filter yet. */
  | { kind: "waiting" }
  /** Mail arrived and was thrown away: nobody approved its sender. */
  | { kind: "discarded"; senders: string[] }
  /** It got through and is still being read. */
  | { kind: "reading"; at: number }
  /** It got through, and nothing could be read out of it. */
  | { kind: "unreadable"; at: number }
  /** It got through earlier, and the recent page shows nothing about it. */
  | { kind: "received" }
  /** A movement came out of it. `movement` is null when none is listed. */
  | { kind: "registered"; movement: Transaction | null };

export function evidenceOf({
  firstAlert,
  unapprovedSenders,
  notifications,
  movement,
}: {
  firstAlert: boolean;
  unapprovedSenders: string[];
  /** Newest first, as the API lists them. */
  notifications: Notification[];
  /** The newest movement that came from a bank alert, if any. */
  movement: Transaction | null;
}): Evidence {
  if (movement) return { kind: "registered", movement };

  if (!firstAlert) {
    return unapprovedSenders.length > 0
      ? { kind: "discarded", senders: unapprovedSenders }
      : { kind: "waiting" };
  }

  const accepted = notifications.filter(
    (notification) => outcomeOf(notification.status) !== "discarded",
  );
  if (
    accepted.some((notification) => outcomeOf(notification.status) === "registered")
  ) {
    return { kind: "registered", movement: null };
  }

  const reading = accepted.find(
    (notification) => outcomeOf(notification.status) === "reading",
  );
  if (reading) return { kind: "reading", at: reading.received_at };

  const newest = accepted[0];
  return newest ? { kind: "unreadable", at: newest.received_at } : { kind: "received" };
}

/** Past this without a single accepted email, a connection is not "fine". */
export const STALE_AFTER_DAYS = 14;

/** How many of the newest accepted emails must all fail to call it a fault. */
const UNREADABLE_RUN = 3;

export type Health =
  /** Nothing is listed at all. */
  | { kind: "quiet" }
  | { kind: "ok"; lastAt: number }
  /** Arriving once, and not for a while now. */
  | { kind: "stale"; lastAt: number }
  /** The newest mail is being thrown away for its sender. */
  | { kind: "discarding"; senders: string[] }
  /** The newest emails arrive and none of them can be read; `count` of them. */
  | { kind: "unreadable"; lastAt: number; count: number };

/**
 * How the connection looks from what arrived lately, for somebody whose setup
 * is already finished.
 *
 * Only what the records show. A Gmail filter that stopped forwarding leaves no
 * trace here, so the most this can say is that nothing has arrived in a while
 * — never that the filter broke, and never that all is well past that point.
 */
export function healthOf(
  notifications: Notification[],
  senders: Senders,
  now: number,
): Health {
  if (notifications.length === 0) return { kind: "quiet" };

  const firstAccepted = notifications.findIndex(
    (notification) => outcomeOf(notification.status) !== "discarded",
  );
  // What is still being turned away, newer than anything let through. Checked
  // against today's list, like the setup endpoint does: a sender approved a
  // minute ago keeps its old rejections, and naming it would send somebody
  // round a loop they already closed.
  const turnedAway = notifications
    .slice(0, firstAccepted === -1 ? notifications.length : firstAccepted)
    .map((notification) => notification.sender)
    .filter((sender) => !isApproved(sender, senders));
  if (turnedAway.length > 0) {
    return { kind: "discarding", senders: [...new Set(turnedAway)] };
  }

  const accepted = notifications.filter(
    (notification) => outcomeOf(notification.status) !== "discarded",
  );
  const newest = accepted[0];
  if (!newest) return { kind: "quiet" };

  const run = accepted.slice(0, UNREADABLE_RUN);
  if (run.every((notification) => outcomeOf(notification.status) === "unreadable")) {
    return { kind: "unreadable", lastAt: newest.received_at, count: run.length };
  }

  if (now - newest.received_at > STALE_AFTER_DAYS * 86_400) {
    return { kind: "stale", lastAt: newest.received_at };
  }

  return { kind: "ok", lastAt: newest.received_at };
}
