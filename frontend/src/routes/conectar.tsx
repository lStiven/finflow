import { createFileRoute, Link, redirect, useNavigate } from "@tanstack/react-router";
import { ArrowLeft } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import {
  inboxQuery,
  latestAlertMovementQuery,
  recentMailQuery,
  setupQuery,
} from "@/api/queries";
import { AppShell } from "@/components/AppShell";
import { cn } from "@/lib/cn";
import { nowInSeconds } from "@/lib/dates";
import { useReducedMotion } from "@/lib/useReducedMotion";
import { AddressStep } from "@/onboarding/AddressStep";
import { BanksStep } from "@/onboarding/BanksStep";
import { ConfirmStep } from "@/onboarding/ConfirmStep";
import { ConnectIntro } from "@/onboarding/ConnectIntro";
import { ConnectionSummary } from "@/onboarding/ConnectionSummary";
import { FilterStep } from "@/onboarding/FilterStep";
import { STEP_TITLE_ID } from "@/onboarding/parts";
import { Stepper } from "@/onboarding/Stepper";
import {
  type OnboardingState,
  STAGES,
  type StageId,
  stageAt,
  stageNumber,
} from "@/onboarding/steps";
import { type Onboarding, useOnboarding } from "@/onboarding/useOnboarding";

/**
 * Which step is on screen, carried in the address: the back button walks
 * the steps, a link sent from a phone to a computer opens the same one, and
 * a server answer arriving mid-read never moves the page under somebody.
 */
type ConnectSearch = { paso?: number };

export const Route = createFileRoute("/conectar")({
  beforeLoad: ({ context }) => {
    if (!context.session) throw redirect({ to: "/login" });
  },
  validateSearch: (raw: Record<string, unknown>): ConnectSearch => {
    const paso = Number(raw.paso);
    return Number.isInteger(paso) && stageAt(paso) ? { paso } : {};
  },
  loader: ({ context }) => {
    // Not waited on: only the last step and the connection's status read
    // these, and both draw a placeholder until they land.
    void context.queryClient.prefetchQuery(recentMailQuery);
    void context.queryClient.prefetchQuery(latestAlertMovementQuery);
    return Promise.all([
      context.queryClient.ensureQueryData(setupQuery),
      context.queryClient.ensureQueryData(inboxQuery),
    ]);
  },
  component: ConnectScreen,
});

type View =
  | { kind: "intro" }
  | { kind: "status" }
  | { kind: "step"; stage: StageId; review: boolean };

/**
 * One screen, three faces, decided by what is true rather than by where
 * somebody clicked: the door for an account with nothing done, the steps
 * while anything is open, and the connection's status once expenses arrive.
 * A step asked for by the address always wins — that is how somebody whose
 * setup is finished still manages their banks or rereads the Gmail part.
 */
function viewOf(
  state: OnboardingState,
  paso: number | undefined,
  celebrating: boolean,
): View {
  const asked = paso === undefined ? undefined : stageAt(paso);
  if (asked)
    return { kind: "step", stage: asked, review: state.complete && !celebrating };
  if (state.complete) {
    return celebrating
      ? { kind: "step", stage: "first-alert", review: false }
      : { kind: "status" };
  }
  if (state.intro) return { kind: "intro" };
  return { kind: "step", stage: state.current ?? "banks", review: false };
}

function ConnectScreen() {
  const { state, acknowledge, record } = useOnboarding();
  const { paso } = Route.useSearch();
  const navigate = useNavigate({ from: Route.fullPath });
  const [celebrated, setCelebrated] = useState(false);

  // The moment it all came true, while the last step (or nothing in
  // particular) is on screen: this page is the celebration then, and the
  // shell's dialog stands down. On any other step the dialog says it.
  const celebrateHere = Boolean(state?.celebrate) && (paso === undefined || paso === 4);
  const celebrating = celebrated || celebrateHere;

  useEffect(() => {
    if (!celebrateHere) return;
    setCelebrated(true);
    acknowledge("readyCelebrated");
  }, [celebrateHere, acknowledge]);

  // The introduction is this screen's welcome: having seen it, the shell's
  // dialog has nothing left to say.
  useEffect(() => {
    if (state?.intro && state.welcome) acknowledge("welcomeSeen");
  }, [state?.intro, state?.welcome, acknowledge]);

  const view = state ? viewOf(state, paso, celebrating) : null;
  const pinned = view?.kind === "step" ? view.stage : null;

  useEffect(() => {
    if (pinned && paso === undefined) {
      void navigate({ search: { paso: stageNumber(pinned) }, replace: true });
    }
  }, [pinned, paso, navigate]);

  if (!state || !view) return null;

  const open = (id: StageId) => void navigate({ search: { paso: stageNumber(id) } });
  const toStatus = () => {
    setCelebrated(false);
    void navigate({ search: {} });
  };

  return (
    <AppShell>
      {view.kind === "intro" ? (
        <ConnectIntro
          onStart={() => {
            acknowledge("introSeen");
            open("banks");
          }}
        />
      ) : view.kind === "status" ? (
        <div className="mx-auto w-full max-w-4xl">
          <ConnectionSummary
            state={state}
            onOpenStage={open}
            onFilterUpdated={(terms) => record({ filterSenders: terms })}
          />
        </div>
      ) : (
        <Wizard
          state={state}
          stage={view.stage}
          review={view.review}
          celebrating={celebrating}
          onOpen={open}
          onStatus={toStatus}
          acknowledge={acknowledge}
          record={record}
        />
      )}
    </AppShell>
  );
}

