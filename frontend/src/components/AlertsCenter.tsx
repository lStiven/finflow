/**
 * What reached Telegram, inside the app too: a floating notification the
 * moment a movement arrives, and a bell with the latest ones for somebody who
 * forgot which was the last.
 *
 * "The moment" is a poll every few seconds while the page is visible —
 * `alertsInboxQuery` — which on a deployment with no socket is as close to
 * real time as it gets. Only what arrives *after* the page opened becomes a
 * toast; what was already there is history, and toasting a screen of it on
 * every reload is how notifications become noise.
 *
 * Mounted once, in the shell. The bell is drawn twice — in the rail on a
 * desktop, floating on a phone — and both read the same cache.
 */

import { useQuery } from "@tanstack/react-query";
import { Link, useNavigate } from "@tanstack/react-router";
import { ArrowDownLeft, ArrowUpRight, Bell, CalendarDays, X } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Toaster, toast } from "sonner";
import {
  type Described,
  describeEntry,
  freshEntries,
  MAX_TOASTS,
  readSeen,
  unreadCount,
  writeSeen,
} from "@/alerts/inbox";
import { type AlertsInboxEntry, alertsInboxQuery } from "@/api/queries";
import { useAuth } from "@/auth/AuthContext";
import { cn } from "@/lib/cn";
import { formatDateTime } from "@/lib/dates";
import { useDismissOnEscape } from "@/lib/useDismissOnEscape";

/**
 * The toasts themselves, in the app's own colours.
 *
 * Bottom, not top: on a phone the bell floats in the top corner, and a toast
 * across the top covered it — the one control that lists what the toast is
 * about was untappable for as long as the toast was up. On a phone the offset
 * clears the navigation bar and its floating «+».
 */
export function AlertsToaster() {
  return (
    <Toaster
      position="bottom-right"
      theme="dark"
      visibleToasts={MAX_TOASTS}
      closeButton
      offset={24}
      mobileOffset={{ bottom: 112, left: 12, right: 12 }}
      toastOptions={{
        duration: 8_000,
        classNames: {
          toast: "!rounded-2xl !border-line !bg-surface-raised !text-text !shadow-xl",
          title: "!font-semibold",
          description: "!text-muted",
          actionButton: "!bg-accent !text-accent-ink",
          closeButton: "!border-line !bg-surface !text-muted",
        },
      }}
    />
  );
}

/** Polls, and turns what is new into toasts. Renders nothing. */
export function AlertsWatcher() {
  const { data } = useQuery(alertsInboxQuery);
  const seen = useRef<Set<string> | null>(null);
  const navigate = useNavigate();

  useEffect(() => {
    if (!data) return;

    const fresh = freshEntries(seen.current, data.entries);
    seen.current = new Set([...(seen.current ?? []), ...data.entries.map((e) => e.id)]);

    for (const entry of fresh.slice(-MAX_TOASTS)) {
      const described = describeEntry(entry);
      const movementId = described.movementId;

      toast(described.title, {
        id: entry.id,
        description: <Lines described={described} />,
        icon: <ToneIcon tone={described.tone} />,
        action: movementId
          ? {
              label: "Ver",
              onClick: () =>
                void navigate({
                  to: "/transacciones/$transactionId",
                  params: { transactionId: movementId },
                }),
            }
          : undefined,
      });
    }

    const rest = fresh.length - MAX_TOASTS;
    if (rest > 0) {
      toast(`Y ${rest} ${rest === 1 ? "aviso más" : "avisos más"}`, {
        description: "Están en la campana.",
      });
    }
  }, [data, navigate]);

  return null;
}

/** The bell, and the list it opens. `placement` only decides where it sits. */
export function AlertsBell({ placement }: { placement: "rail" | "floating" }) {
  const { session } = useAuth();
  const userId = session?.userId ?? "";
  // The watcher drives the polling; the bell only reads what it fetched.
  const { data } = useQuery({ ...alertsInboxQuery, refetchInterval: false });
  const entries = data?.entries ?? [];
  const [open, setOpen] = useState(false);
  const [seen, setSeen] = useState<Set<string> | null>(() =>
    userId ? readSeen(userId) : null,
  );
  const unread = data ? unreadCount(entries, seen) : 0;

  const toggle = useCallback(() => {
    setOpen((previous) => {
      const next = !previous;

      // Opening the list is seeing what is in it.
      if (next && userId && entries.length > 0) {
        const ids = entries.map((entry) => entry.id);
        writeSeen(userId, ids);
        setSeen(new Set([...ids, ...(seen ?? [])]));
      }

      return next;
    });
  }, [entries, seen, userId]);

  return (
    <>
      <button
        type="button"
        data-alerts-bell
        onClick={toggle}
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-label={unread > 0 ? `Avisos, ${unread} sin ver` : "Avisos"}
        className={cn(
          "relative grid place-items-center transition-colors duration-150",
          placement === "floating"
            ? "fixed top-3 right-3 z-40 size-11 rounded-full border border-line bg-surface/90 text-muted shadow-lg backdrop-blur hover:text-text lg:hidden"
            : "size-9 rounded-xl text-muted hover:bg-surface-raised hover:text-text",
        )}
      >
        <Bell className="size-[18px]" aria-hidden />
        {unread > 0 ? (
          <span className="-top-0.5 -right-0.5 absolute grid h-[18px] min-w-[18px] place-items-center rounded-full bg-accent px-1 font-semibold text-[10px] text-accent-ink">
            {unread > 9 ? "9+" : unread}
          </span>
        ) : null}
      </button>
      {open
        ? createPortal(
            <AlertsPanel
              entries={entries}
              placement={placement}
              onClose={() => setOpen(false)}
            />,
            document.body,
          )
        : null}
    </>
  );
}

