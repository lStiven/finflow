/**
 * One category, in depth: what it cost this month, against the months before,
 * where it went, and the movements behind it.
 *
 * Transacciones can already filter by category, and that answers «which
 * movements». This answers «how am I doing on it»: a total that means
 * something next to last month and to a real average, a run of six months to
 * see the shape, the merchants it went to, and the cap if there is one. Every
 * figure links to the list behind it, so nothing here is a dead end.
 *
 * No endpoint of its own: four reads the API already answers, each filtered to
 * the category, the month and the currency, with transfers left out — moving
 * money between one's own accounts is not spending on anything.
 */

import { keepPreviousData, useQuery, useSuspenseQuery } from "@tanstack/react-query";
import { createFileRoute, Link, redirect, useNavigate } from "@tanstack/react-router";
import {
  ArrowLeft,
  ArrowRight,
  CalendarRange,
  ChevronLeft,
  ChevronRight,
  Hash,
  Loader2,
  Sigma,
  Store,
  Tag,
  Target,
} from "lucide-react";
import {
  type Account,
  accountsQuery,
  budgetsQuery,
  type Currency,
  categoriesQuery,
  summaryQuery,
  transactionsQuery,
  trendQuery,
} from "@/api/queries";
import { AppShell } from "@/components/AppShell";
import { Columns } from "@/components/charts/Columns";
import { RankedBars, type RankedRow } from "@/components/charts/RankedBars";
import { Money } from "@/components/Money";
import { PageHeader } from "@/components/PageHeader";
import { Card } from "@/components/ui/Card";
import { Select } from "@/components/ui/Select";
import { cn } from "@/lib/cn";
import {
  currentMonthKey,
  dayStringOf,
  formatDayMonth,
  formatMonthKey,
  monthDayRange,
  monthKeyMinus,
  monthRange,
} from "@/lib/dates";
import { toChartValue } from "@/lib/money";
import { categoryLabel, categoryLabels, labelFrom } from "@/merchants/categories";
import { categoryStats, describeChange, monthlyOutgoing } from "@/reports/category";
import { bucketsOf } from "@/reports/shape";

/** How many months the run shows, the chosen one included. */
const MONTHS = 6;
/** The merchants it ranks before folding the rest into «Otros». */
const TOP_MERCHANTS = 6;
/** The movements listed under the figures; the rest are one link away. */
const LISTED = 30;

type CategorySearch = { c?: string; mes?: string };

function text(value: unknown): string | undefined {
  return typeof value === "string" && value.trim() !== "" ? value : undefined;
}

function monthKey(value: unknown): string | undefined {
  return typeof value === "string" && /^\d{4}-(0[1-9]|1[0-2])$/.test(value)
    ? value
    : undefined;
}

export const Route = createFileRoute("/reportes/categoria")({
  beforeLoad: ({ context }) => {
    if (!context.session) throw redirect({ to: "/login" });
  },
  validateSearch: (raw: Record<string, unknown>): CategorySearch => ({
    c: text(raw.c),
    mes: monthKey(raw.mes),
  }),
  loader: ({ context }) =>
    Promise.all([
      context.queryClient.query({ ...accountsQuery("all"), staleTime: "static" }),
      context.queryClient.query(categoriesQuery),
    ]),
  component: CategoryScreen,
});

/** The first currency this person holds, which every figure is read in. */
function currencyOf(accounts: { net_worth: { currency: string }[] }): Currency {
  return (accounts.net_worth[0]?.currency ?? "COP") as Currency;
}

