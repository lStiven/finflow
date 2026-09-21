import { useNavigate } from "@tanstack/react-router";
import { ArrowRight, Ban, Mail } from "lucide-react";
import { useCallback } from "react";
import { Logo } from "@/components/Logo";
import { Button } from "@/components/ui/Button";
import { useDismissOnEscape } from "@/lib/useDismissOnEscape";
import { useScrollLock } from "@/lib/useScrollLock";
import { STAGE_COPY } from "@/onboarding/copy";
import { useOnboarding } from "@/onboarding/useOnboarding";

/**
 * Said once, the first time somebody arrives with an account and nothing
 * connected.
 *
 * The dashboard a new account lands on is empty by definition — no movements
 * have arrived, because nothing is forwarding yet — and an empty dashboard
 * explains nothing about why. The banner above it is a reminder, which is a
 * different job: it is written for somebody who already knows what the step
 * is and left it pending. This is the one that says what Finflow needs before
 * it can do anything at all.
 *
 * Sibling of `ReadyDialog`, and deliberately its mirror image: that one fires
 * on `complete`, this one on `!complete`, so the two can never be on screen
 * together. Both are acknowledged in the browser rather than on the server —
 * "this was shown" is not a fact the API has any business storing.
 */
export function WelcomeDialog() {
  const { state, acknowledge } = useOnboarding();
  const navigate = useNavigate();

  // Before the early return, so the condition is an argument rather than a
  // hook that sometimes runs.
  useScrollLock(Boolean(state?.welcome));
  // Cerrarlo es «ahora no», que es exactamente lo que el enlace de abajo
  // ofrece: Escape no se salta ningún paso, solo lo aplaza.
  useDismissOnEscape(
    useCallback(() => acknowledge("welcomeSeen"), [acknowledge]),
    Boolean(state?.welcome),
  );

  if (!state?.welcome) return null;

  const dismiss = () => acknowledge("welcomeSeen");
  // The stage the guide would open on anyway, named here so the dialog and
  // the banner cannot describe two different "next steps".
  const next = state.current ? STAGE_COPY[state.current] : null;

  return (
    <div
      role="dialog"
      aria-modal
      aria-labelledby="welcome-title"
      className="fixed inset-0 z-50 grid place-items-center bg-ink/80 p-5 backdrop-blur-sm"
    >
      <div className="rise surface w-full max-w-md rounded-card border border-accent/30 bg-surface p-7">
        <Logo className="pulse-ring mx-auto size-16" />

        <h2
          id="welcome-title"
          className="mt-5 text-center font-semibold text-xl tracking-tight"
        >
          Te damos la bienvenida a Finflow
        </h2>
        <p className="mt-2.5 text-center text-muted text-sm leading-relaxed">
          Finflow lee los correos que tu banco ya te manda y arma con ellos tus
          movimientos y tus saldos.{" "}
          <strong className="text-text">
            Falta un paso para que empiece a llegar algo.
          </strong>
        </p>

        <ul className="mt-6 flex flex-col gap-3.5 rounded-xl border border-line bg-ink p-4">
          <Point icon={Mail}>
            Te damos una dirección tuya y reenvías ahí las alertas de tu banco.
          </Point>
          <Point icon={Ban}>
            Solo se lee lo que llegue de quien tú apruebes. Nadie entra a tu correo.
          </Point>
        </ul>

        <div className="mt-7 flex flex-col gap-2.5">
          <Button
            full
            onClick={() => {
              dismiss();
              void navigate({ to: "/conectar" });
            }}
          >
            {next ? next.title : "Conectar mi banco"}
            <ArrowRight className="size-4" />
          </Button>
          {/*
            Closing is a real answer, not a way out of a nag: the app works
            without this — every alert is still recorded — and the banner
            carries the step from here on.
          */}
          <Button variant="quiet" onClick={dismiss}>
            Ahora no, mirar primero
          </Button>
        </div>
      </div>
    </div>
  );
}

function Point({
  icon: Icon,
  children,
}: {
  icon: typeof Mail;
  children: React.ReactNode;
}) {
  return (
    <li className="flex items-start gap-3">
      <span
        aria-hidden
        className="mt-0.5 grid size-7 shrink-0 place-items-center rounded-lg border border-line bg-surface"
      >
        <Icon className="size-3.5 text-cyan" />
      </span>
      <span className="min-w-0 text-muted text-sm leading-relaxed">{children}</span>
    </li>
  );
}
