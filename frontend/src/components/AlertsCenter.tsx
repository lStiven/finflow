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
import {
  ArrowDownLeft,
  ArrowUpRight,
  Bell,
  CalendarDays,
  Trash2,
  X,
} from "lucide-react";
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
import {
  type AlertsInboxEntry,
  alertsInboxQuery,
  useDismissAlert,
  useDismissAllAlerts,
} from "@/api/queries";
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
      visibleToasts={MAX_TOASTS}
      offset={24}
      mobileOffset={{ bottom: 112, left: 12, right: 12 }}
      toastOptions={{ duration: 8_000, unstyled: true }}
    />
  );
}

/**
 * One alert as a floating card, drawn here rather than by sonner's own
 * layout: its icon slot is a 16 px glyph, and the app's 32 px tone tile in it
 * sat crooked beside a title indented to make room for something smaller.
 */
function AlertToast({
  toastId,
  described,
  onOpen,
}: {
  toastId: string | number;
  described: Described;
  onOpen?: () => void;
}) {
  return (
    <div className="flex w-[min(380px,calc(100vw-24px))] items-start gap-3 rounded-2xl border border-line bg-surface-raised/95 p-3.5 text-text shadow-2xl backdrop-blur">
      <ToneIcon tone={described.tone} />
      <div className="min-w-0 flex-1">
        <p className="font-semibold text-sm leading-tight">{described.title}</p>
        <Lines described={described} />
      </div>
      <div className="flex shrink-0 flex-col items-end gap-2">
        <button
          type="button"
          aria-label="Cerrar aviso"
          onClick={() => toast.dismiss(toastId)}
          className="-mt-1 -mr-1 grid size-7 place-items-center rounded-lg text-muted transition-colors hover:bg-surface hover:text-text"
        >
          <X className="size-4" aria-hidden />
        </button>
        {onOpen ? (
          <button
            type="button"
            onClick={() => {
              toast.dismiss(toastId);
              onOpen();
            }}
            className="rounded-lg bg-accent px-3 py-1.5 font-semibold text-accent-ink text-xs transition-all hover:brightness-110"
          >
            Ver
          </button>
        ) : null}
      </div>
    </div>
  );
}

/** Polls, and turns what is new into toasts. Renders nothing. */
export function AlertsWatcher() {
  const { data } = useQuery(alertsInboxQuery);
  const seen = useRef<Set<string> | null>(null);
  const previous = useRef<readonly AlertsInboxEntry[] | null>(null);
  const navigate = useNavigate();

  useEffect(() => {
    if (!data) return;

    const fresh = freshEntries(seen.current, data.entries, previous.current);
    previous.current = data.entries;
    seen.current = new Set([...(seen.current ?? []), ...data.entries.map((e) => e.id)]);

    for (const entry of fresh.slice(-MAX_TOASTS)) {
      const described = describeEntry(entry);
      const movementId = described.movementId;
      const onOpen = movementId
        ? () =>
            void navigate({
              to: "/transacciones/$transactionId",
              params: { transactionId: movementId },
            })
        : undefined;

      toast.custom(
        (toastId) => (
          <AlertToast toastId={toastId} described={described} onOpen={onOpen} />
        ),
        { id: entry.id },
      );
    }

    const rest = fresh.length - MAX_TOASTS;
    if (rest > 0) {
      toast.custom((toastId) => (
        <AlertToast
          toastId={toastId}
          described={{
            title: `Y ${rest} ${rest === 1 ? "aviso más" : "avisos más"}`,
            lines: ["Están en la campana."],
            tone: "summary",
            movementId: null,
          }}
        />
      ));
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
  const dismiss = useDismissAlert();
  const dismissAll = useDismissAllAlerts();
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
      <header className="flex items-center gap-2 border-line border-b px-4 py-3">
        <h2 className="flex-1 font-semibold text-sm">Avisos recientes</h2>
        {entries.length > 0 ? (
          <button
            type="button"
            onClick={() => dismissAll.mutate()}
            disabled={dismissAll.isPending}
            className="rounded-lg px-2 py-1.5 text-muted text-xs transition-colors hover:bg-surface-raised hover:text-text disabled:opacity-50"
          >
            Borrar todo
          </button>
        ) : null}
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
              <Row
                entry={entry}
                onNavigate={onClose}
                onDismiss={() => dismiss.mutate(entry.id)}
              />
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
  onDismiss,
}: {
  entry: AlertsInboxEntry;
  onNavigate: () => void;
  onDismiss: () => void;
}) {
  const described = describeEntry(entry);
  const body = (
    <div className="flex gap-3 py-3 pr-12 pl-4">
      <ToneIcon tone={described.tone} />
      <div className="min-w-0 flex-1">
        <p className="font-medium text-sm">{described.title}</p>
        <Lines described={described} />
        <time className="mt-1 block text-faint text-[11px]">
          {formatDateTime(entry.created_at)}
        </time>
      </div>
    </div>
  );

  return (
    // The dismiss button sits beside the link, never inside it: a button in
    // an anchor is one control nested in another.
    <div className="relative">
      {described.movementId ? (
        <Link
          to="/transacciones/$transactionId"
          params={{ transactionId: described.movementId }}
          onClick={onNavigate}
          className="block transition-colors duration-150 hover:bg-surface-raised"
        >
          {body}
        </Link>
      ) : (
        body
      )}
      <button
        type="button"
        onClick={onDismiss}
        aria-label={`Borrar aviso: ${described.title}`}
        className="absolute top-2.5 right-2.5 grid size-8 place-items-center rounded-lg text-faint transition-colors hover:bg-surface-raised hover:text-outgoing"
      >
        <Trash2 className="size-4" aria-hidden />
      </button>
    </div>
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
