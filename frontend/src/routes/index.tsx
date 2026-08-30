import { useSuspenseQuery } from "@tanstack/react-query";
import { createFileRoute, redirect } from "@tanstack/react-router";
import {
  ArrowDownLeft,
  ArrowUpRight,
  Landmark,
  TrendingDown,
  Wallet,
} from "lucide-react";
import {
  type Account,
  accountsQuery,
  type NetWorth,
  type SpendingTotals,
  type SummaryGroup,
  summaryQuery,
  type Transaction,
  transactionsQuery,
} from "@/api/queries";
import { AppShell } from "@/components/AppShell";
import { Donut, type Slice } from "@/components/charts/Donut";
import { Money } from "@/components/Money";
import { Card } from "@/components/ui/Card";
import { StatTile } from "@/components/ui/StatTile";
import {
  currentMonthKey,
  formatDate,
  formatMonthKey,
  monthRange,
  nowInSeconds,
  previousMonthKey,
} from "@/lib/dates";
import { describeBalance, percentChange, signOf, toChartValue } from "@/lib/money";

const RECENT_LIMIT = 6;
/** Beyond this the ring stops being readable; the rest becomes one wedge. */
const MAX_SLICES = 5;

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
  const elapsed = range ? nowInSeconds() - range.from : 0;
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
    return Promise.all([
      context.queryClient.query(accountsQuery("open")),
      context.queryClient.query(summaryQuery("month", range ?? {})),
      context.queryClient.query(summaryQuery("month", previousToDate ?? {})),
      context.queryClient.query(summaryQuery("category", range ?? {})),
      context.queryClient.query(transactionsQuery({ limit: RECENT_LIMIT })),
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
  const { data: byCategory } = useSuspenseQuery(summaryQuery("category", range ?? {}));
  const { data: recent } = useSuspenseQuery(transactionsQuery({ limit: RECENT_LIMIT }));

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
          <p className="rounded-lg border border-line bg-surface px-3 py-1.5 text-muted text-xs">
            {formatMonthKey(month)}
          </p>
        </header>

        <section
          aria-label="Cifras del mes"
          className="grid grid-cols-2 gap-3 lg:grid-cols-4"
        >
          <StatTile label="Patrimonio" icon={Wallet} tint="accent">
            {primary ? (
              <Money
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
            tint="incoming"
            caption={
              <Delta
                previous={lastMonth?.incoming}
                current={thisMonth?.incoming ?? "0"}
              />
            }
          >
            <Money
              amount={thisMonth?.incoming ?? "0"}
              currency={currency}
              size="md"
              tone="positive"
            />
          </StatTile>

          <StatTile
            label="Gastos"
            icon={ArrowUpRight}
            tint="outgoing"
            caption={
              <Delta
                previous={lastMonth?.outgoing}
                current={thisMonth?.outgoing ?? "0"}
                lowerIsBetter
              />
            }
          >
            <Money
              amount={thisMonth?.outgoing ?? "0"}
              currency={currency}
              size="md"
              tone="negative"
            />
          </StatTile>

          <StatTile label="Deuda" icon={TrendingDown} tint="neutral">
            {primary ? (
              <Money
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
          <p className="-mt-4 text-faint text-xs">
            Además tienes saldos en{" "}
            {otherCurrencies.map((entry) => entry.currency).join(", ")}. Se muestran
            aparte porque no existe una tasa de cambio para sumarlos.
          </p>
        ) : null}

        <div className="grid gap-4 lg:grid-cols-2">
          <CategoryCard groups={byCategory.groups} currency={currency} />
          <RecentCard transactions={recent.transactions} />
        </div>

        <AccountList
          accounts={accounts.accounts}
          others={otherCurrencies}
          currency={currency}
        />
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

  const rounded = Math.round(change);
  if (rounded === 0) return <span className="text-faint">Igual que el mes pasado</span>;

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
}: {
  groups: SummaryGroup[];
  currency: string;
}) {
  const slices = toSlices(groups, currency);

  return (
    <Card className="flex flex-col gap-5">
      <h2 className="font-medium">Gastos por categoría</h2>
      {slices.length === 0 ? (
        <Empty>Todavía no hay gastos registrados este mes.</Empty>
      ) : (
        <Donut slices={slices.parts} total={slices.total} currency={currency} />
      )}
    </Card>
  );
}

type Slices = { parts: Slice[]; total: string; length: number };

/**
 * Outgoing money per category, largest first, with the tail folded into one
 * wedge. Shares are computed from the same rounded floats the ring is drawn
 * with, so the legend's percentages always add up to what is on screen.
 */
function toSlices(groups: SummaryGroup[], currency: string): Slices {
  const rows = groups
    .map((group) => {
      const totals = group.totals.find((total) => total.currency === currency);
      // `key` is nullable in the contract — the bucket for movements with no
      // category at all. The label is what the reader sees either way.
      return totals
        ? { key: group.key ?? group.label, label: group.label, amount: totals.outgoing }
        : null;
    })
    .filter((row): row is NonNullable<typeof row> => row !== null)
    .map((row) => ({ ...row, value: toChartValue(row.amount) }))
    .filter((row) => row.value > 0)
    .sort((left, right) => right.value - left.value);

  const sum = rows.reduce((running, row) => running + row.value, 0);
  if (sum === 0) return { parts: [], total: "0", length: 0 };

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

  return { parts, total: sum.toFixed(2), length: parts.length };
}

function RecentCard({ transactions }: { transactions: Transaction[] }) {
  return (
    <Card className="flex flex-col gap-4">
      <h2 className="font-medium">Movimientos recientes</h2>
      {transactions.length === 0 ? (
        <Empty>
          Cuando tu banco te avise de un movimiento, aparecerá aquí sin que tengas que
          escribir nada.
        </Empty>
      ) : (
        <ul className="flex flex-col">
          {transactions.map((movement) => (
            <li
              key={movement.id}
              className="flex items-center gap-3 border-line/60 border-b py-3 last:border-0 last:pb-0"
            >
              <span
                aria-hidden
                className={
                  movement.direction === "incoming"
                    ? "grid size-9 shrink-0 place-items-center rounded-lg bg-chart-4/15 text-incoming"
                    : "grid size-9 shrink-0 place-items-center rounded-lg bg-chart-1/15 text-outgoing"
                }
              >
                {movement.direction === "incoming" ? (
                  <ArrowDownLeft className="size-4" />
                ) : (
                  <ArrowUpRight className="size-4" />
                )}
              </span>

              <div className="min-w-0 flex-1">
                <p className="truncate text-sm">
                  {movement.merchant?.display_name ?? movement.counterparty}
                </p>
                <p className="mt-0.5 truncate text-faint text-xs">
                  {formatDate(movement.occurred_at)}
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
                tone={movement.direction === "incoming" ? "positive" : "plain"}
              />
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

function AccountList({
  accounts,
  others,
  currency,
}: {
  accounts: Account[];
  others: NetWorth[];
  /** The one the tiles above report in — not whichever account sorts first. */
  currency: string;
}) {
  if (accounts.length === 0) {
    return (
      <Card className="flex flex-col items-start gap-3">
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
          return (
            <Card key={account.id} className="flex items-center justify-between gap-4">
              <div className="min-w-0">
                <p className="truncate font-medium">{account.name}</p>
                <p className="mt-0.5 text-faint text-xs">{label}</p>
              </div>
              <Money amount={account.balance} currency={account.currency} tone={tone} />
            </Card>
          );
        })}
      </div>
      {others.length > 0 ? (
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
