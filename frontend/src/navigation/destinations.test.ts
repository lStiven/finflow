import { describe, expect, it } from "vitest";
import { BAR, BAR_SIZE, DESTINATIONS, OVERFLOW } from "./destinations";

describe("las secciones del teléfono", () => {
  it("reparte todas las secciones entre la barra y la hoja", () => {
    expect([...BAR, ...OVERFLOW]).toEqual(DESTINATIONS);
  });

  it("no deja ninguna sección fuera de las dos", () => {
    // The regression this file exists for: a destination in neither list is a
    // screen that works and that nobody with a phone can open.
    const reachable = new Set([...BAR, ...OVERFLOW].map((d) => d.label));
    for (const destination of DESTINATIONS) {
      expect(reachable.has(destination.label)).toBe(true);
    }
  });

  it("no mete más entradas en la barra de las que caben", () => {
    expect(BAR.length).toBe(BAR_SIZE);
    expect(BAR_SIZE).toBeLessThanOrEqual(3);
  });

  it("no gasta un puesto de la barra en una pantalla que no existe", () => {
    // Un botón deshabilitado ocupando uno de los tres huecos del pulgar es
    // peor que no estar: la barra es lo que más se usa y lo que menos cabe.
    for (const destination of BAR) {
      expect(destination.to).toBeDefined();
    }
  });

  it("solo deja Configuración anunciada sin pantalla", () => {
    const pending = DESTINATIONS.filter((d) => d.to === undefined).map((d) => d.label);
    expect(pending).toEqual(["Configuración"]);
  });

  it("deja Facturas al alcance", () => {
    const facturas = DESTINATIONS.find((d) => d.label === "Facturas");
    expect(facturas?.to).toBe("/facturas");
  });

  it("deja Reportes y Comercios al alcance", () => {
    const labels = [...BAR, ...OVERFLOW].map((d) => d.label);
    expect(labels).toContain("Reportes");
    expect(labels).toContain("Comercios");
  });
});
