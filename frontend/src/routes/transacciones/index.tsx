import { useQuery, useSuspenseQuery } from "@tanstack/react-query";
import { createFileRoute, Link, redirect, useNavigate } from "@tanstack/react-router";
import {
  ArrowLeftRight,
  CalendarDays,
  ChevronLeft,
  ChevronRight,
  Mail,
  Pencil,
  Plus,
  Search,
  SlidersHorizontal,
  Unlink,
  X,
} from "lucide-react";
import { type ReactNode, useState } from "react";
import { originLabel } from "@/accounts/kinds";
import {
  type Account,
  accountsQuery,
  type Currency,
  categoriesQuery,
  financialCatalogQuery,
  merchantsForFilterQuery,
  type SpendingTotals,
  type SummaryOptions,
  summaryQuery,
  type Transaction,
  type TransactionFilters,
  type TransactionOrigin,
  transactionsQuery,
} from "@/api/queries";
import { AppShell } from "@/components/AppShell";
import { ExportButton } from "@/components/ExportDialog";
import { Money } from "@/components/Money";
import { PageHeader, type PageHelp } from "@/components/PageHeader";
import { Button, buttonClass } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { Notice } from "@/components/ui/Notice";
import { type Option, Select } from "@/components/ui/Select";
import { storyOf } from "@/guides/stories";
import { cn } from "@/lib/cn";
import {
  currentMonthKey,
  dayStringOf,
  formatDayMonth,
  formatMonthKey,
  fromLocalInput,
  monthDayRange,
  monthKeyOf,
  previousMonthKey,
} from "@/lib/dates";
import { transferTitle } from "@/lib/transfers";
import {
  categoryLabel,
  categoryLabels,
  isUncategorized,
  labelFrom,
} from "@/merchants/categories";
import { categoryIcon, TRANSFER_ICON } from "@/merchants/categoryIcons";
import { EmptyDiagnosis } from "@/onboarding/EmptyDiagnosis";

const PAGE_SIZE = 25;

/**
 * Direction is a real server filter, so the count beside the list is the
 * count of what the tab selected. Filtering a loaded page in the browser
 * instead would show three movements under a total of forty.
 */
/**
 * What «Tipo» offers. Money in and money out leave transfers out, so the three
 * never overlap and each adds up to what the summary calls the same thing.
 */
const KINDS = [
  {
    value: "incoming",
    label: "Ingresos",
    patch: { direction: "incoming", transfers: "exclude" },
  },
  {
    value: "outgoing",
    label: "Gastos",
    patch: { direction: "outgoing", transfers: "exclude" },
  },
  {
    value: "transfers",
    label: "Traslados",
    patch: { direction: undefined, transfers: "only" },
  },
] as const;

/**
 * The kind a URL reads as. One that arrives with a direction and transfers
 * included (an older link) still reads as its direction: the list is that
 * direction, plus the transfers the link asked for.
 */
function kindOf(search: TransactionSearch): string {
  if (search.transfers === "only") return "transfers";
  return search.direction ?? "";
}

function kindPatch(value: string): Partial<TransactionSearch> {
  return (
    KINDS.find((kind) => kind.value === value)?.patch ?? {
      direction: undefined,
      transfers: undefined,
    }
  );
}

/** How many months back «Mes» offers, the running one included. */
const MONTHS_OFFERED = 12;
/** The value «Mes» holds while the dates came from «Desde» and «Hasta». */
const CUSTOM_RANGE = "custom";

/** The month the dates cover exactly, a custom range, or nothing at all. */
function monthOf(search: TransactionSearch): string {
  if (!search.from && !search.to) return "";
  const key = search.from?.slice(0, 7) ?? "";
  const whole = monthDayRange(key);
  return whole && whole.from === search.from && whole.to === search.to
    ? key
    : CUSTOM_RANGE;
}

function monthPatch(value: string): Partial<TransactionSearch> {
  if (value === CUSTOM_RANGE) return {};
  const whole = value ? monthDayRange(value) : null;
  return { from: whole?.from, to: whole?.to };
}

