/**
 * Lo que la pantalla de presupuestos no puede equivocarse sola.
 *
 * Nada de aquí decide si un tope se pasó — eso lo decide el servidor y llega
 * en `state`. Lo que se comprueba es la geometría de la barra, la copia de
 * cada fila y, sobre todo, el paso de mes: un mes construido con `Date` y
 * sumado desde el 31 se salta octubre, y una barra de meses que se salta uno
 * es una barra a la que no se puede volver.
 */

import { describe, expect, it } from "vitest";
import type { BudgetProgress, BudgetTotal } from "@/api/queries";
import {
  captionOf,
  hasCaps,
  isNarrowed,
  leftOf,
  monthLabel,
  overBy,
  percentUsed,
  scopeLabel,
  shiftMonth,
  tallyOf,
  usedShare,
  warningMark,
  worstOf,
} from "@/budgets/progress";

function budget(overrides: Partial<BudgetProgress> = {}): BudgetProgress {
  return {
    id: "11111111-1111-1111-1111-111111111111",
    name: "Restaurantes",
    icon: "",
    scope: {
      categories: ["restaurants"],
      accounts: [],
      total: false,
      every_account: true,
    },
    currency: "COP",
    limit: "600000",
    spent: "150000",
    remaining: "450000",
    warn_at: 80,
    state: "ok",
    month: null,
    recurring: true,
    retired: false,
    missing: [],
    ...overrides,
  };
}

function total(overrides: Partial<BudgetTotal> = {}): BudgetTotal {
  return {
    currency: "COP",
    limit: "1000000",
    spent: "400000",
    remaining: "600000",
    ok: 3,
    warning: 1,
    over: 0,
    ...overrides,
  };
}

describe("la barra", () => {
  it("se llena con lo gastado sobre el tope", () => {
    expect(usedShare(budget({ limit: "600000", spent: "150000" }))).toBeCloseTo(0.25);
  });

  it("no se desborda cuando ya se pasó el tope", () => {
    expect(usedShare(budget({ limit: "100000", spent: "160000" }))).toBe(1);
  });

  it("está vacía sin gasto", () => {
    expect(usedShare(budget({ spent: "0" }))).toBe(0);
  });

  it("se da por llena si el tope llegara en cero", () => {
    // El dominio lo rechaza, así que esto no debería poder pasar; una división
    // por cero dibujada como NaN sí sería un ancho que el navegador ignora.
    expect(usedShare(budget({ limit: "0", spent: "10" }))).toBe(1);
  });

  it("marca dónde eligió el dueño que se ponga ámbar", () => {
    expect(warningMark(budget({ warn_at: 80 }))).toBeCloseTo(0.8);
    expect(warningMark(budget({ warn_at: 50 }))).toBeCloseTo(0.5);
  });
});

describe("lo que queda", () => {
  it("es lo que falta mientras falte algo", () => {
    expect(leftOf(budget({ remaining: "450000" }))).toBe("450000");
    expect(overBy(budget({ remaining: "450000" }))).toBeNull();
  });

  it("deja de decirse cuando ya no queda nada", () => {
    expect(leftOf(budget({ remaining: "0" }))).toBeNull();
  });

  it("pasado el tope se dice por cuánto, en positivo", () => {
    expect(overBy(budget({ remaining: "-140000" }))).toBe("140000");
    expect(leftOf(budget({ remaining: "-140000" }))).toBeNull();
  });
});

describe("la copia", () => {
  it("informa y no regaña al pasarse", () => {
    expect(captionOf(budget({ state: "over", limit: "100000", spent: "160000" }))).toBe(
      "Te pasaste del tope",
    );
  });

  it("dice por dónde va cuando está en ámbar", () => {
    expect(
      captionOf(budget({ state: "warning", limit: "600000", spent: "480000" })),
    ).toBe("Vas por el 80%");
  });

  it("dice lo que queda cuando está en verde", () => {
    expect(captionOf(budget({ state: "ok", limit: "600000", spent: "150000" }))).toBe(
      "Te queda el 75%",
    );
  });

  it("redondea el porcentaje a un entero", () => {
    expect(percentUsed(budget({ limit: "600000", spent: "200000" }))).toBe(33);
  });
});

