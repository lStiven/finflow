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

/**
 * The UTC offset the display zone is on at that instant, in seconds.
 *
 * Read from the runtime's own tz database rather than hardcoded. Bogota has
 * never observed DST, so today this is always -18000 — but the constant above
 * is a constant, not a promise, and a zone that does shift would silently put
 * the first and last hour of every month in the wrong bucket.
 */
function zoneOffsetSeconds(at: Date): number {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: DISPLAY_TIMEZONE,
    timeZoneName: "longOffset",
  }).formatToParts(at);
  const name = parts.find((part) => part.type === "timeZoneName")?.value ?? "";
  const match = /GMT([+-])(\d{2}):(\d{2})/.exec(name);
  if (!match) return 0;
  const [, sign, hours, minutes] = match;
  return (sign === "-" ? -1 : 1) * (Number(hours) * 3600 + Number(minutes) * 60);
}

/** "2026-08" for the month it currently is in Bogota — the `key` the summary groups by. */
export function currentMonthKey(at: Date = new Date()): string {
  // en-CA renders ISO-shaped dates, so the parts need no reordering.
  const [year, month] = new Intl.DateTimeFormat("en-CA", {
    timeZone: DISPLAY_TIMEZONE,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  })
    .format(at)
    .split("-");
  return `${year}-${month}`;
}

/** The bucket before this one, rolling the year over at January. */
export function previousMonthKey(key: string): string {
  const [year, month] = key.split("-").map(Number);
  if (!year || !month) return key;
  const earlier =
    month === 1 ? { year: year - 1, month: 12 } : { year, month: month - 1 };
  return `${earlier.year}-${String(earlier.month).padStart(2, "0")}`;
}

/**
 * The half-open epoch-second range a month covers, as the backend reckons it.
 *
 * `to` is the first instant of the next month rather than the last of this
 * one, because the endpoint's upper bound is exclusive. Naming the last
 * second instead would drop anything that landed inside it.
 */
export function monthRange(key: string): { from: number; to: number } | null {
  const [year, month] = key.split("-").map(Number);
  if (!year || !month || month < 1 || month > 12) return null;

  const startUtc = Date.UTC(year, month - 1, 1) / 1000;
  const nextUtc = Date.UTC(month === 12 ? year + 1 : year, month % 12, 1) / 1000;
  // Those are midnights in UTC; shift them to midnight in the display zone.
  const offset = zoneOffsetSeconds(new Date(startUtc * 1000));
  return { from: startUtc - offset, to: nextUtc - offset };
}

/**
 * An epoch second as the value a `datetime-local` input expects.
 *
 * The input has no timezone of its own — it shows whatever wall-clock string
 * it is given — so the conversion has to be explicit in both directions or a
 * movement drifts by the offset every time somebody opens the form and saves
 * without touching the field.
 */
export function toLocalInput(epochSeconds: number): string {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: DISPLAY_TIMEZONE,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  }).formatToParts(toDate(epochSeconds));

  const value = (type: Intl.DateTimeFormatPartTypes) =>
    parts.find((part) => part.type === type)?.value ?? "00";

  return `${value("year")}-${value("month")}-${value("day")}T${value("hour")}:${value("minute")}`;
}

/** The inverse: a wall-clock string in the display zone, back to epoch seconds. */
export function fromLocalInput(value: string): number | null {
  const match = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/.exec(value.trim());
  if (!match) return null;
  const [, year, month, day, hour, minute] = match;

  // Read as if the wall clock were UTC, then slide by the zone's offset at
  // that moment. Two steps because the offset itself depends on the instant.
  const asUtc =
    Date.UTC(
      Number(year),
      Number(month) - 1,
      Number(day),
      Number(hour),
      Number(minute),
    ) / 1000;
  return asUtc - zoneOffsetSeconds(new Date(asUtc * 1000));
}

/**
 * The `YYYY-MM-DD` day an instant falls on, in the display zone.
 *
 * What the movement list's date inputs take, so a report can hand its own
 * range straight to a drill-down link and land on the same days.
 */
export function dayStringOf(epochSeconds: number): string {
  return new Intl.DateTimeFormat("en-CA", {
    timeZone: DISPLAY_TIMEZONE,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(toDate(epochSeconds));
}

/**
 * `count` months before this key, rolling the year over as it goes.
 *
 * Stepping by calendar arithmetic rather than by subtracting days: a month is
 * not a fixed number of them, and walking back 30 days at a time drifts off
 * the 1st within a year.
 */
export function monthKeyMinus(key: string, count: number): string {
  let cursor = key;
  for (let step = 0; step < count; step += 1) cursor = previousMonthKey(cursor);
  return cursor;
}

/** The `"2026-08"` bucket an instant falls in, for grouping a list by month. */
export function monthKeyOf(epochSeconds: number): string {
  return currentMonthKey(toDate(epochSeconds));
}

/**
 * A month as the two `YYYY-MM-DD` bounds the movement list filters on.
 *
 * Both ends inclusive, because that is what the date inputs mean: the list
 * turns the upper one into the following midnight itself.
 */
export function monthDayRange(key: string): { from: string; to: string } | null {
  const [year, month] = key.split("-").map(Number);
  if (!year || !month || month < 1 || month > 12) return null;
  // Day zero of the next month is the last day of this one.
  const last = new Date(Date.UTC(year, month, 0)).getUTCDate();
  const padded = String(month).padStart(2, "0");
  return {
    from: `${year}-${padded}-01`,
    to: `${year}-${padded}-${String(last).padStart(2, "0")}`,
  };
}
