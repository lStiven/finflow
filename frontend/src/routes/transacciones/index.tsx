import { useSuspenseQuery } from "@tanstack/react-query";
import { createFileRoute, Link, redirect, useNavigate } from "@tanstack/react-router";
import {
  ArrowDownLeft,
  ArrowLeftRight,
  ArrowUpRight,
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
import { useState } from "react";
import { originLabel } from "@/accounts/kinds";
import {
  accountsQuery,
  categoriesQuery,
  financialCatalogQuery,
  merchantsForFilterQuery,
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
import { type Option, Select } from "@/components/ui/Select";
import {
  formatDayMonth,
  formatMonthKey,
  fromLocalInput,
  monthKeyOf,
} from "@/lib/dates";
import { transferTitle } from "@/lib/transfers";
import {
  categoryLabel,
  categoryLabels,
  isUncategorized,
  labelFrom,
} from "@/merchants/categories";

const PAGE_SIZE = 25;

/**
 * Direction is a real server filter, so the count beside the list is the
 * count of what the tab selected. Filtering a loaded page in the browser
 * instead would show three movements under a total of forty.
 */
const DIRECTION_TABS = [
  { label: "Todas", value: undefined },
  { label: "Ingresos", value: "incoming" },
  { label: "Gastos", value: "outgoing" },
] as const;

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
    page: raw.page === undefined ? undefined : Math.max(1, Number(raw.page) || 1),
  }),
  loaderDeps: ({ search }) => search,
  loader: ({ context, deps }) =>
    Promise.all([
      context.queryClient.query({
        ...transactionsQuery(toFilters(deps)),
        staleTime: "static",
      }),
      context.queryClient.query({ ...accountsQuery("all"), staleTime: "static" }),
      context.queryClient.query({ ...merchantsForFilterQuery, staleTime: "static" }),
      context.queryClient.query(categoriesQuery),
      context.queryClient.query({ ...financialCatalogQuery, staleTime: "static" }),
    ]),
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