describe("el resumen del tablero", () => {
  it("cuenta cuántos topes van bien de cuántos hay", () => {
    expect(tallyOf(total({ ok: 3, warning: 1, over: 1 }))).toEqual({ ok: 3, total: 5 });
  });

  it("reporta lo peor y no la mayoría", () => {
    // Un resumen que dijera «todo bien» con una categoría cien mil pesos por
    // encima es justo lo que esta tarjeta existe para no hacer.
    expect(worstOf(total({ ok: 9, warning: 0, over: 1 }))).toBe("over");
    expect(worstOf(total({ ok: 9, warning: 1, over: 0 }))).toBe("warning");
    expect(worstOf(total({ ok: 9, warning: 0, over: 0 }))).toBe("ok");
  });

  it("sin ningún tope no hay tarjeta", () => {
    expect(hasCaps([])).toBe(false);
    expect(hasCaps([total()])).toBe(true);
  });
});

describe("el mes", () => {
  it("se lee en español con su año", () => {
    expect(monthLabel("2026-09")).toBe("septiembre de 2026");
  });

  it("no se rompe con una clave que no es un mes", () => {
    expect(monthLabel("vaya")).toBe("vaya");
  });

  it("avanza y retrocede un mes", () => {
    expect(shiftMonth("2026-09", 1)).toBe("2026-10");
    expect(shiftMonth("2026-09", -1)).toBe("2026-08");
  });

  it("cruza el fin de año en los dos sentidos", () => {
    expect(shiftMonth("2026-12", 1)).toBe("2027-01");
    expect(shiftMonth("2026-01", -1)).toBe("2025-12");
  });

  it("no se salta un mes corto", () => {
    // Un `Date` del 31 sumado un mes cae en el mes siguiente al siguiente.
    expect(shiftMonth("2026-01", 1)).toBe("2026-02");
    expect(shiftMonth("2026-03", -1)).toBe("2026-02");
  });

  it("escribe el mes siempre con dos dígitos", () => {
    expect(shiftMonth("2026-08", 1)).toBe("2026-09");
    expect(shiftMonth("2025-12", 1)).toBe("2026-01");
  });
});

describe("lo que un tope vigila", () => {
  const labels = { restaurants: "Restaurantes", bars: "Bares" };

  it("sin categorías dice que vigila todo el mes", () => {
    const scope = {
      categories: [],
      accounts: [],
      total: true,
      every_account: true,
    };

    expect(scopeLabel(scope, labels)).toBe("Todo el mes");
  });

  it("nunca devuelve una cadena vacía", () => {
    const scope = {
      categories: [],
      accounts: [],
      total: true,
      every_account: true,
    };

    expect(scopeLabel(scope, {})).not.toBe("");
  });

  it("junta varias categorías con sus nombres", () => {
    const scope = {
      categories: ["restaurants", "bars"],
      accounts: [],
      total: false,
      every_account: true,
    };

    expect(scopeLabel(scope, labels)).toBe("Restaurantes · Bares");
  });

  it("una categoría sin nombre cae en su propio valor en vez de desaparecer", () => {
    const scope = {
      categories: ["restaurants", "custom:gatos"],
      accounts: [],
      total: false,
      every_account: true,
    };

    expect(scopeLabel(scope, labels)).toBe("Restaurantes · custom:gatos");
  });

  it("sabe cuándo está limitado a unas cuentas", () => {
    expect(
      isNarrowed({
        categories: [],
        accounts: ["22222222-2222-2222-2222-222222222222"],
        total: true,
        every_account: false,
      }),
    ).toBe(true);
    expect(
      isNarrowed({
        categories: [],
        accounts: [],
        total: true,
        every_account: true,
      }),
    ).toBe(false);
  });
});
