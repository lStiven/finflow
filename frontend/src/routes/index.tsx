import { useQuery, useQueryClient, useSuspenseQuery } from "@tanstack/react-query";
import { createFileRoute, Link, redirect } from "@tanstack/react-router";
import {
  ArrowLeftRight,
  CalendarDays,
  Landmark,
  MousePointerClick,
  Wallet,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";
import {
  type Account,
  accountsQuery,
  allowanceQuery,
  budgetsQuery,
  categoriesQuery,
  historyQuery,
  type MonthlyPoint,
  type NetWorth,
  planQuery,
  queryKeys,
  type SpendingTotals,
  type SummaryGroup,
  summaryQuery,
  type Transaction,
  transactionsQuery,
} from "@/api/queries";
import { BudgetSummaryCard } from "@/budgets/BudgetSummaryCard";
import { AppShell } from "@/components/AppShell";
import { CountUpMoney } from "@/components/CountUpMoney";
import { CategoryBar, type Part } from "@/components/charts/CategoryBar";
import { NetWorthChart, type NetWorthPoint } from "@/components/charts/NetWorthChart";
import { Money } from "@/components/Money";
import { PageHeader, type PageHelp } from "@/components/PageHeader";
import { Card } from "@/components/ui/Card";
import { cn } from "@/lib/cn";
import {
  currentMonthKey,
  formatDate,
  formatMonthKey,
  monthDayRange,
  monthRange,
  nowInSeconds,
  previousMonthKey,
} from "@/lib/dates";
import {
  describeBalance,
  differenceOf,
  formatMoney,
  percentChange,
  signOf,
  toChartValue,
} from "@/lib/money";
import { transferTitle } from "@/lib/transfers";
import { categoryGroupLabel, categoryLabels } from "@/merchants/categories";
import { EmptyDiagnosis } from "@/onboarding/EmptyDiagnosis";
import { AllowanceCard } from "@/plan/AllowanceCard";

const RECENT_LIMIT = 6;
/** Beyond this the bar stops being readable; the rest becomes one part. */
const MAX_SLICES = 5;
const HOUR = 3600;
/** A year: long enough for a shape, short enough that every month is a step. */
const HISTORY_MONTHS = 12;
/**
 * How often the recent movements are asked for again while the screen is
 * open. Movements arrive on their own, from the bank's email; this is what
 * lets one appear here without a reload. React Query skips it while the tab
 * is in the background.
 */
const LIVE_MS = 60_000;
/** How long a movement that just arrived stays lit. */
const FRESH_MS = 2400;

const QUIET_LINK =
  "inline-flex min-h-9 items-center rounded-lg border border-line px-3 text-muted text-xs transition-colors duration-150 hover:border-accent/40 hover:text-text";

/**
 * The window the screen reports on, resolved per render.
 *
 * Not module constants: a tab left open overnight on the 31st would keep
 * naming the old month while `refetchOnWindowFocus` refreshed the figures
 * underneath it, so the header and the numbers would disagree.
 *
 * `previousToDate` covers the same *distance* into the previous month rather
 * than the whole of it. Comparing a month that is three days old against a
 * complete one is what would show spending "down 100%" every 1st: true
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
      context.queryClient.query(categoriesQuery),
      context.queryClient.query({
        ...summaryQuery("category", range ?? {}),
        staleTime: "static",
      }),
      // Last month's spending per category, to the same day, for the bar's
      // «frente a septiembre». Caught: losing it costs the comparison only.
      context.queryClient
        .query({
          ...summaryQuery("category", previousToDate ?? {}),
          staleTime: "static",
        })
        .catch(() => null),
      context.queryClient.query({
        ...transactionsQuery({ limit: RECENT_LIMIT }),
        staleTime: "static",
      }),
      // The line under the net worth is the one figure here that is optional:
      // without it the panel still says what you have and what you owe. So a
      // failure costs the line, never the dashboard.
      context.queryClient
        .query({ ...historyQuery(HISTORY_MONTHS), staleTime: "static" })
        .catch(() => null),
      // Both answer 404 until somebody declares their month, which the query
      // turns into `null`, so this loads a real answer either way and the
      // card never flashes in after the rest of the screen has painted.
      // Caught, because the card is optional: anything else failing is a
      // broken dashboard, and this failing should only be a missing card.
      context.queryClient.query(planQuery).catch(() => null),
      context.queryClient.query(allowanceQuery).catch(() => null),
      // Caught for the same reason, though this one never 404s: the budgets
      // card is optional, and a 5xx behind it must cost a card rather than the
      // dashboard.
      context.queryClient.query(budgetsQuery()).catch(() => null),
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
  const { data: byCategoryPrevious } = useQuery(
    summaryQuery("category", previousToDate ?? {}),
  );
  const { data: recent } = useSuspenseQuery({
    ...transactionsQuery({ limit: RECENT_LIMIT }),
    refetchInterval: LIVE_MS,
  });
  // A summary bucket is keyed by the category value and labelled with it, so
  // one this person wrote needs their own name for it from somewhere.
  const { data: categories } = useSuspenseQuery(categoriesQuery);

  /*
   * One currency drives the screen. Net worth is reported per currency and is
   * never summed across them (there is no exchange rate anywhere in the
   * backend), so the panels commit to the first one and say so when there are
   * others, rather than adding COP to USD behind a single total.
   */
  const primary = accounts.net_worth[0] ?? null;
  const currency = primary?.currency ?? byMonth.totals[0]?.currency ?? "COP";
  const otherCurrencies = accounts.net_worth.slice(1);

  const thisMonth = totalsFor(byMonth.groups, month, currency);
  const lastMonth = totalsFor(byPrevious.groups, previousMonthKey(month), currency);
  /*
   * The figures report month to date, so the list they open has to be bounded
   * the same way. Without this, tapping "Gastos" shows every expense ever
   * recorded under a figure that covers this month: two different numbers,
   * one of them apparently wrong.
   */
  const days = monthDayRange(month);
  const monthSearch = days ? { from: days.from, to: days.to } : {};
  // «septiembre», for comparisons that have to say what they compare with.
  const previousName = formatMonthKey(previousMonthKey(month)).split(" ")[0] ?? "";

  return (
    <AppShell>
      <div className="flex flex-col gap-4 sm:gap-5">
        <PageHeader
          title="Resumen"
          lead="Cuánto tienes, cuánto entró, cuánto gastaste y cuánto debes."
          help={HELP}
          actions={
            <p className="rounded-lg border border-line bg-surface px-3 py-1.5 text-muted text-xs first-letter:uppercase">
              {formatMonthKey(month)}
            </p>
          }
        />

        <div className="grid gap-4 [&>*]:min-w-0 lg:grid-cols-[minmax(0,1.55fr)_minmax(0,1fr)]">
          <NetWorthPanel primary={primary} currency={currency} />
          {/* Both halves of this column are "how the month is going": what came
              in and went out, then how that stands against the caps. Together
              they also fill the height the net worth sets, instead of leaving
              a hole under the shorter card. The caps draw nothing until one is
              declared. */}
          <div className="flex flex-col gap-4">
            <MonthFlow
              thisMonth={thisMonth}
              lastMonth={lastMonth}
              previousName={previousName}
              monthSearch={monthSearch}
              currency={currency}
            />
            <BudgetSummaryCard />
          </div>
        </div>

        {otherCurrencies.length > 0 ? (
          <p className="text-faint text-xs">
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
            previousGroups={byCategoryPrevious?.groups}
            previousName={previousName}
            currency={currency}
            monthSearch={monthSearch}
            labels={categoryLabels(categories.categories)}
          />
          <RecentCard transactions={recent.transactions} />
        </div>

        <AccountList accounts={accounts.accounts} currency={currency} />

        {/* Below the accounts, and that is a correction rather than a tidy-up.
            This card used to sit above the tiles, on the argument that «qué me
            queda» is the question somebody opens this screen with. Watching it
            get used said otherwise: what this screen is first asked is the four
            plain facts (cuánto tengo, cuánto entró, cuánto gasté, cuánto debo)
            and a card that starts as a form asking for two figures nobody has
            typed yet is a wall in front of them. */}
        <AllowanceCard />
      </div>
    </AppShell>
  );
}

/**
 * Not opened on arrival: a first visit here is already greeted by the welcome
 * dialog, and two explanations at once is one too many.
 */
const HELP: PageHelp = {
  id: "resumen",
  openFirstTime: false,
  points: [
    {
      icon: Wallet,
      title: "Patrimonio",
      body: "Lo que tienen tus cuentas menos lo que debes. Los préstamos que solo vigilas no cuentan.",
    },
    {
      icon: ArrowLeftRight,
      title: "Traslados aparte",
      body: "Pagar tu tarjeta desde tu cuenta no es un gasto: no suma ni resta en estas cifras.",
    },
    {
      icon: CalendarDays,
      title: "Contra el mes pasado",
      body: "La comparación llega hasta el mismo día del mes anterior, no al mes entero.",
    },
    {
      icon: MousePointerClick,
      title: "Toca para ver",
      body: "Cada cifra, categoría y cuenta abre lo que la forma. La línea del patrimonio se recorre mes a mes.",
    },
  ],
};

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
 * Where a counting figure should start: zero on arrival, then whatever it
 * showed before each time the amount changes, so a movement that lands moves
 * the figure from the old balance to the new one instead of from nothing.
 *
 * State rather than a ref, adjusted during render, which is React's own
 * pattern for a value derived from the previous props.
 */
function useStartingPoint(amount: string): string {
  const [seen, setSeen] = useState({ amount, from: "0" });
  if (seen.amount !== amount) {
    setSeen({ amount, from: seen.amount });
    return seen.amount;
  }
  return seen.from;
}

/** A decimal string without its sign: the arrow beside it already says which way. */
function magnitude(amount: string): string {
  return amount.replace(/^[+-]/, "");
}

/** «nov», from «2026-11». */
function shortMonth(key: string): string {
  const [year, month] = key.split("-").map(Number);
  if (!year || !month) return key;
  return new Date(Date.UTC(year, month - 1, 15))
    .toLocaleDateString("es-CO", { month: "short", timeZone: "UTC" })
    .replace(".", "");
}

type NetWorthMonth = {
  key: string;
  total: string;
  assets: string;
  liabilities: string;
  point: NetWorthPoint;
};

/** One currency's net worth per month, oldest first. A month with none is zero. */
function netWorthMonths(months: MonthlyPoint[], currency: string): NetWorthMonth[] {
  return [...months]
    .sort((left, right) => left.key.localeCompare(right.key))
    .map((month) => {
      const worth = month.net_worth.find((entry) => entry.currency === currency);
      const total = worth?.total ?? "0";
      const value = Number(total);
      return {
        key: month.key,
        total,
        assets: worth?.assets ?? "0",
        liabilities: worth?.liabilities ?? "0",
        point: {
          key: month.key,
          label: shortMonth(month.key),
          value: Number.isFinite(value) ? value : 0,
          partial: month.partial,
        },
      };
    });
}

/**
 * The screen's one object: what you are worth, and the line of how you got
 * here. Running a finger along the line puts that month's figure in the big
 * number, and letting go brings back today's.
 */
function NetWorthPanel({
  primary,
  currency,
}: {
  primary: NetWorth | null;
  currency: string;
}) {
  const { data: history } = useQuery(historyQuery(HISTORY_MONTHS));
  const months = history ? netWorthMonths(history.months, currency) : [];
  const [scrub, setScrub] = useState<number | null>(null);
  const from = useStartingPoint(primary?.total ?? "0");

  const latest = months.length - 1;
  const selected = scrub ?? latest;
  const past = scrub !== null && scrub !== latest ? months[scrub] : undefined;
  const closed = months[latest]?.point.partial ? months[latest - 1] : months[latest];

  const shownAssets = past?.assets ?? primary?.assets;
  const shownLiabilities = past?.liabilities ?? primary?.liabilities;

  return (
    <Card lift={false} className="flex flex-col gap-4">
      <div className="flex items-center justify-between gap-3">
        <h2 className="font-medium">Patrimonio</h2>
        <Link
          to="/cuentas"
          aria-label="Patrimonio: ver tus cuentas"
          className={QUIET_LINK}
        >
          Ver cuentas
        </Link>
      </div>

      <div className="flex min-w-0 flex-col gap-1">
        {primary ? (
          <>
            {/* Kept mounted while scrubbing, so letting go does not count the
                balance up from zero all over again. */}
            <span className={past ? "hidden" : undefined}>
              <CountUpMoney
                amount={primary.total}
                from={from}
                currency={primary.currency}
                size="lg"
                tone={signOf(primary.total) < 0 ? "negative" : "plain"}
              />
            </span>
            {past ? (
              <Money
                amount={past.total}
                currency={currency}
                size="lg"
                tone={signOf(past.total) < 0 ? "negative" : "plain"}
              />
            ) : null}
          </>
        ) : (
          <p className="text-faint text-numeral-sm">Sin cuentas</p>
        )}

        <p className="flex min-h-5 flex-col gap-0.5 text-sm">
          {past ? (
            <>
              <span className="text-muted">
                Al cierre de {formatMonthKey(past.key)}
                {past.point.partial ? " (en curso)" : ""}
              </span>
              {months[(scrub ?? 0) - 1] ? (
                <SinceClose
                  now={past.total}
                  then={months[(scrub ?? 0) - 1] as NetWorthMonth}
                  currency={currency}
                />
              ) : null}
            </>
          ) : primary && closed ? (
            <SinceClose now={primary.total} then={closed} currency={currency} />
          ) : null}
        </p>
      </div>

      {months.length > 1 ? (
        <div className="flex flex-col gap-1.5">
          <NetWorthChart
            points={months.map((month) => month.point)}
            selected={selected}
            onSelect={setScrub}
            currency={currency}
            describe={(index) => {
              const month = months[index];
              if (!month) return "";
              return `${formatMonthKey(month.key)}${month.point.partial ? ", en curso" : ""}: ${formatMoney(month.total, currency)}`;
            }}
          />
          <p className="text-xs">
            {primary && months[0] && months[0].key !== months[latest]?.key ? (
              <SinceClose
                now={primary.total}
                then={months[0]}
                currency={currency}
                against={`hace ${months.length - 1} meses`}
              />
            ) : (
              <span className="text-faint">Recorre la línea para ver cada mes.</span>
            )}
          </p>
        </div>
      ) : null}

      {primary && shownAssets !== undefined && shownLiabilities !== undefined ? (
        <div className="mt-auto grid grid-cols-2 gap-3 border-line border-t pt-3">
          <Link
            to="/cuentas"
            className="-mx-2 flex min-w-0 flex-col rounded-lg px-2 py-1.5 transition-colors duration-150 hover:bg-surface-raised"
          >
            <span className="text-faint text-xs">Tienes</span>
            <Money amount={shownAssets} currency={currency} size="sm" />
          </Link>
          <Link
            to="/cuentas"
            className="-mx-2 flex min-w-0 flex-col rounded-lg px-2 py-1.5 text-right transition-colors duration-150 hover:bg-surface-raised"
          >
            <span className="text-faint text-xs">Deuda</span>
            <Money
              amount={shownLiabilities}
              currency={currency}
              size="sm"
              tone={signOf(shownLiabilities) > 0 ? "negative" : "neutral"}
            />
          </Link>
        </div>
      ) : null}
    </Card>
  );
}

/** Today's net worth against the last month that has finished. */
function SinceClose({
  now,
  then,
  currency,
  against,
}: {
  now: string;
  then: NetWorthMonth;
  currency: string;
  /** What `then` is called; the default names its month's close. */
  against?: string;
}) {
  const change = differenceOf(now, then.total);
  const name = formatMonthKey(then.key).split(" ")[0] ?? "";
  const when = against ?? `al cierre de ${name}`;
  if (change === null) return null;

  const sign = signOf(change);
  if (sign === 0) {
    return <span className="text-faint">Igual que {when}</span>;
  }

  const up = sign > 0;
  return (
    <span className="inline-flex flex-wrap items-baseline gap-x-1">
      <span className={up ? "text-incoming" : "text-outgoing"}>{up ? "↑" : "↓"}</span>
      <Money
        amount={magnitude(change)}
        currency={currency}
        size="sm"
        tone={up ? "positive" : "negative"}
        className="text-sm"
      />
      <Percent previous={then.total} current={now} up={up} />
      <span className="text-faint">
        {up ? "más" : "menos"} que {when}
      </span>
    </span>
  );
}

/** What came in and what went out this month, on one scale so they compare. */
function MonthFlow({
  thisMonth,
  lastMonth,
  previousName,
  monthSearch,
  currency,
}: {
  thisMonth: SpendingTotals | null;
  lastMonth: SpendingTotals | null;
  previousName: string;
  /** The month's bounds as Transacciones reads them. */
  monthSearch: { from?: string; to?: string };
  currency: string;
}) {
  const incoming = thisMonth?.incoming ?? "0";
  const outgoing = thisMonth?.outgoing ?? "0";
  // One scale for both bars, so their lengths are the comparison.
  const scale = Math.max(toChartValue(incoming), toChartValue(outgoing));
  const net = differenceOf(incoming, outgoing);

  return (
    <Card lift={false} className="flex flex-col gap-4">
      <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
        <h2 className="font-medium">Este mes</h2>
        {lastMonth ? (
          <p className="text-faint text-xs">Frente a {previousName} a esta altura</p>
        ) : null}
      </div>

      <FlowRow
        label="Ingresos"
        amount={incoming}
        previous={lastMonth?.incoming}
        scale={scale}
        currency={currency}
        fill="bg-incoming"
        tone="positive"
        // `/summary` leaves transfers out of income, so the list behind the
        // figure has to be asked for the same thing or its rows would not add
        // up to it.
        search={{ ...monthSearch, direction: "incoming", transfers: "exclude" }}
      />
      <FlowRow
        label="Gastos"
        amount={outgoing}
        previous={lastMonth?.outgoing}
        scale={scale}
        currency={currency}
        fill="bg-accent"
        tone="negative"
        lowerIsBetter
        search={{ ...monthSearch, direction: "outgoing", transfers: "exclude" }}
      />

      {net !== null ? (
        <p className="mt-auto border-line border-t pt-3 text-muted text-sm">
          {signOf(net) === 0 ? (
            "Entró lo mismo que gastaste."
          ) : (
            <>
              {signOf(net) > 0 ? "Entraron " : "Gastaste "}
              <Money
                amount={magnitude(net)}
                currency={currency}
                size="sm"
                className="text-sm"
              />
              {signOf(net) > 0 ? " más de lo que gastaste." : " más de lo que entró."}
            </>
          )}
        </p>
      ) : null}
    </Card>
  );
}

function FlowRow({
  label,
  amount,
  previous,
  scale,
  currency,
  fill,
  tone,
  lowerIsBetter = false,
  search,
}: {
  label: string;
  amount: string;
  previous?: string;
  scale: number;
  currency: string;
  /** A `bg-*` class for the bar. */
  fill: string;
  tone: "positive" | "negative";
  lowerIsBetter?: boolean;
  search: Record<string, unknown>;
}) {
  const from = useStartingPoint(amount);
  const ratio = scale > 0 ? toChartValue(amount) / scale : 0;

  return (
    <Link
      to="/transacciones"
      search={search}
      className="-mx-2 flex flex-col gap-2 rounded-xl px-2 py-2 transition-colors duration-150 hover:bg-surface-raised"
    >
      <div className="flex flex-wrap items-end justify-between gap-x-3 gap-y-1">
        <div className="flex min-w-0 flex-col">
          <span className="text-muted text-sm">{label}</span>
          <CountUpMoney
            amount={amount}
            from={from}
            currency={currency}
            size="md"
            tone={tone}
          />
        </div>
        <Change
          previous={previous}
          current={amount}
          currency={currency}
          lowerIsBetter={lowerIsBetter}
        />
      </div>
      <div className="h-2 overflow-hidden rounded-full bg-surface-raised">
        <div
          className={cn(
            "h-full origin-left rounded-full transition-transform duration-500 ease-out motion-reduce:transition-none",
            fill,
          )}
          style={{
            transform: `scaleX(${ratio})`,
            // `backwards`, not `both`: once grown, the bar must be free to
            // ease to a new length when a movement lands.
            animation:
              "grow-from-zero 800ms cubic-bezier(0.16, 1, 0.3, 1) 150ms backwards",
          }}
        />
      </div>
    </Link>
  );
}

/**
 * The same change as a share of where it started, beside the amount.
 *
 * Second to the amount, never instead of it: against a quiet month a
 * percentage alone runs to «750%», which alarms more than it informs. From
 * zero there is no percentage at all, so nothing is drawn and the amount
 * stands alone. `percentChange` goes through floats, which is fine for a
 * rounded badge nobody reconciles.
 */
function Percent({
  previous,
  current,
  up,
}: {
  previous: string;
  current: string;
  /** Whether the change is the good kind: it takes the amount's colour. */
  up: boolean;
}) {
  const change = percentChange(previous, current);
  if (change === null) return null;

  const size = Math.abs(change);
  const text = new Intl.NumberFormat("es-CO", {
    style: "percent",
    maximumFractionDigits: size < 10 ? 1 : 0,
  }).format(size / 100);
  return (
    <span className={cn("tabular", up ? "text-incoming" : "text-outgoing")}>
      ({text})
    </span>
  );
}

/**
 * Change against the same figure at this point last month: the amount first,
 * then its percentage. Silent when there is no previous month to compare
 * against, which is the normal state for a new account.
 */
function Change({
  previous,
  current,
  currency,
  lowerIsBetter,
}: {
  previous?: string;
  current: string;
  currency: string;
  lowerIsBetter: boolean;
}) {
  if (previous === undefined) return null;
  const change = differenceOf(current, previous);
  if (change === null) return null;

  const sign = signOf(change);
  // Nothing both months says nothing; the header already names the month.
  if (sign === 0) {
    return signOf(current) === 0 ? null : (
      <span className="text-faint text-xs">Sin cambio</span>
    );
  }

  const good = lowerIsBetter ? sign < 0 : sign > 0;
  return (
    <span className="inline-flex items-baseline gap-1 whitespace-nowrap text-xs">
      <span className={good ? "text-incoming" : "text-outgoing"}>
        {sign > 0 ? "↑" : "↓"}
      </span>
      <Money
        amount={magnitude(change)}
        currency={currency}
        size="sm"
        tone={good ? "positive" : "negative"}
        className="text-xs"
      />
      <Percent previous={previous} current={current} up={good} />
      <span className="text-faint">{sign > 0 ? "más" : "menos"}</span>
    </span>
  );
}

/** One category against last month to the same day: spent more is the bad way. */
function PartChange({
  part,
  currency,
  previousName,
}: {
  part: Part;
  currency: string;
  previousName: string;
}) {
  if (part.previous === undefined) return null;
  if (signOf(part.previous) === 0) {
    return (
      <span className="text-faint">Sin gasto en {previousName} a esta altura</span>
    );
  }
  return (
    <span className="inline-flex flex-wrap items-baseline gap-x-1">
      <Change
        previous={part.previous}
        current={part.amount}
        currency={currency}
        lowerIsBetter
      />
      <span className="text-faint">que en {previousName}</span>
    </span>
  );
}

function CategoryCard({
  groups,
  previousGroups,
  previousName,
  currency,
  monthSearch,
  labels,
}: {
  groups: SummaryGroup[];
  /** The same grouping for last month to date; absent while it loads or fails. */
  previousGroups?: SummaryGroup[];
  previousName: string;
  currency: string;
  /** The month's bounds as Transacciones reads them. */
  monthSearch: { from?: string; to?: string };
  labels: Record<string, string>;
}) {
  const parts = toParts(groups, currency, labels, previousGroups);

  return (
    <Card lift={false} className="flex flex-col gap-5">
      <div className="flex items-center justify-between gap-3">
        <h2 className="font-medium">Gastos por categoría</h2>
        <Link to="/reportes" className={QUIET_LINK}>
          Ver en Reportes
        </Link>
      </div>

      {parts.length === 0 ? (
        <Empty>Todavía no hay gastos registrados este mes.</Empty>
      ) : (
        <CategoryBar
          parts={parts}
          currency={currency}
          hint={
            previousGroups
              ? `Toca un segmento para compararlo con ${previousName}.`
              : undefined
          }
          compare={(part) =>
            previousGroups ? (
              <PartChange part={part} currency={currency} previousName={previousName} />
            ) : null
          }
          // The same spending the bar counts: this month, out, no transfers.
          // The folded tail and the no-merchant bucket have no one category.
          movementsOf={(part) =>
            part.key.startsWith("__")
              ? null
              : {
                  ...monthSearch,
                  category: part.key,
                  direction: "outgoing",
                  transfers: "exclude",
                }
          }
        />
      )}
    </Card>
  );
}

/**
 * Outgoing money per category, largest first, with the tail folded into one
 * part. Shares are computed from the same rounded floats the bar is drawn
 * with, so the percentages always add up to what is on screen.
 */
function toParts(
  groups: SummaryGroup[],
  currency: string,
  labels: Record<string, string>,
  previousGroups?: SummaryGroup[],
): Part[] {
  const before = (key: string) =>
    previousGroups
      ? (previousGroups
          .find((group) => (group.key ?? "__none__") === key)
          ?.totals.find((total) => total.currency === currency)?.outgoing ?? "0")
      : undefined;

  const rows = groups
    .map((group) => {
      const totals = group.totals.find((total) => total.currency === currency);
      // `key` is nullable in the contract: the bucket for movements with no
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
  if (sum === 0) return [];

  const head = rows.slice(0, MAX_SLICES);
  const tail = rows.slice(MAX_SLICES);
  const parts: Part[] = head.map((row) => ({
    key: row.key,
    label: row.label,
    amount: row.amount,
    share: row.value / sum,
    previous: before(row.key),
  }));

  if (tail.length > 0) {
    const rest = tail.reduce((running, row) => running + row.value, 0);
    parts.push({
      key: "__rest__",
      label: `Otros (${tail.length})`,
      // Summed through floats like the shares, and shown as a chart figure
      // rather than a ledger one: every exact amount is a category away.
      amount: rest.toFixed(2),
      share: rest / sum,
      previous: previousGroups
        ? tail
            .reduce((running, row) => running + toChartValue(before(row.key) ?? "0"), 0)
            .toFixed(2)
        : undefined,
    });
  }

  return parts;
}

/**
 * The ids that just arrived, for as long as they stay lit.
 *
 * The first list is what was already there, so nothing lights on arrival.
 * After that, an id the screen has never seen is a movement the bank just
 * sent: it lights, and the figures above it are asked for again so the
 * balance moves to include it.
 */
function useArrivals(transactions: Transaction[]): ReadonlySet<string> {
  const queryClient = useQueryClient();
  const known = useRef<Set<string> | null>(null);
  const [fresh, setFresh] = useState<ReadonlySet<string>>(() => new Set());
  const signature = transactions.map((movement) => movement.id).join(",");

  useEffect(() => {
    const ids = signature ? signature.split(",") : [];
    const before = known.current;
    known.current = new Set([...(before ?? []), ...ids]);
    if (before === null) return;

    const arrived = ids.filter((id) => !before.has(id));
    if (arrived.length === 0) return;

    setFresh(new Set(arrived));
    void queryClient.invalidateQueries({ queryKey: queryKeys.summary });
    void queryClient.invalidateQueries({ queryKey: queryKeys.accounts });
    const timer = window.setTimeout(() => setFresh(new Set()), FRESH_MS);
    return () => window.clearTimeout(timer);
  }, [signature, queryClient]);

  return fresh;
}

/** The first letter of a name, as the row's mark. */
function initialOf(name: string): string {
  return Array.from(name.trim())[0]?.toLocaleUpperCase("es-CO") ?? "";
}

function RecentCard({ transactions }: { transactions: Transaction[] }) {
  const fresh = useArrivals(transactions);

  return (
    <Card lift={false} className="flex flex-col gap-4">
      <div className="flex items-center justify-between gap-3">
        <h2 className="font-medium">Movimientos recientes</h2>
        <Link to="/transacciones" className={QUIET_LINK}>
          Ver todos
        </Link>
      </div>

      {transactions.length === 0 ? (
        <EmptyDiagnosis
          fallback={
            <Empty>
              Cuando tu banco te avise de un movimiento, aparecerá aquí sin que tengas
              que escribir nada.
            </Empty>
          }
        />
      ) : (
        <ul className="-mx-2 flex flex-col">
          {transactions.map((movement) => {
            const title = movement.transfer
              ? transferTitle(movement.transfer, movement.counterparty)
              : (movement.merchant?.display_name ?? movement.counterparty);
            const lit = fresh.has(movement.id);
            const notes = [
              movement.transfer ? "traslado" : null,
              movement.account_id ? null : "sin asignar",
            ].filter(Boolean);

            return (
              <li key={movement.id} className={lit ? "rise" : undefined}>
                <Link
                  to="/transacciones/$transactionId"
                  params={{ transactionId: movement.id }}
                  className="group relative isolate flex items-center gap-3 rounded-xl px-2 py-2 transition-colors duration-150 hover:bg-surface-raised"
                >
                  {lit ? (
                    <span
                      aria-hidden
                      className="pointer-events-none absolute inset-0 -z-10 rounded-xl bg-accent-soft"
                      style={{ animation: `arrive ${FRESH_MS}ms ease-out both` }}
                    />
                  ) : null}
                  <span
                    aria-hidden
                    className={
                      movement.transfer
                        ? "grid size-9 shrink-0 place-items-center rounded-lg bg-violet/12 text-violet"
                        : "grid size-9 shrink-0 place-items-center rounded-lg bg-surface-raised font-medium text-muted text-sm transition-colors duration-150 group-hover:text-text"
                    }
                  >
                    {movement.transfer ? (
                      <ArrowLeftRight className="size-4" />
                    ) : (
                      initialOf(title)
                    )}
                  </span>

                  <div className="min-w-0 flex-1">
                    <p className="truncate text-sm">{title}</p>
                    <p className="truncate text-faint text-xs">
                      {formatDate(movement.occurred_at)}
                      {notes.length > 0 ? `, ${notes.join(", ")}` : null}
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
            );
          })}
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
  /** The one the figures above report in, not whichever account sorts first. */
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
            asignar. Declarar una cuenta es retroactivo: adopta lo que estaba esperando.
          </p>
        </div>
      </Card>
    );
  }

  const mixed = accounts.some((account) => account.currency !== currency);

  return (
    <Card lift={false} className="flex flex-col gap-3">
      <h2 className="font-medium">Cuentas</h2>
      <ul className="-mx-2 grid grid-cols-1 gap-x-6 [&>*]:min-w-0 lg:grid-cols-2">
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
            <li key={account.id}>
              <Link
                to="/transacciones"
                search={{ account: account.id }}
                aria-label={`${account.name}: ver sus movimientos`}
                className="flex min-h-11 items-center justify-between gap-4 rounded-xl px-2 py-2.5 transition-colors duration-150 hover:bg-surface-raised"
              >
                <div className="min-w-0">
                  <p className="break-words text-sm">{account.name}</p>
                  {/* A watched credit is not in the Deuda figure above, so it
                    must not read "Debes" either: `/cuentas` names it the
                    same way. */}
                  <p className="text-faint text-xs">
                    {account.informational ? "Saldo del crédito" : label}
                  </p>
                </div>
                <Money
                  amount={account.balance}
                  currency={account.currency}
                  size="sm"
                  tone={tone}
                  className="shrink-0"
                />
              </Link>
            </li>
          );
        })}
      </ul>
      {mixed ? (
        <p className="text-faint text-xs">
          Los totales de arriba cubren {currency} únicamente.
        </p>
      ) : null}
    </Card>
  );
}

function Empty({ children }: { children: React.ReactNode }) {
  return <p className="py-6 text-center text-muted text-sm">{children}</p>;
}
