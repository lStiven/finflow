/**
 * The rules a Telegram card cannot be trusted to get right on its own.
 *
 * Vitest runs in `node` here and there is no DOM, so the screen itself is
 * checked by looking at it (`just shot /perfil`). What lives in this file is
 * everything that has an answer worth pinning: when to stop polling, what a
 * floor typed into a text box actually means, and how a channel reads.
 */

import type { AlertChannel, AlertPreference } from "@/api/queries";

/** How often to ask while a link is outstanding. */
export const LINK_POLL_MS = 3_000;

/**
 * How long a link is worth waiting for, mirroring `ALERTS_LINK_TTL_MINUTES`.
 *
 * Duplicated rather than fetched because what it decides is a poll interval,
 * not an authorisation: the server refuses an expired token whatever this
 * says, and drift here costs a few seconds of asking, early or late. Without
 * it an abandoned link would leave a PENDING channel nothing ever sweeps, and
 * an open tab would poll every three seconds for as long as it stayed open.
 */
export const LINK_TTL_MS = 15 * 60_000;

/** What a floor may be typed as, before it becomes an amount. */
const DIGITS_AND_SEPARATORS = /^[\d.,\s]*$/;

export type ChannelState = "none" | "pending" | "linked";

/**
 * What the card should be showing.
 *
 * A pending channel outranks nothing and is outranked by a linked one: while
 * both exist the person has already finished, and showing them a stale link
 * would invite them to follow it.
 */
export function channelState(channels: readonly AlertChannel[]): ChannelState {
  if (channels.some((channel) => channel.status === "verified")) return "linked";
  if (channels.some((channel) => channel.status === "pending")) return "pending";
  return "none";
}

export function linkedChannel(
  channels: readonly AlertChannel[],
): AlertChannel | undefined {
  return channels.find((channel) => channel.status === "verified");
}

/**
 * Whether anything is still worth waiting for.
 *
 * Drives `refetchInterval`, and returning false is what stops the timer
 * rather than asking forever: binding happens inside Telegram, where the page
 * cannot see it, so asking is the only way it finds out — but only until
 * there is nothing left to find out.
 *
 * A link nobody followed also counts as nothing left: the channel stays
 * PENDING and nothing sweeps it, so without the age check an abandoned
 * attempt would poll for as long as the tab stayed open.
 */
export function hasPendingLink(
  channels: readonly AlertChannel[] | undefined,
  now: number = Date.now(),
): boolean {
  return (channels ?? []).some(
    (channel) =>
      channel.status === "pending" && now - channel.created_at * 1000 < LINK_TTL_MS,
  );
}

export function movementPreference(
  channel: AlertChannel | undefined,
): AlertPreference | undefined {
  return channel?.preferences.find(
    (preference) => preference.alert_type === "movement",
  );
}

/**
 * A floor as the person typed it, turned into what the API takes.
 *
 * Pesos are written `20.000` here and `1 234,56` by some keyboards, so the
 * dots, spaces and commas people use for grouping are dropped and a single
 * decimal comma becomes a point. Empty means no floor at all, which is a
 * real answer and not an error.
 *
 * Returns `undefined` when the text is not a number — the caller keeps the
 * field as it is rather than guessing at a figure that decides what gets
 * announced.
 */
export function parseMinimumAmount(raw: string): string | null | undefined {
  const trimmed = raw.trim();
  if (trimmed === "") return null;
  if (!DIGITS_AND_SEPARATORS.test(trimmed)) return undefined;

  // A comma is a decimal separator only when it is the last one and has one
  // or two digits behind it; otherwise it is grouping, like the dots.
  const decimal = trimmed.match(/,(\d{1,2})$/);
  const whole = (decimal ? trimmed.slice(0, -decimal[0].length) : trimmed).replace(
    /[.,\s]/g,
    "",
  );

  if (whole === "") return undefined;

  const amount = decimal ? `${whole}.${decimal[1]}` : whole;
  return /^\d+(\.\d+)?$/.test(amount) ? amount : undefined;
}

/** The stored floor, back in the shape the field shows. */
export function formatMinimumAmount(preference: AlertPreference | undefined): string {
  const amount = preference?.minimum_amount;
  if (!amount) return "";

  const dot = amount.indexOf(".");
  const whole = dot === -1 ? amount : amount.slice(0, dot);
  const cents = dot === -1 ? "" : amount.slice(dot + 1);
  const grouped = whole.replace(/\B(?=(\d{3})+(?!\d))/g, ".");
  return cents !== "" && Number(cents) > 0 ? `${grouped},${cents}` : grouped;
}
