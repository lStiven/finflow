import { useSuspenseQuery } from "@tanstack/react-query";
import { createFileRoute, Link, redirect } from "@tanstack/react-router";
import {
  ArrowDownLeft,
  ArrowLeftRight,
  ArrowUpRight,
  Landmark,
  TrendingDown,
  Wallet,
} from "lucide-react";
import {
  type Account,
  accountsQuery,
  categoriesQuery,
  type SpendingTotals,
  type SummaryGroup,
  summaryQuery,
  type Transaction,
  transactionsQuery,
} from "@/api/queries";
import { AppShell } from "@/components/AppShell";
import { CountUpMoney } from "@/components/CountUpMoney";
import { Donut, type Slice } from "@/components/charts/Donut";
import { Sparkline } from "@/components/charts/Sparkline";
import { Money } from "@/components/Money";
import { Card } from "@/components/ui/Card";
import { StatTile } from "@/components/ui/StatTile";
import {
  currentMonthKey,
  formatDate,
  formatMonthKey,
  monthDayRange,
  monthRange,
  nowInSeconds,
  previousMonthKey,
} from "@/lib/dates";
import { describeBalance, percentChange, signOf, toChartValue } from "@/lib/money";
import { transferTitle } from "@/lib/transfers";
import { categoryGroupLabel, categoryLabels } from "@/merchants/categories";

const RECENT_LIMIT = 6;
/** Beyond this the ring stops being readable; the rest becomes one wedge. */
const MAX_SLICES = 5;
const HOUR = 3600;
/** Enough months for a shape, few enough that each one is still a segment. */
const TREND_MONTHS = 8;

/**
 * The window the screen reports on, resolved per render.
 *
 * Not module constants: a tab left open overnight on the 31st would keep
 * naming the old month while `refetchOnWindowFocus` refreshed the figures
 * underneath it, so the header and the numbers would disagree.
 *
 * `previousToDate` covers the same *distance* into the previous month rather
 * than the whole of it. Comparing a month that is three days old against a
 * complete one is what would show spending "down 100%" every 1st — true
 * arithmetic, and a useless thing to tell somebody.
 */
function currentPeriod() {
  const month = currentMonthKey();
  const range = monthRange(month);
  const previous = monthRange(previousMonthKey(month));
  /*
   * Rounded down to the hour. Taken to the second, this lands in the query
   * key, so the previous-month summary would be a different query on every
   * render: the loader's prefetch would never be the component's cache hit,
   * the screen would suspend and refetch, and the cache would grow an entry
   * per second the tab stayed open.
   */
  const elapsed = range ? Math.floor((nowInSeconds() - range.from) / HOUR) * HOUR : 0;
  return {
    month,
    range,
    previousToDate: previous
      ? { from: previous.from, to: Math.min(previous.from + elapsed, previous.to) }
      : null,
  };
}

export const Route = createFileRoute("/")({
  beforeLoad: ({ context }) => {
    if (!context.session) throw redirect({ to: "/login" });
  },
  loader: ({ context }) => {
    const { range, previousToDate } = currentPeriod();
    // `staleTime: "static"` so coming back to the dashboard paints from cache
    // instead of blocking the route on six refetches; the 30s default makes
    // every navigation back here wait on the network.
    return Promise.all([
      context.queryClient.query({ ...accountsQuery("open"), staleTime: "static" }),
      context.queryClient.query({
        ...summaryQuery("month", range ?? {}),
        staleTime: "static",
      }),
      context.queryClient.query({
        ...summaryQuery("month", previousToDate ?? {}),
        staleTime: "static",
      }),
      context.queryClient.query({ ...summaryQuery("month"), staleTime: "static" }),
      context.queryClient.query(categoriesQuery),
      context.queryClient.query({
        ...summaryQuery("category", range ?? {}),
        staleTime: "static",
      }),
      context.queryClient.query({
        ...transactionsQuery({ limit: RECENT_LIMIT }),
        staleTime: "static",
      }),
    ]);
  },
  component: Dashboard,
});

