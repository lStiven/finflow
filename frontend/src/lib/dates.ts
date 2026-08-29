/**
 * Time, as the API hands it over: epoch seconds, UTC.
 *
 * Everything renders in Bogota, matching the timezone the backend uses to
 * decide which month a movement belongs to. Letting the browser pick its own
 * would put an 8pm purchase on the 31st into the following month for anyone
 * travelling — the summary and the list would then disagree.
 */

export const DISPLAY_TIMEZONE = "America/Bogota";

const LOCALE = "es-CO";

function toDate(epochSeconds: number): Date {
  return new Date(epochSeconds * 1000);
}

export function formatDate(epochSeconds: number): string {
  return new Intl.DateTimeFormat(LOCALE, {
    day: "numeric",
    month: "short",
    year: "numeric",
    timeZone: DISPLAY_TIMEZONE,
  }).format(toDate(epochSeconds));
}

export function formatDateTime(epochSeconds: number): string {
  return new Intl.DateTimeFormat(LOCALE, {
    day: "numeric",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
    timeZone: DISPLAY_TIMEZONE,
  }).format(toDate(epochSeconds));
}

/** "2026-08" — the `key` of a `group_by=month` bucket — as "agosto 2026". */
export function formatMonthKey(key: string): string {
  const [year, month] = key.split("-");
  if (!year || !month) return key;
  const date = new Date(Date.UTC(Number(year), Number(month) - 1, 1));
  return new Intl.DateTimeFormat(LOCALE, {
    month: "long",
    year: "numeric",
    timeZone: "UTC",
  }).format(date);
}

export function nowInSeconds(): number {
  return Math.floor(Date.now() / 1000);
}
