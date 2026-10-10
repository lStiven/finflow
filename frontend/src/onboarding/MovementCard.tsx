import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { ArrowDownLeft, ArrowLeftRight, ArrowUpRight, Sparkles } from "lucide-react";
import { ingestionCatalogQuery, type Transaction } from "@/api/queries";
import { Money } from "@/components/Money";
import { cn } from "@/lib/cn";
import { formatDateTime } from "@/lib/dates";
import { transferTitle } from "@/lib/transfers";
import { bankDisplayName } from "@/onboarding/banks";

/**
 * A movement that arrived on its own, as the proof the route works.
 *
 * Only ever a real one: the newest movement the API lists as coming from a
 * bank alert. Drawn like a row of Transacciones — same icon, same sign, same
 * hues — so the first thing somebody sees arrive looks like what they will
 * see every day after, and opening it lands on the same detail screen.
 */
export function MovementCard({
  movement,
  className,
  onOpen,
}: {
  movement: Transaction;
  className?: string;
  /** Before leaving for the movement — a dialog showing it closes itself. */
  onOpen?: () => void;
}) {
  const incoming = movement.direction === "incoming";
  const transfer = movement.transfer;
  const title = transfer
    ? transferTitle(transfer, movement.counterparty)
    : (movement.merchant?.display_name ?? movement.counterparty);
  const kind = transfer ? "Traslado" : incoming ? "Ingreso" : "Gasto";
  // Not suspended: this card also lives in a dialog over any screen, and a
  // bank shown as its parser spells it is better than a card that waits.
  const banks = useQuery(ingestionCatalogQuery).data?.known_banks ?? [];

  return (
    <Link
      to="/transacciones/$transactionId"
      params={{ transactionId: movement.id }}
      onClick={onOpen}
      className={cn(
        "group block rounded-2xl border border-line bg-surface p-4 text-left transition-colors duration-150 hover:border-incoming/40 hover:bg-surface-raised",
        className,
      )}
    >
      <span className="flex items-center gap-3">
        <span
          aria-hidden
          className={cn(
            "grid size-10 shrink-0 place-items-center rounded-xl bg-gradient-to-br",
            transfer
              ? "from-violet/25 to-cyan/5 text-violet"
              : incoming
                ? "from-incoming/25 to-cyan/5 text-incoming"
                : "from-accent/25 to-violet/5 text-accent",
          )}
        >
          {transfer ? (
            <ArrowLeftRight className="size-4" />
          ) : incoming ? (
            <ArrowDownLeft className="size-4" />
          ) : (
            <ArrowUpRight className="size-4" />
          )}
        </span>
        <span className="min-w-0 flex-1">
          <span className="block truncate font-medium">{title}</span>
          <span className="mt-0.5 block truncate text-faint text-xs">
            {[
              bankDisplayName(movement.bank, banks),
              kind,
              formatDateTime(movement.occurred_at),
            ]
              .filter(Boolean)
              .join(" · ")}
          </span>
        </span>
        <Money
          amount={incoming ? movement.amount : `-${movement.amount}`}
          currency={movement.currency}
          signed
          size="sm"
          tone={transfer ? "neutral" : incoming ? "positive" : "plain"}
        />
      </span>
      <span className="mt-3 flex items-center gap-1.5 border-line/60 border-t pt-3 text-incoming text-xs">
        <Sparkles className="size-3.5" aria-hidden />
        Registrado solo, desde la alerta de tu banco
      </span>
    </Link>
  );
}

/** Where a movement will be, while its alert is still being read. */
export function MovementPlaceholder() {
  return (
    <div
      aria-hidden
      className="shimmer flex items-center gap-3 rounded-2xl border border-line bg-surface p-4"
    >
      <span className="size-10 shrink-0 rounded-xl bg-surface-raised" />
      <span className="flex min-w-0 flex-1 flex-col gap-2">
        <span className="h-2.5 w-2/5 rounded-full bg-surface-raised" />
        <span className="h-2 w-3/5 rounded-full bg-surface-raised" />
      </span>
      <span className="h-3 w-16 rounded-full bg-surface-raised" />
    </div>
  );
}
