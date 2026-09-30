import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  describeEntry,
  freshEntries,
  MAX_SEEN,
  oneLine,
  readSeen,
  seenKey,
  unreadCount,
  weekLabel,
  writeSeen,
} from "@/alerts/inbox";
import type { AlertsInboxEntry } from "@/api/queries";

function movement(
  overrides: Partial<NonNullable<AlertsInboxEntry["movement"]>> = {},
  entry: Partial<AlertsInboxEntry> = {},
): AlertsInboxEntry {
  return {
    id: "e1",
    kind: "movement",
    created_at: 1_790_000_000,
    summary: null,
    movement: {
      movement_id: "4f2a9c",
      direction: "outgoing",
      amount: "84300",
      currency: "COP",
      counterparty: "COMPRA EN EXITO",
      bank: "Bancolombia",
      occurred_at: 1_789_999_970,
      origin: "bank_alert",
      unassigned: false,
      budgets: [],
      ...overrides,
    },
    ...entry,
  };
}

function summary(
  overrides: Partial<NonNullable<AlertsInboxEntry["summary"]>> = {},
): AlertsInboxEntry {
  return {
    id: "w1",
    kind: "weekly_summary",
    created_at: 1_790_100_000,
    movement: null,
    summary: {
      week_start: "2026-09-21",
      week_end: "2026-09-27",
      currency: "COP",
      spent: "820000",
      movements: 12,
      typical: "930000",
      rise: null,
      ...overrides,
    },
  };
}

// `Intl` puts a no-break space between the symbol and the figure.
const nbsp = (text: string) => text.replace(/ /g, " ");

describe("describeEntry — a movement", () => {
  it("says what moved and with whom, and links to it", () => {
    const described = describeEntry(movement());

    expect(nbsp(described.title)).toBe("Gasto $ 84.300");
    expect(described.lines).toEqual(["COMPRA EN EXITO"]);
    expect(described.tone).toBe("outgoing");
    expect(described.movementId).toBe("4f2a9c");
  });

  it("calls income income", () => {
    const described = describeEntry(movement({ direction: "incoming" }));

    expect(described.title.startsWith("Ingreso")).toBe(true);
    expect(described.tone).toBe("incoming");
  });

  it("adds a line per budget: left, reached and passed", () => {
    const described = describeEntry(
      movement({
        budgets: [
          {
            name: "Mercado",
            currency: "COP",
            limit: "600000",
            spent: "480000",
            remaining: "120000",
            state: "warning",
          },
          {
            name: "Todo el mes",
            currency: "COP",
            limit: "2000000",
            spent: "2000000",
            remaining: "0",
            state: "over",
          },
          {
            name: "Salidas",
            currency: "COP",
            limit: "300000",
            spent: "330000",
            remaining: "-30000",
            state: "over",
          },
        ],
      }),
    );

    expect(described.lines.slice(1).map(nbsp)).toEqual([
      "Mercado: te quedan $ 120.000 de $ 600.000",
      "Todo el mes: llegaste al tope de $ 2.000.000",
      "Salidas: vas $ 30.000 por encima del tope de $ 300.000",
    ]);
  });

  it("sums up more than three budgets", () => {
    const budget = {
      name: "B",
      currency: "COP",
      limit: "100",
      spent: "50",
      remaining: "50",
      state: "ok" as const,
    };
    const described = describeEntry(
      movement({ budgets: [budget, budget, budget, budget, budget] }),
    );

    expect(described.lines.at(-1)).toBe("y 2 presupuestos más");
  });

  it("never lets bank text forge a second line", () => {
    const described = describeEntry(
      movement({ counterparty: "COMPRA\nGasto $1 PAGADO", unassigned: true }),
    );

    expect(described.lines).toEqual(["COMPRA Gasto $1 PAGADO", "Sin cuenta asignada"]);
  });

  it("trims a very long counterparty", () => {
    expect(oneLine("x".repeat(100)).length).toBe(64);
  });
});

