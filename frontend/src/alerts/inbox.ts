/**
 * The alerts the app shows, turned into words, and which of them are new.
 *
 * The API hands over facts; the wording is decided here, in the same words
 * the Telegram message uses, so somebody who reads both reads one voice. The
 * counterparty is what the bank wrote — untrusted — and it only ever reaches
 * the screen as text, never as markup.
 *
 * Pure on purpose, like `@/lib/exporting`: loadable without a base URL, so it
 * is tested without the API client.
 */

import type { AlertsInboxEntry } from "@/api/queries";
import { formatMoney, percentChange } from "@/lib/money";
import { categoryLabel } from "@/merchants/categories";

/** How many new alerts become toasts at once; the rest are summed up. */
export const MAX_TOASTS = 3;

/** One budget line under a purchase; more than this is summed up. */
const MAX_BUDGET_LINES = 3;

const MAX_COUNTERPARTY = 64;

const MONTHS = [
  "ene",
  "feb",
  "mar",
  "abr",
  "may",
  "jun",
  "jul",
  "ago",
  "sep",
  "oct",
  "nov",
  "dic",
];

export type Described = {
  title: string;
  lines: string[];
  tone: "outgoing" | "incoming" | "summary";
  /** The movement to open, when the alert is about one. */
  movementId: string | null;
};

/** Collapses whitespace so bank text cannot forge a line, and trims it. */
export function oneLine(text: string, limit = MAX_COUNTERPARTY): string {
  const collapsed = text.split(/\s+/).filter(Boolean).join(" ");
  if (collapsed.length <= limit) return collapsed;
  return `${collapsed.slice(0, limit - 1).trimEnd()}…`;
}

function withoutSign(amount: string): string {
  return amount.trim().replace(/^[+-]/, "");
}

function budgetLine(budget: {
  name: string;
  currency: string;
  limit: string;
  remaining: string;
}): string {
  const name = oneLine(budget.name, 40) || "Presupuesto";
  const limit = formatMoney(budget.limit, budget.currency);
  const remaining = budget.remaining.trim();

  if (/^-/.test(remaining) && !/^-0*(\.0*)?$/.test(remaining)) {
    const over = formatMoney(withoutSign(remaining), budget.currency);
    return `${name}: vas ${over} por encima del tope de ${limit}`;
  }

  if (/^[+-]?0*(\.0*)?$/.test(remaining)) {
    return `${name}: llegaste al tope de ${limit}`;
  }

  return `${name}: te quedan ${formatMoney(remaining, budget.currency)} de ${limit}`;
}

function isoDay(value: string): { day: number; month: number } {
  const [, month, day] = value.split("-").map(Number);
  return { day: day ?? 0, month: month ?? 1 };
}

/** `21–27 sep`, or `28 sep – 4 oct` across a month. */
export function weekLabel(start: string, end: string): string {
  const from = isoDay(start);
  const to = isoDay(end);
  const monthOf = (month: number) => MONTHS[month - 1] ?? "";

  return from.month === to.month
    ? `${from.day}–${to.day} ${monthOf(to.month)}`
    : `${from.day} ${monthOf(from.month)} – ${to.day} ${monthOf(to.month)}`;
}