function Dashboard() {
  const { month, range, previousToDate } = currentPeriod();

  const { data: accounts } = useSuspenseQuery(accountsQuery("open"));
  const { data: byMonth } = useSuspenseQuery(summaryQuery("month", range ?? {}));
  const { data: byPrevious } = useSuspenseQuery(
    summaryQuery("month", previousToDate ?? {}),
  );
  const { data: everyMonth } = useSuspenseQuery(summaryQuery("month"));
  const { data: byCategory } = useSuspenseQuery(summaryQuery("category", range ?? {}));
  const { data: recent } = useSuspenseQuery(transactionsQuery({ limit: RECENT_LIMIT }));
  // A summary bucket is keyed by the category value and labelled with it, so
  // one this person wrote needs their own name for it from somewhere.
  const { data: categories } = useSuspenseQuery(categoriesQuery);

  /*
   * One currency drives the screen. Net worth is reported per currency and is
   * never summed across them — there is no exchange rate anywhere in the
   * backend — so the tiles commit to the first one and say so when there are
   * others, rather than adding COP to USD behind a single total.
   */
  const primary = accounts.net_worth[0] ?? null;
  const currency = primary?.currency ?? byMonth.totals[0]?.currency ?? "COP";
  const otherCurrencies = accounts.net_worth.slice(1);

  const thisMonth = totalsFor(byMonth.groups, month, currency);
  const lastMonth = totalsFor(byPrevious.groups, previousMonthKey(month), currency);
  const trend = monthlyTrend(everyMonth.groups, currency);
  /*
   * The tiles report month to date, so the list they open has to be bounded
   * the same way. Without this, tapping "Gastos" shows every expense ever
   * recorded under a figure that covers this month — two different numbers,
   * one of them apparently wrong.
   */
  const days = monthDayRange(month);
  const monthSearch = days ? { from: days.from, to: days.to } : {};

  return (
    <AppShell>
      <div className="flex flex-col gap-8">
        <header className="flex flex-wrap items-end justify-between gap-3">
          <div>
            <h1 className="font-semibold text-2xl tracking-tight">Resumen</h1>
            <p className="mt-1 text-muted text-sm">
              Aquí tienes un resumen de tus finanzas.
            </p>
          </div>
          <p className="rounded-lg border border-line bg-surface px-3 py-1.5 text-muted text-xs first-letter:uppercase">
            {formatMonthKey(month)}
          </p>
        </header>

        <section
          aria-label="Cifras del mes"
          className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4"
        >
          <StatTile label="Patrimonio" icon={Wallet} hue="violet">
            {primary ? (
              <CountUpMoney
                amount={primary.total}
                currency={primary.currency}
                size="md"
                tone={signOf(primary.total) < 0 ? "negative" : "plain"}
              />
            ) : (
              <span className="text-faint text-numeral-sm tabular">—</span>
            )}
          </StatTile>

          <StatTile
            label="Ingresos"
            icon={ArrowDownLeft}
            hue="green"
            to={{
              to: "/transacciones",
              search: {
                ...monthSearch,
                direction: "incoming",
                // The tile reports income, and `/summary` leaves transfers
                // out of that. The list has to be asked for the same thing or
                // the rows behind the figure would not add up to it.
                transfers: "exclude",
              },
            }}
            caption={
              <Delta
                previous={lastMonth?.incoming}
                current={thisMonth?.incoming ?? "0"}
              />
            }
            chart={
              trend.length > 1 ? (
                <Sparkline
                  values={trend.map((entry) => entry.incoming)}
                  className="text-incoming"
                />
              ) : null
            }
          >
            <CountUpMoney
              amount={thisMonth?.incoming ?? "0"}
              currency={currency}
              size="md"
              tone="positive"
            />
          </StatTile>

          <StatTile
            label="Gastos"
            icon={ArrowUpRight}
            hue="accent"
            to={{
              to: "/transacciones",
              search: {
                ...monthSearch,
                direction: "outgoing",
                transfers: "exclude",
              },
            }}
            caption={
              <Delta
                previous={lastMonth?.outgoing}
                current={thisMonth?.outgoing ?? "0"}
                lowerIsBetter
              />
            }
            chart={
              trend.length > 1 ? (
                <Sparkline
                  values={trend.map((entry) => entry.outgoing)}
                  className="text-accent"
                />
              ) : null
            }
          >
            <CountUpMoney
              amount={thisMonth?.outgoing ?? "0"}
              currency={currency}
              size="md"
              tone="negative"
            />
          </StatTile>

          <StatTile label="Deuda" icon={TrendingDown} hue="cyan">
            {primary ? (
              <CountUpMoney
                amount={primary.liabilities}
                currency={primary.currency}
                size="md"
                tone={signOf(primary.liabilities) > 0 ? "negative" : "neutral"}
              />
            ) : (
              <span className="text-faint text-numeral-sm tabular">—</span>
            )}
          </StatTile>
        </section>

        {otherCurrencies.length > 0 ? (
          <p className="-mt-5 text-faint text-xs">
            Además tienes saldos en{" "}
            {otherCurrencies.map((entry) => entry.currency).join(", ")}. Se muestran
            aparte porque no existe una tasa de cambio para sumarlos.
          </p>
        ) : null}

        {/* `[&>*]:min-w-0` is load-bearing, not tidiness: a grid track is at
            least its item's min-content, and a movement's title never wraps
            (`truncate`), so the recent-movements card demanded ~440px and the
            whole dashboard scrolled sideways on any phone. */}
        <div className="grid gap-4 [&>*]:min-w-0 lg:grid-cols-2">
          <CategoryCard
            groups={byCategory.groups}
            currency={currency}
            previousOutgoing={lastMonth?.outgoing}
            month={month}
            labels={categoryLabels(categories.categories)}
          />
          <RecentCard transactions={recent.transactions} />
        </div>

        <AccountList accounts={accounts.accounts} currency={currency} />
      </div>
    </AppShell>
  );
}

