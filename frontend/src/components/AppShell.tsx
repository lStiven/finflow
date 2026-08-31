/**
 * The frame every signed-in screen sits in.
 *
 * One route tree, two presentations of the same navigation: a rail on the
 * left from `lg` up, a bar along the bottom below it. They are not two
 * layouts sharing a name — the bar is thumb-reachable and shows five
 * destinations at most, the rail has room for all of them plus labels, so
 * each lists what it can actually fit rather than hiding the overflow.
 */

import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import {
  ArrowLeftRight,
  BarChart3,
  BookOpen,
  ChevronRight,
  LayoutGrid,
  LogOut,
  Plug,
  Plus,
  Settings,
  Store,
  User,
  Wallet,
} from "lucide-react";
import type { ComponentType, ReactNode } from "react";
import { profileQuery } from "@/api/queries";
import { useAuth } from "@/auth/AuthContext";
import { BetaMark } from "@/components/BetaMark";
import { Logo } from "@/components/Logo";
import { NeonBackdrop } from "@/components/NeonBackdrop";
import { OnboardingNudge } from "@/components/OnboardingNudge";
import { ReadyDialog } from "@/components/ReadyDialog";
import { WelcomeDialog } from "@/components/WelcomeDialog";
import { cn } from "@/lib/cn";
import { useOnboarding } from "@/onboarding/useOnboarding";

type Destination = {
  label: string;
  icon: ComponentType<{ className?: string }>;
  /** Absent until the screen exists — rendered as pending, never as a dead link. */
  to?: "/" | "/transacciones" | "/cuentas" | "/perfil" | "/conectar" | "/guias";
};

const DESTINATIONS: Destination[] = [
  { label: "Resumen", icon: LayoutGrid, to: "/" },
  { label: "Transacciones", icon: ArrowLeftRight, to: "/transacciones" },
  { label: "Cuentas", icon: Wallet, to: "/cuentas" },
  { label: "Reportes", icon: BarChart3 },
  { label: "Comercios", icon: Store },
  { label: "Configuración", icon: Settings },
];

/**
 * What the bottom bar shows, in thumb order, with the action in the middle.
 * The account closes it: the rail says who is signed in in its footer, and a
 * phone has no footer to say it in.
 */
const BAR: Destination[] = [
  ...DESTINATIONS.slice(0, 3),
  { label: "Perfil", icon: User, to: "/perfil" },
];

export function AppShell({ children }: { children: ReactNode }) {
  return (
    <div className="min-h-dvh lg:flex">
      <NeonBackdrop variant="ambient" />
      <Rail />
      <div className="min-w-0 flex-1">
        <main className="rise mx-auto w-full max-w-6xl px-4 pt-6 pb-32 sm:px-6 lg:px-10 lg:pt-10 lg:pb-16">
          <OnboardingNudge />
          {children}
        </main>
      </div>
      <Bar />
      <WelcomeDialog />
      <ReadyDialog />
    </div>
  );
}

/**
 * The guide's own entry, which changes meaning rather than disappearing.
 *
 * While the setup is open it is the one thing to finish, so it points
 * straight at it and carries a dot. Once expenses are arriving there is
 * nothing left to do, and it becomes the way into every explanation — the
 * connection walkthrough among them, one click further in. Two entries would
 * be one asking for something nobody has left to do.
 */
function ConnectLink({ compact = false }: { compact?: boolean }) {
  const { state } = useOnboarding();
  const pending = state !== null && !state.complete;
  const label = pending ? "Conectar" : "Guías";
  const Icon = pending ? Plug : BookOpen;
  const to = pending ? "/conectar" : "/guias";

  if (compact) {
    return (
      <Link
        to={to}
        className="relative flex flex-1 flex-col items-center gap-1 py-2 text-[0.6875rem] text-muted transition-colors"
        activeProps={{ className: "text-accent", "aria-current": "page" }}
      >
        <Icon className="size-5" />
        {label}
        {pending ? <Dot className="top-1.5 right-1/2 mr-2" /> : null}
      </Link>
    );
  }

  return (
    <Link
      to={to}
      className="relative flex items-center gap-3 rounded-xl border border-transparent px-3 py-2.5 text-muted text-sm transition-all duration-200 hover:bg-surface-raised hover:text-text"
      activeProps={{
        className:
          "border-accent/25 bg-gradient-to-r from-accent/18 via-violet/12 to-transparent font-medium text-text",
        "aria-current": "page",
      }}
    >
      <Icon className="size-4 shrink-0" />
      {label}
      {pending ? (
        <span className="ml-auto flex items-center gap-2 text-[0.5625rem] text-accent uppercase tracking-wider">
          {state.doneCount}/{state.total}
          <Dot className="static" />
        </span>
      ) : null}
    </Link>
  );
}

/** The unfinished-setup mark: small, and never on its own without a count. */
function Dot({ className }: { className?: string }) {
  return (
    <span
      aria-hidden
      className={cn("absolute size-1.5 rounded-full bg-accent", className)}
    />
  );
}

function Wordmark({ className }: { className?: string }) {
  return (
    <span className={cn("flex items-center gap-2", className)}>
      <Logo className="size-8" />
      <span className="text-base font-semibold tracking-tight">Finflow</span>
    </span>
  );
}

function Rail() {
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
        <ConnectLink />
      </nav>

      <div className="mt-auto flex flex-col gap-3 border-line border-t pt-4">
        <div className="flex items-center gap-1">
          <AccountLink />
          <SignOutButton />
        </div>
        <BetaMark className="px-3 pb-1" />
      </div>
    </aside>
  );
}

/**
 * Who is signed in, and the way into their account.
 *
 * A plain `useQuery`, not the suspense form: the shell frames every screen,
 * and a name is not worth holding one back. Until it arrives — or if it never
 * does — the row still works as the link to the account screen.
 */
function AccountLink() {
  const { data: profile } = useQuery(profileQuery);

  return (
    <Link
      to="/perfil"
      className="flex items-center gap-3 rounded-xl border border-transparent px-3 py-2.5 text-left transition-all duration-200 hover:bg-surface-raised"
      activeProps={{ className: "border-accent/25 bg-surface-raised" }}
    >
      <span
        aria-hidden
        className="grid size-8 shrink-0 place-items-center rounded-lg bg-accent/15 font-semibold text-accent text-sm ring-1 ring-accent/25"
      >
        {profile ? initialOf(profile.name, profile.email) : "·"}
      </span>
      <span className="min-w-0 flex-1">
        <span className="block truncate font-medium text-sm">
          {profile?.name ?? "Tu cuenta"}
        </span>
        <span className="block truncate text-faint text-xs">
          {profile?.email ?? "Ver y editar"}
        </span>
      </span>
      <ChevronRight className="size-4 shrink-0 text-faint" />
    </Link>
  );
}

/** Signing out stays one click from anywhere, not a stop on the account screen. */
function SignOutButton() {
  const { logout } = useAuth();

  return (
    <button
      type="button"
      onClick={logout}
      title="Cerrar sesión"
      className="grid size-9 shrink-0 place-items-center rounded-xl text-faint transition-colors hover:bg-surface-raised hover:text-text"
    >
      <LogOut className="size-4" />
      <span className="sr-only">Cerrar sesión</span>
    </button>
  );
}

function initialOf(name: string | null | undefined, email: string): string {
  const source = name?.trim() || email;
  return source.slice(0, 1).toUpperCase();
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
      <ConnectLink compact />
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
