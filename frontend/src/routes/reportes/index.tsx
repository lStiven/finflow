/**
 * Reportes — what the money did over a window the reader chooses.
 *
 * The dashboard answers "how am I doing right now" and is fixed to this
 * month. This screen answers the questions that need a *period*: what changed
 * against the stretch before it, where it goes period after period, and which
 * few categories and merchants actually move the total.
 *
 * Two rules shape the whole thing. **One filter row scopes everything below
 * it**, so every figure on screen describes the same window and the numbers
 * always agree with each other. And **one currency drives the screen**,
 * because nothing in the backend converts between two — which is also what
 * makes ranking by amount answerable at all.
 *
 * The shaping lives in `@/reports/shape`; this file is composition.
 */

import { useSuspenseQuery } from "@tanstack/react-query";
import { createFileRoute, Link, redirect, useNavigate } from "@tanstack/react-router";
import {
  ArrowDownLeft,
  ArrowLeftRight,
  ArrowUpRight,
  CalendarDays,
  CalendarRange,
  MousePointerClick,
  Receipt,
  Scale,
} from "lucide-react";
import type { ReactNode } from "react";
import {
  type Account,
  accountsQuery,
  type Currency,
  categoriesQuery,
  summaryQuery,
  type Transaction,
  transactionsQuery,
  trendQuery,
} from "@/api/queries";
import { AppShell } from "@/components/AppShell";
import { CountUpMoney } from "@/components/CountUpMoney";
import { Columns } from "@/components/charts/Columns";
import { bandPalette } from "@/components/charts/palette";
import { RankedBars } from "@/components/charts/RankedBars";
import { Money } from "@/components/Money";
import { PageHeader, type PageHelp } from "@/components/PageHeader";
import { Card } from "@/components/ui/Card";
import { StatTile } from "@/components/ui/StatTile";
import { cn } from "@/lib/cn";
import { formatDate } from "@/lib/dates";
import { percentChange } from "@/lib/money";
import { intervalFor, PRESETS, type PresetId, resolveRange } from "@/lib/periods";
import { categoryLabels, labelFrom } from "@/merchants/categories";
import {
  bandsOf,
  bucketsOf,
  cashflowSeries,
  describePrevious,
  drilldown,
  elapsedDays,
  figures,
  netOf,
  perDay,
  rankedRows,
  type View,
  WEEKDAYS,
  weekdaySeries,
} from "@/reports/shape";

/** Six is where a ranked list stops being scannable; the rest becomes "Otros". */
const TOP_ROWS = 6;
/** Five bands plus a remainder — the ceiling a stack stays readable at. */
const TREND_BANDS = 5;
const BIGGEST = 5;

/* ------------------------------------------------------------------ route */

type ReportSearch = {
  periodo?: PresetId;
  from?: string;
  to?: string;
  cuenta?: string;
  moneda?: string;
};

function text(value: unknown): string | undefined {
  return typeof value === "string" && value.trim() !== "" ? value : undefined;
}

function preset(value: unknown): PresetId | undefined {
  return PRESETS.some((entry) => entry.id === value) ? (value as PresetId) : undefined;
}

export const Route = createFileRoute("/reportes/")({
  beforeLoad: ({ context }) => {
    if (!context.session) throw redirect({ to: "/login" });
  },
  validateSearch: (raw: Record<string, unknown>): ReportSearch => ({
    periodo: preset(raw.periodo),
    from: text(raw.from),
    to: text(raw.to),
    cuenta: text(raw.cuenta),
    moneda: text(raw.moneda),
  }),
  loaderDeps: ({ search }) => search,
  loader: async ({ context, deps }) => {
    /*
     * The accounts come first and alone because the currency is read off
     * them, and the currency is a parameter of every other call — a report
     * ranked by amount cannot be asked for without one. It is almost always a
     * cache hit: every other screen loads the same query.
     */
    const accounts = await context.queryClient.query({
      ...accountsQuery("all"),
      staleTime: "static",
    });
    const [totals, categories, cashflow, overTime, merchants, weekday, biggest] =
      queriesFor(resolve(deps, accounts));

    /*
     * Listed one by one rather than mapped: these are seven differently
     * shaped queries and mapping collapses the tuple into a union the client
     * cannot be handed. `staleTime: "static"` so coming back paints from
     * cache instead of blocking the route on seven refetches.
     */
    const cached = { staleTime: "static" } as const;
    return Promise.all([
      context.queryClient.query({ ...totals, ...cached }),
      context.queryClient.query({ ...categories, ...cached }),
      context.queryClient.query({ ...cashflow, ...cached }),
      context.queryClient.query({ ...overTime, ...cached }),
      context.queryClient.query({ ...merchants, ...cached }),
      context.queryClient.query({ ...weekday, ...cached }),
      context.queryClient.query({ ...biggest, ...cached }),
      context.queryClient.query(categoriesQuery),
    ]);
  },
  component: ReportsScreen,
});