function monthOptions(search: TransactionSearch): Option[] {
  const options: Option[] = [];
  let key = currentMonthKey();
  for (let at = 0; at < MONTHS_OFFERED; at += 1) {
    const name = formatMonthKey(key);
    options.push({ value: key, label: name.charAt(0).toUpperCase() + name.slice(1) });
    key = previousMonthKey(key);
  }
  const chosen = monthOf(search);
  if (chosen === CUSTOM_RANGE) {
    options.unshift({ value: CUSTOM_RANGE, label: "Fechas a medida" });
  } else if (chosen && !options.some((option) => option.value === chosen)) {
    // A month older than the offer, arriving by URL, still names itself.
    const name = formatMonthKey(chosen);
    options.push({
      value: chosen,
      label: name.charAt(0).toUpperCase() + name.slice(1),
    });
  }
  return options;
}

/**
 * The filters live in the URL, not in component state.
 *
 * A filtered list is something people send to themselves and come back to,
 * and the back button has to undo a filter rather than leave the screen. It
 * also means the loader can fetch exactly what will be rendered.
 */
export type TransactionSearch = {
  search?: string;
  account?: string;
  category?: string;
  merchant?: string;
  origin?: TransactionOrigin;
  direction?: "incoming" | "outgoing";
  unassigned?: boolean;
  /**
   * Carried in the URL so a figure and the list behind it can agree: the
   * dashboard's tiles report spending, which leaves transfers out, and they
   * link here asking for the same thing.
   */
  transfers?: "exclude" | "only";
  from?: string;
  to?: string;
  /** Largest first instead of newest first. Absent is by date. */
  sort?: "amount";
  /**
   * Optional so a plain `<Link to="/transacciones">` needs no search object;
   * every read below defaults it to the first page.
   */
  page?: number;
};

function text(value: unknown): string | undefined {
  return typeof value === "string" && value.trim() !== "" ? value : undefined;
}

/**
 * Every origin the API knows, and the guard the URL is read through.
 *
 * Listed once here rather than inline at each of the two places that used to
 * spell out two of them: the dropdown is built from the catalog, so a value it
 * offers and this refuses is an option that silently does nothing. Adding a
 * member to the Python enum breaks this build, which is the point.
 */
const ORIGINS: readonly TransactionOrigin[] = [
  "bank_alert",
  "manual",
  "accrual",
  "scheduled",
];

function asOrigin(value: unknown): TransactionOrigin | undefined {
  return ORIGINS.find((origin) => origin === value);
}

export const Route = createFileRoute("/transacciones/")({
  beforeLoad: ({ context }) => {
    if (!context.session) throw redirect({ to: "/login" });
  },
  validateSearch: (raw: Record<string, unknown>): TransactionSearch => ({
    search: text(raw.search),
    account: text(raw.account),
    category: text(raw.category),
    merchant: text(raw.merchant),
    origin: asOrigin(raw.origin),
    direction:
      raw.direction === "incoming" || raw.direction === "outgoing"
        ? raw.direction
        : undefined,
    unassigned: raw.unassigned === true || raw.unassigned === "true" ? true : undefined,
    transfers:
      raw.transfers === "exclude" || raw.transfers === "only"
        ? raw.transfers
        : undefined,
    from: text(raw.from),
    to: text(raw.to),
    sort: raw.sort === "amount" ? "amount" : undefined,
    page: raw.page === undefined ? undefined : Math.max(1, Number(raw.page) || 1),
  }),
  loaderDeps: ({ search }) => search,
  loader: async ({ context, deps }) => {
    // First, because sorting by amount needs the currency it ranks in.
    const accounts = await context.queryClient.query({
      ...accountsQuery("all"),
      staleTime: "static",
    });
    const currency = rankingCurrency(accounts);
    return Promise.all([
      context.queryClient.query({
        ...transactionsQuery(toFilters(deps, currency)),
        staleTime: "static",
      }),
      // The month headings' totals. Optional: failing costs the totals only.
      monthTotalsShown(deps)
        ? context.queryClient
            .query({
              ...summaryQuery("month", summaryFilters(deps)),
              staleTime: "static",
            })
            .catch(() => null)
        : null,
      context.queryClient.query({ ...merchantsForFilterQuery, staleTime: "static" }),
      context.queryClient.query(categoriesQuery),
      context.queryClient.query({ ...financialCatalogQuery, staleTime: "static" }),
    ]);
  },
  component: TransactionsScreen,
});