function CategoryScreen() {
  const search = Route.useSearch();
  const navigate = useNavigate({ from: Route.fullPath });
  const { data: accounts } = useSuspenseQuery(accountsQuery("all"));
  const { data: categories } = useSuspenseQuery(categoriesQuery);
  const labels = categoryLabels(categories.categories);
  const currency = currencyOf(accounts);
  const thisMonth = currentMonthKey();
  const month = search.mes && search.mes <= thisMonth ? search.mes : thisMonth;
  const range = monthRange(month) ?? { from: 0, to: 86_400 };

  // Untold which one, the month's biggest — something to look at, not a form.
  // Never suspends: changing the month must not blank the screen (see below).
  const { data: ranking } = useQuery({
    ...summaryQuery("category", {
      from: range.from,
      to: range.to,
      currency,
      direction: "outgoing",
      transfers: "exclude",
      order: "amount",
      top: 1,
    }),
    placeholderData: keepPreviousData,
    enabled: search.c === undefined,
  });
  // Undefined only while the ranking that picks one is still on its way.
  const category =
    search.c ??
    (ranking
      ? (ranking.groups[0]?.key ?? categories.categories[0]?.value ?? "")
      : undefined);

  const go = (next: CategorySearch) =>
    navigate({ search: (old: CategorySearch) => ({ ...old, ...next }), replace: true });

  return (
    <AppShell>
      <div className="flex flex-col gap-6">
        <Link
          to="/reportes"
          className="-my-2 flex min-h-11 items-center gap-1.5 self-start text-muted text-xs transition-colors hover:text-text"
        >
          <ArrowLeft className="size-3.5" aria-hidden />
          Reportes
        </Link>

        <PageHeader
          title="Por categoría"
          lead="Cuánto se va en una categoría, frente a los meses anteriores y en qué comercios."
        />

        <div className="flex flex-wrap items-center justify-between gap-3">
          <Select
            variant="pill"
            icon={Tag}
            label="Categoría"
            value={category ?? ""}
            onChange={(event) => go({ c: event.target.value })}
            options={categories.categories.map((option) => ({
              value: option.value,
              label: categoryLabel(option.value, option.label),
            }))}
          />
          <MonthPicker
            month={month}
            latest={thisMonth}
            onChange={(next) => go({ mes: next === thisMonth ? undefined : next })}
          />
        </div>

        {category === undefined ? (
          <Card lift={false} aria-busy>
            <p className="flex items-center justify-center gap-2 py-10 text-muted text-sm">
              <Loader2 className="size-4 animate-spin" aria-hidden />
              Buscando la categoría con más gasto…
            </p>
          </Card>
        ) : category === "" ? (
          <Card lift={false}>
            <p className="py-6 text-center text-muted text-sm">
              Todavía no hay categorías con gasto.
            </p>
          </Card>
        ) : (
          <CategoryDetail
            category={category}
            label={labelFrom(labels, category)}
            month={month}
            currency={currency}
            accounts={accounts.accounts}
          />
        )}
      </div>
    </AppShell>
  );
}

/** «‹ octubre 2026 ›», never past the current month. */
function MonthPicker({
  month,
  latest,
  onChange,
}: {
  month: string;
  latest: string;
  onChange: (month: string) => void;
}) {
  const next = nextMonthKey(month);

  return (
    <div className="flex items-center gap-1 rounded-xl border border-line bg-surface p-1">
      <button
        type="button"
        onClick={() => onChange(monthKeyMinus(month, 1))}
        aria-label="Mes anterior"
        className="grid size-9 place-items-center rounded-lg text-muted transition-colors hover:bg-surface-raised hover:text-text"
      >
        <ChevronLeft className="size-4" />
      </button>
      <span className="min-w-32 text-center text-sm first-letter:uppercase">
        {formatMonthKey(month)}
      </span>
      <button
        type="button"
        onClick={() => onChange(next)}
        disabled={next > latest}
        aria-label="Mes siguiente"
        className="grid size-9 place-items-center rounded-lg text-muted transition-colors hover:bg-surface-raised hover:text-text disabled:cursor-not-allowed disabled:opacity-35"
      >
        <ChevronRight className="size-4" />
      </button>
    </div>
  );
}

/** «octubre», or «octubre de 2025» when it is not this year's. */
function monthLabel(key: string, thisMonth: string): string {
  const full = formatMonthKey(key);

  return key.slice(0, 4) === thisMonth.slice(0, 4)
    ? (full.split(" ")[0] ?? full)
    : full;
}

function nextMonthKey(key: string): string {
  const [year, month] = key.split("-").map(Number);
  if (!year || !month) return key;

  return month === 12
    ? `${year + 1}-01`
    : `${year}-${String(month + 1).padStart(2, "0")}`;
}

