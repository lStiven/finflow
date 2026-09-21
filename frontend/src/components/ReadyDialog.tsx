import { useNavigate } from "@tanstack/react-router";
import { PartyPopper } from "lucide-react";
import { useCallback } from "react";
import { Button } from "@/components/ui/Button";
import { useDismissOnEscape } from "@/lib/useDismissOnEscape";
import { useScrollLock } from "@/lib/useScrollLock";
import { useOnboarding } from "@/onboarding/useOnboarding";

/**
 * Said once, the first time expenses actually start arriving.
 *
 * The moment worth interrupting somebody for: everything up to here was
 * instructions, and this is the only point where the app can say the thing
 * they were doing it for. It is shown from the shell rather than from the
 * guide because the poll can flip to ready while they are looking at any
 * screen — including a dashboard that has just filled with their first
 * movement.
 */
export function ReadyDialog() {
  const { state, acknowledge } = useOnboarding();
  const navigate = useNavigate();

  // See `WelcomeDialog`: the condition is passed in because the hook cannot
  // sit after the early return.
  useScrollLock(Boolean(state?.celebrate));
  useDismissOnEscape(
    useCallback(() => acknowledge("readyCelebrated"), [acknowledge]),
    Boolean(state?.celebrate),
  );

  if (!state?.celebrate) return null;

  const dismiss = () => acknowledge("readyCelebrated");

  return (
    <div
      role="dialog"
      aria-modal
      aria-labelledby="ready-title"
      className="fixed inset-0 z-50 grid place-items-center bg-ink/80 p-5 backdrop-blur-sm"
    >
      <div className="rise surface w-full max-w-md rounded-card border border-incoming/30 bg-surface p-7 text-center">
        <span
          aria-hidden
          className="pulse-ring mx-auto grid size-14 place-items-center rounded-2xl bg-incoming/15 ring-1 ring-incoming/30"
        >
          <PartyPopper className="size-6 text-incoming" />
        </span>

        <h2 id="ready-title" className="mt-5 font-semibold text-xl tracking-tight">
          Listo, ya quedó conectado
        </h2>
        <p className="mt-2.5 text-muted text-sm leading-relaxed">
          Tu banco ya le está escribiendo a Finflow. Desde ahora{" "}
          <strong className="text-text">
            cada gasto que te notifique se registra solo
          </strong>
          : no tienes que escribir nada. Revisa tus movimientos cuando quieras y corrige
          lo que haga falta.
        </p>

        <div className="mt-7 flex flex-col gap-2.5">
          {/* Navigating in the handler rather than wrapping the button in a
              link: a <button> inside an <a> is one control nested in
              another, which assistive technology reads as ambiguous. */}
          <Button
            full
            onClick={() => {
              dismiss();
              void navigate({ to: "/" });
            }}
          >
            Ver mis movimientos
          </Button>
          <Button variant="quiet" onClick={dismiss}>
            Seguir aquí
          </Button>
        </div>
      </div>
    </div>
  );
}
