/**
 * Taking the movements out of the app, as a CSV or an Excel file.
 *
 * The server writes the file from the same filters `/financial/transactions`
 * takes, so what the export dialog chooses is exactly what a list with those
 * filters would page through — every page of it. The dialog starts from what
 * the Transacciones screen has selected and lets somebody change the period,
 * the kind of movement, the account, the category and the format.
 *
 * Only the pure half lives here, loadable without a base URL the way
 * `@/api/errors` is; the request itself is `downloadExport` in `@/api/export`.
 */

import { ApiError } from "@/api/errors";
import type {
  TransactionFilters,
  TransactionOrigin,
  TransferView,
} from "@/api/queries";
import {
  currentMonthKey,
  DISPLAY_TIMEZONE,
  fromLocalInput,
  monthDayRange,
  monthKeyMinus,
  previousMonthKey,
} from "@/lib/dates";

export type ExportFormat = "csv" | "xlsx";

export const EXPORT_FORMATS: ReadonlyArray<{ value: ExportFormat; label: string }> = [
  { value: "xlsx", label: "Excel (.xlsx)" },
  { value: "csv", label: "CSV" },
];

/** Mirrors `MAX_EXPORT_ROWS` on the server, so the dialog can say it first. */
export const MAX_EXPORT_ROWS = 10_000;

export type ExportPeriod =
  | "this_month"
  | "last_month"
  | "last_3_months"
  | "this_year"
  | "all"
  | "custom";

export const EXPORT_PERIODS: ReadonlyArray<{ value: ExportPeriod; label: string }> = [
  { value: "this_month", label: "Este mes" },
  { value: "last_month", label: "Mes pasado" },
  { value: "last_3_months", label: "Últimos 3 meses" },
  { value: "this_year", label: "Este año" },
  { value: "all", label: "Todo" },
  { value: "custom", label: "Elegir fechas" },
];

export type ExportDirection = "all" | "outgoing" | "incoming";

export const EXPORT_DIRECTIONS: ReadonlyArray<{
  value: ExportDirection;
  label: string;
}> = [
  { value: "all", label: "Todos" },
  { value: "outgoing", label: "Gastos" },
  { value: "incoming", label: "Ingresos" },
];

/**
 * Everything the export dialog decides, as plain values a form can hold.
 *
 * Days are `YYYY-MM-DD` in Bogotá and both ends inclusive, like the list's
 * date inputs. Empty strings mean "any" for the account and the category,
 * because that is what a `<select>` with a "Todas" entry holds.
 *
 * `search`, `merchantId`, `origin` and `unassigned` have no control of their
 * own in the dialog: they are carried over from the list when it had them,
 * shown, and can only be dropped — so nothing is exported under a filter the
 * person cannot see.
 */
export type ExportOptions = {
  period: ExportPeriod;
  from: string;
  to: string;
  direction: ExportDirection;
  accountId: string;
  category: string;
  transfers: Extract<TransferView, "include" | "exclude">;
  format: ExportFormat;
  search?: string;
  merchantId?: string;
  origin?: TransactionOrigin;
  unassigned?: boolean;
};

/** What the Transacciones screen currently has selected, as its URL holds it. */
export type ListSelection = {
  from?: string;
  to?: string;
  direction?: "incoming" | "outgoing";
  account?: string;
  category?: string;
  transfers?: TransferView;
  search?: string;
  merchant?: string;
  origin?: TransactionOrigin;
  unassigned?: boolean;
};

/**
 * The dialog's starting point: the list as it is, so exporting without
 * touching anything gives the movements on screen.
 *
 * A list without dates starts the dialog on "Este mes" rather than on the
 * whole history — the file somebody usually wants — and the choice is the
 * first thing the dialog shows. `transfers=only` has no place in the dialog
 * (a file of only card payments is not a thing anybody asks for), so it
 * starts as "include".
 */
export function initialExportOptions(list: ListSelection): ExportOptions {
  const dated = Boolean(list.from || list.to);

  return {
    period: dated ? "custom" : "this_month",
    from: list.from ?? "",
    to: list.to ?? "",
    direction: list.direction ?? "all",
    accountId: list.account ?? "",
    category: list.category ?? "",
    transfers: list.transfers === "exclude" ? "exclude" : "include",
    format: "xlsx",
    search: list.search,
    merchantId: list.merchant,
    origin: list.origin,
    unassigned: list.unassigned,
  };
}

/**
 * The inclusive days a preset covers, counted from `today` in Bogotá.
 *
 * "Este mes" runs to the end of the month rather than to today, so a
 * movement dated later today — or a confirmed bill dated tomorrow — is never
 * the one left out. `all` has no bounds; `custom` answers with whatever the
 * form holds.
 */