/**
 * URL search into API query.
 *
 * `undefined` entries are dropped by the serializer, which is what keeps an
 * enum parameter from being sent empty — that is a 422, never "no filter".
 */
/**
 * Midnight starting the day *after* the one chosen.
 *
 * The endpoint's upper bound is exclusive, so naming the chosen day's last
 * minute would silently drop anything that happened inside it — and "hasta el
 * 15" has to include the 15th.
 */
function dayAfter(day: string): number | undefined {
  const start = fromLocalInput(`${day}T00:00`);
  return start === null ? undefined : start + 86_400;
}

function pageOf(search: TransactionSearch): number {
  return search.page ?? 1;
}

/**
 * The currency a largest-first list ranks in: the one the net worth leads
 * with, as on the dashboard. The API refuses to rank across currencies.
 */
function rankingCurrency(accounts: { net_worth: { currency: string }[] }): Currency {
  return (accounts.net_worth[0]?.currency ?? "COP") as Currency;
}

function toFilters(search: TransactionSearch, currency: Currency): TransactionFilters {
  return {
    limit: PAGE_SIZE,
    offset: (pageOf(search) - 1) * PAGE_SIZE,
    ...(search.sort === "amount" ? { sort: "amount", currency } : {}),
    ...summaryFilters(search),
  };
}

/**
 * The list's filters without its paging: what the month totals are asked
 * with, so a heading's figure covers exactly the movements under it.
 */
function summaryFilters(search: TransactionSearch): SummaryOptions {
  return {
    search: search.search,
    account_id: search.account,
    category: search.category,
    merchant_id: search.merchant,
    origin: search.origin,
    direction: search.direction,
    unassigned: search.unassigned,
    transfers: search.transfers,
    from: search.from
      ? (fromLocalInput(`${search.from}T00:00`) ?? undefined)
      : undefined,
    to: search.to ? dayAfter(search.to) : undefined,
  };
}

/**
 * Month totals only where the list is grouped by month, and never for
 * transfers alone: a transfer is neither money in nor money out, so «entró» and
 * «salió» would both be zero over a list of them.
 */
function monthTotalsShown(search: TransactionSearch): boolean {
  return search.sort !== "amount" && search.transfers !== "only";
}

