/**
 * What an account is still missing before it does its job, if anything.
 *
 * The accounts screen used to fold this into three identical panels at the
 * foot of every card, so the one thing worth doing — linking a card that
 * nothing reaches, or saying what a loan costs — looked exactly like the
 * settings nobody needs. This picks it out, so the card can say it first.
 *
 * At most one answer per account, the most pressing one: a loan without its
 * rate is wrong every month, while an account without alerts is only empty.
 */

import { isFinanceable } from "@/accounts/financing";
import { canLinkAlerts, neverBelowZero } from "@/accounts/kinds";
import type { Account } from "@/api/queries";
import { signOf } from "@/lib/money";

export type Attention =
  /** A loan or an investment whose rate and cut-off were never declared. */
  | { kind: "terms"; shape: "loan" | "investment" }
  /**
   * Below zero on a kind the bank never lets go there: an income Finflow
   * never saw, or an opening balance that was not the real one. Never refused
   * at the ledger — the bank already let the movement through — only said.
   */
  | { kind: "below-zero" }
  /** A kind that emails, with no card or account number linked to it. */
  | { kind: "link-alerts" };

export function attentionOf(account: Account): Attention | null {
  // A closed account takes nothing new, so nothing about it is pending.
  if (account.closed_at !== null) return null;

  const shape = isFinanceable(account.kind);
  if (shape !== null && (account.loan ?? account.investment ?? null) === null) {
    return { kind: "terms", shape };
  }

  // Before the link: a number that is wrong today outranks an account that is
  // only empty, and the missing link is often why the income never landed.
  if (neverBelowZero(account.kind) && signOf(account.balance) < 0) {
    return { kind: "below-zero" };
  }

  if (canLinkAlerts(account.kind) && account.instruments.length === 0) {
    return { kind: "link-alerts" };
  }

  return null;
}

/** How many open accounts are watched rather than counted. */
export function watchedCount(accounts: readonly Account[]): number {
  return accounts.filter(
    (account) => account.informational && account.closed_at === null,
  ).length;
}