/** The totals a group carries for one currency, or null if it has none. */
function totalsFor(
  groups: SummaryGroup[],
  key: string,
  currency: string,
): SpendingTotals | null {
  const group = groups.find((candidate) => candidate.key === key);
  return group?.totals.find((total) => total.currency === currency) ?? null;
}

/**
 * The last few months, oldest first, for the shapes behind the KPIs.
 *
 * Through floats like every other chart value — these drive a polyline, and
 * no figure beside them comes from here.
 */
function monthlyTrend(
  groups: SummaryGroup[],
  currency: string,
): { incoming: number; outgoing: number }[] {
  return groups
    .filter((group) => group.key !== null)
    .sort((left, right) => String(left.key).localeCompare(String(right.key)))
    .slice(-TREND_MONTHS)
    .map((group) => {
      const totals = group.totals.find((total) => total.currency === currency);
      return {
        incoming: totals ? toChartValue(totals.incoming) : 0,
        outgoing: totals ? toChartValue(totals.outgoing) : 0,
      };
    });
}

/**
 * Change against the same figure last month.
 *
 * Silent when there is no previous month to compare against, which is the
 * normal state for a new account — a badge reading "+100%" against nothing
 * is the kind of number people quote back at you.
 */
function Delta({
  previous,
  current,
  lowerIsBetter = false,
}: {
  previous?: string;
  current: string;
  lowerIsBetter?: boolean;
}) {
  if (previous === undefined) return null;
  const change = percentChange(previous, current);
  if (change === null) return null;

  const rounded = Math.round(change * 10) / 10;
  if (rounded === 0) {
    return <span className="text-faint">Igual que el mes pasado</span>;
  }

  const good = lowerIsBetter ? rounded < 0 : rounded > 0;
  return (
    <span className={good ? "text-incoming" : "text-outgoing"}>
      {rounded > 0 ? "↑" : "↓"} {Math.abs(rounded)}%{" "}
      <span className="text-faint">vs mes pasado</span>
    </span>
  );
}