function TransactionsScreen() {
  const search = Route.useSearch();
  const navigate = useNavigate({ from: Route.fullPath });

  const { data: accounts } = useSuspenseQuery(accountsQuery("all"));
  const currency = rankingCurrency(accounts);
  const { data: page } = useSuspenseQuery(
    transactionsQuery(toFilters(search, currency)),
  );
  const showTotals = monthTotalsShown(search);
  const { data: byMonth } = useQuery({
    ...summaryQuery("month", summaryFilters(search)),
    enabled: showTotals,
  });
  const { data: merchants } = useSuspenseQuery(merchantsForFilterQuery);
  const { data: categories } = useSuspenseQuery(categoriesQuery);
  const labels = categoryLabels(categories.categories);
  const { data: catalog } = useSuspenseQuery(financialCatalogQuery);

  const [panelOpen, setPanelOpen] = useState(false);

  /**
   * Every filter change resets to the first page — page 4 of a new filter is
   * empty. Pushed rather than replaced, so back undoes the filter, which is
   * what putting this state in the URL was for.
   */
  function apply(patch: Partial<TransactionSearch>) {
    void navigate({
      search: (previous) => ({ ...previous, ...patch, page: undefined }),
    });
  }

  const active = countActive(search);
  const current = pageOf(search);
  const lastPage = Math.max(1, Math.ceil(page.total / PAGE_SIZE));

  return (
    <AppShell>
      <div className="flex flex-col gap-6">
        <PageHeader
          title="Transacciones"
          lead={
            <>
              {page.total === 0
                ? "Nada todavía"
                : `${page.total} ${page.total === 1 ? "movimiento" : "movimientos"}`}
              {active > 0 ? " con los filtros aplicados" : ""}
            </>
          }
          help={HELP}
          actions={
            <>
              {/* Starts from what the list has selected; the dialog decides. */}
              <ExportButton
                selection={search}
                accounts={accounts.accounts}
                categories={categories.categories}
                merchantName={
                  merchants.merchants.find(
                    (merchant) => merchant.id === search.merchant,
                  )?.display_name
                }
                originName={
                  search.origin
                    ? originLabel(
                        search.origin,
                        catalog.transaction_origins.find(
                          (option) => option.value === search.origin,
                        )?.label ?? search.origin,
                      )
                    : undefined
                }
              />
              <Link to="/transacciones/nueva" className={buttonClass("primary")}>
                <Plus className="size-4" />
                Agregar
              </Link>
            </>
          }
        />

        {/*
          Keyed on the URL: the box holds a local draft until submitted, and
          without this "Limpiar" or the back button would clear the filter
          while leaving the old text sitting in the field.
        */}
        {/* One toolbar: on a desktop the search, the direction and the
            filters sit on a single line above the ledger; on a phone they
            wrap, the search first. */}
        <div className="flex flex-wrap items-center gap-2">
          <div className="w-full min-w-0 lg:w-auto lg:flex-1">
            <SearchBar
              key={search.search ?? ""}
              value={search.search ?? ""}
              onSubmit={(value) => apply({ search: value })}
            />
          </div>

          <div className="grid w-full grid-cols-[minmax(0,1.25fr)_minmax(0,1fr)] gap-2 lg:flex lg:w-auto">
            <Select
              variant="toolbar"
              label="Mes"
              icon={CalendarDays}
              placeholder="Todos los meses"
              value={monthOf(search)}
              onChange={(event) => apply(monthPatch(event.target.value))}
              options={monthOptions(search)}
              className="min-w-0 lg:w-56"
            />
            <Select
              variant="toolbar"
              label="Tipo"
              placeholder="Todos los tipos"
              value={kindOf(search)}
              onChange={(event) => apply(kindPatch(event.target.value))}
              options={KINDS.map((kind) => ({ value: kind.value, label: kind.label }))}
              className="min-w-0 lg:w-44"
            />
          </div>

          <Button
            variant="ghost"
            className="min-h-11 rounded-xl px-4 text-sm"
            onClick={() => setPanelOpen((open) => !open)}
            aria-expanded={panelOpen}
          >
            <SlidersHorizontal className="size-3.5" />
            Filtros
            {active > 0 ? (
              <span className="rounded-full bg-accent-solid px-1.5 text-[0.625rem] text-accent-ink">
                {active}
              </span>
            ) : null}
          </Button>

          <Toggle
            label="Sin asignar"
            on={search.unassigned === true}
            onClick={() => apply({ unassigned: search.unassigned ? undefined : true })}
          />

          {active > 0 ? (
            <Button
              variant="quiet"
              className="px-2 py-2 text-xs"
              onClick={() => void navigate({ search: {} })}
            >
              <X className="size-3.5" />
              Limpiar
            </Button>
          ) : null}
        </div>

        {panelOpen ? (
          <Card lift={false} className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            <Select
              label="Cuenta"
              placeholder="Todas"
              value={search.account ?? ""}
              onChange={(event) => apply({ account: event.target.value || undefined })}
              options={accounts.accounts.map((account) => ({
                value: account.id,
                label: account.name,
              }))}
            />
            <Select
              label="Categoría"
              placeholder="Todas"
              value={search.category ?? ""}
              onChange={(event) => apply({ category: event.target.value || undefined })}
              options={categories.categories.map((option) => ({
                value: option.value,
                label: categoryLabel(option.value, option.label),
              }))}
            />
            <Select
              label="Comercio"
              placeholder="Todos"
              value={search.merchant ?? ""}
              onChange={(event) => apply({ merchant: event.target.value || undefined })}
              options={merchantOptions(merchants.merchants, search.merchant)}
            />
            <Select
              label="Origen"
              placeholder="Cualquiera"
              value={search.origin ?? ""}
              onChange={(event) => apply({ origin: asOrigin(event.target.value) })}
              options={catalog.transaction_origins.map((option) => ({
                value: option.value,
                label: originLabel(option.value, option.label),
              }))}
            />
            <DateFilter
              label="Desde"
              value={search.from ?? ""}
              onChange={(value) => apply({ from: value })}
            />
            <DateFilter
              label="Hasta"
              value={search.to ?? ""}
              onChange={(value) => apply({ to: value })}
            />
          </Card>
        ) : null}

        {search.unassigned && page.transactions.length > 0 ? (
          <Notice
            icon={Unlink}
            title="Llegaron sin saber de qué cuenta son"
            className="rise"
          >
            Abre uno para asignarlo, o{" "}
            <Link to="/cuentas" className="text-cyan hover:underline">
              enlaza su tarjeta en Cuentas
            </Link>{" "}
            y los de esa tarjeta se ordenan solos.
          </Notice>
        ) : null}

        {page.transactions.length === 0 ? (
          <Empty filtered={active > 0} />
        ) : (
          <MovementList
            transactions={page.transactions}
            labels={labels}
            accounts={accounts.accounts}
            byLargest={search.sort === "amount"}
            totals={showTotals ? byMonth?.groups : undefined}
            direction={search.direction}
            currency={currency}
            sortControl={
              <SortSwitch
                byLargest={search.sort === "amount"}
                onChange={(byLargest) =>
                  apply({ sort: byLargest ? "amount" : undefined })
                }
              />
            }
          />
        )}

        {page.total > PAGE_SIZE ? (
          <nav
            className="flex items-center justify-between gap-3"
            aria-label="Paginación"
          >
            <Button
              variant="ghost"
              className="px-3 py-2 text-xs"
              disabled={current <= 1}
              onClick={() =>
                void navigate({
                  search: (previous) => ({ ...previous, page: pageOf(previous) - 1 }),
                })
              }
            >
              <ChevronLeft className="size-4" />
              Anterior
            </Button>
            <p className="text-faint text-xs tabular">
              {(current - 1) * PAGE_SIZE + 1}–
              {Math.min(current * PAGE_SIZE, page.total)} de {page.total}
            </p>
            <Button
              variant="ghost"
              className="px-3 py-2 text-xs"
              disabled={current >= lastPage}
              onClick={() =>
                void navigate({
                  search: (previous) => ({ ...previous, page: pageOf(previous) + 1 }),
                })
              }
            >
              Siguiente
              <ChevronRight className="size-4" />
            </Button>
          </nav>
        ) : null}
      </div>
    </AppShell>
  );
}

