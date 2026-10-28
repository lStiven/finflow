import { describe, expect, it } from "vitest";
import { categoryStats, describeChange, type MonthFigure } from "@/reports/category";

const month = (key: string, outgoing: string, partial = false): MonthFigure => ({
  key,
  outgoing,
  partial,
});

describe("una categoría, mes a mes", () => {
  it("compara el mes elegido con el anterior", () => {
    const stats = categoryStats([
      month("2026-09", "200000"),
      month("2026-10", "300000"),
    ]);

    expect(stats.total).toBe("300000");
    expect(stats.previous).toBe("200000");
    expect(stats.vsPrevious).toBe(50);
  });

  it("promedia desde el primer mes con gasto, no desde antes de usar la app", () => {
    // Abril a junio en cero son meses sin Finflow, no meses sin mercado.
    const stats = categoryStats([
      month("2026-05", "0"),
      month("2026-06", "0"),
      month("2026-07", "300000"),
      month("2026-08", "0"),
      month("2026-09", "300000"),
      month("2026-10", "400000", true),
    ]);

    // Julio, agosto y septiembre: un mes en cero dentro del rango sí cuenta.
    expect(stats.averagedOver).toBe(3);
    expect(stats.average).toBe("200000.00");
    expect(stats.vsAverage).toBe(100);
  });

  it("sin meses previos con gasto, no inventa un promedio", () => {
    const stats = categoryStats([month("2026-09", "0"), month("2026-10", "50000")]);

    expect(stats.average).toBeNull();
    expect(stats.averagedOver).toBe(0);
    // El mes anterior gastó cero: un porcentaje contra cero no dice nada.
    expect(stats.vsPrevious).toBeNull();
  });

  it("dice el cambio en palabras", () => {
    expect(describeChange(50)).toBe("50% más");
    expect(describeChange(-12.4)).toBe("12% menos");
    expect(describeChange(0.3)).toBe("igual");
    expect(describeChange(null)).toBeNull();
  });
});
