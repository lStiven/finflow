import { describe, expect, it } from "vitest";
import type { Notification, Transaction } from "@/api/queries";
import {
  evidenceOf,
  healthOf,
  outcomeOf,
  STALE_AFTER_DAYS,
} from "@/onboarding/activity";
import type { Senders } from "@/onboarding/banks";

const NOW = 1_760_000_000;
const HOUR = 3_600;
const BANK = "alertasynotificaciones@an.notificacionesbancolombia.com";
const SENDERS: Senders = {
  domains: ["an.notificacionesbancolombia.com"],
  addresses: [],
};

let next = 0;

/** Newest first, like the API: pass them in that order. */
function mail(status: string, ago: number, sender = BANK): Notification {
  next += 1;
  return {
    id: `n${next}`,
    message_id: `<m${next}@test>`,
    sender,
    subject: "Alerta",
    status,
    deferred_reason: null,
    received_at: NOW - ago,
  };
}

const MOVEMENT = { id: "t1", amount: "45000", currency: "COP" } as Transaction;

describe("a notification's outcome", () => {
  it("names each status the way the screen talks about it", () => {
    expect(outcomeOf("processed")).toBe("registered");
    expect(outcomeOf("queued")).toBe("reading");
    expect(outcomeOf("pending_fallback")).toBe("unreadable");
    expect(outcomeOf("failed")).toBe("unreadable");
    expect(outcomeOf("ignored")).toBe("discarded");
  });

  it("never reads a status it does not know as a success", () => {
    expect(outcomeOf("archived_by_a_newer_backend")).toBe("reading");
  });
});

describe("the evidence that the route works", () => {
  it("waits while nothing got past the filter", () => {
    expect(
      evidenceOf({
        firstAlert: false,
        unapprovedSenders: [],
        notifications: [],
        movement: null,
      }),
    ).toEqual({ kind: "waiting" });
  });

  it("says mail is being thrown away when that is what is happening", () => {
    expect(
      evidenceOf({
        firstAlert: false,
        unapprovedSenders: ["alertas@otro.com"],
        notifications: [mail("ignored", 60, "alertas@otro.com")],
        movement: null,
      }),
    ).toEqual({ kind: "discarded", senders: ["alertas@otro.com"] });
  });

  it("reports an alert still being read, with when it landed", () => {
    expect(
      evidenceOf({
        firstAlert: true,
        unapprovedSenders: [],
        notifications: [mail("processing", 30)],
        movement: null,
      }),
    ).toEqual({ kind: "reading", at: NOW - 30 });
  });

  /*
   * An email got through and produced nothing: the route works, but saying
   * "Finflow is working" would claim a movement that does not exist.
   */
  it("keeps an unreadable alert apart from a registered one", () => {
    expect(
      evidenceOf({
        firstAlert: true,
        unapprovedSenders: [],
        notifications: [mail("pending_fallback", 30)],
        movement: null,
      }),
    ).toEqual({ kind: "unreadable", at: NOW - 30 });
  });

  it("points at the movement when there is one", () => {
    expect(
      evidenceOf({
        firstAlert: true,
        unapprovedSenders: [],
        notifications: [mail("processed", 30)],
        movement: MOVEMENT,
      }),
    ).toEqual({ kind: "registered", movement: MOVEMENT });
  });

  it("counts a processed alert even when no movement is listed for it", () => {
    expect(
      evidenceOf({
        firstAlert: true,
        unapprovedSenders: [],
        notifications: [mail("ignored", 10, "x@otro.com"), mail("processed", 30)],
        movement: null,
      }),
    ).toEqual({ kind: "registered", movement: null });
  });

  it("says only that mail arrived when the recent page shows none of it", () => {
    expect(
      evidenceOf({
        firstAlert: true,
        unapprovedSenders: [],
        notifications: [],
        movement: null,
      }),
    ).toEqual({ kind: "received" });
  });
});

describe("a finished connection's health", () => {
  it("is fine when the newest accepted email is recent and readable", () => {
    expect(healthOf([mail("processed", 2 * HOUR)], SENDERS, NOW)).toEqual({
      kind: "ok",
      lastAt: NOW - 2 * HOUR,
    });
  });

  it("is quiet when nothing is listed", () => {
    expect(healthOf([], SENDERS, NOW)).toEqual({ kind: "quiet" });
  });

  /*
   * A filter that stopped forwarding leaves no record. All that can be said
   * is that nothing arrived — which must not wear a success label.
   */
  it("goes stale when nothing was accepted for too long", () => {
    const ago = (STALE_AFTER_DAYS + 1) * 86_400;

    expect(healthOf([mail("processed", ago)], SENDERS, NOW)).toEqual({
      kind: "stale",
      lastAt: NOW - ago,
    });
  });

  it("names who is being turned away, newer than anything let through", () => {
    expect(
      healthOf(
        [
          mail("ignored", 10, "nuevo@bancolombia.com.co"),
          mail("ignored", 20, "nuevo@bancolombia.com.co"),
          mail("processed", 3 * HOUR),
        ],
        SENDERS,
        NOW,
      ),
    ).toEqual({ kind: "discarding", senders: ["nuevo@bancolombia.com.co"] });
  });

  it("forgets a rejection once its sender is approved", () => {
    const approved: Senders = { ...SENDERS, addresses: ["nuevo@bancolombia.com.co"] };

    expect(
      healthOf(
        [mail("ignored", 10, "nuevo@bancolombia.com.co"), mail("processed", HOUR)],
        approved,
        NOW,
      ).kind,
    ).toBe("ok");
  });

  it("flags a run of unreadable emails, and says how many", () => {
    expect(
      healthOf(
        [
          mail("failed", 10),
          mail("pending_fallback", 20),
          mail("pending_fallback", 30),
        ],
        SENDERS,
        NOW,
      ),
    ).toEqual({ kind: "unreadable", lastAt: NOW - 10, count: 3 });
  });

  it("does not flag one unreadable email among readable ones", () => {
    expect(
      healthOf([mail("pending_fallback", 10), mail("processed", 20)], SENDERS, NOW)
        .kind,
    ).toBe("ok");
  });
});