function CategoryCard({
  groups,
  currency,
  previousOutgoing,
  month,
  labels,
}: {
  groups: SummaryGroup[];
  currency: string;
  previousOutgoing?: string;
  month: string;
  labels: Record<string, string>;
}) {
  const slices = toSlices(groups, currency, labels);
  const biggest = slices.parts[0];
  const change =
    previousOutgoing === undefined
      ? null
      : percentChange(previousOutgoing, slices.total);

  return (
    <Card glow="violet" lift={false} className="flex flex-col gap-5">
      <h2 className="font-medium">Gastos por categoría</h2>

      {slices.parts.length === 0 ? (
        <Empty>Todavía no hay gastos registrados este mes.</Empty>
      ) : (
        <>
          <Donut slices={slices.parts} total={slices.total} currency={currency} />

          {/* The two readings the ring cannot make on its own. */}
          <dl className="mt-auto grid grid-cols-2 gap-3 border-line/70 border-t pt-4 text-sm">
            <div className="min-w-0">
              <dt className="text-faint text-xs">Mayor gasto</dt>
              <dd className="mt-0.5 truncate">{biggest?.label ?? "—"}</dd>
            </div>
            <div className="min-w-0 text-right">
              <dt className="text-faint text-xs">Frente al mes pasado</dt>
              <dd className="mt-0.5">
                {change === null ? (
                  <span className="text-faint">Sin comparación</span>
                ) : (
                  <span className={change > 0 ? "text-outgoing" : "text-incoming"}>
                    {Math.abs(Math.round(change))}% {change > 0 ? "más" : "menos"} que{" "}
                    <span className="capitalize">
                      {formatMonthKey(previousMonthKey(month)).split(" ")[0]}
                    </span>
                  </span>
                )}
              </dd>
            </div>
          </dl>
        </>
      )}
    </Card>
  );
}

type Slices = { parts: Slice[]; total: string };

/**
 * Outgoing money per category, largest first, with the tail folded into one
 * wedge. Shares are computed from the same rounded floats the ring is drawn
 * with, so the legend's percentages always add up to what is on screen.
 */
function toSlices(
  groups: SummaryGroup[],
  currency: string,
  labels: Record<string, string>,
): Slices {
  const rows = groups
    .map((group) => {
      const totals = group.totals.find((total) => total.currency === currency);
      // `key` is nullable in the contract — the bucket for movements with no
      // merchant behind them, and the one whose label the API sends in
      // English. `categoryGroupLabel` is what answers both.
      return totals
        ? {
            key: group.key ?? "__none__",
            label: categoryGroupLabel(group, labels),
            amount: totals.outgoing,
          }
        : null;
    })
    .filter((row): row is NonNullable<typeof row> => row !== null)
    .map((row) => ({ ...row, value: toChartValue(row.amount) }))
    .filter((row) => row.value > 0)
    .sort((left, right) => right.value - left.value);

  const sum = rows.reduce((running, row) => running + row.value, 0);
  if (sum === 0) return { parts: [], total: "0" };

  const head = rows.slice(0, MAX_SLICES);
  const tail = rows.slice(MAX_SLICES);
  const parts: Slice[] = head.map((row) => ({
    key: row.key,
    label: row.label,
    amount: row.amount,
    share: row.value / sum,
  }));

  if (tail.length > 0) {
    const rest = tail.reduce((running, row) => running + row.value, 0);
    parts.push({
      key: "__rest__",
      label: `Otros (${tail.length})`,
      // Summed through floats like the shares, and shown as a chart figure
      // rather than a ledger one — every exact amount is a category away.
      amount: rest.toFixed(2),
      share: rest / sum,
    });
  }

  return { parts, total: sum.toFixed(2) };
}