/** Every currency this person actually holds, in the order net worth reports. */
function heldCurrencies(accounts: { net_worth: { currency: string }[] }): string[] {
  return accounts.net_worth.map((entry) => entry.currency);
}

function resolve(
  search: ReportSearch,
  accounts: { net_worth: { currency: string }[] },
): View {
  // A hand-edited or unusable range falls back rather than charting an
  // invented window: an empty screen with no explanation is the worse answer.
  const range = resolveRange(search.periodo ?? "mes", {
    from: search.from,
    to: search.to,
  }) ??
    resolveRange("mes") ?? { from: 0, to: 86_400 };

  const held = heldCurrencies(accounts);
  const currency = (
    search.moneda && held.includes(search.moneda) ? search.moneda : (held[0] ?? "COP")
  ) as Currency;

  return { range, currency, accountId: search.cuenta, interval: intervalFor(range) };
}

/**
 * The seven reads behind the screen, in one place so the loader prefetches
 * exactly what the component subscribes to.
 *
 * Every one carries the same window, currency and account, which is what
 * makes the figures agree with each other. `transfers: "exclude"` is on all
 * of them for the same reason: money moved between the owner's own accounts
 * is neither spending nor income, and counting it would report a card payment
 * as the period's largest expense and again as income on the card.
 */
function queriesFor(view: View) {
  const { range, currency, accountId, interval } = view;
  const scope = {
    from: range.from,
    to: range.to,
    currency,
    account_id: accountId,
    transfers: "exclude" as const,
  };
  const spending = { ...scope, direction: "outgoing" as const };

  return [
    // Grouped by month only to reach `totals` and `previous_totals`: it is
    // the one grouping the API answers without reading the merchant list.
    summaryQuery("month", { ...scope, compare: true }),
    summaryQuery("category", {
      ...spending,
      order: "amount",
      top: TOP_ROWS,
      compare: true,
    }),
    trendQuery({ ...scope, interval, dimension: "none" }),
    trendQuery({
      ...spending,
      interval,
      dimension: "category",
      series: TREND_BANDS,
      order: "amount",
    }),
    summaryQuery("merchant", { ...spending, order: "amount", top: TOP_ROWS }),
    summaryQuery("weekday", spending),
    transactionsQuery({ ...spending, sort: "amount", limit: BIGGEST }),
  ] as const;
}