export function describeEntry(entry: AlertsInboxEntry): Described {
  if (entry.kind === "movement" && entry.movement) {
    const movement = entry.movement;
    const outgoing = movement.direction === "outgoing";
    const lines = [oneLine(movement.counterparty) || "Sin descripción"];

    if (movement.unassigned) lines.push("Sin cuenta asignada");

    for (const budget of movement.budgets.slice(0, MAX_BUDGET_LINES)) {
      lines.push(budgetLine(budget));
    }

    const extra = movement.budgets.length - MAX_BUDGET_LINES;
    if (extra > 0) {
      lines.push(`y ${extra} ${extra === 1 ? "presupuesto" : "presupuestos"} más`);
    }

    return {
      title: `${outgoing ? "Gasto" : "Ingreso"} ${formatMoney(movement.amount, movement.currency)}`,
      lines,
      tone: outgoing ? "outgoing" : "incoming",
      movementId: movement.movement_id,
    };
  }

  const summary = entry.summary;
  if (!summary) {
    return { title: "Aviso", lines: [], tone: "summary", movementId: null };
  }

  const lines: string[] = [];
  const spent = formatMoney(summary.spent, summary.currency);

  if (summary.movements === 0) {
    lines.push("No registraste gastos esta semana.");
    if (summary.typical !== null) {
      lines.push(
        `Tu semana normal es de ${formatMoney(summary.typical, summary.currency)}.`,
      );
    }
  } else {
    lines.push(
      `Gastaste ${spent} en ${summary.movements} ${summary.movements === 1 ? "gasto" : "gastos"}.`,
    );

    if (summary.typical === null) {
      lines.push(
        "Es tu primera semana: desde la próxima te la comparo con las anteriores.",
      );
    } else {
      const typical = formatMoney(summary.typical, summary.currency);
      const change = percentChange(summary.typical, summary.spent);

      if (change === null) {
        lines.push("Las semanas anteriores no habías registrado gastos.");
      } else {
        const rounded = Math.round(change);
        lines.push(
          rounded === 0
            ? `Casi igual que tu semana normal (${typical}).`
            : `${Math.abs(rounded)} % ${rounded < 0 ? "menos" : "más"} que tu semana normal (${typical}).`,
        );
      }
    }
  }

  if (summary.rise) {
    const rise = summary.rise;
    const name = categoryLabel(rise.category, oneLine(rise.label, 40));
    lines.push(
      `Lo que más subió: ${name}, ${formatMoney(rise.spent, summary.currency)} (normalmente ${formatMoney(rise.typical, summary.currency)}).`,
    );
  }

  return {
    title: `Tu semana (${weekLabel(summary.week_start, summary.week_end)})`,
    lines,
    tone: "summary",
    movementId: null,
  };
}

/**
 * The entries that arrived since the last look, oldest first.
 *
 * `seen` null is the first look after the page opened: everything already
 * there is history, not news, and toasting a page of it on every reload is
 * how a notification becomes noise.
 */
export function freshEntries(
  seen: ReadonlySet<string> | null,
  entries: readonly AlertsInboxEntry[],
): AlertsInboxEntry[] {
  if (seen === null) return [];

  return entries.filter((entry) => !seen.has(entry.id)).reverse();
}

/** How many seen ids are remembered — more than the inbox ever lists. */
export const MAX_SEEN = 100;

/**
 * How many entries have not been seen in the open list.
 *
 * By id, never by time. A week's entry is stamped with a fixed instant so a
 * retried run lands on the same row — so one written late would be *older*
 * than a purchase already seen, and a rule of «newer than the last look»
 * would count it as seen without anybody having seen it.
 *
 * `seen` null is somebody who never opened the list: everything is unseen.
 */
export function unreadCount(
  entries: readonly AlertsInboxEntry[],
  seen: ReadonlySet<string> | null,
): number {
  if (seen === null) return entries.length;
  return entries.filter((entry) => !seen.has(entry.id)).length;
}

/** Where the ids already seen are kept, per user. */
export function seenKey(userId: string): string {
  return `finflow.alerts.seen.${userId}`;
}

export function readSeen(userId: string): Set<string> | null {
  try {
    const raw = window.localStorage.getItem(seenKey(userId));
    if (raw === null) return null;
    const parsed: unknown = JSON.parse(raw);
    return Array.isArray(parsed)
      ? new Set(parsed.filter((id): id is string => typeof id === "string"))
      : null;
  } catch {
    // Private mode, blocked storage, garbage: everything reads as unseen,
    // which is the honest answer when nothing could be remembered.
    return null;
  }
}

/** Remembers these ids as seen, newest kept first, bounded. */
export function writeSeen(userId: string, ids: readonly string[]): void {
  try {
    const previous = readSeen(userId) ?? new Set<string>();
    const merged = [...new Set([...ids, ...previous])].slice(0, MAX_SEEN);
    window.localStorage.setItem(seenKey(userId), JSON.stringify(merged));
  } catch {
    // Nothing to do: the badge just comes back next time.
  }
}