function CategoryDetail({
  category,
  label,
  month,
  currency,
  accounts,
}: {
  category: string;
  label: string;
  month: string;
  currency: Currency;
  accounts: readonly Account[];
}) {
  const range = monthRange(month) ?? { from: 0, to: 86_400 };
  const firstMonth = monthKeyMinus(month, MONTHS - 1);
  const runFrom = monthRange(firstMonth)?.from ?? range.from;
  const days = monthDayRange(month);
  const scope = {
    category,
    currency,
    direction: "outgoing" as const,
    transfers: "exclude" as const,
  };

  /*
   * Kept on screen while the next answer loads. Every one of these is keyed
   * by the category and the month, so changing either is four new reads —
   * and suspending on them would swap the whole screen for a loading state
   * on every choice. The previous figures stay, dimmed, until the new ones
   * land; only the very first visit has nothing to show yet.
   */
  const keep = { placeholderData: keepPreviousData } as const;
  const trendRead = useQuery({
    ...trendQuery({ ...scope, from: runFrom, to: range.to, interval: "month" }),
    ...keep,
  });
  const merchantsRead = useQuery({
    ...summaryQuery("merchant", {
      ...scope,
      from: range.from,
      to: range.to,
      order: "amount",
      top: TOP_MERCHANTS,
    }),
    ...keep,
  });
  const movementsRead = useQuery({
    ...transactionsQuery({ ...scope, from: range.from, to: range.to, limit: LISTED }),
    ...keep,
  });
  const budgetsRead = useQuery({ ...budgetsQuery(month), ...keep });

  const trend = trendRead.data;
  const merchants = merchantsRead.data;
  const movements = movementsRead.data;
  const budgets = budgetsRead.data;
  const reads = [trendRead, merchantsRead, movementsRead, budgetsRead];
  const failed = reads.find((read) => read.error)?.error ?? null;

  if (failed && !(trend && merchants && movements && budgets)) {
    return (
      <Card lift={false}>
        <p role="alert" className="py-6 text-center text-outgoing text-sm">
          No pude leer esta categoría: {failed.message}
        </p>
      </Card>
    );
  }

  if (!(trend && merchants && movements && budgets)) {
    return (
      <Card lift={false} aria-busy>
        <p className="flex items-center justify-center gap-2 py-10 text-muted text-sm">
          <Loader2 className="size-4 animate-spin" aria-hidden />
          Leyendo {label}…
        </p>
      </Card>
    );
  }

  // Showing the previous category's figures while this one's load.
  const refreshing = reads.some((read) => read.isPlaceholderData || read.isFetching);

  const months = monthlyOutgoing(trend, currency);
  const stats = categoryStats(months);
  const totals = merchants.totals.find((entry) => entry.currency === currency);
  const count = totals?.movements ?? 0;
  const ticket = count > 0 ? (Number(stats.total) / count).toFixed(2) : null;
  const thisMonth = currentMonthKey();
  // The chosen month is still running — not the bucket's own `partial`, which
  // only says the window cut it, and this window always reaches the month's end.
  const partial = month === thisMonth;
  const monthName = monthLabel(month, thisMonth);
  const previousName = monthLabel(monthKeyMinus(month, 1), thisMonth);
  const cap = budgets.budgets.find(
    (budget) =>
      !budget.retired &&
      budget.currency === currency &&
      budget.scope.categories.includes(category),
  );
  const listSearch = {
    category,
    direction: "outgoing" as const,
    transfers: "exclude" as const,
    from: days?.from,
    to: days?.to,
  };

  const peak = Math.max(
    ...merchants.groups.map((group) => toChartValue(amountIn(group.totals, currency))),
    toChartValue(amountIn(merchants.others?.totals ?? [], currency)),
    0,
  );
  const rows: RankedRow[] = [
    ...merchants.groups.map((group, index) => ({
      key: group.key ?? `sin-comercio-${index}`,
      label: group.key === null ? "Sin comercio" : group.label,
      amount: amountIn(group.totals, currency),
      fill: peak > 0 ? toChartValue(amountIn(group.totals, currency)) / peak : 0,
      tone: "bg-accent",
      to:
        group.key === null
          ? undefined
          : {
              to: "/transacciones" as const,
              search: { ...listSearch, merchant: group.key },
            },
    })),
    ...(merchants.others
      ? [
          {
            key: "otros",
            label: `Otros (${merchants.folded})`,
            amount: amountIn(merchants.others.totals, currency),
            fill:
              peak > 0
                ? toChartValue(amountIn(merchants.others.totals, currency)) / peak
                : 0,
            tone: "bg-line",
          },
        ]
      : []),
  ].filter((row) => toChartValue(row.amount) > 0);

  return (
    <div
      aria-busy={refreshing}
      className={cn(
        "flex flex-col gap-6 transition-opacity duration-200",
        refreshing && "opacity-60",
      )}
    >
      <section className="grid grid-cols-2 gap-3 [&>*]:min-w-0 lg:grid-cols-5">
        <Figure
          wide
          icon={Sigma}
          label={
            partial ? `${label} en ${monthName}, hasta hoy` : `${label} en ${monthName}`
          }
          highlight
        >
          <Money amount={stats.total} currency={currency} size="lg" />
        </Figure>

        <Figure icon={CalendarRange} label={`Frente a ${previousName}`}>
          {stats.previous === null ? (
            <span className="text-faint text-sm">Sin mes anterior</span>
          ) : Number(stats.previous) === 0 ? (
            <span className="text-faint text-sm">No hubo gasto en {previousName}.</span>
          ) : (
            <>
              <Money
                amount={stats.previous}
                currency={currency}
                size="md"
                tone="neutral"
              />
              <Change change={stats.vsPrevious} partial={partial} />
            </>
          )}
        </Figure>

        <Figure
          icon={Hash}
          label={
            stats.averagedOver > 0
              ? `Promedio de ${stats.averagedOver} ${stats.averagedOver === 1 ? "mes" : "meses"}`
              : "Promedio"
          }
        >
          {stats.average === null ? (
            <span className="text-faint text-sm">Aún no hay meses para comparar</span>
          ) : (
            <>
              <Money
                amount={stats.average}
                currency={currency}
                size="md"
                tone="neutral"
              />
              <Change change={stats.vsAverage} partial={partial} />
            </>
          )}
        </Figure>

        <Figure icon={Store} label="Movimientos">
          <span className="font-semibold text-xl tabular-nums">{count}</span>
          {ticket ? (
            <span className="text-faint text-xs">
              En promedio <Money amount={ticket} currency={currency} size="sm" /> cada
              uno
            </span>
          ) : null}
        </Figure>
      </section>

      {cap ? <CapLine cap={cap} currency={currency} /> : null}

      <Card lift={false} className="flex flex-col gap-3">
        <h2 className="font-medium text-sm">Los últimos {MONTHS} meses</h2>
        <Columns
          buckets={bucketsOf(trend, "month")}
          series={[
            {
              key: "outgoing",
              label,
              tone: "bg-accent",
              points: months.map((each) => ({
                value: toChartValue(each.outgoing),
                amount: each.outgoing,
              })),
            },
          ]}
          mode="grouped"
          currency={currency}
          caption={`${label}, mes a mes, durante los últimos ${MONTHS} meses.`}
          movementsOf={(index) => {
            const bucket = trend.buckets[index];
            if (!bucket) return null;

            return {
              category,
              direction: "outgoing",
              transfers: "exclude",
              from: dayStringOf(bucket.starts_at),
              to: dayStringOf(bucket.ends_at - 1),
            };
          }}
        />
      </Card>

      <div className="grid grid-cols-1 gap-4 [&>*]:min-w-0 lg:grid-cols-2">
        <Card lift={false} className="flex flex-col gap-3">
          <h2 className="font-medium text-sm">Dónde se gastó</h2>
          <RankedBars
            rows={rows}
            currency={currency}
            empty={`No hubo gastos de ${label} en ${monthName}.`}
          />
        </Card>

        <Card lift={false} className="flex flex-col gap-3">
          <div className="flex items-baseline justify-between gap-3">
            <h2 className="font-medium text-sm">Movimientos de {monthName}</h2>
            <Link
              to="/transacciones"
              search={listSearch}
              className="-my-2 inline-flex min-h-11 shrink-0 items-center gap-1.5 text-cyan text-xs hover:underline"
            >
              Ver en Transacciones
              <ArrowRight className="size-3.5" aria-hidden />
            </Link>
          </div>

          {movements.transactions.length === 0 ? (
            <p className="py-6 text-center text-muted text-sm">
              Ningún movimiento de {label} en {monthName}.
            </p>
          ) : (
            <ul className="flex flex-col [&>*+*]:border-line/60 [&>*+*]:border-t">
              {movements.transactions.map((movement) => (
                <li key={movement.id}>
                  <Link
                    to="/transacciones/$transactionId"
                    params={{ transactionId: movement.id }}
                    className="flex min-h-12 items-center justify-between gap-3 py-2 transition-colors hover:text-cyan"
                  >
                    <span className="min-w-0">
                      <span className="block truncate text-sm">
                        {movement.merchant?.display_name ?? movement.counterparty}
                      </span>
                      <span className="block text-faint text-xs">
                        {formatDayMonth(movement.occurred_at)}
                        {movement.account_id
                          ? ` · ${accounts.find((each) => each.id === movement.account_id)?.name ?? ""}`
                          : ""}
                      </span>
                    </span>
                    <Money
                      amount={movement.amount}
                      currency={movement.currency}
                      size="sm"
                      className="shrink-0"
                    />
                  </Link>
                </li>
              ))}
            </ul>
          )}

          {movements.total > movements.transactions.length ? (
            <p className="text-faint text-xs">
              Se ven {movements.transactions.length} de {movements.total}; el resto, en
              Transacciones.
            </p>
          ) : null}
        </Card>
      </div>
    </div>
  );
}

