import { describe, expect, it } from "vitest";
import type { Allowance, RecurringSeries } from "@/api/queries";
import {
  breakdownOf,
  incomeSuggestions,
  monthlyEquivalent,
  parseKept,
  perDay,
  toneOf,
  usedShare,
} from "@/plan/allowance";

function allowance(overrides: Partial<Allowance> = {}): Allowance {
  return {
    currency: "COP",
    expected_income: "5000000",
    savings_target: "0",
    spent: "0",
    committed: "0",
    available: "5000000",
    since: "2026-09-01",
    until: "2026-09-30",
    days_left: 16,
    ...overrides,
  };
}

function series(overrides: Partial<RecurringSeries> = {}): RecurringSeries {
  return {
    key: "merchant:acme|incoming|COP",
    name: "NOMINA ACME",
    merchant_id: "acme",
    category: "income",
    direction: "incoming",
    cadence: "monthly",
    amount: "4200000",
    currency: "COP",
    variable: false,
    confidence: "0.95",
    sightings: 6,
    missed: 0,
    first_seen: "2026-04-30",
    last_seen: "2026-09-30",
    next_due_on: "2026-10-30",
    state: "active",
    account_id: null,
    bill_id: null,
    ...overrides,
  };
}

describe("lo que se quiere guardar", () => {
  it("vacío es cero, porque casi nadie aparta algo", () => {
    expect(parseKept("")).toBe("0");
    expect(parseKept("   ")).toBe("0");
  });

  it("y un cero tecleado también", () => {
    // El parser compartido lo rechaza a propósito —una factura de cero es un
    // error— y aquí es la respuesta más común de todas.
    expect(parseKept("0")).toBe("0");
    expect(parseKept("0,00")).toBe("0");
  });

  it("una cifra de verdad se respeta", () => {
    expect(parseKept("1.200.000")).toBe("1200000");
  });

  it("lo que no es un número se rechaza", () => {
    expect(parseKept("mucho")).toBeNull();
  });
});

describe("cómo se lee el disponible", () => {
  it("con margen es sano", () => {
    expect(toneOf(allowance({ available: "3000000" }))).toBe("healthy");
  });

  it("con menos de una décima parte está apretado", () => {
    // "Te queda poco" y "te pasaste" son situaciones distintas, y solo una de
    // las dos sigue siendo un presupuesto.
    expect(toneOf(allowance({ available: "400000" }))).toBe("tight");
  });

  it("en negativo se dice que se pasó", () => {
    expect(toneOf(allowance({ available: "-200000" }))).toBe("over");
  });
});

describe("cuánto por día", () => {
  it("reparte lo que queda entre los días que faltan", () => {
    expect(perDay(allowance({ available: "1600000", days_left: 16 }))).toBe("100000");
  });

  it("redondea hacia abajo, nunca hacia arriba", () => {
    // Redondear hacia arriba entrega una cifra que, gastada todos los días,
    // termina el mes por encima — la única dirección en la que este número no
    // puede equivocarse.
    expect(perDay(allowance({ available: "100", days_left: 3 }))).toBe("33");
  });

  it("no dice nada cuando ya no queda nada", () => {
    expect(perDay(allowance({ available: "0" }))).toBeNull();
    expect(perDay(allowance({ available: "-50000" }))).toBeNull();
  });

  it("ni cuando el mes ya se acabó", () => {
    expect(perDay(allowance({ available: "500000", days_left: 0 }))).toBeNull();
  });
});

describe("de qué está hecho el número", () => {
  it("enseña la resta completa", () => {
    const rows = breakdownOf(
      allowance({
        savings_target: "500000",
        spent: "300000",
        committed: "120000",
        available: "4080000",
      }),
    );

    expect(rows.map((row) => [row.label, row.amount, row.subtracted])).toEqual([
      ["Esperas que entren", "5000000", false],
      ["Quieres guardar", "500000", true],
      ["Ya gastaste", "300000", true],
      ["Facturas sin pagar", "120000", true],
    ]);
  });

  it("no pinta las líneas en cero", () => {
    // «$0 guardados» es una fila que empuja fuera de la pantalla a las que sí
    // importan.
    expect(breakdownOf(allowance()).map((row) => row.label)).toEqual([
      "Esperas que entren",
    ]);
  });
});

describe("la barra", () => {
  it("cuenta lo gastado y lo que se debe", () => {
    // Una factura sin pagar es plata que no se puede gastar dos veces.
    expect(
      usedShare(allowance({ spent: "1000000", committed: "1500000" })),
    ).toBeCloseTo(0.5);
  });

  it("no se sale de sus topes", () => {
    expect(usedShare(allowance({ spent: "9000000" }))).toBe(1);
    expect(
      usedShare(allowance({ expected_income: "1000", savings_target: "1000" })),
    ).toBe(1);
  });
});

describe("la nómina que el detector ya conoce", () => {
  it("una quincenal vale más que dos veces al mes", () => {
    // Cae veintiséis veces al año: 2,17 meses de sueldo, no 2. Un formulario
    // que rellenara 2 subestimaría el año en una quincena.
    expect(monthlyEquivalent(series({ cadence: "biweekly", amount: "2100000" }))).toBe(
      "4550000",
    );
  });

  it("una mensual vale lo que dice", () => {
    expect(monthlyEquivalent(series({ amount: "4200000" }))).toBe("4200000");
  });

  it("solo se ofrece lo que entra, y en la moneda que se está declarando", () => {
    const found = incomeSuggestions(
      [
        series({ key: "a", name: "NOMINA", direction: "incoming" }),
        series({ key: "b", name: "GIMNASIO", direction: "outgoing" }),
        series({ key: "c", name: "UPWORK", direction: "incoming", currency: "USD" }),
      ],
      "COP",
    );

    expect(found.map((each) => each.name)).toEqual(["NOMINA"]);
  });
});
