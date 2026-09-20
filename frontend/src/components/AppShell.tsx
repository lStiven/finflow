/**
 * The frame every signed-in screen sits in.
 *
 * One route tree, two presentations of the same navigation: a rail on the
 * left from `lg` up, a bar along the bottom below it. They are not two
 * layouts sharing a name — the bar is thumb-reachable and fits three
 * destinations beside the action, the rail fits every one of them with room
 * for a label.
 *
 * What the bar cannot fit is not dropped, it is one tap away in `MoreSheet`.
 * The difference matters: dropping it is what this file used to do, and
 * Reportes and Comercios were then reachable on a phone only by typing the
 * address. `navigation/destinations.ts` holds the split, and a test holds the
 * rule that nothing may fall out of both lists.
 */

import { useQuery } from "@tanstack/react-query";
import { Link, useRouterState } from "@tanstack/react-router";
import { BookOpen, ChevronRight, LogOut, Menu, Plug, Plus, X } from "lucide-react";
import type { ComponentType, ReactNode } from "react";
import { useEffect, useState } from "react";
import { profileQuery } from "@/api/queries";
import { useAuth } from "@/auth/AuthContext";
import { BetaMark } from "@/components/BetaMark";
import { Logo } from "@/components/Logo";
import { NeonBackdrop } from "@/components/NeonBackdrop";
import { OnboardingNudge } from "@/components/OnboardingNudge";
import { ReadyDialog } from "@/components/ReadyDialog";
import { WelcomeDialog } from "@/components/WelcomeDialog";
import { cn } from "@/lib/cn";
import { useScrollLock } from "@/lib/useScrollLock";
import type { Destination } from "@/navigation/destinations";
import { BAR, DESTINATIONS, inSheet, OVERFLOW } from "@/navigation/destinations";
import { useOnboarding } from "@/onboarding/useOnboarding";

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
function ConnectLink({ onNavigate }: { onNavigate?: () => void }) {
  const { state } = useOnboarding();
  const pending = state !== null && !state.complete;
  const label = pending ? "Conectar" : "Guías";
  const Icon = pending ? Plug : BookOpen;
  const to = pending ? "/conectar" : "/guias";

  return (
    <Link
      to={to}
      onClick={onNavigate}
      className="relative flex items-center gap-3 rounded-xl border border-transparent px-3 py-2.5 text-sm transition-all duration-200 hover:bg-surface-raised hover:text-text"
      inactiveProps={{ className: "text-muted" }}
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
              className="flex items-center gap-3 rounded-xl border border-transparent px-3 py-2.5 text-sm transition-all duration-200 hover:bg-surface-raised hover:text-text"
              inactiveProps={{ className: "text-muted" }}
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
      className="flex min-w-0 flex-1 items-center gap-3 rounded-xl border border-transparent px-3 py-2.5 text-left transition-all duration-200 hover:bg-surface-raised"
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
  const [open, setOpen] = useState(false);

  return (
    <>
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
        <MoreButton open={open} onOpen={() => setOpen(true)} />
      </nav>

      {open ? <MoreSheet onClose={() => setOpen(false)} /> : null}
    </>
  );
}

/**
 * The one shape every bar entry takes, link or button.
 *
 * The label steps down a size below 360px because that is where
 * "Transacciones" stops fitting in its fifth of the bar — measured, not
 * guessed. Above it, nothing changes.
 */
const BAR_ITEM =
  "flex min-w-0 flex-1 flex-col items-center gap-1 py-2 text-[0.5625rem] transition-colors min-[360px]:px-0.5 min-[360px]:text-[0.625rem]";

/** Lit and unlit, as a pair — see `BarItem` for why they are not one class. */
const BAR_ON = "font-medium text-accent";
const BAR_OFF = "text-muted";

/**
 * The tint that says "this one".
 *
 * The rail marks the entry you are on by filling its whole row; the bar has
 * no row to fill, so the same border and gradient go behind the icon as a
 * pill. Same vocabulary, the shape the bar has room for. The transparent
 * border is carried when unlit as well, so nothing shifts by a pixel on the
 * way in or out.
 */
function BarPill({ active, children }: { active: boolean; children: ReactNode }) {
  return (
    <span
      className={cn(
        "grid place-items-center rounded-lg border border-transparent px-3 py-0.5 transition-colors",
        active && "border-accent/25 bg-gradient-to-b from-accent/20 to-violet/12",
      )}
    >
      {children}
    </span>
  );
}

/**
 * One bar entry, lit when it is the screen you are on.
 *
 * The lit and unlit colours are `activeProps`/`inactiveProps` rather than a
 * base class with an override, because the router **concatenates**
 * `activeProps.className` onto `className` instead of merging it: written the
 * other way, `text-muted` and `text-accent` both land on the element and the
 * winner is whichever Tailwind happened to emit last. That is not a style
 * question, it is why the bar marked nothing at all until now — the rail got
 * away with the same mistake only because its border and fill said it too.
 */
function BarItem({ item }: { item: Destination }) {
  const { label, icon: Icon, to } = item;
  if (!to) return <Pending label={label} icon={Icon} compact />;

  return (
    <Link
      to={to}
      activeOptions={{ exact: to === "/" }}
      className={BAR_ITEM}
      activeProps={{ className: BAR_ON, "aria-current": "page" }}
      inactiveProps={{ className: BAR_OFF }}
    >
      {({ isActive }) => (
        <>
          <BarPill active={isActive}>
            <Icon className="size-5 shrink-0" />
          </BarPill>
          <span className="max-w-full truncate">{label}</span>
        </>
      )}
    </Link>
  );
}

/**
 * The way into everything the bar cannot fit.
 *
 * It carries the unfinished-setup dot on behalf of the entry inside it: the
 * mark exists so nobody loses the thread of connecting their bank, and a mark
 * hidden behind a sheet marks nothing.
 */
function MoreButton({ open, onOpen }: { open: boolean; onOpen: () => void }) {
  const { state } = useOnboarding();
  const pending = state !== null && !state.complete;
  // Lit on behalf of whatever is behind it. Four of the app's seven sections
  // are in there, so a bar that only marks its own three tells somebody on
  // Reportes that they are nowhere. `aria-current="true"`, not `"page"`: this
  // is the current *item*, and the page is one tap further in.
  const here = useRouterState({ select: (s) => inSheet(s.location.pathname) });

  return (
    <button
      type="button"
      onClick={onOpen}
      aria-expanded={open}
      aria-haspopup="dialog"
      aria-current={here ? "true" : undefined}
      className={cn(BAR_ITEM, "relative", here ? BAR_ON : BAR_OFF)}
    >
      <BarPill active={here}>
        <Menu className="size-5 shrink-0" />
      </BarPill>
      <span className="max-w-full truncate">Más</span>
      {pending ? <Dot className="top-1.5 right-1/2 mr-2" /> : null}
    </button>
  );
}

/**
 * Everything the rail shows and the bar has no room for: the rest of the
 * destinations, who is signed in, and the way out.
 *
 * A sheet rather than a second row of icons — a row that grows with each
 * release is how a phone ends up with eight five-pixel labels — and it closes
 * on the backdrop, on Escape and on going anywhere, because a navigation menu
 * that stays open over the screen it just opened is a bug people report as
 * "the app froze".
 */
function MoreSheet({ onClose }: { onClose: () => void }) {
  const { data: profile } = useQuery(profileQuery);
  const { logout } = useAuth();

  useScrollLock();

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div
      role="dialog"
      aria-modal
      aria-label="Más secciones"
      className="fixed inset-0 z-30 flex flex-col justify-end bg-ink/70 backdrop-blur-sm lg:hidden"
    >
      {/* The backdrop closes it. A button rather than a click handler on the
          overlay so it is reachable without a pointer. */}
      <button
        type="button"
        aria-label="Cerrar"
        onClick={onClose}
        className="flex-1 cursor-default"
      />

      {/* `max-h`/`overflow-y` for the phone held sideways, where the whole
          sheet is taller than the screen it opens on. `overscroll-contain`
          so reaching the end of it stops there instead of handing the swipe
          on to the page behind. */}
      <div className="rise relative flex max-h-[85dvh] flex-col gap-1 overflow-y-auto overscroll-contain rounded-t-card border-line border-t bg-surface px-3 pt-3 pb-[calc(env(safe-area-inset-bottom)+0.75rem)]">
        {/* The sheet covers the bar it opened from, so the way back has to be
            on the sheet itself — the backdrop and Escape are not affordances
            anybody can see. */}
        <button
          type="button"
          onClick={onClose}
          className="absolute top-2.5 right-2.5 grid size-9 place-items-center rounded-xl text-faint transition-colors hover:bg-surface-raised hover:text-text"
        >
          <X className="size-4" />
          <span className="sr-only">Cerrar</span>
        </button>

        <span
          aria-hidden
          className="mx-auto mb-2 h-1 w-10 shrink-0 rounded-full bg-line"
        />

        <Link
          to="/perfil"
          onClick={onClose}
          className="flex min-w-0 items-center gap-3 rounded-xl px-3 py-2.5 text-left transition-colors hover:bg-surface-raised"
        >
          <span
            aria-hidden
            className="grid size-9 shrink-0 place-items-center rounded-lg bg-accent/15 font-semibold text-accent text-sm ring-1 ring-accent/25"
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

        <span aria-hidden className="my-1 h-px bg-line" />

        {OVERFLOW.map(({ label, icon: Icon, to }) =>
          to ? (
            <Link
              key={label}
              to={to}
              onClick={onClose}
              className="flex items-center gap-3 rounded-xl px-3 py-3 text-sm transition-colors hover:bg-surface-raised hover:text-text"
              inactiveProps={{ className: "text-muted" }}
              activeProps={{
                className: "bg-surface-raised font-medium text-text",
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
        <ConnectLink onNavigate={onClose} />

        <span aria-hidden className="my-1 h-px bg-line" />

        <button
          type="button"
          onClick={logout}
          className="flex items-center gap-3 rounded-xl px-3 py-3 text-left text-muted text-sm transition-colors hover:bg-surface-raised hover:text-text"
        >
          <LogOut className="size-4 shrink-0" />
          Cerrar sesión
        </button>
      </div>
    </div>
  );
}
