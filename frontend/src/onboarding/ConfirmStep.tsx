import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { ArrowRight, Check, Circle, FileQuestion, Loader2 } from "lucide-react";
import { useEffect } from "react";
import { latestAlertMovementQuery, queryKeys, recentMailQuery } from "@/api/queries";
import { Button, buttonClass } from "@/components/ui/Button";
import { SuccessMark } from "@/components/ui/SuccessMark";
import { cn } from "@/lib/cn";
import { evidenceOf, outcomeOf } from "@/onboarding/activity";
import { DiscardedSenders } from "@/onboarding/BanksStep";
import { isApproved } from "@/onboarding/banks";
import { STAGE_COPY } from "@/onboarding/copy";
import { FlowAnimation } from "@/onboarding/FlowAnimation";
import { MovementCard, MovementPlaceholder } from "@/onboarding/MovementCard";
import { StepHeading, WaitingDot } from "@/onboarding/parts";
import type { OnboardingState, StageId } from "@/onboarding/steps";
import { useSenders } from "@/onboarding/useSenders";

/** While the outcome is still open, how often to look again. */
const WATCH_MS = 6_000;

/**
 * Step four: the proof, and the only step nobody performs.
 *
 * "An email got through" and "a movement came out of it" are told apart
 * here, because they are different facts: the first proves the route, only
 * the second proves Finflow works. Each answer the records can give has its
 * own screen, and none of them claims more than its evidence — a celebration
 * needs a movement to point at.
 *
 * Nothing waits on this step: the rest of the app works without a first
 * alert, and the shell tells somebody the moment it lands wherever they are.
 */
export function ConfirmStep({
  state,
  celebrating,
  onOpenStage,
  onSummary,
}: {
  state: OnboardingState;
  /** The moment it all just came true, rather than a later visit. */
  celebrating: boolean;
  onOpenStage: (id: StageId) => void;
  onSummary: () => void;
}) {
  const control = useSenders();
  const queryClient = useQueryClient();

  const mail = useQuery({
    ...recentMailQuery,
    // Until a movement exists the outcome can still change under the screen.
    refetchInterval: (query) =>
      query.state.data?.notifications.some(
        (notification) => outcomeOf(notification.status) === "registered",
      )
        ? false
        : WATCH_MS,
  });
  const latest = useQuery({
    ...latestAlertMovementQuery,
    refetchInterval: (query) =>
      query.state.data?.transactions.length ? false : WATCH_MS,
  });

  const notifications = mail.data?.notifications ?? [];
  const movement = latest.data?.transactions[0] ?? null;
  // An accepted email is the first alert, whether or not the setup endpoint
  // has been asked since: this list is polled faster than that one.
  const sawAccepted = notifications.some(
    (notification) => outcomeOf(notification.status) !== "discarded",
  );
  const firstAlert = state.firstAlertAt !== null || state.complete || sawAccepted;

  useEffect(() => {
    // Bring the stepper and the shell up to what this screen already knows.
    if ((sawAccepted || movement) && !state.complete) {
      void queryClient.invalidateQueries({ queryKey: queryKeys.setup });
    }
  }, [sawAccepted, movement, state.complete, queryClient]);

  const evidence = evidenceOf({
    firstAlert,
    unapprovedSenders: state.unapprovedSenders.filter(
      (sender) => !isApproved(sender, control.senders),
    ),
    notifications,
    movement,
  });

  const filterDone = state.stages.find((stage) => stage.id === "filter")?.done ?? false;
  const loading = mail.isPending || latest.isPending;

  if (loading && !firstAlert) {
    return (
      <div>
        <StepHeading title={STAGE_COPY["first-alert"].title} />
        <p className="flex items-center gap-2 text-muted text-sm">
          <Loader2 className="size-4 animate-spin" aria-hidden />
          Revisando qué ha llegado…
        </p>
      </div>
    );
  }

  switch (evidence.kind) {
    case "registered":
      return (
        <div className="flex flex-col items-center text-center">
          <SuccessMark celebrate={celebrating} className="mt-2" />
          <div className="mt-6 w-full">
            <StepHeading
              center
              title="¡Finflow ya está funcionando!"
              lead={
                evidence.movement
                  ? "Este movimiento llegó solo, desde la alerta de tu banco."
                  : "Tu primera alerta ya se convirtió en un movimiento."
              }
            />
          </div>
          {evidence.movement ? (
            <MovementCard movement={evidence.movement} className="w-full max-w-md" />
          ) : null}
          <div className="mt-8 flex w-full flex-col-reverse gap-2 sm:flex-row sm:justify-center">
            <Button variant="quiet" onClick={onSummary}>
              Ver el estado de la conexión
            </Button>
            <Link to="/transacciones" className={buttonClass("primary")}>
              Ver mis movimientos
              <ArrowRight className="size-4" aria-hidden />
            </Link>
          </div>
        </div>
      );

    case "reading":
      return (
        <div>
          <StepHeading
            title="¡Llegó tu primera alerta!"
            lead="Finflow la está leyendo. En unos segundos verás el movimiento aquí."
          />
          <MovementPlaceholder />
          <p role="status" className="mt-3 flex items-center gap-2 text-muted text-sm">
            <Loader2 className="size-4 animate-spin text-cyan" aria-hidden />
            Leyendo tu alerta…
          </p>
        </div>
      );

    case "unreadable":
      return (
        <div>
          <StepHeading
            title="Llegó tu correo, pero no traía un movimiento"
            lead="El reenvío funciona: Finflow recibió tu correo. Pasa con los correos del banco que no son compras ni pagos, como la publicidad. Las próximas alertas se leerán solas."
          />
          <Proven />
          <div className="mt-8 flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
            <Button variant="quiet" onClick={onSummary}>
              Ver el estado de la conexión
            </Button>
            <Link to="/" className={buttonClass("primary")}>
              Ir al Resumen
            </Link>
          </div>
        </div>
      );

    case "received":
      return (
        <div>
          <StepHeading
            title="Ya llegan correos de tu banco"
            lead="El reenvío funciona. Los movimientos que salgan de tus alertas aparecerán en Transacciones."
          />
          <Proven />
          <div className="mt-8 flex justify-end">
            <Link to="/transacciones" className={buttonClass("primary")}>
              Ver mis movimientos
            </Link>
          </div>
        </div>
      );

    case "discarded":
      return (
        <div>
          <StepHeading
            title="Llegó un correo, pero no de tus bancos"
            lead="Finflow lo descartó sin leerlo, como debe hacer con quien no elegiste."
          />
          <DiscardedSenders senders={evidence.senders} control={control} />
          <Checklist state={state} onOpenStage={onOpenStage} />
        </div>
      );

    case "waiting":
      return filterDone ? (
        <div>
          <div className="mb-8 flex justify-center pt-2">
            <FlowAnimation size="lg" live />
          </div>
          <StepHeading
            title="Esperando tu primera alerta"
            lead="Usa tu tarjeta o recibe una transferencia: cuando tu banco te avise, el movimiento aparecerá aquí solo."
          />
          <Checklist state={state} onOpenStage={onOpenStage} />
          <div className="mt-6 flex flex-col gap-3 rounded-2xl border border-line bg-surface p-4 sm:flex-row sm:items-center sm:justify-between">
            <p className="text-muted text-sm leading-relaxed">
              No tienes que esperar aquí: puedes usar Finflow y te avisaremos cuando
              llegue.
            </p>
            <Link to="/" className={cn(buttonClass("ghost"), "shrink-0")}>
              Ir al Resumen
            </Link>
          </div>
        </div>
      ) : (
        <div>
          <StepHeading
            title={STAGE_COPY["first-alert"].title}
            lead="Cuando termines los pasos anteriores, aquí verás llegar tu primera alerta."
          />
          <Checklist state={state} onOpenStage={onOpenStage} />
          {state.current && state.current !== "first-alert" ? (
            <Button
              onClick={() => state.current && onOpenStage(state.current)}
              className="mt-6"
            >
              Seguir con: {STAGE_COPY[state.current].title}
              <ArrowRight className="size-4" aria-hidden />
            </Button>
          ) : null}
        </div>
      );
  }
}