function amountIn(
  totals: readonly { currency: string; outgoing: string }[],
  currency: string,
): string {
  return totals.find((entry) => entry.currency === currency)?.outgoing ?? "0";
}

function Figure({
  icon: Icon,
  label,
  highlight = false,
  wide = false,
  children,
}: {
  icon: typeof Tag;
  label: string;
  highlight?: boolean;
  /** Two columns wide: the figure the screen is about. */
  wide?: boolean;
  children: React.ReactNode;
}) {
  return (
    <Card
      lift={false}
      glow={highlight ? "accent" : "none"}
      className={cn("flex flex-col gap-2 p-4 sm:p-5", wide && "col-span-2")}
    >
      <div className="flex items-start justify-between gap-3">
        <p className="text-muted text-sm">{label}</p>
        <span
          aria-hidden
          className={cn(
            "grid size-8 shrink-0 place-items-center rounded-lg",
            highlight ? "bg-accent/12 text-accent" : "bg-surface-raised text-muted",
          )}
        >
          <Icon className="size-4" />
        </span>
      </div>
      <div className="flex min-w-0 flex-col gap-1">{children}</div>
    </Card>
  );
}

/**
 * The chosen month against another figure, in words. Spending more reads in
 * the warning colour, less in the good one; a month not over yet says so,
 * because «30% menos» on the 10th is not news.
 */
