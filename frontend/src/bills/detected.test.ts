import { describe, expect, it } from "vitest";
import type { RecurringSeries } from "@/api/queries";
import {
  asDeclaration,
  certaintyOf,
  evidenceLabel,
  hasProposals,
  nextChargeLabel,
  suggestions,
} from "@/bills/detected";

function series(overrides: Partial<RecurringSeries> = {}): RecurringSeries {
  return {
    key: "merchant:netflix",
    name: "Netflix",
    merchant_id: "netflix",
    category: "subscriptions",
    direction: "outgoing",
    cadence: "monthly",
    amount: "44900",
    currency: "COP",
    variable: false,
    confidence: "0.92",
    sightings: 6,
    missed: 0,
    first_seen: "2026-04-15",
    last_seen: "2026-09-15",
    next_due_on: "2026-10-15",
    state: "active",
    account_id: "account-1",
    bill_id: null,
    ...overrides,
  };
}

describe("qué se propone", () => {
  it("pone primero lo más seguro", () => {
    const list = suggestions([
      series({ key: "a", name: "Spotify", confidence: "0.61" }),
      series({ key: "b", name: "Netflix", confidence: "0.95" }),
    ]);

    expect(list.map((found) => found.name)).toEqual(["Netflix", "Spotify"]);
  });

  it("deja al final lo que ya está declarado, sin esconderlo", () => {
    // Hiding it would read as the detector having missed it; marking it is
    // what shows the list knows what it is looking at.
    const list = suggestions([
      series({ key: "a", name: "Gimnasio", confidence: "0.99", bill_id: "bill-1" }),
      series({ key: "b", name: "Netflix", confidence: "0.70" }),
    ]);

    expect(list.map((found) => found.name)).toEqual(["Netflix", "Gimnasio"]);
  });

  it("no ofrece declarar la nómina", () => {
    // The server detects it and E3 wants it. "Declarar" beside money somebody
    // is waiting for asks them to do the wrong thing.
    const list = suggestions([
      series({ name: "NOMINA ACME", direction: "incoming" }),
      series({ name: "Netflix" }),
    ]);

    expect(list.map((found) => found.name)).toEqual(["Netflix"]);
  });

  it("una lista donde todo está declarado ya no propone nada", () => {
    expect(hasProposals([series({ bill_id: "bill-1" })])).toBe(false);
    expect(hasProposals([series()])).toBe(true);
  });
});

describe("qué tan seguro", () => {
  it("son tres bandas, no un porcentaje", () => {
    expect(certaintyOf(series({ confidence: "0.95" }))).toBe("high");
    expect(certaintyOf(series({ confidence: "0.72" }))).toBe("medium");
    expect(certaintyOf(series({ confidence: "0.41" }))).toBe("low");
  });

  it("la evidencia se puede contrastar con el banco de uno", () => {
    expect(evidenceLabel(series({ sightings: 4, missed: 0 }))).toBe(
      "4 cobros, ninguno faltó",
    );
    expect(evidenceLabel(series({ sightings: 5, missed: 1 }))).toBe(
      "5 cobros, faltó uno",
    );
    expect(evidenceLabel(series({ sightings: 6, missed: 2 }))).toBe(
      "6 cobros, faltaron 2",
    );
  });
});

describe("cuándo cae el próximo", () => {
  it("lo que viene se anuncia", () => {
    expect(
      nextChargeLabel(series({ next_due_on: "2026-10-15" }), "15 oct", "2026-09-15"),
    ).toBe("próximo 15 oct, en 30 días");
  });

  it("lo que ya venció no se llama «próximo»", () => {
    // A detected charge can be days past its day and the series still be
    // live — the grace is a fifth of the cadence. "Próximo 9 de sept, hace 6
    // días" is the sentence this rule exists to prevent.
    expect(
      nextChargeLabel(series({ next_due_on: "2026-09-09" }), "9 sept", "2026-09-15"),
    ).toBe("se esperaba el 9 sept, hace 6 días");
  });
});

describe("aceptar una sugerencia", () => {
  it("declara con el próximo cobro como ancla", () => {
    // The anchor decides the day every later charge lands on, so it has to be
    // the day this one has been landing on — not the day somebody accepted.
    const body = asDeclaration(series());

    expect(body).toEqual({
      name: "Netflix",
      amount: "44900",
      currency: "COP",
      cadence: "monthly",
      starts_on: "2026-10-15",
      direction: "outgoing",
      account_id: "account-1",
      category: "subscriptions",
    });
  });

  it("no arrastra «sin categoría» como si fuera una categoría", () => {
    // It is the absence of an answer, and the picker itself hides it.
    expect(asDeclaration(series({ category: "uncategorized" })).category).toBeNull();
    expect(asDeclaration(series({ category: null })).category).toBeNull();
  });
});