function AlertsPanel({
  entries,
  placement,
  onClose,
}: {
  entries: readonly AlertsInboxEntry[];
  placement: "rail" | "floating";
  onClose: () => void;
}) {
  const panel = useRef<HTMLDivElement>(null);
  useDismissOnEscape(onClose, true);

  useEffect(() => {
    function onPointer(event: PointerEvent) {
      const target = event.target as Node;
      // The bell itself toggles; anywhere else outside the panel closes it.
      if (panel.current?.contains(target)) return;
      if ((target as Element).closest?.("[data-alerts-bell]")) return;
      onClose();
    }

    document.addEventListener("pointerdown", onPointer);
    return () => document.removeEventListener("pointerdown", onPointer);
  }, [onClose]);

  return (
    <div
      ref={panel}
      role="dialog"
      aria-label="Avisos recientes"
      className={cn(
        "rise fixed z-50 flex max-h-[min(70dvh,560px)] w-[min(380px,calc(100vw-24px))] flex-col overflow-hidden rounded-card border border-line bg-surface shadow-2xl",
        placement === "floating" ? "top-16 right-3" : "top-4 left-64",
      )}
    >
      <header className="flex items-center justify-between border-line border-b px-4 py-3">
        <h2 className="font-semibold text-sm">Avisos recientes</h2>
        <button
          type="button"
          onClick={onClose}
          aria-label="Cerrar avisos"
          className="grid size-8 place-items-center rounded-lg text-muted hover:bg-surface-raised hover:text-text"
        >
          <X className="size-4" aria-hidden />
        </button>
      </header>

      {entries.length === 0 ? (
        <p className="px-4 py-8 text-center text-muted text-sm">
          Aquí aparece cada movimiento en cuanto llega, y el resumen de tu semana los
          lunes.
        </p>
      ) : (
        <ul className="flex flex-col overflow-y-auto">
          {entries.map((entry) => (
            <li key={entry.id} className="border-line border-b last:border-b-0">
              <Row entry={entry} onNavigate={onClose} />
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function Row({
  entry,
  onNavigate,
}: {
  entry: AlertsInboxEntry;
  onNavigate: () => void;
}) {
  const described = describeEntry(entry);
  const body = (
    <div className="flex gap-3 px-4 py-3">
      <ToneIcon tone={described.tone} />
      <div className="min-w-0 flex-1">
        <div className="flex items-baseline justify-between gap-2">
          <p className="font-medium text-sm">{described.title}</p>
          <time className="shrink-0 text-faint text-xs">
            {formatDateTime(entry.created_at)}
          </time>
        </div>
        <Lines described={described} />
      </div>
    </div>
  );

  if (!described.movementId) return body;

  return (
    <Link
      to="/transacciones/$transactionId"
      params={{ transactionId: described.movementId }}
      onClick={onNavigate}
      className="block transition-colors duration-150 hover:bg-surface-raised"
    >
      {body}
    </Link>
  );
}

function Lines({ described }: { described: Described }) {
  return (
    <span className="mt-0.5 flex flex-col gap-0.5 text-muted text-xs leading-relaxed">
      {described.lines.map((line) => (
        <span key={line}>{line}</span>
      ))}
    </span>
  );
}

function ToneIcon({ tone }: { tone: Described["tone"] }) {
  const Icon =
    tone === "summary"
      ? CalendarDays
      : tone === "incoming"
        ? ArrowDownLeft
        : ArrowUpRight;

  return (
    <span
      aria-hidden
      className={cn(
        "grid size-8 shrink-0 place-items-center rounded-xl border",
        tone === "outgoing" && "border-outgoing/30 bg-outgoing/10 text-outgoing",
        tone === "incoming" && "border-incoming/30 bg-incoming/10 text-incoming",
        tone === "summary" && "border-violet/30 bg-violet/10 text-violet",
      )}
    >
      <Icon className="size-4" />
    </span>
  );
}