function ReportsScreen() {
  const search = Route.useSearch();
  const navigate = useNavigate({ from: Route.fullPath });
  const { data: accounts } = useSuspenseQuery(accountsQuery("all"));
  const view = resolve(search, accounts);
  const [totals, categories, cashflow, overTime, merchants, weekday, biggest] =
    queriesFor(view);

  const { data: period } = useSuspenseQuery(totals);
  const { data: byCategory } = useSuspenseQuery(categories);
  const { data: flow } = useSuspenseQuery(cashflow);
  const { data: trend } = useSuspenseQuery(overTime);
  const { data: byMerchant } = useSuspenseQuery(merchants);
  const { data: byWeekday } = useSuspenseQuery(weekday);
  const { data: largest } = useSuspenseQuery(biggest);
  /*
   * Every category on this screen arrives as a value and nothing else — a
   * summary bucket's label *is* its key — so one this person wrote would read
   * as `custom:mascotas` in a legend without their own name for it.
   */
  const { data: vocabulary } = useSuspenseQuery(categoriesQuery);
  const labels = categoryLabels(vocabulary.categories);

  const { currency, range, interval } = view;
  const now = figures(period.totals, currency);
  const before = figures(period.previous_totals, currency);

  /*
   * One palette for the whole screen, built from the category ranking. The
   * stacked run looks its bands up in the same map, so a category is the same
   * colour in both charts — colour follows the entity, never the row it
   * happens to occupy in whichever chart is drawing.
   */
  const hues = bandPalette(byCategory.groups.map((group) => group.key));
  // Built once and handed to both the legend and the chart: two calls would
  // rank and colour the same bands twice on every render.
  const bands = bandsOf(trend, hues, (value) => labelFrom(labels, value));

  return (
    <AppShell>
      <div className="flex flex-col gap-6">
        <PageHeader
          title="Reportes"
          lead="En qué se va tu plata, y cómo cambia periodo a periodo."
          help={HELP}
        />

        <Filters
          search={search}
          view={view}
          accounts={accounts.accounts}
          currencies={heldCurrencies(accounts)}
          onChange={(next) =>
            navigate({ search: (old: ReportSearch) => ({ ...old, ...next }) })
          }
        />

        {now === null ? (
          <NothingHere />
        ) : (
          <>
            <section
              aria-label="Cifras del periodo"
              className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4"
            >
              <StatTile
                label="Gastos"
                icon={ArrowUpRight}
                hue="accent"
                to={{
                  to: "/transacciones",
                  search: drilldown(view, { direction: "outgoing" }),
                }}
                caption={
                  <Delta
                    previous={before?.outgoing}
                    current={now.outgoing}
                    lowerIsBetter
                  />
                }
              >
                <CountUpMoney
                  amount={now.outgoing}
                  currency={currency}
                  size="md"
                  tone="negative"
                />
              </StatTile>

              <StatTile
                label="Ingresos"
                icon={ArrowDownLeft}
                hue="green"
                to={{
                  to: "/transacciones",
                  search: drilldown(view, { direction: "incoming" }),
                }}
                caption={<Delta previous={before?.incoming} current={now.incoming} />}
              >
                <CountUpMoney
                  amount={now.incoming}
                  currency={currency}
                  size="md"
                  tone="positive"
                />
              </StatTile>

              <StatTile
                label="Balance"
                icon={Scale}
                hue="cyan"
                caption={
                  <span className="text-faint">
                    {netOf(now) >= 0
                      ? "Te quedó a favor"
                      : "Gastaste más de lo que entró"}
                  </span>
                }
              >
                <CountUpMoney
                  amount={now.net}
                  currency={currency}
                  size="md"
                  signed
                  tone={netOf(now) >= 0 ? "positive" : "negative"}
                />
              </StatTile>

              <StatTile
                label="Gasto diario"
                icon={CalendarDays}
                hue="violet"
                caption={
                  <span className="text-faint">
                    Promedio en {elapsedDays(range)} días
                  </span>
                }
              >
                <CountUpMoney
                  amount={perDay(now.outgoing, range)}
                  currency={currency}
                  size="md"
                />
              </StatTile>
            </section>

            <div className="grid gap-4 lg:grid-cols-2">
              <Panel
                title="Flujo de caja"
                hint="Lo que entró contra lo que salió, periodo a periodo."
                glow="cyan"
              >
                <Legend
                  entries={[
                    { label: "Ingresos", tone: "bg-incoming" },
                    { label: "Gastos", tone: "bg-accent" },
                  ]}
                />
                <Columns
                  mode="grouped"
                  buckets={bucketsOf(flow, interval)}
                  series={cashflowSeries(flow)}
                  currency={currency}
                  caption="Ingresos y gastos por periodo"
                />
              </Panel>

              <Panel
                title="Gastos por categoría"
                hint={
                  before === null
                    ? "En qué se fue la plata este periodo."
                    : `Comparado con ${describePrevious(byCategory)}.`
                }
                glow="accent"
              >
                <RankedBars
                  rows={rankedRows(byCategory, {
                    view,
                    hues,
                    label: (group) =>
                      group.key === null
                        ? "Sin comercio"
                        : labelFrom(labels, group.key),
                    link: (group) =>
                      group.key === null ? undefined : { category: group.key },
                  })}
                  currency={currency}
                  empty="No hay gastos registrados en este periodo."
                />
              </Panel>
            </div>

            <Panel
              title="En qué se va, periodo a periodo"
              hint="Cada banda es una categoría. Se ordenan una sola vez sobre todo el rango, así que la pila es comparable de una barra a otra."
              glow="violet"
            >
              <Legend
                entries={bands.map((band) => ({
                  key: band.key,
                  label: band.label,
                  tone: band.tone,
                }))}
              />
              <Columns
                mode="stacked"
                buckets={bucketsOf(trend, interval)}
                series={bands}
                currency={currency}
                caption="Gasto por categoría y periodo"
              />
            </Panel>

            <div className="grid gap-4 lg:grid-cols-2">
              <Panel
                title="Dónde más gastas"
                hint="Los comercios que más pesan en el periodo."
                glow="accent"
              >
                <RankedBars
                  rows={rankedRows(byMerchant, {
                    view,
                    // Merchants appear nowhere else on the screen, so length
                    // carries the whole story and one hue is enough.
                    hues: null,
                    label: (group) =>
                      group.key === null ? "Sin comercio" : group.label,
                    link: (group) =>
                      group.key === null ? undefined : { merchant: group.key },
                  })}
                  currency={currency}
                  empty="Todavía no hay comercios reconocidos en este periodo."
                />
              </Panel>

              <Panel
                title="Por día de la semana"
                hint="Cuándo se te va la plata."
                glow="cyan"
              >
                <Columns
                  mode="grouped"
                  buckets={WEEKDAYS.map((day) => ({
                    key: day.key,
                    label: day.short,
                    full: day.full,
                  }))}
                  series={weekdaySeries(byWeekday, currency)}
                  currency={currency}
                  caption="Gasto por día de la semana"
                />
              </Panel>
            </div>

            <Panel
              title="Tus mayores gastos"
              hint="Los movimientos más grandes del periodo, de uno en uno."
              glow="accent"
            >
              <Biggest transactions={largest.transactions} />
            </Panel>
          </>
        )}
      </div>
    </AppShell>
  );
}

