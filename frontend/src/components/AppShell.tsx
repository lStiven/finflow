/**
 * The frame every signed-in screen sits in.
 *
 * One route tree, two presentations of the same navigation: a rail on the
 * left from `lg` up, a bar along the bottom below it. They are not two
 * layouts sharing a name — the bar is thumb-reachable and shows five
 * destinations at most, the rail has room for all of them plus labels, so
 * each lists what it can actually fit rather than hiding the overflow.
 */

import { Link } from "@tanstack/react-router";
import {
  ArrowLeftRight,
  BarChart3,
  LayoutGrid,
  LogOut,
  MoreHorizontal,
  Plus,
  Settings,
  Store,
  Wallet,
} from "lucide-react";
import type { ComponentType, ReactNode } from "react";
import { useAuth } from "@/auth/AuthContext";
import { cn } from "@/lib/cn";

type Destination = {
  label: string;
  icon: ComponentType<{ className?: string }>;
  /** Absent until the screen exists — rendered as pending, never as a dead link. */
  to?: "/" | "/transacciones";
};

const DESTINATIONS: Destination[] = [
  { label: "Resumen", icon: LayoutGrid, to: "/" },
  { label: "Transacciones", icon: ArrowLeftRight, to: "/transacciones" },
  { label: "Cuentas", icon: Wallet },
  { label: "Reportes", icon: BarChart3 },
  { label: "Comercios", icon: Store },
  { label: "Configuración", icon: Settings },
];

/** What the bottom bar shows, in thumb order, with the action in the middle. */
const BAR = DESTINATIONS.slice(0, 4);

export function AppShell({ children }: { children: ReactNode }) {
  return (
    <div className="min-h-dvh lg:flex">
      <Rail />
      <div className="min-w-0 flex-1">
        <main className="aurora rise mx-auto w-full max-w-6xl px-4 pt-6 pb-32 sm:px-6 lg:px-10 lg:pt-10 lg:pb-16">
          {children}
        </main>
      </div>
      <Bar />
    </div>
  );
}

function Wordmark({ className }: { className?: string }) {
  return (
    <span className={cn("flex items-center gap-2", className)}>
      <span
        aria-hidden
        className="grid size-8 place-items-center rounded-lg bg-accent text-accent-ink"
      >
        <BarChart3 className="size-4" />
      </span>
      <span className="text-base font-semibold tracking-tight">Finflow</span>
    </span>
  );
}

function Rail() {
  const { logout } = useAuth();

  return (
    <aside className="sticky top-0 hidden h-dvh w-60 shrink-0 flex-col gap-8 border-line border-r bg-surface px-4 py-6 lg:flex">
      <Wordmark className="px-2" />

      <nav aria-label="Secciones" className="flex flex-col gap-1">
        {DESTINATIONS.map(({ label, icon: Icon, to }) =>
          to ? (
            <Link
              key={label}
              to={to}
              activeOptions={{ exact: to === "/" }}
              className="flex items-center gap-3 rounded-xl border border-transparent px-3 py-2.5 text-muted text-sm transition-all duration-200 hover:bg-surface-raised hover:text-text"
              activeProps={{
                className:
                  "border-accent/25 bg-gradient-to-r from-accent/18 via-violet/12 to-transparent font-medium text-text",
                "aria-current": "page",
              }}
            >
              <Icon className="size-4 shrink-0" />
              {label}
            </Link>
          ) : (
            <Pending key={label} label={label} icon={Icon} />
          ),
        )}
      </nav>

      <div className="mt-auto flex flex-col gap-1 border-line border-t pt-4">
        <p className="px-3 text-faint text-xs">Sesión iniciada</p>
        <button
          type="button"
          onClick={logout}
          className="flex items-center gap-3 rounded-xl px-3 py-2.5 text-muted text-sm transition-colors hover:bg-surface-raised hover:text-text"
        >
          <LogOut className="size-4 shrink-0" />
          Salir
        </button>
      </div>
    </aside>
  );
}

/**
 * A destination whose screen has not been built yet.
 *
 * Rendered rather than hidden, and disabled rather than linked: a nav that
 * grows an item per release reads as instability, and a link that goes
 * nowhere reads as a bug. This says "not yet" in the one place someone would
 * look for it.
 */
function Pending({
  label,
  icon: Icon,
  compact = false,
}: {
  label: string;
  icon: ComponentType<{ className?: string }>;
  compact?: boolean;
}) {
  const shared = "cursor-not-allowed text-faint/55";
  return (
    <button
      type="button"
      disabled
      aria-disabled
      title={`${label} — próximamente`}
      className={
        compact
          ? cn("flex flex-1 flex-col items-center gap-1 py-2 text-[0.6875rem]", shared)
          : cn(
              "flex items-center gap-3 rounded-xl px-3 py-2.5 text-left text-sm",
              shared,
            )
      }
    >
      <Icon className={compact ? "size-5" : "size-4 shrink-0"} />
      {label}
      {compact ? null : (
        <span className="ml-auto text-[0.5625rem] text-faint/50 uppercase tracking-wider">
          Pronto
        </span>
      )}
    </button>
  );
}

function Bar() {
  return (
    <nav
      aria-label="Secciones"
      className="fixed inset-x-0 bottom-0 z-20 flex items-stretch border-line border-t bg-surface/95 pb-[env(safe-area-inset-bottom)] backdrop-blur lg:hidden"
    >
      {BAR.slice(0, 2).map((item) => (
        <BarItem key={item.label} item={item} />
      ))}

      {/* The action the design anchors the bar on. */}
      <div className="relative w-16 shrink-0">
        <Link
          to="/transacciones/nueva"
          className="-translate-x-1/2 -top-5 absolute left-1/2 grid size-14 place-items-center rounded-full border-4 border-ink bg-accent text-accent-ink"
        >
          <Plus className="size-6" />
          <span className="sr-only">Registrar un movimiento</span>
        </Link>
      </div>

      {BAR.slice(2).map((item) => (
        <BarItem key={item.label} item={item} />
      ))}
      <Pending label="Más" icon={MoreHorizontal} compact />
    </nav>
  );
}

function BarItem({ item }: { item: Destination }) {
  const { label, icon: Icon, to } = item;
  if (!to) return <Pending label={label} icon={Icon} compact />;

  return (
    <Link
      to={to}
      activeOptions={{ exact: to === "/" }}
      className="flex flex-1 flex-col items-center gap-1 py-2 text-[0.6875rem] text-muted transition-colors"
      activeProps={{ className: "text-accent", "aria-current": "page" }}
    >
      <Icon className="size-5" />
      {label}
    </Link>
  );
}