function RecentCard({ transactions }: { transactions: Transaction[] }) {
  return (
    <Card glow="cyan" lift={false} className="flex flex-col gap-4">
      <div className="flex items-center justify-between gap-3">
        <h2 className="font-medium">Movimientos recientes</h2>
        <Link
          to="/transacciones"
          className="rounded-lg border border-line px-2.5 py-1 text-muted text-xs transition-colors duration-150 hover:border-accent/40 hover:text-text"
        >
          Ver todos
        </Link>
      </div>

      {transactions.length === 0 ? (
        <Empty>
          Cuando tu banco te avise de un movimiento, aparecerá aquí sin que tengas que
          escribir nada.
        </Empty>
      ) : (
        <ul className="-mx-2 flex flex-col">
          {transactions.map((movement) => (
            <li key={movement.id}>
              <Link
                to="/transacciones/$transactionId"
                params={{ transactionId: movement.id }}
                className="group flex items-center gap-3 rounded-xl px-2 py-2 transition-colors duration-150 hover:bg-surface-raised"
              >
                <span
                  aria-hidden
                  className={
                    movement.transfer
                      ? "grid size-8 shrink-0 place-items-center rounded-lg bg-gradient-to-br from-violet/25 to-cyan/5 text-violet transition-transform duration-200 group-hover:scale-110"
                      : movement.direction === "incoming"
                        ? "grid size-8 shrink-0 place-items-center rounded-lg bg-gradient-to-br from-incoming/25 to-cyan/5 text-incoming transition-transform duration-200 group-hover:scale-110"
                        : "grid size-8 shrink-0 place-items-center rounded-lg bg-gradient-to-br from-accent/25 to-violet/5 text-accent transition-transform duration-200 group-hover:scale-110"
                  }
                >
                  {movement.transfer ? (
                    <ArrowLeftRight className="size-3.5" />
                  ) : movement.direction === "incoming" ? (
                    <ArrowDownLeft className="size-3.5" />
                  ) : (
                    <ArrowUpRight className="size-3.5" />
                  )}
                </span>

                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm">
                    {movement.transfer
                      ? transferTitle(movement.transfer, movement.counterparty)
                      : (movement.merchant?.display_name ?? movement.counterparty)}
                  </p>
                  <p className="truncate text-faint text-xs">
                    {formatDate(movement.occurred_at)}
                    {movement.transfer ? " · traslado" : null}
                    {movement.account_id ? null : " · sin asignar"}
                  </p>
                </div>

                <Money
                  amount={
                    movement.direction === "outgoing"
                      ? `-${movement.amount}`
                      : movement.amount
                  }
                  currency={movement.currency}
                  signed
                  size="sm"
                  tone={
                    movement.transfer
                      ? "neutral"
                      : movement.direction === "incoming"
                        ? "positive"
                        : "plain"
                  }
                />
              </Link>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

function AccountList({
  accounts,
  currency,
}: {
  accounts: Account[];
  /** The one the tiles above report in — not whichever account sorts first. */
  currency: string;
}) {
  if (accounts.length === 0) {
    return (
      <Card glow="accent" className="flex flex-col items-start gap-3">
        <Landmark className="size-5 text-accent" aria-hidden />
        <div>
          <h2 className="font-medium">Todavía no declaraste ninguna cuenta</h2>
          <p className="mt-1 text-muted text-sm">
            Finflow funciona sin ninguna: todo lo que llegue queda registrado sin
            asignar. Declarar una cuenta es retroactivo — adopta lo que estaba
            esperando.
          </p>
        </div>
      </Card>
    );
  }

  const mixed = accounts.some((account) => account.currency !== currency);

  return (
    <section className="flex flex-col gap-3">
      <h2 className="text-muted text-sm">Cuentas</h2>
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {accounts.map((account) => {
          // Tone and caption both, from the one place allowed to decide what
          // a balance means: a paid-off card must not read "Debes" beside a
          // zero, and deriving the caption from `category` here is exactly
          // how that happens.
          const { tone, label } = describeBalance(
            account.balance,
            account.currency,
            account.category,
          );
          const owed = account.category === "liability";
          return (
            <Card
              key={account.id}
              glow={owed ? "accent" : "cyan"}
              className="relative flex items-center justify-between gap-4 overflow-hidden"
            >
              {/* What kind of account this is, said in one hairline. */}
              <span
                aria-hidden
                className={
                  owed
                    ? "absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-accent/50 to-transparent"
                    : "absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-cyan/50 to-transparent"
                }
              />
              <div className="min-w-0">
                <p className="truncate font-medium">{account.name}</p>
                {/* A watched credit is not in the Deuda tile above it, so it
                    must not read "Debes" either — `/cuentas` names it the
                    same way. */}
                <p className="mt-0.5 text-faint text-xs">
                  {account.informational ? "Saldo del crédito" : label}
                </p>
              </div>
              <Money amount={account.balance} currency={account.currency} tone={tone} />
            </Card>
          );
        })}
      </div>
      {mixed ? (
        <p className="text-faint text-xs">
          Los totales de arriba cubren {currency} únicamente.
        </p>
      ) : null}
    </section>
  );
}

function Empty({ children }: { children: React.ReactNode }) {
  return <p className="py-6 text-center text-muted text-sm">{children}</p>;
}
