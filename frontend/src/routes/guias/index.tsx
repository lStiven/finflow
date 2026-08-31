import { createFileRoute, Link, redirect } from "@tanstack/react-router";
import { ArrowRight, BookOpen, Mail, Wallet } from "lucide-react";
import type { ComponentType } from "react";
import { AppShell } from "@/components/AppShell";
import { Card } from "@/components/ui/Card";
import { useOnboarding } from "@/onboarding/useOnboarding";

export const Route = createFileRoute("/guias/")({
  beforeLoad: ({ context }) => {
    if (!context.session) throw redirect({ to: "/login" });
  },
  component: GuidesScreen,
});

/**
 * Where the explanations live, once they stop being steps.
 *
 * Finflow asks somebody to trust two things that no other finance app does —
 * that forwarding a bank email is enough, and that an account is a label they
 * declare rather than a connection — so the explanations are not decoration.
 * They are collected here instead of hidden inside each screen's empty state,
 * which is the only place they used to exist and the one place they vanish
 * from the moment there is data.
 */
function GuidesScreen() {
  const { state } = useOnboarding();
  const setupPending = state !== null && !state.complete;

  return (
    <AppShell>
      <header className="mb-8">
        <h1 className="font-semibold text-2xl tracking-tight">Guías</h1>
        <p className="mt-1.5 max-w-xl text-muted text-sm">
          Lo que hay que entender de Finflow, en dos lecturas cortas. Están aquí para
          volver cuando algo no cuadre.
        </p>
      </header>

      <div className="grid gap-4 sm:grid-cols-2">
        <GuideCard
          to="/conectar"
          icon={Mail}
          glow="cyan"
          title="Conectar tu banco"
          blurb="Los cinco pasos que hacen que tus gastos se registren solos: tu dirección, los remitentes que apruebas y el reenvío en Gmail."
          badge={
            state === null
              ? undefined
              : setupPending
                ? `${state.doneCount} de ${state.total} pasos`
                : "Completada"
          }
          pending={setupPending}
        />

        <GuideCard
          to="/guias/cuentas-y-movimientos"
          icon={Wallet}
          glow="accent"
          title="Cuentas y movimientos"
          blurb="Qué es cada una, cómo nacen, cómo se juntan y qué hacer cuando un movimiento queda sin asignar."
        />
      </div>

      <p className="mt-8 flex items-start gap-2.5 text-faint text-xs leading-relaxed">
        <BookOpen className="mt-0.5 size-3.5 shrink-0" />
        Todo lo que se explica aquí se puede corregir después desde la app. Nada de lo
        que declares queda escrito en piedra.
      </p>
    </AppShell>
  );
}

function GuideCard({
  to,
  icon: Icon,
  glow,
  title,
  blurb,
  badge,
  pending = false,
}: {
  to: "/conectar" | "/guias/cuentas-y-movimientos";
  icon: ComponentType<{ className?: string }>;
  glow: "cyan" | "accent";
  title: string;
  blurb: string;
  badge?: string;
  pending?: boolean;
}) {
  return (
    <Link to={to} className="block">
      <Card glow={glow} className="flex h-full flex-col gap-4">
        <div className="flex items-start justify-between gap-3">
          <span
            aria-hidden
            className={
              glow === "cyan"
                ? "grid size-11 place-items-center rounded-xl bg-cyan/12 text-cyan ring-1 ring-cyan/25"
                : "grid size-11 place-items-center rounded-xl bg-accent/12 text-accent ring-1 ring-accent/25"
            }
          >
            <Icon className="size-5" />
          </span>

          {badge ? (
            <span
              className={
                pending
                  ? "rounded-full border border-accent/30 bg-accent/10 px-2.5 py-1 text-[0.625rem] text-accent uppercase tracking-wider"
                  : "rounded-full border border-incoming/30 bg-incoming/10 px-2.5 py-1 text-[0.625rem] text-incoming uppercase tracking-wider"
              }
            >
              {badge}
            </span>
          ) : null}
        </div>

        <div>
          <h2 className="font-medium">{title}</h2>
          <p className="mt-1.5 text-muted text-sm leading-relaxed">{blurb}</p>
        </div>

        <span className="mt-auto flex items-center gap-1.5 text-cyan text-sm">
          Leer
          <ArrowRight className="size-3.5" />
        </span>
      </Card>
    </Link>
  );
}
