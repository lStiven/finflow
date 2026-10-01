/**
 * What somebody sees when a screen cannot be drawn — instead of the router's
 * bare «Something went wrong!». Which of the three cases it is lives in
 * `@/lib/failures`.
 */

import { useQueryErrorResetBoundary } from "@tanstack/react-query";
import { type ErrorComponentProps, Link, useRouter } from "@tanstack/react-router";
import { ArrowLeft, Home, RotateCw } from "lucide-react";
import { Logo } from "@/components/Logo";
import { COPY, type ErrorKind, errorKind } from "@/lib/failures";

/** The router's error screen, for any route that throws while loading. */
export function RouteError({ error, reset }: ErrorComponentProps) {
  const router = useRouter();
  const queries = useQueryErrorResetBoundary();
  const kind = errorKind(error);

  return (
    <ErrorScreen
      kind={kind}
      onRetry={
        kind === "missing"
          ? undefined
          : () => {
              queries.reset();
              reset();
              void router.invalidate();
            }
      }
    />
  );
}

/** An address that matches no screen. */
export function NotFoundScreen() {
  return <ErrorScreen kind="missing" />;
}

export function ErrorScreen({
  kind,
  onRetry,
}: {
  kind: ErrorKind;
  onRetry?: () => void;
}) {
  const copy = COPY[kind];

  return (
    <main className="grid min-h-dvh place-items-center px-5 py-10">
      <div className="rise flex w-full max-w-md flex-col items-center text-center">
        <Logo className="mb-8 size-12" label="Finflow" />
        <Illustration kind={kind} />
        <h1 className="mt-8 font-semibold text-2xl tracking-tight">{copy.title}</h1>
        <p className="mt-3 text-muted text-sm leading-relaxed">{copy.body}</p>

        <div className="mt-8 flex w-full flex-col gap-2.5 sm:flex-row sm:justify-center">
          {onRetry ? (
            <button
              type="button"
              onClick={onRetry}
              className="inline-flex items-center justify-center gap-2 rounded-xl bg-accent px-5 py-3 font-semibold text-accent-ink text-sm transition-all hover:brightness-108"
            >
              <RotateCw className="size-4" aria-hidden />
              Intentar de nuevo
            </button>
          ) : (
            <button
              type="button"
              onClick={() => window.history.back()}
              className="inline-flex items-center justify-center gap-2 rounded-xl bg-accent px-5 py-3 font-semibold text-accent-ink text-sm transition-all hover:brightness-108"
            >
              <ArrowLeft className="size-4" aria-hidden />
              Volver
            </button>
          )}
          <Link
            to="/"
            className="inline-flex items-center justify-center gap-2 rounded-xl border border-line bg-surface px-5 py-3 text-sm transition-colors hover:bg-surface-raised"
          >
            <Home className="size-4" aria-hidden />
            Ir al resumen
          </Link>
        </div>
      </div>
    </main>
  );
}

/**
 * Somebody at a laptop, working on it — with a wrench for our fault, a cloud
 * with no signal for the connection, a question mark for what is missing.
 * Drawn inline in the app's own tokens so it follows the theme.
 */
function Illustration({ kind }: { kind: ErrorKind }) {
  return (
    <svg
      viewBox="0 0 240 180"
      className="w-56 max-w-full"
      role="img"
      aria-label="Una persona trabajando en su computador"
    >
      <defs>
        <radialGradient id="err-glow" cx="50%" cy="55%" r="55%">
          <stop offset="0%" stopColor="var(--color-accent)" stopOpacity="0.28" />
          <stop offset="100%" stopColor="var(--color-accent)" stopOpacity="0" />
        </radialGradient>
      </defs>
      <ellipse cx="120" cy="100" rx="110" ry="78" fill="url(#err-glow)" />

      {/* desk */}
      <rect x="28" y="140" width="184" height="6" rx="3" fill="var(--color-line)" />

      {/* person */}
      <circle
        cx="92"
        cy="62"
        r="15"
        fill="var(--color-surface-raised)"
        stroke="var(--color-muted)"
        strokeWidth="2"
      />
      <path d="M78 58c3-12 25-14 29 1" fill="var(--color-violet)" opacity="0.9" />
      <path
        d="M66 140c0-28 11-46 26-46s26 18 26 46"
        fill="var(--color-surface-raised)"
        stroke="var(--color-muted)"
        strokeWidth="2"
      />
      <path
        d="M100 112c10 4 20 10 28 18"
        stroke="var(--color-muted)"
        strokeWidth="6"
        strokeLinecap="round"
        fill="none"
      />

      {/* laptop */}
      <path
        d="M124 98h58l-8 38h-58z"
        fill="var(--color-surface)"
        stroke="var(--color-muted)"
        strokeWidth="2"
        strokeLinejoin="round"
      />
      <rect x="110" y="136" width="76" height="5" rx="2.5" fill="var(--color-muted)" />
      <circle cx="149" cy="117" r="3" fill="var(--color-accent)" />

      {/* what is wrong */}
      {kind === "broken" ? (
        <g transform="translate(176 40) rotate(35)" fill="var(--color-warn)">
          <rect x="-4" y="0" width="8" height="34" rx="4" />
          <path d="M-12 -2a12 12 0 1 1 24 0l-6 4h-12z" />
        </g>
      ) : null}
      {kind === "offline" ? (
        <g transform="translate(160 26)">
          <path
            d="M10 34h40a14 14 0 0 0-2-28 18 18 0 0 0-34 4 12 12 0 0 0-4 24z"
            fill="var(--color-surface-raised)"
            stroke="var(--color-cyan)"
            strokeWidth="2"
          />
          <path
            d="M18 42l26-30"
            stroke="var(--color-outgoing)"
            strokeWidth="3"
            strokeLinecap="round"
          />
        </g>
      ) : null}
      {kind === "missing" ? (
        <text
          x="178"
          y="66"
          fontSize="44"
          fontWeight="700"
          fill="var(--color-cyan)"
          fontFamily="inherit"
        >
          ?
        </text>
      ) : null}
    </svg>
  );
}
