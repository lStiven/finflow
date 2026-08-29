/**
 * The one place that talks to the API.
 *
 * Typed from `docs/openapi.json` through `src/api/schema.d.ts`, which is
 * generated — `just web-types`. That is the point of the whole setup: a
 * path, a query parameter or a response field that changes in the Python
 * routers stops this project from compiling, instead of failing in a browser
 * for whoever opens that screen next.
 */

import createClient, { type Middleware } from "openapi-fetch";
import type { paths } from "@/api/schema";
import { clearSession, loadSession } from "@/auth/token";

const baseUrl = import.meta.env.VITE_API_BASE_URL;

if (!baseUrl) {
  // Failing loudly at boot rather than sending every request to the page's own
  // origin, which 404s in a way that looks like a backend problem.
  throw new Error(
    "VITE_API_BASE_URL is not set — copy frontend/.env.example to .env.development",
  );
}

/**
 * Signing in and signing up are the two calls whose 401 is not an expired
 * session — it is a wrong password, on a screen that has no session to lose.
 * Treating them like the rest would clear a session that does not exist and
 * tell the user their session expired while they are trying to create one.
 */
const AUTH_ENDPOINTS = ["/identity/login", "/identity/register"];

function isAuthEndpoint(url: string): boolean {
  const path = new URL(url, "http://placeholder").pathname;
  return AUTH_ENDPOINTS.some((endpoint) => path.endsWith(endpoint));
}

const authMiddleware: Middleware = {
  onRequest({ request }) {
    const session = loadSession();
    if (session) {
      request.headers.set("Authorization", `Bearer ${session.accessToken}`);
    }
    return request;
  },
  onResponse({ request, response }) {
    // Token absent, invalid or expired — the three are indistinguishable by
    // design, and all three mean the same thing here.
    if (response.status === 401 && !isAuthEndpoint(request.url)) {
      clearSession();
      window.dispatchEvent(new CustomEvent("finflow:unauthorized"));
    }
    return response;
  },
};

export const api = createClient<paths>({ baseUrl });
api.use(authMiddleware);

/**
 * Unwrap an `openapi-fetch` result into the value TanStack Query expects.
 *
 * `openapi-fetch` never throws on a non-2xx: it returns `{ data, error }`.
 * Query needs a rejected promise to mark something as failed, so every call
 * goes through here.
 */
export async function unwrap<T>(
  result: Promise<{ data?: T; error?: unknown; response: Response }>,
): Promise<T> {
  const { data, error, response } = await result;
  if (error !== undefined || !response.ok) {
    throw new ApiError(response.status, error);
  }
  return data as T;
}

export class ApiError extends Error {
  readonly status: number;
  readonly detail: unknown;

  constructor(status: number, detail: unknown) {
    super(messageFor(status, detail));
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}

/**
 * A message worth showing. FastAPI puts a string in `detail` for the errors
 * this API raises deliberately, and a list of field errors for a 422 — the
 * two need different handling, and neither should reach a user as `[object
 * Object]`.
 */
function messageFor(status: number, detail: unknown): string {
  // 401 is answered before the body is read, and deliberately so. The API
  // returns an identical 401 for an unknown email, a wrong password and a
  // malformed one — the guide requires the interface not to tell them apart,
  // so there is nothing in that body worth showing, and this is the one
  // message a user actually reads (a 401 anywhere else redirects to login
  // rather than rendering).
  if (status === 401) return "Correo o contraseña incorrectos";

  if (typeof detail === "object" && detail !== null && "detail" in detail) {
    const inner = (detail as { detail: unknown }).detail;
    if (typeof inner === "string") return inner;
    if (Array.isArray(inner)) {
      const first = inner[0] as { msg?: unknown } | undefined;
      if (first && typeof first.msg === "string") return first.msg;
    }
  }
  if (status === 409) return "Ya existe algo igual";
  if (status === 404) return "No encontrado";
  if (status >= 500) return "El servidor falló. Intenta de nuevo.";
  return `Error ${status}`;
}
