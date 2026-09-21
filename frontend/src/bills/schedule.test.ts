import { describe, expect, it } from "vitest";
import type { Bill, BillOccurrence, BillTotal } from "@/api/queries";
import {
  billState,
  chargedAmount,
  chargeLabel,
  chargeVerbs,
  countsTowardsSpending,
  daysUntil,
  formatAmountInput,
  groupByDay,
  initialOf,
  isMatched,
  isSettled,
  parseAmount,
  settledShare,
  singleTotal,
  whenLabel,
} from "@/bills/schedule";

function bill(overrides: Partial<Bill> = {}): Bill {
  return {
    id: "b1",
    name: "Gimnasio",
    amount: "120000",
    currency: "COP",
    cadence: "monthly",
    starts_on: "2026-09-04",
    direction: "outgoing",
    account_id: null,
    category: null,
    status: "active",
    autopay: false,
    autopay_from: null,
    frozen: false,
    next_occurrence: null,
    ...overrides,
  };
}

function occurrence(overrides: Partial<BillOccurrence> = {}): BillOccurrence {
  return {
    bill_id: "b1",
    due_on: "2026-09-04",
    amount: "120000",
    currency: "COP",
    direction: "outgoing",
    state: "expected",
    ...overrides,
  };
}

describe("qué dice una factura de sí misma", () => {
  it("una pausada no está atrasada, está parada", () => {
    // The order is the rule: a paused bill predicts nothing, so calling it
    // overdue would be a charge nobody is expecting.
    const paused = bill({
      status: "paused",
      next_occurrence: occurrence({ state: "overdue" }),
    });

    expect(billState(paused)).toBe("paused");
  });

  it("una congelada tampoco está atrasada", () => {
    const frozen = bill({
      frozen: true,
      next_occurrence: occurrence({ state: "overdue" }),
    });

    expect(billState(frozen)).toBe("frozen");
  });

  it("una cuyo cobro ya pasó su fecha lo dice", () => {
    expect(billState(bill({ next_occurrence: occurrence({ state: "overdue" }) }))).toBe(
      "overdue",
    );
  });

  it("una normal no dice nada", () => {
    expect(billState(bill())).toBe("active");
  });
});

describe("qué entra en el total del mes", () => {
  it("un ingreso declarado no se resta del gasto", () => {
    // A salary is a real recurring series and the next deliverable wants it.
    // Netting it here would report a month costing less than it costs.
    expect(countsTowardsSpending(bill({ direction: "incoming" }))).toBe(false);
  });

  it("una pausada no suma", () => {
    expect(countsTowardsSpending(bill({ status: "paused" }))).toBe(false);
  });

  it("una activa que sale sí", () => {
    expect(countsTowardsSpending(bill())).toBe(true);
  });
});

describe("las dos cifras", () => {
  const cop: BillTotal = {
    currency: "COP",
    expected: "850000",
    outstanding: "310000",
  };
  const usd: BillTotal = { currency: "USD", expected: "15", outstanding: "15" };

  it("con una sola moneda hay titular", () => {
    expect(singleTotal([cop])).toBe(cop);
  });

  it("con dos no lo hay: sumarlas necesitaría una tasa que no tenemos", () => {
    expect(singleTotal([cop, usd])).toBeNull();
  });

  it("sin facturas tampoco", () => {
    expect(singleTotal([])).toBeNull();
  });
});

describe("agrupar por día", () => {
  it("junta los cobros del mismo día sin reordenar", () => {
    const groups = groupByDay([
      occurrence({ due_on: "2026-09-01", bill_id: "a" }),
      occurrence({ due_on: "2026-09-04", bill_id: "b" }),
      occurrence({ due_on: "2026-09-04", bill_id: "c" }),
    ]);

    expect(groups.map((g) => g.day)).toEqual(["2026-09-01", "2026-09-04"]);
    expect(groups[1]?.occurrences).toHaveLength(2);
  });

  it("sin cobros no hay grupos", () => {
    expect(groupByDay([])).toEqual([]);
  });
});

describe("el monto tal como se teclea", () => {
  it("quita los puntos de miles", () => {
    expect(parseAmount("120.000")).toBe("120000");
  });

  it("entiende la coma como decimal", () => {
    expect(parseAmount("120.000,50")).toBe("120000.50");
  });

  it("rechaza el cero: una factura de nada es un error o un recordatorio vacío", () => {
    expect(parseAmount("0")).toBeNull();
  });

  it("rechaza lo que no es un número", () => {
    expect(parseAmount("como veinte mil")).toBeNull();
    expect(parseAmount("")).toBeNull();
  });

  it("da la vuelta para volver al campo", () => {
    expect(formatAmountInput("120000")).toBe("120.000");
    expect(formatAmountInput("120000.50")).toBe("120.000,50");
    // Cents the server sends as `.00` are noise in pesos.
    expect(formatAmountInput("120000.00")).toBe("120.000");
  });
});

