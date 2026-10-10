import { useQuery } from "@tanstack/react-query";
import { useNavigate, useRouterState } from "@tanstack/react-router";
import { useCallback } from "react";
import { latestAlertMovementQuery } from "@/api/queries";
import { Button } from "@/components/ui/Button";
import { useDismissOnEscape } from "@/lib/useDismissOnEscape";
import { useScrollLock } from "@/lib/useScrollLock";
import { MovementCard } from "@/onboarding/MovementCard";
import { SuccessMark } from "@/onboarding/parts";
import { useOnboarding } from "@/onboarding/useOnboarding";

/**
 * Said once, the first time expenses actually start arriving.
 *
 * The moment worth interrupting somebody for: everything up to here was
 * instructions, and this is the only point where the app can say the thing
 * they were doing it for. It is shown from the shell rather than from the
 * guide because the poll can flip to ready while they are looking at any
 * screen — including a dashboard that has just filled with their first
 * movement. On the guide's last step it stands down: that step says it in
 * place.
 *
 * What it claims follows what exists. "Ready" means an email got through,
 * not that a movement came out of it, so "Finflow is working" waits for a
 * movement it can show; until then it says what is known — the first alert
 * arrived.
 */
export function ReadyDialog() {
  const { state, acknowledge } = useOnboarding();
  const navigate = useNavigate();
  const onLastStep = useRouterState({
    select: (router) => {
      if (router.location.pathname !== "/conectar") return false;
      const { paso } = router.location.search as { paso?: unknown };
      return paso === undefined || paso === 4;
    },
  });
  const open = Boolean(state?.celebrate) && !onLastStep;
  const latest = useQuery({ ...latestAlertMovementQuery, enabled: open });

  // See `WelcomeDialog`: the condition is passed in because the hook cannot
  // sit after the early return.
  useScrollLock(open);
  useDismissOnEscape(
    useCallback(() => acknowledge("readyCelebrated"), [acknowledge]),
    open,
  );

  if (!open) return null;

  const dismiss = () => acknowledge("readyCelebrated");
  const movement = latest.data?.transactions[0] ?? null;

  return (
    <div
      role="dialog"
      aria-modal
      aria-labelledby="ready-title"
      aria-describedby="ready-body"
      className="fixed inset-0 z-50 grid place-items-center bg-ink/80 p-5 backdrop-blur-sm"
    >
      <div className="rise surface w-full max-w-md rounded-card border border-incoming/30 bg-surface p-7 text-center">
        <SuccessMark celebrate className="mx-auto" />

        <h2 id="ready-title" className="mt-6 font-semibold text-xl tracking-tight">
          {movement ? "¡Finflow ya está funcionando!" : "Llegó tu primera alerta"}
        </h2>
        <p id="ready-body" className="mt-2.5 text-muted text-sm leading-relaxed">
          {movement ? (
            <>
              Este movimiento llegó solo, desde tu banco.{" "}
              <strong className="text-text">
                Desde ahora no tienes que escribir nada
              </strong>
              : revisa y corrige cuando quieras.
            </>
          ) : (
            "Tu banco ya le escribe a Finflow. En cuanto se lea, verás el movimiento en Transacciones."
          )}
        </p>

        {movement ? (
          <MovementCard
            movement={movement}
            onOpen={dismiss}
            className="mt-5 text-left"
          />
        ) : null}

        <div className="mt-7 flex flex-col gap-2.5">
          {/* Navigating in the handler rather than wrapping the button in a
              link: a <button> inside an <a> is one control nested in
              another, which assistive technology reads as ambiguous. */}
          <Button
            full
            onClick={() => {
              dismiss();
              void navigate({ to: "/transacciones" });
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