const HELP: PageHelp = {
  id: "transacciones",
  story: storyOf("movimiento"),
  points: [
    {
      icon: Mail,
      title: "Llegan solos",
      body: "Cada alerta de tu banco se vuelve un movimiento. «Agregar» es para el efectivo y lo que no avisa.",
    },
    {
      icon: Unlink,
      title: "«Sin asignar»",
      body: "Llegó, pero aún no sabemos de qué cuenta. Enlaza esa tarjeta en Cuentas y se ordena sola.",
    },
    {
      icon: ArrowLeftRight,
      title: "Traslados",
      body: "Mover plata entre tus cuentas no es gasto ni ingreso. Se marcan en violeta.",
    },
    {
      icon: Pencil,
      title: "Todo se corrige",
      body: "Abre un movimiento para corregirlo o borrarlo. Lo que dijo el banco queda guardado.",
    },
  ],
};

/**
 * The merchants offered, plus whichever one is already filtering.
 *
 * The list is a window over the busiest hundred. A filter arriving by URL —
 * shared, or from a bucket in the summary — can name one outside it, and the
 * select would then show "Todos" while the list stayed filtered.
 */
function merchantOptions(
  merchants: { id: string; display_name: string }[],
  selected: string | undefined,
): Option[] {
  const options = merchants.map((merchant) => ({
    value: merchant.id,
    label: merchant.display_name,
  }));
  if (selected && !options.some((option) => option.value === selected)) {
    options.unshift({ value: selected, label: "Comercio seleccionado" });
  }
  return options;
}