describe("cuándo cae el próximo cobro", () => {
  it("cuenta días de calendario, no instantes", () => {
    // A `Date` built from one of these and read back in another zone is how a
    // charge due on the 1st starts showing up on the 31st.
    expect(daysUntil("2026-10-01", "2026-09-28")).toBe(3);
    expect(daysUntil("2026-09-28", "2026-10-01")).toBe(-3);
  });

  it("cruza el cambio de mes sin equivocarse", () => {
    expect(daysUntil("2026-03-01", "2026-02-28")).toBe(1);
  });

  it("dice hoy, mañana y ayer por su nombre", () => {
    expect(whenLabel("2026-09-14", "2026-09-14")).toBe("hoy");
    expect(whenLabel("2026-09-15", "2026-09-14")).toBe("mañana");
    expect(whenLabel("2026-09-13", "2026-09-14")).toBe("ayer");
  });

  it("y el resto en días", () => {
    expect(whenLabel("2026-09-18", "2026-09-14")).toBe("en 4 días");
    expect(whenLabel("2026-09-04", "2026-09-14")).toBe("hace 10 días");
  });
});

describe("la barra del mes", () => {
  it("llena lo que ya se pagó, no lo que ya venció", () => {
    // 850.000 comprometidos, 310.000 sin pagar → 63,5 % confirmado. Antes esto
    // medía días pasados, que es algo sobre lo que nadie puede actuar.
    expect(settledShare("850000", "310000")).toBeCloseTo(0.635, 3);
  });

  it("vacía cuando no se ha confirmado nada, aunque el mes se acabe", () => {
    expect(settledShare("850000", "850000")).toBe(0);
  });

  it("no divide por cero cuando no hay nada comprometido", () => {
    expect(settledShare("0", "0")).toBe(0);
  });

  it("se queda entre 0 y 1 aunque los números no cuadren", () => {
    expect(settledShare("100", "500")).toBe(0);
    expect(settledShare("100", "-500")).toBe(1);
  });
});

describe("responder por un cobro", () => {
  it("pagado y saltado están resueltos; lo demás no", () => {
    expect(isSettled(occurrence({ state: "paid" }))).toBe(true);
    expect(isSettled(occurrence({ state: "skipped" }))).toBe(true);
    expect(isSettled(occurrence({ state: "expected" }))).toBe(false);
    expect(isSettled(occurrence({ state: "overdue" }))).toBe(false);
  });

  it("lo normal no lleva etiqueta: ponerle una a todo esconde las dos que importan", () => {
    expect(chargeLabel(occurrence({ state: "expected" }))).toBeNull();
  });

  it("lo resuelto y lo vencido sí", () => {
    expect(chargeLabel(occurrence({ state: "paid" }))).toBe("Pagado");
    expect(chargeLabel(occurrence({ state: "skipped" }))).toBe("Saltado");
    expect(chargeLabel(occurrence({ state: "overdue" }))).toBe("Sin pagar");
  });

  it("un ingreso no se paga, llega", () => {
    // «Sin pagar» junto a la nómina lee como una deuda propia. El verbo lo
    // decide la dirección, que es lo único que distingue una de otra.
    const salary = { direction: "incoming" as const };

    expect(chargeLabel(occurrence({ ...salary, state: "overdue" }))).toBe("Sin llegar");
    expect(chargeLabel(occurrence({ ...salary, state: "paid" }))).toBe("Recibido");
    expect(chargeVerbs(occurrence(salary)).settle).toBe("Ya llegó");
    expect(chargeVerbs(occurrence()).settle).toBe("Pagar");
  });

  it("una vez pagado manda lo que salió, no lo que la factura proyectaba", () => {
    // El gimnasio subió de precio. Enseñar la previsión después de que el
    // dinero salió es que la pantalla y el extracto no coincidan.
    expect(chargedAmount(occurrence({ state: "paid", settled_amount: "130000" }))).toBe(
      "130000",
    );
  });

  it("sin pagar, la previsión es lo único que hay", () => {
    expect(chargedAmount(occurrence())).toBe("120000");
  });
});

describe("la letra de la tarjeta", () => {
  it("es la primera del nombre, en mayúscula", () => {
    expect(initialOf("gimnasio")).toBe("G");
    expect(initialOf("  Ñandú  ")).toBe("Ñ");
  });

  it("no revienta con un nombre vacío", () => {
    expect(initialOf("   ")).toBe("·");
  });
});

describe("un cobro respondido por un movimiento que ya estaba", () => {
  const paid = { state: "paid" } as const;

  it("se distingue del que escribió la app", () => {
    expect(isMatched(occurrence({ ...paid, settled_by: "matched" }))).toBe(true);
    expect(isMatched(occurrence({ ...paid, settled_by: "confirmed" }))).toBe(false);
    expect(isMatched(occurrence())).toBe(false);
  });

  it("no ofrece «deshacer el pago», porque no hay nada que borrar", () => {
    const linked = occurrence({ ...paid, settled_by: "matched" });
    const written = occurrence({ ...paid, settled_by: "confirmed" });

    expect(chargeVerbs(linked).undo).toBe("No es este");
    expect(chargeVerbs(written).undo).toBe("Deshacer el pago");
  });
});
