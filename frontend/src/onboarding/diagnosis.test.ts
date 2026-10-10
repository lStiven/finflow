import { describe, expect, it } from "vitest";
import type { InboxSetup } from "@/api/queries";
import { diagnoseEmpty } from "@/onboarding/diagnosis";

function setup(approved: boolean, confirmed: boolean): InboxSetup {
  return {
    address: "finflowingest+abc@gmail.com",
    steps: [
      { key: "address_assigned", done: true, at: null },
      { key: "senders_approved", done: approved, at: null },
      { key: "forwarding_confirmed", done: confirmed, at: confirmed ? 1 : null },
      { key: "first_alert", done: false, at: null },
    ],
    current: null,
    ready: false,
    unapproved_senders: [],
    gmail_filter: "",
    gmail_filter_terms: [],
  };
}

describe("why a screen has no movements", () => {
  it("says nothing until both answers are in", () => {
    expect(diagnoseEmpty(undefined, { kind: "quiet" })).toBeNull();
    expect(diagnoseEmpty(setup(true, true), null)).toBeNull();
  });

  it("sends somebody with no bank chosen to choose one", () => {
    const said = diagnoseEmpty(setup(false, false), { kind: "quiet" });

    expect(said?.title).toBe("Tu banco todavía no está conectado");
    expect(said?.action.paso).toBe(1);
  });

  /* The failure that looks exactly like silence from a list of movements. */
  it("names the sender being thrown away", () => {
    const said = diagnoseEmpty(setup(true, true), {
      kind: "discarding",
      senders: ["alertas@otrobanco.com"],
    });

    expect(said?.tone).toBe("warn");
    expect(said?.body).toContain("alertas@otrobanco.com");
  });

  it("tells mail that arrived unread from mail that never arrived", () => {
    expect(
      diagnoseEmpty(setup(true, true), { kind: "unreadable", lastAt: 1, count: 3 })
        ?.title,
    ).toBe("Llegaron alertas, pero ninguna se pudo leer");
    expect(diagnoseEmpty(setup(true, false), { kind: "quiet" })?.title).toBe(
      "Falta que Gmail confirme tu dirección",
    );
  });

  /* A Gmail filter cannot be seen from here: it is never called wrong. */
  it("says what cannot be observed instead of guessing at it", () => {
    const said = diagnoseEmpty(setup(true, true), { kind: "quiet" });

    expect(said?.body).toContain("no se puede ver desde aquí");
  });

  it("warns when nothing has arrived for days", () => {
    expect(diagnoseEmpty(setup(true, true), { kind: "stale", lastAt: 1 })?.tone).toBe(
      "warn",
    );
  });

  it("stays quiet when mail arrives and is read", () => {
    expect(diagnoseEmpty(setup(true, true), { kind: "ok", lastAt: 1 })).toBeNull();
  });
});
