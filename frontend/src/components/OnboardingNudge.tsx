import { Link, useRouterState } from "@tanstack/react-router";
import { ArrowRight, Plug } from "lucide-react";
import { STAGE_COPY } from "@/onboarding/copy";
import { useOnboarding } from "@/onboarding/useOnboarding";

/**
 * The mark that says the setup is unfinished, and where it stands.
 *
 * On every signed-in screen while anything is open, and gone the moment
 * expenses are arriving — an onboarding banner that outlives the onboarding
 * is the thing people learn to stop reading. What it shows is the count and
 * the next stage by name, so "what is missing" is answerable without opening
 * anything. Not on the guide itself, which shows the same thing larger.
 */
export function OnboardingNudge() {
  const { state } = useOnboarding();
  const onGuide = useRouterState({
    select: (router) => router.location.pathname === "/conectar",
  });

  if (!state || state.complete || !state.current || onGuide) return null;

  const next = STAGE_COPY[state.current];

  return (
    <Link
      to="/conectar"
      className="surface rise mb-6 flex items-center gap-4 rounded-card border border-accent/25 bg-gradient-to-r from-accent/12 via-violet/8 to-transparent p-4 glow-accent"
    >
      <span
        aria-hidden
        className="grid size-10 shrink-0 place-items-center rounded-xl bg-accent/15 text-accent ring-1 ring-accent/30"
      >
        <Plug className="size-4" />
      </span>

      <span className="min-w-0 flex-1">
        <span className="flex flex-wrap items-baseline gap-x-2">
          <span className="font-medium text-sm">Te falta conectar tu banco</span>
          <span className="tabular text-faint text-xs">
            {state.doneCount} de {state.total} listos
          </span>
        </span>
        <span className="mt-0.5 block truncate text-muted text-sm">
          Sigue: {next.title}
        </span>
      </span>

      <span
        aria-hidden
        className="hidden shrink-0 items-center gap-1.5 text-accent text-sm sm:flex"
      >
        Continuar
        <ArrowRight className="size-4" />
      </span>
    </Link>
  );
}