describe("describeEntry — a week", () => {
  it("compares the week with its owner's normal", () => {
    const described = describeEntry(summary());

    expect(described.title).toBe("Tu semana (21–27 sep)");
    expect(described.lines.map(nbsp)).toEqual([
      "Gastaste $ 820.000 en 12 gastos.",
      "12 % menos que tu semana normal ($ 930.000).",
    ]);
    expect(described.movementId).toBeNull();
  });

  it("says more, and names what went up", () => {
    const described = describeEntry(
      summary({
        spent: "1116000",
        rise: {
          category: "restaurants",
          label: "Restaurants",
          spent: "240000",
          typical: "155000",
        },
      }),
    );

    expect(nbsp(described.lines[1] ?? "")).toBe(
      "20 % más que tu semana normal ($ 930.000).",
    );
    expect(nbsp(described.lines[2] ?? "")).toBe(
      "Lo que más subió: Restaurantes, $ 240.000 (normalmente $ 155.000).",
    );
  });

  it("does not invent a comparison in the first week", () => {
    const described = describeEntry(summary({ typical: null }));

    expect(described.lines[1]).toContain("primera semana");
  });

  it("says a week without spending plainly", () => {
    const described = describeEntry(summary({ spent: "0", movements: 0 }));

    expect(described.lines.map(nbsp)).toEqual([
      "No registraste gastos esta semana.",
      "Tu semana normal es de $ 930.000.",
    ]);
  });
});

describe("weekLabel", () => {
  it("names both months across a boundary", () => {
    expect(weekLabel("2026-09-28", "2026-10-04")).toBe("28 sep – 4 oct");
  });
});

describe("freshEntries", () => {
  const a = movement({}, { id: "a", created_at: 1 });
  const b = movement({}, { id: "b", created_at: 2 });
  const c = movement({}, { id: "c", created_at: 3 });

  it("treats everything on the first look as history, not news", () => {
    expect(freshEntries(null, [c, b, a])).toEqual([]);
  });

  it("returns only what arrived since, oldest first", () => {
    expect(freshEntries(new Set(["a"]), [c, b, a]).map((entry) => entry.id)).toEqual([
      "b",
      "c",
    ]);
  });
});

describe("unreadCount", () => {
  const entries = [
    movement({}, { id: "a", created_at: 10 }),
    movement({}, { id: "b", created_at: 20 }),
  ];

  it("counts what has not been seen, by id", () => {
    expect(unreadCount(entries, new Set(["a"]))).toBe(1);
    expect(unreadCount(entries, new Set(["a", "b"]))).toBe(0);
  });

  it("counts an entry stamped earlier than one already seen", () => {
    // A retried weekly run: fixed, older stamp, never seen.
    const late = summary();
    expect(
      unreadCount([movement({}, { id: "b", created_at: 9e9 }), late], new Set(["b"])),
    ).toBe(1);
  });

  it("counts everything when the list was never opened", () => {
    expect(unreadCount(entries, null)).toBe(2);
  });
});

describe("seen ids", () => {
  let store: Map<string, string>;

  /** Node has no `window`; only `localStorage` is touched, so only it is stubbed. */
  function stubStorage(throws = false) {
    store = new Map();
    vi.stubGlobal("window", {
      localStorage: {
        getItem: (key: string) => {
          if (throws) throw new Error("blocked");
          return store.get(key) ?? null;
        },
        setItem: (key: string, value: string) => {
          if (throws) throw new Error("blocked");
          store.set(key, value);
        },
      },
    });
  }

  beforeEach(() => stubStorage());
  afterEach(() => vi.unstubAllGlobals());

  it("are remembered per user, and added to", () => {
    writeSeen("u1", ["a"]);
    writeSeen("u1", ["b"]);

    expect(readSeen("u1")).toEqual(new Set(["a", "b"]));
    expect(readSeen("u2")).toBeNull();
  });

  it("stay bounded, newest kept", () => {
    writeSeen("u1", ["old"]);
    writeSeen(
      "u1",
      Array.from({ length: MAX_SEEN }, (_, index) => `n${index}`),
    );

    const seen = readSeen("u1");
    expect(seen?.size).toBe(MAX_SEEN);
    expect(seen?.has("old")).toBe(false);
  });

  it("read garbage as never", () => {
    store.set(seenKey("u1"), "nope");

    expect(readSeen("u1")).toBeNull();
  });

  it("survive storage that refuses", () => {
    stubStorage(true);

    expect(() => writeSeen("u1", ["a"])).not.toThrow();
    expect(readSeen("u1")).toBeNull();
  });
});
