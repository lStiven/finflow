import { Banknote, HeartPulse, Receipt } from "lucide-react";
import { describe, expect, it } from "vitest";
import { lookOf } from "@/bills/look";

describe("cómo se dibuja una factura", () => {
  it("le da a cada categoría conocida su icono", () => {
    expect(lookOf({ category: "health", direction: "outgoing" }).icon).toBe(HeartPulse);
  });

  it("un ingreso manda sobre su categoría", () => {
    // What has to be told apart at a glance in this grid is money coming in
    // from money going out. That beats "this salary is filed under income".
    const salary = lookOf({ category: "health", direction: "incoming" });

    expect(salary.icon).toBe(Banknote);
    expect(salary.glow).toBe("green");
  });

  it("una categoría que este build no conoce no rompe nada", () => {
    // Every category somebody wrote for themselves lands here, and so does a
    // shipped one added on the server after this bundle was built.
    expect(lookOf({ category: "custom:mascotas", direction: "outgoing" }).icon).toBe(
      Receipt,
    );
  });

  it("sin categoría también", () => {
    expect(lookOf({ category: null, direction: "outgoing" }).icon).toBe(Receipt);
  });

  it("nunca sale del repertorio de cuatro colores de la app", () => {
    const hues = new Set(
      [
        "groceries",
        "restaurants",
        "transport",
        "fuel",
        "shopping",
        "entertainment",
        "subscriptions",
        "utilities",
        "health",
        "education",
        "travel",
        "fees",
        "transfers",
        "other",
        null,
      ].map((category) => lookOf({ category, direction: "outgoing" }).glow),
    );

    expect([...hues].sort()).toEqual(["accent", "cyan", "green", "violet"]);
  });
});
