import { createFileRoute, Link, redirect, useNavigate } from "@tanstack/react-router";
import { ArrowLeft } from "lucide-react";
import { AppShell } from "@/components/AppShell";
import { PageHeader } from "@/components/PageHeader";
import { Tutorial } from "@/components/ui/Tutorial";
import { STORIES, type StoryId, storyOf } from "@/guides/stories";
import { cn } from "@/lib/cn";

/** Which flow is on screen, in the address — a link can open the one it means. */
type FlowsSearch = { ver?: StoryId };

export const Route = createFileRoute("/guias/flujos")({
  beforeLoad: ({ context }) => {
    if (!context.session) throw redirect({ to: "/login" });
  },
  validateSearch: (raw: Record<string, unknown>): FlowsSearch => {
    const found = STORIES.find((story) => story.id === raw.ver);
    return found ? { ver: found.id } : {};
  },
  component: FlowsScreen,
});

/**
 * The four flows, together: what people otherwise learn by getting a number
 * wrong. Each screen's «Cómo funciona» opens its own; this is where all of
 * them can be found from Guías.
 */
function FlowsScreen() {
  const { ver } = Route.useSearch();
  const navigate = useNavigate({ from: Route.fullPath });
  const story = storyOf(ver ?? "movimiento");

  return (
    <AppShell>
      <div className="mx-auto flex w-full max-w-2xl flex-col gap-6">
        <Link
          to="/guias"
          className="-my-2 flex min-h-11 items-center gap-1.5 self-start text-muted text-xs transition-colors hover:text-text"
        >
          <ArrowLeft className="size-3.5" />
          Guías
        </Link>

        <PageHeader
          title="Así funciona Finflow"
          lead="Cuatro recorridos cortos, paso a paso y a tu ritmo."
        />

        <div
          role="tablist"
          aria-label="Recorridos"
          className="grid grid-cols-1 gap-2 min-[440px]:grid-cols-2"
        >
          {STORIES.map((each) => (
            <button
              key={each.id}
              type="button"
              role="tab"
              aria-selected={each.id === story.id}
              onClick={() => void navigate({ search: { ver: each.id }, replace: true })}
              className={cn(
                "min-h-11 rounded-xl border px-3 text-left text-sm transition-colors",
                each.id === story.id
                  ? "border-accent/50 bg-accent-soft text-text"
                  : "border-line text-muted hover:border-accent/30 hover:text-text",
              )}
            >
              {each.title}
            </button>
          ))}
        </div>

        {/* Keyed on the story: switching starts the new one at its first step. */}
        <div key={story.id} role="tabpanel" aria-label={story.title} className="rise">
          <Tutorial label={story.title} slides={story.slides} />
        </div>
      </div>
    </AppShell>
  );
}