function countActive(search: TransactionSearch): number {
  // «Ingresos» and «Gastos» write `transfers` beside the direction; a kind is
  // one filter, so that `transfers` rides on the direction's count.
  const fromTab = search.transfers === "exclude" && search.direction !== undefined;
  return [
    search.search,
    search.account,
    search.category,
    search.merchant,
    search.origin,
    search.direction,
    search.unassigned,
    fromTab ? undefined : search.transfers,
    // A whole month chosen in «Mes» is one filter, not a «Desde» and a «Hasta».
    ...(monthOf(search) === CUSTOM_RANGE
      ? [search.from, search.to]
      : [monthOf(search) || undefined]),
  ].filter((value) => value !== undefined).length;
}

function SearchBar({
  value,
  onSubmit,
}: {
  value: string;
  onSubmit: (value: string | undefined) => void;
}) {
  // Local until submitted: filtering on every keystroke would fire a request
  // per letter against an endpoint that reads a whole partition.
  const [draft, setDraft] = useState(value);

  return (
    <form
      onSubmit={(event) => {
        event.preventDefault();
        onSubmit(draft.trim() || undefined);
      }}
      className="relative"
    >
      <Search
        className="-translate-y-1/2 pointer-events-none absolute top-1/2 left-4 size-4 text-faint"
        aria-hidden
      />
      <input
        type="search"
        value={draft}
        onChange={(event) => setDraft(event.target.value)}
        placeholder="Buscar por comercio o descripción…"
        aria-label="Buscar movimientos"
        className="w-full rounded-xl border border-line bg-surface py-2.5 pr-4 pl-11 text-base text-text placeholder:text-faint focus:border-accent focus:outline-none"
      />
    </form>
  );
}

function DateFilter({
  label,
  value,
  onChange,
}: {
  label: string;
  value: string;
  onChange: (value: string | undefined) => void;
}) {
  const id = `date-${label}`;
  return (
    <div className="flex min-w-0 flex-col gap-2">
      <label htmlFor={id} className="text-muted text-sm">
        {label}
      </label>
      <input
        id={id}
        type="date"
        value={value}
        onChange={(event) => onChange(event.target.value || undefined)}
        className="rounded-xl border border-line bg-ink px-4 py-3 text-base text-text focus:border-accent focus:outline-none"
      />
    </div>
  );
}

function Toggle({
  label,
  on,
  onClick,
}: {
  label: string;
  on: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={on}
      className={
        on
          ? "min-h-11 rounded-full border border-accent bg-accent-soft px-4 text-sm text-text transition-colors active:bg-surface"
          : "min-h-11 rounded-full border border-line px-4 text-muted text-sm transition-colors hover:bg-surface-raised hover:text-text active:bg-surface"
      }
    >
      {label}
    </button>
  );
}

/** Newest first or largest first: the one ordering choice the list offers. */
/** Newest first or largest first: the one ordering choice the list offers. */
function SortSwitch({
  byLargest,
  onChange,
}: {
  byLargest: boolean;
  onChange: (byLargest: boolean) => void;
}) {
  return (
    <Select
      variant="quiet"
      label="Orden"
      value={byLargest ? "amount" : "date"}
      onChange={(event) => onChange(event.target.value === "amount")}
      options={[
        { value: "date", label: "Más recientes" },
        { value: "amount", label: "Más grandes" },
      ]}
    />
  );
}

/**
 * Grouped by month, the way a statement reads; largest first is one ranking,
 * so it is one block with each row's full date instead.
 */
