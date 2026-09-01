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

// Re-exported so callers have one import for "talking to the API", while the
// error type itself stays loadable without a base URL. See `@/api/errors`.
export { ApiError, unwrap } from "@/api/errors";