export function periodDays(
  period: ExportPeriod,
  today: Date = new Date(),
  custom: { from: string; to: string } = { from: "", to: "" },
): { from?: string; to?: string } {
  const month = currentMonthKey(today);
  const year = month.slice(0, 4);

  switch (period) {
    case "this_month":
      return monthDayRange(month) ?? {};
    case "last_month":
      return monthDayRange(previousMonthKey(month)) ?? {};
    case "last_3_months": {
      const first = monthDayRange(monthKeyMinus(month, 2));
      const last = monthDayRange(month);
      return first && last ? { from: first.from, to: last.to } : {};
    }
    case "this_year":
      return { from: `${year}-01-01`, to: `${year}-12-31` };
    case "all":
      return {};
    case "custom":
      return {
        from: custom.from || undefined,
        to: custom.to || undefined,
      };
  }
}

/** Why these options cannot be exported yet, or null when they can. */
export function exportOptionsError(options: ExportOptions): string | null {
  if (options.period !== "custom") return null;

  if (!options.from && !options.to) return "Elige al menos una de las dos fechas.";

  if (options.from && options.to && options.from > options.to) {
    return "La fecha inicial va después de la final.";
  }

  return null;
}

/**
 * The options as the API's filters: days turned into the half-open instants
 * the backend reads, `from` at midnight and `to` at the next midnight.
 */
export function exportFilters(
  options: ExportOptions,
  today: Date = new Date(),
): TransactionFilters {
  const days = periodDays(options.period, today, options);
  const from = days.from ? fromLocalInput(`${days.from}T00:00`) : null;
  const to = days.to ? fromLocalInput(`${days.to}T00:00`) : null;

  return {
    from: from ?? undefined,
    to: to === null ? undefined : to + 86_400,
    direction: options.direction === "all" ? undefined : options.direction,
    account_id: options.accountId || undefined,
    category: options.category || undefined,
    transfers: options.transfers,
    search: options.search || undefined,
    merchant_id: options.merchantId || undefined,
    origin: options.origin,
    unassigned: options.unassigned || undefined,
  };
}

/**
 * The list's filters without what only a page needs.
 *
 * `limit` and `offset` would be meaningless — the file is every page — and
 * `sort` is not a parameter of the export, which is always newest first.
 */
export function exportQuery(
  filters: TransactionFilters,
): Omit<TransactionFilters, "limit" | "offset" | "sort"> {
  const { limit: _limit, offset: _offset, sort: _sort, ...rest } = filters;
  return rest;
}

/** `finflow-movimientos-2026-09-29.xlsx`, dated in the app's own timezone. */
export function exportFileName(format: ExportFormat, now: Date = new Date()): string {
  // `en-CA` is the locale whose short date is already ISO: 2026-09-29.
  const day = new Intl.DateTimeFormat("en-CA", {
    timeZone: DISPLAY_TIMEZONE,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(now);

  return `finflow-movimientos-${day}.${format}`;
}

/**
 * The ceiling's own refusal, told apart from every other 422.
 *
 * An unknown category — one deleted in another tab while this list was
 * cached — or a malformed filter is a 422 too, and telling somebody to pick
 * fewer dates for those would send them after the wrong fix.
 */
function tooLarge(error: unknown): { matched: number; limit: number } | null {
  if (!(error instanceof ApiError) || error.status !== 422) return null;

  const body = error.detail as { detail?: unknown } | null | undefined;
  const detail = body?.detail as
    | { code?: unknown; matched?: unknown; limit?: unknown }
    | null
    | undefined;

  if (
    typeof detail !== "object" ||
    detail === null ||
    detail.code !== "export_too_large" ||
    typeof detail.matched !== "number" ||
    typeof detail.limit !== "number"
  ) {
    return null;
  }

  return { matched: detail.matched, limit: detail.limit };
}

/** What to tell somebody when the file could not be made. */
export function exportErrorMessage(error: unknown): string {
  const ceiling = tooLarge(error);

  if (ceiling) {
    return `Son ${ceiling.matched.toLocaleString("es-CO")} movimientos y un archivo lleva hasta ${ceiling.limit.toLocaleString("es-CO")}. Acota las fechas para exportarlos.`;
  }

  if (error instanceof ApiError && error.status === 422) {
    return "Los filtros ya no son válidos. Recarga la página e inténtalo de nuevo.";
  }

  if (error instanceof ApiError && error.status !== 0 && error.status < 500) {
    return "No pudimos generar el archivo. Inténtalo de nuevo.";
  }

  if (error instanceof ApiError) return error.message;

  return "No pudimos generar el archivo. Inténtalo de nuevo.";
}