function MovementList({
  transactions,
  labels,
  accounts,
  byLargest,
  totals,
  direction,
  currency,
  sortControl,
}: {
  transactions: Transaction[];
  labels: Record<string, string>;
  accounts: Account[];
  byLargest: boolean;
  /** The summary's month groups for the same filters, when it has them. */
  totals?: { key: string | null; totals: SpendingTotals[] }[];
  direction?: "incoming" | "outgoing";
  currency: string;
  sortControl: ReactNode;
}) {
  const byId = new Map(accounts.map((account) => [account.id, account]));

  const sections: { key: string; title: string; movements: Transaction[] }[] = [];
  if (byLargest) {
    sections.push({
      key: "largest",
      title: `Más grandes primero, en ${currency}`,
      movements: transactions,
    });
  } else {
    for (const movement of transactions) {
      const key = monthKeyOf(movement.occurred_at);
      const last = sections.at(-1);
      if (last?.key === key) last.movements.push(movement);
      else {
        const month = formatMonthKey(key);
        sections.push({
          key,
          title: month.charAt(0).toUpperCase() + month.slice(1),
          movements: [movement],
        });
      }
    }
  }

  return (
    <div className="flex flex-col gap-8">
      {sections.map((section, sectionIndex) => {
        const monthTotals = totals
          ?.find((group) => group.key === section.key)
          ?.totals.find((entry) => entry.currency === currency);
        return (
          <section key={section.key} className="flex flex-col gap-3">
            <div className="flex flex-wrap items-end justify-between gap-x-4 gap-y-2 px-1">
              <div className="flex min-w-0 flex-col gap-1">
                <h2 className="font-medium text-base text-text">{section.title}</h2>
                {monthTotals ? (
                  <MonthTotals totals={monthTotals} direction={direction} />
                ) : null}
              </div>
              {sectionIndex === 0 ? sortControl : null}
            </div>
            <Card lift={false} className="p-0">
              <div
                aria-hidden
                className="hidden grid-cols-[6rem_minmax(0,1.6fr)_minmax(0,1fr)_9rem] gap-x-6 border-line border-b px-6 py-3 text-faint text-xs lg:grid"
              >
                <span>Día</span>
                <span>Movimiento</span>
                <span>Cuenta</span>
                <span className="text-right">Monto</span>
              </div>
              <ul>
                {section.movements.map((movement, index) => (
                  <li
                    key={movement.id}
                    className="border-line/40 border-b last:border-b-0"
                  >
                    <MovementRow
                      movement={movement}
                      labels={labels}
                      account={
                        movement.account_id ? byId.get(movement.account_id) : undefined
                      }
                      fullDate={byLargest}
                      // A statement prints the day once, on the first line of
                      // that day; the rows under it read as the same day.
                      sameDayAsPrevious={
                        !byLargest && sameDay(section.movements[index - 1], movement)
                      }
                    />
                  </li>
                ))}
              </ul>
            </Card>
          </section>
        );
      })}
    </div>
  );
}

/**
 * What came in and went out over the month, for the same filters as the list.
 * A tab that shows one direction shows only its figure: the other is zero by
 * construction.
 */
function MonthTotals({
  totals,
  direction,
}: {
  totals: SpendingTotals;
  direction?: "incoming" | "outgoing";
}) {
  return (
    <p className="flex flex-wrap items-baseline gap-x-4 gap-y-1 text-faint text-xs">
      {direction !== "outgoing" ? (
        <span className="inline-flex items-baseline gap-1">
          Entró
          <Money
            amount={totals.incoming}
            currency={totals.currency}
            size="sm"
            tone="positive"
            className="text-xs"
          />
        </span>
      ) : null}
      {direction !== "incoming" ? (
        <span className="inline-flex items-baseline gap-1">
          Salió
          <Money
            amount={totals.outgoing}
            currency={totals.currency}
            size="sm"
            className="text-xs"
          />
        </span>
      ) : null}
    </p>
  );
}

/** «nov», the month a row belongs to where the list is not grouped by month. */
function shortMonthOf(epochSeconds: number): string {
  return formatDayMonth(epochSeconds).split(" ").at(-1)?.replace(".", "") ?? "";
}

function sameDay(previous: Transaction | undefined, movement: Transaction): boolean {
  return (
    previous !== undefined &&
    dayStringOf(previous.occurred_at) === dayStringOf(movement.occurred_at)
  );
}

/**
 * One movement: the day, what it was (its category under the name, an icon
 * beside both), the account and the amount. On a phone the account rides
 * under the amount instead of having a column.
 *
 * What a row does not say is on purpose: the note, how it was entered and
 * the account's kind are a tap away, in the movement itself. A list that says
 * everything about every row is one where nothing stands out.
 */