function Wizard({
  state,
  stage,
  review,
  celebrating,
  onOpen,
  onStatus,
  acknowledge,
  record,
}: {
  state: OnboardingState;
  stage: StageId;
  /** Revisiting a finished setup rather than doing it. */
  review: boolean;
  celebrating: boolean;
  onOpen: (id: StageId) => void;
  onStatus: () => void;
  acknowledge: Onboarding["acknowledge"];
  record: Onboarding["record"];
}) {
  const reduced = useReducedMotion();
  const top = useRef<HTMLDivElement>(null);
  const lastStage = useRef(stage);

  // Which way the step came from, so it slides in from that side. Adjusted
  // during render, the way React suggests for state that follows a prop.
  const [shown, setShown] = useState<{ stage: StageId; direction: "forward" | "back" }>(
    {
      stage,
      direction: "forward",
    },
  );
  if (shown.stage !== stage) {
    setShown({
      stage,
      direction:
        STAGES.indexOf(stage) > STAGES.indexOf(shown.stage) ? "forward" : "back",
    });
  }

  useEffect(() => {
    if (lastStage.current === stage) return;
    lastStage.current = stage;
    // A new step is announced by its title, and seen from its top: "Continuar"
    // is pressed at the bottom of a long step.
    document.getElementById(STEP_TITLE_ID)?.focus({ preventScroll: true });
    if ((top.current?.getBoundingClientRect().top ?? 0) < 0) {
      top.current?.scrollIntoView({
        behavior: reduced ? "auto" : "smooth",
        block: "start",
      });
    }
  }, [stage, reduced]);

  const done = (id: StageId) =>
    state.stages.find((each) => each.id === id)?.done ?? false;

  return (
    <div ref={top} className="mx-auto w-full max-w-3xl scroll-mt-6">
      <header className="mb-6 flex flex-wrap items-end justify-between gap-3">
        <div>
          <p className="text-faint text-xs uppercase tracking-wider">
            {review
              ? "Tu conexión"
              : `${state.doneCount} de ${state.total} pasos listos`}
          </p>
          <h1 className="mt-1 font-semibold text-2xl tracking-tight">
            Conecta tu banco
          </h1>
        </div>
        {review ? (
          <Link
            to="/conectar"
            search={{}}
            className="inline-flex min-h-10 items-center gap-1.5 rounded-lg text-cyan text-sm underline-offset-4 hover:underline"
          >
            <ArrowLeft className="size-4" aria-hidden />
            Estado de la conexión
          </Link>
        ) : null}
      </header>

      <Stepper stages={state.stages} viewing={stage} onSelect={onOpen} />

      <div
        key={stage}
        className={cn(
          "mt-8",
          shown.direction === "back" ? "step-in-back" : "step-in-forward",
        )}
      >
        {stage === "banks" ? (
          <BanksStep
            unapprovedSenders={state.unapprovedSenders}
            filterDone={done("filter")}
            continueLabel={review ? "Listo" : "Continuar"}
            onContinue={review ? onStatus : () => onOpen("address")}
            onOpenFilter={() => onOpen("filter")}
          />
        ) : stage === "address" ? (
          <AddressStep
            address={state.address}
            confirmedAt={state.forwardingConfirmedAt}
            receiving={state.firstAlertAt !== null || state.complete}
            requestedAt={state.forwardingRequestedAt}
            onCopied={() => acknowledge("addressCopied")}
            onRequested={() => record({ forwardingRequestedAt: nowInSeconds() })}
            onContinue={() => onOpen("filter")}
          />
        ) : stage === "filter" ? (
          <FilterStep
            address={state.address}
            addressConfirmed={done("address")}
            done={done("filter")}
            onDone={(terms) => {
              record({ gmailSubmitted: true, filterSenders: terms });
              onOpen("first-alert");
            }}
            onOpenBanks={() => onOpen("banks")}
            onOpenAddress={() => onOpen("address")}
          />
        ) : (
          <ConfirmStep
            state={state}
            celebrating={celebrating}
            onOpenStage={onOpen}
            onSummary={onStatus}
          />
        )}
      </div>
    </div>
  );
}
