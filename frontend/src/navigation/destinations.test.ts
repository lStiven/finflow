import { describe, expect, it } from "vitest";
import { BAR, BAR_SIZE, DESTINATIONS, inSheet, OVERFLOW } from "./destinations";

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

  it("anuncia sin pantalla Presupuestos y Configuración, y nada más", () => {
    // Las dos que están en construcción. Esta lista es la que hay que mover
    // el día que una de ellas exista — si crece sola, el menú se llenó de
    // promesas.
    const pending = DESTINATIONS.filter((d) => d.to === undefined).map((d) => d.label);
    expect(pending).toEqual(["Presupuestos", "Configuración"]);
  });

  it("deja Presupuestos donde va a quedarse, junto a Facturas", () => {
    // El orden es la mitad del mensaje: si al llegar la pantalla la entrada
    // salta de sitio, quien ya se había acostumbrado la busca donde no está.
    const labels = DESTINATIONS.map((d) => d.label);
    expect(labels.indexOf("Presupuestos")).toBe(labels.indexOf("Facturas") + 1);
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

describe("dónde estoy, según la barra", () => {
  it("da por suya cada sección que la barra no muestra", () => {
    for (const destination of OVERFLOW) {
      if (destination.to) expect(inSheet(destination.to)).toBe(true);
    }
  });

  it("da por suyas la cuenta y las guías, que tampoco están en la barra", () => {
    expect(inSheet("/perfil")).toBe(true);
    expect(inSheet("/guias")).toBe(true);
    expect(inSheet("/conectar")).toBe(true);
  });

  it("no reclama lo que la barra sí muestra", () => {
    // Si «Más» se encendiera aquí, dos entradas dirían a la vez que son la
    // pantalla actual, que es la misma confusión que no encender ninguna.
    for (const destination of BAR) {
      if (destination.to) expect(inSheet(destination.to)).toBe(false);
    }
  });

  it("cuenta la pantalla de un comercio como estar en Comercios", () => {
    expect(inSheet("/comercios/abc-123")).toBe(true);
  });

  it("no confunde una ruta que solo empieza igual", () => {
    expect(inSheet("/comercios-de-alguien")).toBe(false);
  });
});