function MovementRow({
  movement,
  // A row holds a category value and nothing else, so one this person wrote
  // would render as `custom:mascotas` without the names beside it.
  labels,
  account,
  fullDate,
  sameDayAsPrevious,
}: {
  movement: Transaction;
  labels: Record<string, string>;
  account?: Account;
  /** «17 nov» instead of «17», where rows are not grouped by month. */
  fullDate: boolean;
  sameDayAsPrevious: boolean;
}) {
  const incoming = movement.direction === "incoming";
  const transfer = movement.transfer;
  const value = movement.merchant?.category;
  const categorised = !isUncategorized(value);
  const category = transfer
    ? "Traslado"
    : categorised && value
      ? labelFrom(labels, value)
      : "Sin categoría";
  const Icon = transfer ? TRANSFER_ICON : categoryIcon(value);
  /*
   * A transfer wears neither green nor magenta: green means money arriving
   * and magenta money leaving, and this is neither. It says so in violet.
   */
  const tile = transfer
    ? "bg-violet/15 text-violet"
    : incoming
      ? "bg-incoming/15 text-incoming"
      : "bg-surface-raised text-muted";
  const categoryText = transfer
    ? "text-violet"
    : categorised
      ? "text-muted"
      : "text-faint";
  const accountName = account?.name ?? "Sin asignar";
  const accountText = account ? "text-muted" : "text-warn";
  const day = dayStringOf(movement.occurred_at).slice(8);

  return (
    <Link
      to="/transacciones/$transactionId"
      params={{ transactionId: movement.id }}
      className="grid grid-cols-[1.75rem_minmax(0,1fr)_auto] items-center gap-x-3 px-4 py-3.5 transition-colors duration-150 hover:bg-surface-raised/70 active:bg-surface-raised sm:grid-cols-[3rem_minmax(0,1fr)_auto] sm:gap-x-4 lg:grid-cols-[6rem_minmax(0,1.6fr)_minmax(0,1fr)_9rem] lg:gap-x-6 lg:px-6 lg:py-4"
      activeProps={{ className: "bg-surface-raised/70" }}
    >
      {/* The day is the row's anchor, the way a bank statement reads. */}
      <span className="flex flex-col text-base text-muted tabular">
        <span aria-hidden>{sameDayAsPrevious ? "" : day}</span>
        {fullDate ? (
          <span aria-hidden className="text-faint text-xs">
            {shortMonthOf(movement.occurred_at)}
          </span>
        ) : null}
        <span className="sr-only">{formatDayMonth(movement.occurred_at)}</span>
      </span>

      <div className="flex min-w-0 items-center gap-3 lg:gap-4">
        <span
          aria-hidden
          className={cn(
            "grid size-9 shrink-0 place-items-center rounded-xl lg:size-10",
            tile,
          )}
        >
          <Icon className="size-4 lg:size-5" />
        </span>
        <div className="min-w-0">
          <p className="truncate font-medium text-sm text-text">
            {transfer
              ? transferTitle(transfer, movement.counterparty)
              : (movement.merchant?.display_name ?? movement.counterparty)}
          </p>
          <p className={cn("mt-0.5 truncate text-xs", categoryText)}>{category}</p>
        </div>
      </div>

      <span className={cn("hidden truncate text-sm lg:block", accountText)}>
        {accountName}
      </span>

      <div className="flex flex-col items-end gap-0.5">
        <Money
          amount={incoming ? movement.amount : `-${movement.amount}`}
          currency={movement.currency}
          signed
          size="sm"
          tone={transfer ? "neutral" : incoming ? "positive" : "plain"}
          className="font-semibold"
        />
        <span
          className={cn(
            "max-w-24 truncate text-xs sm:max-w-40 lg:hidden",
            account ? "text-faint" : "text-warn",
          )}
        >
          {accountName}
        </span>
      </div>
    </Link>
  );
}

function Empty({ filtered }: { filtered: boolean }) {
  const plain = (
    <Card lift={false}>
      <p className="py-8 text-center text-muted text-sm">
        {filtered
          ? "Ningún movimiento coincide con estos filtros."
          : "Cuando tu banco te avise de un movimiento, aparecerá aquí sin que tengas que escribir nada."}
      </p>
    </Card>
  );

  // Filtered to nothing is the filter's doing; empty with no filter is the
  // connection's, and that one says why.
  return filtered ? plain : <EmptyDiagnosis fallback={plain} />;
}
