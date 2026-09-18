/**
 * The caps, in one line, low on the dashboard.
 *
 * Deliberately *not* the breakdown. The summary screen answers four questions
 * — cuánto tengo, cuánto entró, cuánto gasté, cuánto debo — and a second
 * category-by-category table below them would bury all four. What belongs here
 * is the one sentence somebody would want without opening anything: **how many
 * of my ceilings are still fine, and is any of them past**. The rest is a tap
 * away.
 *
 * Absent until something is declared, like the allowance card and for the same
 * reason: «no has puesto ningún tope» and «todos tus topes están en cero» are
 * different things, and a card showing zeros says the wrong one.
 *
 * It never does the arithmetic. The counts come from the server already
 * tallied, so this card and the screen it links to cannot disagree about the
 * same category on the same day.
 */

import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { ChevronRight, Target } from "lucide-react";
import { type BudgetState, type BudgetTotal, budgetsQuery } from "@/api/queries";
import { hasCaps, tallyOf, worstOf } from "@/budgets/progress";
import { Money } from "@/components/Money";
import { Card } from "@/components/ui/Card";
import { cn } from "@/lib/cn";

/** The same three as the budgets screen, and they have to stay the same. */
const TONES: Record<BudgetState, { bar: string; text: string }> = {
  ok: { bar: "bg-incoming/70", text: "text-muted" },
  warning: { bar: "bg-warn", text: "text-warn" },
  over: { bar: "bg-outgoing/80", text: "text-outgoing" },
};

export function BudgetSummaryCard() {
  // Read rather than suspended, and the route prefetches it, so this is
  // normally a cache hit with no flash. What the plain query buys is the bad
  // day: a 5xx here leaves the card out instead of taking the whole dashboard
  // down with it.
  const { data: view } = useQuery(budgetsQuery());

  if (!view || !hasCaps(view.totals)) return null;

  return (
    <Card glow="violet" lift={false} className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 className="flex items-center gap-2 font-medium">
          <Target className="size-4 text-violet" aria-hidden />
          Presupuestos
        </h2>
        <Link
          to="/presupuestos"
          className="flex items-center gap-1 rounded-lg border border-line px-2.5 py-1 text-muted text-xs transition-colors duration-150 hover:border-accent/40 hover:text-text"
        >
          Ver todos
          <ChevronRight className="size-3" />
        </Link>
      </div>

      <div className="flex flex-col gap-4">
        {view.totals.map((total) => (
          <CurrencyRow
            key={total.currency}
            total={total}
            showCurrency={view.totals.length > 1}
          />
        ))}
      </div>
    </Card>
  );
}

function CurrencyRow({
  total,
  showCurrency,
}: {
  total: BudgetTotal;
  showCurrency: boolean;
}) {
  const tally = tallyOf(total);
  const tone = TONES[worstOf(total)];
  const limit = Number(total.limit);
  const used = limit > 0 ? Math.min(1, Math.max(0, Number(total.spent) / limit)) : 1;

  return (
    // Three stacked rows rather than a tally and two figures on one line: at
    // 390px that line truncated to «1 de 3 en ve…», which is a sentence that
    // says nothing and a card that looks broken.
    <div className="flex flex-col gap-2">
      <span className={cn("text-sm", tone.text)}>
        {tally.ok} de {tally.total} en verde
        {showCurrency ? ` · ${total.currency}` : null}
      </span>

      <div
        role="presentation"
        className="h-1.5 w-full overflow-hidden rounded-full bg-surface-raised"
      >
        <div
          className={cn("h-full rounded-full", tone.bar)}
          style={{ width: `${Math.round(used * 100)}%` }}
        />
      </div>

      <span className="text-faint text-xs">
        <Money amount={total.spent} currency={total.currency} size="sm" /> de{" "}
        <Money amount={total.limit} currency={total.currency} size="sm" /> presupuestado
      </span>
    </div>
  );
}
