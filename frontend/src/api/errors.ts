/**
 * What a failed call looks like to the rest of the app.
 *
 * Split from `@/api/client` because that module refuses to load without
 * `VITE_API_BASE_URL` — which is right for the thing that makes requests, and
 * wrong for the type every screen matches on. Keeping them apart is what lets
 * the error copy be tested without standing up a client.
 */

/**
 * Unwrap an `openapi-fetch` result into the value TanStack Query expects.
 *
 * `openapi-fetch` never throws on a non-2xx: it returns `{ data, error }`.
 * Query needs a rejected promise to mark something as failed, so every call
 * goes through here.
 */
/**
 * The status an `ApiError` carries when the request never got an answer.
 *
 * Not a status the API can send — it is the absence of one. Zero rather than
 * a made-up 5xx so nothing that branches on a real code can confuse the two,
 * and so a screen offline is never reported as the server having failed.
 */
export const NO_RESPONSE = 0;

export async function unwrap<T>(
  result: Promise<{ data?: T; error?: unknown; response: Response }>,
): Promise<T> {
  let settled: { data?: T; error?: unknown; response: Response };

  try {
    settled = await result;
  } catch (cause) {
    // `fetch` rejects with a `TypeError` before there is a response at all:
    // no network, the tab offline, DNS gone, the API down. That one used to
    // arrive as the browser's own `TypeError: Failed to fetch` and be
    // rendered verbatim — English, technical, and shown at exactly the moment
    // somebody is on a bad connection. The original is kept as the cause, for
    // a console that still needs it.
    //
    // **Only that one.** `openapi-fetch` also rejects when a 2xx body fails
    // to parse — a proxy's HTML error page, a truncated response — and
    // telling somebody to check their connection over a server-side fault
    // sends them to restart a router that is working. Anything else is
    // re-thrown as it came.
    if (cause instanceof TypeError) {
      throw new ApiError(NO_RESPONSE, null, undefined, { cause });
    }

    throw cause;
  }

  const { data, error, response } = settled;
  if (error !== undefined || !response.ok) {
    throw new ApiError(response.status, error, retryAfterOf(response));
  }
  return data as T;
}

/**
 * How long the API asked us to wait, in seconds.
 *
 * Only the identity endpoints that send mail set it, and it is the only
 * detail their 429 gives: how much mail has already gone to that address, and
 * whether it has an account at all, is exactly what those endpoints refuse to
 * say. `Retry-After` may also be an HTTP date; this API only ever sends
 * seconds, and anything unparseable is read as "no idea" rather than zero.
 */
function retryAfterOf(response: Response): number | undefined {
  const header = response.headers.get("Retry-After");
  if (!header) return undefined;
  const seconds = Number.parseInt(header, 10);
  return Number.isFinite(seconds) && seconds > 0 ? seconds : undefined;
}

export class ApiError extends Error {
  readonly status: number;
  readonly detail: unknown;
  /** Set only on a 429, and only when the API said how long. */
  readonly retryAfterSeconds: number | undefined;

  constructor(
    status: number,
    detail: unknown,
    retryAfterSeconds?: number,
    options?: ErrorOptions,
  ) {
    super(messageFor(status, detail), options);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
    this.retryAfterSeconds = retryAfterSeconds;
  }
}

/**
 * A message worth showing. FastAPI puts a string in `detail` for the errors
 * this API raises deliberately, and a list of field errors for a 422 — the
 * two need different handling, and neither should reach a user as `[object
 * Object]`.
 */
function messageFor(status: number, detail: unknown): string {
  // Before anything reads `detail`: there is no body to read. Worded without
  // blaming either side, because from here the two are indistinguishable —
  // the phone may be on the underground, or the API may be down.
  if (status === NO_RESPONSE) {
    return "No pudimos conectar con Finflow. Revisa tu conexión e inténtalo de nuevo.";
  }

  // 401 is answered before the body is read, and deliberately so. The API
  // returns an identical 401 for an unknown email, a wrong password and a
  // malformed one — the guide requires the interface not to tell them apart,
  // so there is nothing in that body worth showing, and this is the one
  // message a user actually reads (a 401 anywhere else redirects to login
  // rather than rendering).
  if (status === 401) return "Correo o contraseña incorrectos";

  // Its own copy rather than the server's `detail`, which is what this used
  // to fall through to. The text happened to be right — and that is the
  // problem: a message a user reads was living in a Python exception, one
  // rewording away from reaching a Spanish screen in English. Screens that
  // want the *wait* and not just the fact use `identityErrorMessage`, which
  // reads `Retry-After`; this is what the rest say.
  if (status === 429) {
    return "Demasiados intentos. Espera un momento y vuelve a intentarlo.";
  }

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