function toFilters(search: TransactionSearch): TransactionFilters {
  return {
    limit: PAGE_SIZE,
    offset: (pageOf(search) - 1) * PAGE_SIZE,
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

function TransactionsScreen() {
  const search = Route.useSearch();
  const navigate = useNavigate({ from: Route.fullPath });

  const { data: page } = useSuspenseQuery(transactionsQuery(toFilters(search)));
  const { data: accounts } = useSuspenseQuery(accountsQuery("all"));
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
        <SearchBar
          key={search.search ?? ""}
          value={search.search ?? ""}
          onSubmit={(value) => apply({ search: value })}
        />

        <div
          role="tablist"
          aria-label="Dirección"
          className="flex gap-1 rounded-xl border border-line bg-surface p-1"
        >
          {DIRECTION_TABS.map((tab) => (
            <button
              key={tab.label}
              type="button"
              role="tab"
              aria-selected={search.direction === tab.value}
              onClick={() => apply({ direction: tab.value })}
              className={
                search.direction === tab.value
                  ? "flex-1 rounded-lg bg-accent-soft py-2 font-medium text-sm text-text transition-colors"
                  : "flex-1 rounded-lg py-2 text-muted text-sm transition-colors hover:text-text"
              }
            >
              {tab.label}
            </button>
          ))}
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <Button
            variant="ghost"
            className="px-3 py-2 text-xs"
            onClick={() => setPanelOpen((open) => !open)}
            aria-expanded={panelOpen}
          >
            <SlidersHorizontal className="size-3.5" />
            Filtros
            {active > 0 ? (
              <span className="rounded-full bg-accent px-1.5 text-[0.625rem] text-accent-ink">
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

        {page.transactions.length === 0 ? (
          <Empty filtered={active > 0} />
        ) : (
          <MovementList transactions={page.transactions} labels={labels} />
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
              Página {current} de {lastPage}
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
  return [
    search.search,
    search.account,
    search.category,
    search.merchant,
    search.origin,
    search.direction,
    search.unassigned,
    search.transfers,
    search.from,
    search.to,
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
        className="w-full rounded-xl border border-line bg-surface py-3 pr-4 pl-11 text-base text-text placeholder:text-faint focus:border-accent focus:outline-none"
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
          ? "rounded-full border border-accent bg-accent-soft px-3 py-1.5 text-text text-xs"
          : "rounded-full border border-line px-3 py-1.5 text-muted text-xs hover:text-text"
      }
    >
      {label}
    </button>
  );
}

/** Grouped by month, the way a statement reads. */
function MovementList({
  transactions,
  labels,
}: {
  transactions: Transaction[];
  labels: Record<string, string>;
}) {
  const months = new Map<string, Transaction[]>();
  for (const movement of transactions) {
    const key = monthKeyOf(movement.occurred_at);
    const bucket = months.get(key);
    if (bucket) bucket.push(movement);
    else months.set(key, [movement]);
  }

  return (
    <div className="flex flex-col gap-6">
      {[...months].map(([key, movements]) => (
        <section key={key} className="flex flex-col gap-2">
          <h2 className="text-muted text-sm first-letter:uppercase">
            {formatMonthKey(key)}
          </h2>
          <Card lift={false} className="p-0">
            <ul>
              {movements.map((movement) => (
                <li key={movement.id}>
                  <MovementRow movement={movement} labels={labels} />
                </li>
              ))}
            </ul>
          </Card>
        </section>
      ))}
    </div>
  );
}

function MovementRow({
  movement,
  // A row holds a category value and nothing else, so one this person wrote
  // would render as `custom:mascotas` without the names beside it.
  labels,
}: {
  movement: Transaction;
  labels: Record<string, string>;
}) {
  const incoming = movement.direction === "incoming";
  const transfer = movement.transfer;

  return (
    <Link
      to="/transacciones/$transactionId"
      params={{ transactionId: movement.id }}
      className="group flex items-center gap-3 border-line/40 border-b px-4 py-2.5 transition-colors duration-150 hover:bg-surface-raised/70"
      activeProps={{ className: "bg-surface-raised/70" }}
    >
      {/*
       * A transfer wears neither hue: green means money arriving and magenta
       * means money leaving, and this is the one movement that is neither.
       */}
      <span
        aria-hidden
        className={
          transfer
            ? "grid size-8 shrink-0 place-items-center rounded-lg bg-gradient-to-br from-violet/25 to-cyan/5 text-violet transition-transform duration-200 group-hover:scale-110"
            : incoming
              ? "grid size-8 shrink-0 place-items-center rounded-lg bg-gradient-to-br from-incoming/25 to-cyan/5 text-incoming transition-transform duration-200 group-hover:scale-110"
              : "grid size-8 shrink-0 place-items-center rounded-lg bg-gradient-to-br from-accent/25 to-violet/5 text-accent transition-transform duration-200 group-hover:scale-110"
        }
      >
        {transfer ? (
          <ArrowLeftRight className="size-3.5" />
        ) : incoming ? (
          <ArrowDownLeft className="size-3.5" />
        ) : (
          <ArrowUpRight className="size-3.5" />
        )}
      </span>

      <div className="min-w-0 flex-1">
        <p className="truncate text-sm">
          {transfer
            ? transferTitle(transfer, movement.counterparty)
            : (movement.merchant?.display_name ?? movement.counterparty)}
        </p>
        {/* One line, cut at the end rather than wrapped: these read
            most-important-first, and wrapping made every third row two lines
            taller than its neighbours on a phone. */}
        <p className="mt-0.5 truncate text-faint text-xs">
          <span>{formatDayMonth(movement.occurred_at)}</span>
          {transfer ? <span className="text-violet"> · traslado</span> : null}
          {movement.merchant?.category &&
          !isUncategorized(movement.merchant.category) ? (
            <span> · {labelFrom(labels, movement.merchant.category)}</span>
          ) : null}
          {movement.origin === "manual" ? <span> · a mano</span> : null}
          {movement.account_id ? null : (
            <span className="text-warn"> · sin asignar</span>
          )}
        </p>
      </div>

      <Money
        amount={incoming ? movement.amount : `-${movement.amount}`}
        currency={movement.currency}
        signed
        size="sm"
        tone={transfer ? "neutral" : incoming ? "positive" : "plain"}
      />
    </Link>
  );
}

function Empty({ filtered }: { filtered: boolean }) {
  return (
    <Card lift={false}>
      <p className="py-8 text-center text-muted text-sm">
        {filtered
          ? "Ningún movimiento coincide con estos filtros."
          : "Cuando tu banco te avise de un movimiento, aparecerá aquí sin que tengas que escribir nada."}
      </p>
    </Card>
  );
}
