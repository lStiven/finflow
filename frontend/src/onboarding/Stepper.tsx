import { Check } from "lucide-react";
import { cn } from "@/lib/cn";
import { STAGE_COPY } from "@/onboarding/copy";
import { WaitingDot } from "@/onboarding/parts";
import type { Stage, StageId } from "@/onboarding/steps";

const STATUS_WORDS = {
  done: "listo",
  waiting: "en espera",
  todo: "pendiente",
} as const;

/**
 * Where the setup stands, and the way between its steps.
 *
 * Every step can be opened, in any order: the steps are independent facts,
 * not a corridor, and each one says for itself what it is still waiting on.
 * What is *being looked at* and what is *done* are separate marks — the bar
 * lights for the step on screen, the tick for a step the server confirmed —
 * so revisiting a finished step never makes it look unfinished.
 */
export function Stepper({
  stages,
  viewing,
  onSelect,
}: {
  stages: Stage[];
  viewing: StageId;
  onSelect: (id: StageId) => void;
}) {
  return (
    <nav aria-label="Pasos para conectar tu banco">
      <ol className="grid grid-cols-4 gap-2 sm:gap-3">
        {stages.map((stage, index) => {
          const here = stage.id === viewing;
          const copy = STAGE_COPY[stage.id];

          return (
            <li key={stage.id} className="min-w-0">
              <button
                type="button"
                onClick={() => onSelect(stage.id)}
                aria-current={here ? "step" : undefined}
                className="group flex min-h-11 w-full min-w-0 flex-col gap-2 rounded-lg pt-1 text-left"
              >
                <span
                  aria-hidden
                  className={cn(
                    "h-1 w-full rounded-full transition-colors duration-500",
                    here
                      ? "bg-accent"
                      : stage.status === "done"
                        ? "bg-incoming/70"
                        : stage.status === "waiting"
                          ? "bg-cyan/50"
                          : "bg-surface-raised group-hover:bg-line",
                  )}
                />
                <span className="flex min-w-0 items-center gap-1.5 sm:gap-2">
                  <Marker index={index} status={stage.status} here={here} />
                  <span
                    className={cn(
                      "truncate text-xs sm:text-sm",
                      here
                        ? "font-medium text-text"
                        : "text-muted group-hover:text-text",
                    )}
                  >
                    {copy.short}
                  </span>
                </span>
                <span className="sr-only">
                  {`Paso ${index + 1} de ${stages.length}, ${copy.title}: ${STATUS_WORDS[stage.status]}`}
                </span>
              </button>
            </li>
          );
        })}
      </ol>
    </nav>
  );
}

function Marker({
  index,
  status,
  here,
}: {
  index: number;
  status: Stage["status"];
  here: boolean;
}) {
  if (status === "done") {
    // Keyed on the status, so the tick pops in the moment the server says so
    // rather than on every render.
    return (
      <span
        key="done"
        aria-hidden
        className="pop grid size-5 shrink-0 place-items-center rounded-full bg-incoming/15 text-incoming ring-1 ring-incoming/40"
      >
        <Check className="size-3" strokeWidth={3} />
      </span>
    );
  }

  if (status === "waiting") {
    return (
      <span
        aria-hidden
        className="grid size-5 shrink-0 place-items-center rounded-full bg-cyan/10 ring-1 ring-cyan/30"
      >
        <WaitingDot className="size-1.5" />
      </span>
    );
  }

  return (
    <span
      aria-hidden
      className={cn(
        "grid size-5 shrink-0 place-items-center rounded-full text-[0.6875rem] tabular",
        here
          ? "bg-accent/15 text-accent ring-1 ring-accent/40"
          : "bg-surface-raised text-faint",
      )}
    >
      {index + 1}
    </span>
  );
}