/** The route is proven even when this email had nothing to read. */
function Proven() {
  return (
    <div className="flex items-start gap-3 rounded-2xl border border-line bg-surface p-4">
      <FileQuestion className="mt-0.5 size-5 shrink-0 text-faint" aria-hidden />
      <p className="text-muted text-sm leading-relaxed">
        Si era una alerta de una compra y no aparece en Transacciones, la puedes
        registrar a mano.{" "}
        <Link
          to="/transacciones/nueva"
          className="font-medium text-cyan underline-offset-4 hover:underline"
        >
          Registrar un movimiento
        </Link>
      </p>
    </div>
  );
}

const CHECKLIST: Record<StageId, string> = {
  banks: "Bancos elegidos",
  address: "Dirección autorizada en Gmail",
  filter: "Filtro creado en Gmail",
  "first-alert": "Primera alerta recibida",
};

/**
 * Where everything stands, with who vouches for each line: a tick the server
 * proved and a tick somebody gave themselves are not drawn the same.
 */
function Checklist({
  state,
  onOpenStage,
}: {
  state: OnboardingState;
  onOpenStage: (id: StageId) => void;
}) {
  return (
    <ul className="mt-2 flex flex-col divide-y divide-line/70 rounded-2xl border border-line bg-surface">
      {state.stages.map((stage) => {
        const proven = stage.done && stage.proof === "verified";
        return (
          <li key={stage.id}>
            <button
              type="button"
              onClick={() => onOpenStage(stage.id)}
              className="flex min-h-12 w-full items-center gap-3 px-4 py-3 text-left transition-colors hover:bg-surface-raised/60"
            >
              <span aria-hidden className="grid size-6 shrink-0 place-items-center">
                {stage.done ? (
                  <span
                    className={cn(
                      "grid size-5 place-items-center rounded-full",
                      proven ? "bg-incoming text-ink" : "bg-incoming/15 text-incoming",
                    )}
                  >
                    <Check className="size-3" strokeWidth={3} />
                  </span>
                ) : stage.status === "waiting" ? (
                  <WaitingDot />
                ) : (
                  <Circle className="size-5 text-line" />
                )}
              </span>
              <span className="min-w-0 flex-1 text-sm">{CHECKLIST[stage.id]}</span>
              <span
                className={cn(
                  "shrink-0 text-xs",
                  stage.done
                    ? "text-incoming"
                    : stage.status === "waiting"
                      ? "text-cyan"
                      : "text-faint",
                )}
              >
                {stage.done
                  ? proven
                    ? "Comprobado"
                    : "Lo marcaste tú"
                  : stage.status === "waiting"
                    ? "Esperando…"
                    : "Pendiente"}
              </span>
            </button>
          </li>
        );
      })}
    </ul>
  );
}
