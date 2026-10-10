import { useNavigate, useRouterState } from "@tanstack/react-router";
import { ArrowRight } from "lucide-react";
import { useCallback } from "react";
import { Button } from "@/components/ui/Button";
import { useDismissOnEscape } from "@/lib/useDismissOnEscape";
import { useScrollLock } from "@/lib/useScrollLock";
import { FlowAnimation } from "@/onboarding/FlowAnimation";
import { stageNumber } from "@/onboarding/steps";
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
 * It is the guide's own introduction, condensed, so starting from here skips
 * that introduction rather than repeating it — and it never opens over the
 * guide itself, which has its own.
 *
 * Sibling of `ReadyDialog`, and deliberately its mirror image: that one fires
 * on `complete`, this one on `!complete`, so the two can never be on screen
 * together. Both are acknowledged in the browser rather than on the server —
 * "this was shown" is not a fact the API has any business storing.
 */
export function WelcomeDialog() {
  const { state, acknowledge, record } = useOnboarding();
  const navigate = useNavigate();
  const onGuide = useRouterState({
    select: (router) => router.location.pathname === "/conectar",
  });
  const open = Boolean(state?.welcome) && !onGuide;

  // Before the early return, so the condition is an argument rather than a
  // hook that sometimes runs.
  useScrollLock(open);
  // Cerrarlo es «ahora no», que es exactamente lo que el enlace de abajo
  // ofrece: Escape no se salta ningún paso, solo lo aplaza.
  useDismissOnEscape(
    useCallback(() => acknowledge("welcomeSeen"), [acknowledge]),
    open,
  );

  if (!open || !state) return null;

  const dismiss = () => acknowledge("welcomeSeen");
  // Somebody who started in another browser picks up where they are, not at
  // the beginning.
  const resuming = state.doneCount > 0;

  return (
    <div
      role="dialog"
      aria-modal
      aria-labelledby="welcome-title"
      aria-describedby="welcome-body"
      className="fixed inset-0 z-50 grid place-items-center bg-ink/80 p-5 backdrop-blur-sm"
    >
      <div className="rise surface w-full max-w-md rounded-card border border-accent/30 bg-surface p-7 text-center">
        <FlowAnimation size="sm" className="mx-auto" />

        <h2 id="welcome-title" className="mt-6 font-semibold text-xl tracking-tight">
          Tus gastos, registrados solos
        </h2>
        <p id="welcome-body" className="mt-2.5 text-muted text-sm leading-relaxed">
          Conecta las alertas de tu banco y Finflow organizará tus movimientos.{" "}
          <strong className="text-text">
            Sin contraseñas del banco: tú decides qué le llega.
          </strong>
        </p>

        <div className="mt-7 flex flex-col gap-2.5">
          <Button
            full
            onClick={() => {
              record({ welcomeSeen: true, introSeen: true });
              void navigate({
                to: "/conectar",
                search: { paso: stageNumber(state.current ?? "banks") },
              });
            }}
          >
            {resuming ? "Seguir configurando" : "Comenzar configuración"}
            <ArrowRight className="size-4" aria-hidden />
          </Button>
          {/*
            Closing is a real answer, not a way out of a nag: the app works
            without this — every alert is still recorded — and the banner
            carries the step from here on.
          */}
          <Button variant="quiet" onClick={dismiss}>
            Ahora no, explorar primero
          </Button>
        </div>
      </div>
    </div>
  );
}
