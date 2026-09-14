import { describe, expect, it } from "vitest";
import type { AlertChannel, AlertPreference } from "@/api/queries";
import {
  channelState,
  formatMinimumAmount,
  hasPendingLink,
  movementPreference,
  parseMinimumAmount,
} from "./channels";

function channel(
  status: "pending" | "verified",
  minimum?: string,
  createdAt = Math.floor(Date.now() / 1000),
): AlertChannel {
  return {
    channel_id: "c1",
    kind: "telegram",
    status,
    chat_hint: status === "verified" ? "…6789" : null,
    label: null,
    created_at: createdAt,
    verified_at: null,
    preferences: [
      {
        alert_type: "movement",
        enabled: true,
        minimum_amount: minimum ?? null,
        minimum_currency: minimum ? "COP" : null,
      },
    ],
  } as AlertChannel;
}

describe("qué muestra la tarjeta", () => {
  it("sin canales, no hay nada vinculado", () => {
    expect(channelState([])).toBe("none");
  });

  it("un canal pendiente se está esperando", () => {
    expect(channelState([channel("pending")])).toBe("pending");
  });

  it("un canal vinculado gana sobre uno pendiente", () => {
    // Si ya terminó, enseñarle un enlace viejo lo invita a seguirlo.
    expect(channelState([channel("pending"), channel("verified")])).toBe("linked");
  });
});

describe("cuándo dejar de preguntar", () => {
  it("mientras algo esté pendiente, sí", () => {
    expect(hasPendingLink([channel("pending")])).toBe(true);
  });

  it("cuando ya no queda nada pendiente, no", () => {
    expect(hasPendingLink([channel("verified")])).toBe(false);
    expect(hasPendingLink([])).toBe(false);
    expect(hasPendingLink(undefined)).toBe(false);
  });

  it("un enlace que nadie siguió deja de contar al vencer", () => {
    // Nada barre un canal pendiente, así que sin esto una pestaña abierta
    // preguntaría cada tres segundos para siempre.
    const abandoned = channel("pending", undefined, Math.floor(Date.now() / 1000));
    const sixteenMinutes = Date.now() + 16 * 60_000;

    expect(hasPendingLink([abandoned])).toBe(true);
    expect(hasPendingLink([abandoned], sixteenMinutes)).toBe(false);
  });
});

describe("el monto mínimo que alguien teclea", () => {
  it("vacío es no tener mínimo, que es una respuesta", () => {
    expect(parseMinimumAmount("")).toBeNull();
    expect(parseMinimumAmount("   ")).toBeNull();
  });

  it("los puntos y espacios de miles se van", () => {
    expect(parseMinimumAmount("20.000")).toBe("20000");
    expect(parseMinimumAmount("1 234 567")).toBe("1234567");
  });

  it("una coma final con dos dígitos son centavos", () => {
    expect(parseMinimumAmount("1.234,56")).toBe("1234.56");
    expect(parseMinimumAmount("20,5")).toBe("20.5");
  });

  it("una coma de miles no es un decimal", () => {
    expect(parseMinimumAmount("1,234,567")).toBe("1234567");
  });

  it("lo que no es un número no se adivina", () => {
    // El mínimo decide qué se avisa: inventarlo es peor que no cambiarlo.
    expect(parseMinimumAmount("abc")).toBeUndefined();
    expect(parseMinimumAmount("-5")).toBeUndefined();
    expect(parseMinimumAmount("1e10")).toBeUndefined();
    expect(parseMinimumAmount(",")).toBeUndefined();
  });
});

describe("el monto mínimo que ya está guardado", () => {
  it("vuelve agrupado", () => {
    expect(formatMinimumAmount(preference("1234567"))).toBe("1.234.567");
  });

  it("sin centavos cuando no los hay", () => {
    expect(formatMinimumAmount(preference("20000.00"))).toBe("20.000");
  });

  it("con centavos cuando sí", () => {
    expect(formatMinimumAmount(preference("20000.50"))).toBe("20.000,50");
  });

  it("sin mínimo, campo vacío", () => {
    expect(formatMinimumAmount(undefined)).toBe("");
    expect(formatMinimumAmount(preference(null))).toBe("");
  });

  it("ida y vuelta no cambia la cifra", () => {
    expect(parseMinimumAmount(formatMinimumAmount(preference("1234567")))).toBe(
      "1234567",
    );
    expect(parseMinimumAmount(formatMinimumAmount(preference("20000.50")))).toBe(
      "20000.50",
    );
  });
});

describe("la preferencia de movimientos", () => {
  it("sale del canal vinculado", () => {
    expect(movementPreference(channel("verified", "20000"))?.minimum_amount).toBe(
      "20000",
    );
  });

  it("sin canal no hay preferencia", () => {
    expect(movementPreference(undefined)).toBeUndefined();
  });
});

function preference(minimum: string | null): AlertPreference {
  return {
    alert_type: "movement",
    enabled: true,
    minimum_amount: minimum,
    minimum_currency: minimum ? "COP" : null,
  } as AlertPreference;
}