function Change({ change, partial }: { change: number | null; partial: boolean }) {
  const words = describeChange(change);
  if (words === null) return null;

  const more = change !== null && Math.round(change) > 0;
  const less = change !== null && Math.round(change) < 0;

  return (
    <span
      className={cn(
        "text-xs",
        more ? "text-outgoing" : less ? "text-incoming" : "text-faint",
      )}
    >
      Este mes: {words}
      {partial ? <span className="text-faint"> (el mes no ha terminado)</span> : null}
    </span>
  );
}

/** The cap that covers this category, as far as it has gone this month. */
function CapLine({
  cap,
  currency,
}: {
  cap: {
    name: string;
    spent: string;
    limit: string;
    state: "ok" | "warning" | "over";
  };
  currency: string;
}) {
  const share = Math.min(1, toChartValue(cap.spent) / (toChartValue(cap.limit) || 1));

  return (
    <Card lift={false} className="flex flex-col gap-2 p-4">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <Link
          to="/presupuestos"
          className="flex items-center gap-2 text-sm transition-colors hover:text-cyan"
        >
          <Target className="size-4 text-cyan" aria-hidden />
          Tope «{cap.name}»
        </Link>
        <span className="text-muted text-xs">
          <Money amount={cap.spent} currency={currency} size="sm" /> de{" "}
          <Money amount={cap.limit} currency={currency} size="sm" />
        </span>
      </div>
      <div className="h-1.5 overflow-hidden rounded-full bg-surface-raised">
        <div
          className={cn(
            "h-full rounded-full",
            cap.state === "over"
              ? "bg-outgoing"
              : cap.state === "warning"
                ? "bg-warn"
                : "bg-incoming",
          )}
          style={{ width: `${Math.round(share * 100)}%` }}
        />
      </div>
    </Card>
  );
}