const HELP: PageHelp = {
  id: "reportes",
  points: [
    {
      icon: MousePointerClick,
      title: "Toca para ver el detalle",
      body: "Las cifras, las categorías y los comercios abren los movimientos que las forman.",
    },
    {
      icon: CalendarRange,
      title: "Elige el periodo",
      body: "Arriba cambias el rango. Uno que aún no termina se ve más tenue y dice «en curso».",
    },
    {
      icon: ArrowLeftRight,
      title: "Sin traslados",
      body: "Pagar tu tarjeta desde otra cuenta tuya no aparece como gasto.",
    },
  ],
};

/* ------------------------------------------------------------------ parts */

function Panel({
  title,
  hint,
  glow,
  children,
}: {
  title: string;
  hint: string;
  glow: "accent" | "cyan" | "violet";
  children: ReactNode;
}) {
  return (
    <Card glow={glow} lift={false} className="flex flex-col gap-4">
      <div>
        <h2 className="font-medium">{title}</h2>
        <p className="mt-0.5 text-faint text-xs">{hint}</p>
      </div>
      {children}
    </Card>
  );
}

/**
 * Identity, said in text as well as in colour.
 *
 * Always present once there are two bands: colour alone is not an identity
 * channel everybody has.
 */
function Legend({
  entries,
}: {
  entries: { key?: string; label: string; tone: string }[];
}) {
  if (entries.length < 2) return null;

  return (
    <ul className="-mt-1 flex flex-wrap gap-x-4 gap-y-1.5">
      {entries.map((entry) => (
        // Keyed by the band's own key where there is one: two bands can share
        // a label — a merchant really called "Otros" — and a duplicate React
        // key silently drops one of them.
        <li
          key={entry.key ?? entry.label}
          className="flex items-center gap-1.5 text-muted text-xs"
        >
          <span
            aria-hidden
            className={cn("size-2 shrink-0 rounded-full", entry.tone)}
          />
          {entry.label}
        </li>
      ))}
    </ul>
  );
}

