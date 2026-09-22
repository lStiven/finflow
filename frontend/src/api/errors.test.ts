import { describe, expect, it } from "vitest";
import { ApiError, NO_RESPONSE, unwrap } from "@/api/errors";

function answered(status: number, body?: unknown) {
  return Promise.resolve({
    data: status < 400 ? ({ ok: true } as unknown) : undefined,
    error: status < 400 ? undefined : body,
    response: new Response(null, { status }),
  });
}

describe("cuando la petición sí obtuvo respuesta", () => {
  it("devuelve los datos de un 2xx", async () => {
    await expect(unwrap(answered(200))).resolves.toEqual({ ok: true });
  });

  it("no le cuenta al usuario en qué falló el 401", async () => {
    await expect(unwrap(answered(401))).rejects.toThrow(
      "Correo o contraseña incorrectos",
    );
  });

  it("prefiere el `detail` que escribió la API", async () => {
    await expect(
      unwrap(answered(409, { detail: "Esa cuenta ya existe" })),
    ).rejects.toThrow("Esa cuenta ya existe");
  });
});

describe("cuando no hubo respuesta", () => {
  // El agujero que esta prueba cierra: `fetch` rechaza antes de que exista un
  // status, así que el `TypeError: Failed to fetch` del navegador llegaba
  // crudo a la pantalla — en inglés y justo cuando alguien está sin señal.
  const offline = () => unwrap(Promise.reject(new TypeError("Failed to fetch")));

  it("lo dice en español y sin culpar a nadie", async () => {
    await expect(offline()).rejects.toThrow(/No pudimos conectar con Finflow/);
  });

  it("no se hace pasar por un error del servidor", async () => {
    await offline().catch((error: unknown) => {
      expect(error).toBeInstanceOf(ApiError);
      expect((error as ApiError).status).toBe(NO_RESPONSE);
      expect((error as ApiError).message).not.toMatch(/servidor falló/);
    });
    expect.assertions(3);
  });

  it("conserva el error original como causa", async () => {
    await offline().catch((error: unknown) => {
      expect((error as Error).cause).toBeInstanceOf(TypeError);
    });
    expect.assertions(1);
  });

  it("no se traga un fallo del servidor haciéndolo pasar por falta de señal", async () => {
    // `openapi-fetch` también rechaza cuando un 2xx trae un cuerpo que no se
    // puede leer —la página de error de un proxy, una respuesta cortada—.
    // Mandar a revisar el wifi por una falla del servidor es mandar a alguien
    // a reiniciar un router que funciona.
    const broken = unwrap(Promise.reject(new SyntaxError("Unexpected token <")));

    await expect(broken).rejects.toThrow(SyntaxError);
    await expect(broken).rejects.not.toThrow(/No pudimos conectar/);
  });
});

describe("cuando el servidor dice que son demasiados intentos", () => {
  it("lo cuenta en español sin depender del texto del backend", async () => {
    // El `detail` a propósito en inglés: el mensaje que se lee en pantalla no
    // puede vivir en una excepción de Python.
    await expect(
      unwrap(answered(429, { detail: "Too many attempts" })),
    ).rejects.toThrow("Demasiados intentos. Espera un momento y vuelve a intentarlo.");
  });

  it("guarda cuánto hay que esperar cuando la API lo dice", async () => {
    const limited = Promise.resolve({
      data: undefined,
      error: { detail: "Too many attempts" },
      response: new Response(null, { status: 429, headers: { "Retry-After": "90" } }),
    });

    await unwrap(limited).catch((error: unknown) => {
      expect((error as ApiError).retryAfterSeconds).toBe(90);
    });
    expect.assertions(1);
  });
});
