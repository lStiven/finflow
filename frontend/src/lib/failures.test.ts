import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, NO_RESPONSE } from "@/api/errors";
import { errorKind } from "@/lib/failures";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("qué pantalla de error toca", () => {
  it("sin respuesta del servidor es falta de conexión", () => {
    expect(errorKind(new ApiError(NO_RESPONSE, null))).toBe("offline");
  });

  it("un 404 es algo que ya no existe, no un fallo nuestro", () => {
    expect(errorKind(new ApiError(404, "Transaction not found"))).toBe("missing");
  });

  it("un 500 es nuestro y se dice que estamos en ello", () => {
    expect(errorKind(new ApiError(500, null))).toBe("broken");
  });

  it("un módulo que no se pudo descargar tras un despliegue es conexión", () => {
    const error = new TypeError(
      "Failed to fetch dynamically imported module: /assets/index.js",
    );
    expect(errorKind(error)).toBe("offline");
  });

  it("con el navegador sin red, cualquier fallo es de conexión", () => {
    vi.stubGlobal("navigator", { onLine: false });
    expect(errorKind(new Error("boom"))).toBe("offline");
  });

  it("un error cualquiera al dibujar es nuestro", () => {
    expect(errorKind(new Error("Cannot read properties of undefined"))).toBe("broken");
  });
});