function Filters({
  search,
  view,
  accounts,
  currencies,
  onChange,
}: {
  search: ReportSearch;
  view: View;
  accounts: Account[];
  currencies: string[];
  onChange: (next: Partial<ReportSearch>) => void;
}) {
  const active = search.periodo ?? "mes";

  return (
    <div className="flex flex-col gap-3 rounded-card border border-line bg-surface p-3">
      <div className="flex flex-wrap items-center gap-1.5">
        {PRESETS.map((entry) => (
          <button
            key={entry.id}
            type="button"
            onClick={() =>
              onChange(
                entry.id === "custom"
                  ? { periodo: "custom" }
                  : { periodo: entry.id, from: undefined, to: undefined },
              )
            }
            className={cn(
              "rounded-lg border px-3 py-1.5 text-xs transition-colors duration-150",
              active === entry.id
                ? "border-accent/50 bg-accent/12 text-text"
                : "border-line text-muted hover:border-accent/30 hover:text-text",
            )}
          >
            {entry.label}
          </button>
        ))}
      </div>

      {active === "custom" ? (
        <div className="flex flex-wrap items-end gap-3">
          <label className="flex flex-col gap-1 text-muted text-xs">
            Desde
            <input
              type="date"
              value={search.from ?? ""}
              onChange={(event) => onChange({ from: event.target.value })}
              className="rounded-lg border border-line bg-ink px-3 py-2 text-sm text-text focus:border-accent focus:outline-none"
            />
          </label>
          <label className="flex flex-col gap-1 text-muted text-xs">
            Hasta
            <input
              type="date"
              value={search.to ?? ""}
              onChange={(event) => onChange({ to: event.target.value })}
              className="rounded-lg border border-line bg-ink px-3 py-2 text-sm text-text focus:border-accent focus:outline-none"
            />
          </label>
          {/*
           * Until both are picked there is no window to chart, so the screen
           * falls back to this month — and says so, rather than letting the
           * dates below quietly disagree with the chip above.
           */}
          {!search.from || !search.to ? (
            <p className="text-faint text-xs">
              Elige las dos fechas. Mientras tanto se muestra este mes.
            </p>
          ) : null}
        </div>
      ) : null}

      <div className="flex flex-wrap items-center gap-3 border-line/70 border-t pt-3">
        <p className="text-faint text-xs">
          {formatDate(view.range.from)} — {formatDate(view.range.to - 1)}
        </p>

        <div className="ml-auto flex flex-wrap items-center gap-2">
          {accounts.length > 0 ? (
            <select
              aria-label="Cuenta"
              value={search.cuenta ?? ""}
              onChange={(event) =>
                onChange({ cuenta: event.target.value || undefined })
              }
              className="rounded-lg border border-line bg-ink px-3 py-1.5 text-muted text-xs focus:border-accent focus:outline-none"
            >
              <option value="">Todas las cuentas</option>
              {accounts.map((account) => (
                <option key={account.id} value={account.id}>
                  {account.name}
                </option>
              ))}
            </select>
          ) : null}

          {/*
           * Only worth a control when there is a choice to make. Nothing here
           * converts between currencies, so this switches which one the whole
           * screen reports — it never adds two together.
           */}
          {currencies.length > 1 ? (
            <select
              aria-label="Moneda"
              value={view.currency}
              onChange={(event) => onChange({ moneda: event.target.value })}
              className="rounded-lg border border-line bg-ink px-3 py-1.5 text-muted text-xs focus:border-accent focus:outline-none"
            >
              {currencies.map((code) => (
                <option key={code} value={code}>
                  {code}
                </option>
              ))}
            </select>
          ) : null}
        </div>
      </div>
    </div>
  );
}

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
  if (rounded === 0) return <span className="text-faint">Igual que antes</span>;

  const good = lowerIsBetter ? rounded < 0 : rounded > 0;
  return (
    <span className={good ? "text-incoming" : "text-outgoing"}>
      {rounded > 0 ? "↑" : "↓"} {Math.abs(rounded)}%{" "}
      <span className="text-faint">vs periodo anterior</span>
    </span>
  );
}

function Biggest({ transactions }: { transactions: Transaction[] }) {
  if (transactions.length === 0) {
    return (
      <p className="py-6 text-center text-muted text-sm">
        No hay gastos registrados en este periodo.
      </p>
    );
  }

  return (
    <ol className="-mx-2 flex flex-col">
      {transactions.map((movement, index) => (
        <li key={movement.id}>
          <Link
            to="/transacciones/$transactionId"
            params={{ transactionId: movement.id }}
            className="flex items-center gap-3 rounded-xl px-2 py-2 transition-colors duration-150 hover:bg-surface-raised"
          >
            <span
              aria-hidden
              className="grid size-7 shrink-0 place-items-center rounded-lg bg-surface-raised text-faint text-xs tabular"
            >
              {index + 1}
            </span>
            <div className="min-w-0 flex-1">
              <p className="truncate text-sm">
                {movement.merchant?.display_name ?? movement.counterparty}
              </p>
              <p className="truncate text-faint text-xs">
                {formatDate(movement.occurred_at)}
              </p>
            </div>
            <Money amount={movement.amount} currency={movement.currency} size="sm" />
          </Link>
        </li>
      ))}
    </ol>
  );
}

function NothingHere() {
  return (
    <Card glow="violet" lift={false} className="flex flex-col items-start gap-3">
      <Receipt className="size-5 text-violet" aria-hidden />
      <div>
        <h2 className="font-medium">No hay nada que reportar en este periodo</h2>
        <p className="mt-1 text-muted text-sm">
          Cambia el periodo arriba, o conecta tu banco para que tus movimientos empiecen
          a llegar solos. También puedes registrar un gasto a mano.
        </p>
      </div>
      <div className="flex flex-wrap gap-2">
        <Link
          to="/conectar"
          className="rounded-lg border border-line px-3 py-1.5 text-muted text-xs transition-colors duration-150 hover:border-accent/40 hover:text-text"
        >
          Conectar mi banco
        </Link>
        <Link
          to="/transacciones/nueva"
          className="rounded-lg border border-line px-3 py-1.5 text-muted text-xs transition-colors duration-150 hover:border-accent/40 hover:text-text"
        >
          Registrar un gasto
        </Link>
      </div>
    </Card>
  );
}
