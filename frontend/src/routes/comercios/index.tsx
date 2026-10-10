import { useSuspenseQuery } from "@tanstack/react-query";
import { createFileRoute, Link, redirect, useNavigate } from "@tanstack/react-router";
import {
  Check,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  Combine,
  ChevronRight as Enter,
  Loader2,
  Search,
  Sparkles,
  Store,
  Tag,
  X,
} from "lucide-react";
import { useState } from "react";
import {
  categoriesQuery,
  categoryUsageQuery,
  type Merchant,
  type MerchantFilters,
  type MerchantSort,
  merchantCatalogQuery,
  merchantsQuery,
  useConfirmMerchant,
  useCreateCategory,
  useEditMerchant,
} from "@/api/queries";
import { AppShell } from "@/components/AppShell";
import { PageHeader, type PageHelp } from "@/components/PageHeader";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { Select } from "@/components/ui/Select";
import { cn } from "@/lib/cn";
import { aliasCountLabel, sortLabel, timesSeenLabel } from "@/merchants/aliases";
import { CategoryManager } from "@/merchants/CategoryManager";
import { CategoryNameForm } from "@/merchants/CategoryNameForm";
import {
  categoryLabel,
  categoryLabels,
  createCategoryError,
  labelFrom,
} from "@/merchants/categories";

const PAGE_SIZE = 25;

const SORTS: MerchantSort[] = ["last_seen", "times_seen", "name"];

function isSort(value: unknown): value is MerchantSort {
  return SORTS.includes(value as MerchantSort);
}

/**
 * The filters live in the URL, like the movements list and for the same
 * reasons: the back button has to undo a filter rather than leave the screen,
 * and the loader can then fetch exactly what will be rendered.
 *
 * `review` is the one that is not really a filter but a mode — it is the
 * queue, and it is where this screen opens when there is anything in it.
 */
export type MerchantSearch = {
  search?: string;
  category?: string;
  sort?: MerchantSort;
  /** `true` shows only what has never been looked at. */
  review?: boolean;
  page?: number;
};

function text(value: unknown): string | undefined {
  return typeof value === "string" && value.trim() !== "" ? value : undefined;
}

export const Route = createFileRoute("/comercios/")({
  beforeLoad: ({ context }) => {
    if (!context.session) throw redirect({ to: "/login" });
  },
  validateSearch: (raw: Record<string, unknown>): MerchantSearch => ({
    search: text(raw.search),
    category: text(raw.category),
    sort: isSort(raw.sort) ? raw.sort : undefined,
    review: raw.review === true || raw.review === "true" ? true : undefined,
    page: raw.page === undefined ? undefined : Math.max(1, Number(raw.page) || 1),
  }),
  loaderDeps: ({ search }) => search,
  loader: ({ context, deps }) =>
    Promise.all([
      context.queryClient.query({
        ...merchantsQuery(toFilters(deps)),
        staleTime: "static",
      }),
      context.queryClient.query({ ...merchantCatalogQuery, staleTime: "static" }),
      context.queryClient.query(categoriesQuery),
      context.queryClient.query(categoryUsageQuery),
    ]),
  component: MerchantsScreen,
});

function pageOf(search: MerchantSearch): number {
  return search.page ?? 1;
}

function toFilters(search: MerchantSearch): MerchantFilters {
  return {
    limit: PAGE_SIZE,
    offset: (pageOf(search) - 1) * PAGE_SIZE,
    search: search.search,
    category: search.category,
    sort: search.sort,
    needs_review: search.review,
  };
}

function MerchantsScreen() {
  const search = Route.useSearch();
  const navigate = useNavigate({ from: Route.fullPath });

  const { data: page } = useSuspenseQuery(merchantsQuery(toFilters(search)));
  const { data: catalog } = useSuspenseQuery(merchantCatalogQuery);
  // Not in the catalogue and it cannot be: half of this vocabulary is theirs.
  const { data: categories } = useSuspenseQuery(categoriesQuery);
  const labels = categoryLabels(categories.categories);

  /**
   * Every filter change resets to the first page — page 4 of a new filter is
   * empty. Pushed rather than replaced, so back undoes the filter.
   */
  function apply(patch: Partial<MerchantSearch>) {
    void navigate({
      search: (previous) => ({ ...previous, ...patch, page: undefined }),
    });
  }

  const filtering =
    search.search !== undefined || search.category !== undefined || search.review;
  const current = pageOf(search);
  const lastPage = Math.max(1, Math.ceil(page.total / PAGE_SIZE));
  // Nothing at all, ever — the only case the explanation is for. Somebody who
  // has merchants but filtered them all away needs a different sentence.
  const nothingYet = page.total === 0 && !filtering;
  // What the last review in the list did, said once above it: the row it
  // happened on leaves the queue, so the row cannot be where it is said.
  const [reviewed, setReviewed] = useState<string | null>(null);

  return (
    <AppShell>
      <div className="flex flex-col gap-6">
        <PageHeader
          title="Comercios"
          lead="El negocio real detrás del texto que escribe tu banco."
          // Before any merchant exists, the first-run card is the explanation.
          help={{ ...HELP, openFirstTime: !nothingYet }}
        />

        {nothingYet ? (
          <FirstRun />
        ) : (
          <>
            <ReviewBanner
              pending={page.needs_review}
              inQueue={search.review === true}
              onOpenQueue={() => apply({ review: true })}
            />

            <Tabs
              review={search.review === true}
              pending={page.needs_review}
              onChange={(review) => apply({ review })}
            />

            {/*
              Keyed on the URL: the box holds a local draft until submitted,
              and without this "Limpiar" or the back button would clear the
              filter while leaving the old text in the field.
            */}
            <SearchBar
              key={search.search ?? ""}
              value={search.search ?? ""}
              onSubmit={(value) => apply({ search: value })}
            />

            <div className="grid gap-3 sm:grid-cols-[1fr_1fr_auto] sm:items-end">
              <Select
                label="Categoría"
                placeholder="Todas"
                value={search.category ?? ""}
                onChange={(event) =>
                  apply({ category: event.target.value || undefined })
                }
                options={categories.categories.map((option) => ({
                  value: option.value,
                  label: categoryLabel(option.value, option.label),
                }))}
              />
              <Select
                label="Orden"
                value={search.sort ?? "last_seen"}
                onChange={(event) =>
                  apply({
                    sort: isSort(event.target.value) ? event.target.value : undefined,
                  })
                }
                options={catalog.sorts.map((option) => ({
                  value: option.value,
                  label: sortLabel(option.value, option.label),
                }))}
              />
              {filtering ? (
                <Button
                  variant="quiet"
                  className="justify-self-start px-2 py-3 text-xs"
                  onClick={() => void navigate({ search: {} })}
                >
                  <X className="size-3.5" />
                  Limpiar
                </Button>
              ) : null}
            </div>

            {/* Under the filters, not above them: the list is what this
                screen is about, and the categories are the buckets it is
                filtered by — near enough to be found, far enough not to be
                in the way. */}
            <CategoryManager />

            <p className="text-faint text-xs">
              {page.total === 0
                ? "Ningún comercio coincide"
                : `${page.total} ${page.total === 1 ? "comercio" : "comercios"}`}
              {search.review ? " sin revisar" : ""}
              {search.search || search.category ? " con estos filtros" : ""}
            </p>

            {reviewed ? (
              <p
                role="status"
                className="rise flex items-center gap-2 text-incoming text-sm"
              >
                <Check className="size-4 shrink-0" aria-hidden />
                Listo: {reviewed}
              </p>
            ) : null}

            {page.merchants.length === 0 ? (
              <Empty inQueue={search.review === true} />
            ) : (
              <Card lift={false} className="p-0">
                <ul>
                  {page.merchants.map((merchant, index) => (
                    <li key={merchant.id}>
                      <Row
                        merchant={merchant}
                        index={index}
                        inQueue={search.review === true}
                        labels={labels}
                        onReviewed={setReviewed}
                      />
                    </li>
                  ))}
                </ul>
              </Card>
            )}

            {/*
              `current > 1` and not just a long list: confirming the last
              pending merchant on page 2 drops the total below one page, and a
              pager that vanished then would strand the reader on an empty
              page with no control to leave it.
            */}
            {page.total > PAGE_SIZE || current > 1 ? (
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
                      search: (previous) => ({
                        ...previous,
                        page: pageOf(previous) - 1,
                      }),
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
                      search: (previous) => ({
                        ...previous,
                        page: pageOf(previous) + 1,
                      }),
                    })
                  }
                >
                  Siguiente
                  <ChevronRight className="size-4" />
                </Button>
              </nav>
            ) : null}
          </>
        )}
      </div>
    </AppShell>
  );
}

const HELP: PageHelp = {
  id: "comercios",
  points: [
    {
      icon: Store,
      title: "Aparecen solos",
      body: "Cada alerta trae el texto del negocio. Finflow lo limpia y lo junta con sus otras formas.",
    },
    {
      icon: Combine,
      title: "Uno solo, muchos nombres",
      body: "«TIENDAS ARA 123» y «ARA 900» son el mismo sitio. Unirlos hace que tus categorías cuadren.",
    },
    {
      icon: Tag,
      title: "La categoría es del comercio",
      body: "Cámbiala en el comercio y todos sus movimientos, los de antes también, la toman.",
    },
    {
      icon: Sparkles,
      title: "Pendientes de revisar",
      body: "Los que Finflow dedujo solo. «Está bien» u «Otra categoría», sin salir de la lista.",
    },
  ],
};

/* ------------------------------------------------------------------- la cola */

/**
 * What "sin revisar" means, said once, where the number is.
 *
 * The count is the global one the API sends beside the page — it does not
 * move when somebody types in the search box, so it stays a reliable "how
 * much is left" rather than "how much is left of what you are looking at".
 */
function ReviewBanner({
  pending,
  inQueue,
  onOpenQueue,
}: {
  pending: number;
  inQueue: boolean;
  onOpenQueue: () => void;
}) {
  if (pending === 0) {
    return (
      <p className="flex items-center gap-2 text-incoming text-xs">
        <Check className="size-3.5 shrink-0" />
        Todo revisado. Los comercios nuevos aparecerán aquí cuando lleguen.
      </p>
    );
  }

  return (
    <Card glow="accent" lift={false} className="relative overflow-hidden">
      <span
        aria-hidden
        className="absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-accent/60 to-transparent"
      />
      <div className="flex flex-col gap-4 sm:flex-row sm:items-start">
        <span
          aria-hidden
          className="grid size-11 shrink-0 place-items-center rounded-xl bg-accent/12 text-accent ring-1 ring-accent/25"
        >
          <Sparkles className="size-5" />
        </span>
        <div className="min-w-0 flex-1">
          <p className="font-medium">
            {pending === 1
              ? "1 comercio que nadie ha mirado"
              : `${pending} comercios que nadie ha mirado`}
          </p>
          <p className="mt-1 text-muted text-sm">
            Finflow los dedujo de tus alertas. Revisarlos no corre prisa.
          </p>
        </div>
        {inQueue ? null : (
          <Button
            variant="ghost"
            className="shrink-0 py-2 text-xs"
            onClick={onOpenQueue}
          >
            Ver los pendientes
          </Button>
        )}
      </div>
    </Card>
  );
}

function Tabs({
  review,
  pending,
  onChange,
}: {
  review: boolean;
  pending: number;
  onChange: (review: boolean | undefined) => void;
}) {
  const tabs = [
    { label: "Todos", active: !review, value: undefined },
    {
      label: pending > 0 ? `Por revisar · ${pending}` : "Por revisar",
      active: review,
      value: true as const,
    },
  ];

  return (
    <div
      role="tablist"
      aria-label="Qué comercios ver"
      className="flex gap-1 rounded-xl border border-line bg-surface p-1 sm:max-w-sm"
    >
      {tabs.map((tab) => (
        <button
          key={tab.label}
          type="button"
          role="tab"
          aria-selected={tab.active}
          onClick={() => onChange(tab.value)}
          className={
            tab.active
              ? "flex-1 rounded-lg bg-accent-soft py-2 font-medium text-sm text-text transition-colors"
              : "flex-1 rounded-lg py-2 text-muted text-sm transition-colors hover:text-text"
          }
        >
          {tab.label}
        </button>
      ))}
    </div>
  );
}

function SearchBar({
  value,
  onSubmit,
}: {
  value: string;
  onSubmit: (value: string | undefined) => void;
}) {
  // Local until submitted: the endpoint reads a whole partition, so filtering
  // on every keystroke would fire a request per letter.
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
        // The backend matches the name *and* every spelling under it, which
        // is the useful half and the one nobody would guess.
        placeholder="Busca un comercio…"
        aria-label="Buscar comercios"
        className="w-full rounded-xl border border-line bg-surface py-3 pr-4 pl-11 text-base text-text placeholder:text-faint focus:border-accent focus:outline-none"
      />
    </form>
  );
}

/* --------------------------------------------------------------- la lista */

function Row({
  merchant,
  index,
  inQueue,
  // A row holds a value and nothing else, so a category this person wrote
  // would render as `custom:mascotas` without the names beside it.
  labels,
  onReviewed,
}: {
  merchant: Merchant;
  index: number;
  /** In «Por revisar» every row is unreviewed, so saying it again is noise. */
  inQueue: boolean;
  labels: Record<string, string>;
  onReviewed: (message: string) => void;
}) {
  const reviewing = merchant.needs_review;

  return (
    <div
      className="rise flex flex-col border-line/40 border-b sm:flex-row sm:items-center"
      style={{ animationDelay: `${Math.min(index, 8) * 40}ms` }}
    >
      <Link
        to="/comercios/$merchantId"
        params={{ merchantId: merchant.id }}
        className="group flex min-w-0 flex-1 items-center gap-3 px-4 py-3 transition-colors duration-150 hover:bg-surface-raised/70"
      >
        <span
          aria-hidden
          className={cn(
            "grid size-9 shrink-0 place-items-center rounded-lg transition-transform duration-200 group-hover:scale-110",
            reviewing
              ? "bg-gradient-to-br from-accent/25 to-violet/5 text-accent"
              : "bg-surface-raised text-muted",
          )}
        >
          <Store className="size-4" />
        </span>

        <span className="min-w-0 flex-1">
          <span className="flex flex-wrap items-center gap-x-2 gap-y-1">
            <span className="truncate font-medium text-sm">
              {merchant.display_name}
            </span>
            {reviewing && !inQueue ? (
              <span className="rounded-full bg-accent/15 px-2 py-0.5 text-[0.625rem] text-accent uppercase tracking-wider">
                Sin revisar
              </span>
            ) : null}
          </span>
          {/* One line, short enough to finish. The category is left out
              while it is a control right beside this — said twice, it was
              the first thing to look like clutter. */}
          <span className="mt-0.5 block truncate text-faint text-xs">
            {reviewing ? null : (
              <span className="inline-flex items-center gap-1 align-middle">
                <Tag className="size-3" aria-hidden />
                {labelFrom(labels, merchant.category)} ·{" "}
              </span>
            )}
            <span>{timesSeenLabel(merchant.times_seen)}</span>
            {merchant.alias_count > 1 ? (
              <span> · {aliasCountLabel(merchant.alias_count)}</span>
            ) : null}
          </span>
        </span>

        {/* On a wide screen the controls sit right after this, and a chevron
            between the name and them points at nothing. */}
        <Enter
          className={cn(
            "size-4 shrink-0 text-faint transition-transform duration-200 group-hover:translate-x-0.5",
            reviewing && "sm:hidden",
          )}
          aria-hidden
        />
      </Link>

      {/*
        Outside the link: a control nested in an anchor is invalid and
        swallows the click. Beside the name on a wide screen, under it on a
        phone — one row either way, not a second block per merchant.
      */}
      {reviewing ? (
        <ReviewControls merchant={merchant} labels={labels} onReviewed={onReviewed} />
      ) : null}
    </div>
  );
}

/** A control that shares the row's quiet look: a pill, not a block. */
const PILL =
  "inline-flex min-h-11 items-center gap-1.5 rounded-full border border-line bg-surface px-3 text-xs transition-colors hover:border-cyan/40 disabled:opacity-50 sm:min-h-9";

/** The dropdown entry that opens the name field. Never a category value. */
const CREATE = "__create__";

/**
 * Reviewing a merchant is saying what it is, so the category *is* the
 * control: choosing one saves it, and that counts as reviewed. «Está bien»
 * is for when the guess was already right.
 *
 * A native select, styled as a pill: it opens the platform's own picker on a
 * phone and needs no code to be keyboard- and screen-reader-friendly.
 */
function ReviewControls({
  merchant,
  labels,
  onReviewed,
}: {
  merchant: Merchant;
  labels: Record<string, string>;
  onReviewed: (message: string) => void;
}) {
  const { data } = useSuspenseQuery(categoriesQuery);
  const confirm = useConfirmMerchant(merchant.id);
  const edit = useEditMerchant(merchant.id);
  const create = useCreateCategory();
  const [naming, setNaming] = useState(false);
  const [nameError, setNameError] = useState<string | null>(null);
  const busy = confirm.isPending || edit.isPending;
  const failure = confirm.error ?? edit.error;

  function choose(category: string) {
    edit.mutate(
      { category },
      {
        onSuccess: () =>
          onReviewed(
            `${merchant.display_name} quedó en ${labels[category] ?? labelFrom(labels, category)}.`,
          ),
      },
    );
  }

  async function onCreate(name: string) {
    setNameError(null);
    try {
      const created = await create.mutateAsync({ label: name });
      setNaming(false);
      edit.mutate(
        { category: created.value },
        { onSuccess: () => onReviewed(`${merchant.display_name} quedó en ${name}.`) },
      );
    } catch (cause) {
      setNameError(createCategoryError(cause));
    }
  }

  return (
    <div className="flex flex-col gap-2 px-4 pb-3 pl-16 sm:py-3 sm:pl-0">
      <div className="flex flex-wrap items-center gap-2">
        <span className={cn(PILL, "relative pr-8")}>
          <Tag className="size-3.5 shrink-0 text-faint" aria-hidden />
          <select
            aria-label={`Categoría de ${merchant.display_name}`}
            value={merchant.category}
            disabled={busy}
            onChange={(event) => {
              if (event.target.value === CREATE) {
                setNaming(true);
                return;
              }
              choose(event.target.value);
            }}
            className="-inset-px absolute h-[calc(100%+2px)] w-[calc(100%+2px)] cursor-pointer appearance-none rounded-full bg-transparent pr-8 pl-8 text-transparent focus-visible:outline-2 focus-visible:outline-cyan focus-visible:outline-offset-2"
          >
            {data.categories.map((option) => (
              <option key={option.value} value={option.value} className="text-text">
                {categoryLabel(option.value, option.label)}
              </option>
            ))}
            <option value={CREATE} className="text-text">
              ＋ Crear una categoría…
            </option>
          </select>
          <span aria-hidden className="pointer-events-none">
            {labelFrom(labels, merchant.category)}
          </span>
          {edit.isPending ? (
            <Loader2
              aria-hidden
              className="pointer-events-none absolute right-3 size-3.5 animate-spin"
            />
          ) : (
            <ChevronDown
              aria-hidden
              className="pointer-events-none absolute right-3 size-3.5 text-faint"
            />
          )}
        </span>

        <button
          type="button"
          disabled={busy}
          onClick={() =>
            confirm.mutate(undefined, {
              onSuccess: () => onReviewed(`${merchant.display_name} quedó revisado.`),
            })
          }
          aria-label={`Está bien ${merchant.display_name}`}
          className={cn(PILL, "text-text")}
        >
          {confirm.isPending ? (
            <Loader2 className="size-3.5 animate-spin" aria-hidden />
          ) : (
            <Check className="size-3.5 text-incoming" aria-hidden />
          )}
          Está bien
        </button>
      </div>

      {naming ? (
        <CategoryNameForm
          label="Nueva categoría"
          submitLabel="Crear y usar"
          pendingLabel="Creando…"
          pending={create.isPending}
          error={nameError}
          categories={data.categories}
          onSubmit={(name) => void onCreate(name)}
          onCancel={() => {
            setNaming(false);
            setNameError(null);
          }}
        />
      ) : null}

      {failure ? (
        <p role="alert" className="text-outgoing text-xs">
          {failure.message}
        </p>
      ) : null}
    </div>
  );
}

function Empty({ inQueue }: { inQueue: boolean }) {
  return (
    <Card lift={false}>
      <p className="py-8 text-center text-muted text-sm">
        {inQueue
          ? "No queda ningún comercio por revisar."
          : "Ningún comercio coincide con estos filtros."}
      </p>
    </Card>
  );
}

/**
 * What somebody sees before a single alert has arrived.
 *
 * The explanation *is* the content here, because a merchant is the one thing
 * on this app nobody creates: they appear on their own out of the emails, and
 * a screen with an "Añadir comercio" button would be lying about that.
 */
function FirstRun() {
  return (
    <Card glow="cyan" lift={false} className="relative overflow-hidden p-7 sm:p-9">
      <span
        aria-hidden
        className="absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-cyan/60 to-transparent"
      />
      <div className="rise flex flex-col gap-5">
        <span
          aria-hidden
          className="pulse-ring grid size-14 place-items-center rounded-2xl bg-cyan/15 ring-1 ring-cyan/30"
        >
          <Store className="size-6 text-cyan" />
        </span>
        <div>
          <h2 className="font-semibold text-xl tracking-tight">
            Todavía no hay comercios
          </h2>
          <p className="mt-2 max-w-2xl text-muted text-sm leading-relaxed">
            Estos no se crean a mano: aparecen solos.{" "}
            <strong className="text-text">
              Cada vez que llega una alerta de tu banco
            </strong>
            , Finflow lee el texto del negocio, lo limpia y lo agrupa con las otras
            formas en que ese mismo sitio se escribe. Cuando eso pase, este es el lugar
            para corregirle el nombre, la categoría, o decirle que dos son el mismo.
          </p>
        </div>
        <Link
          to="/conectar"
          className="inline-flex items-center justify-center gap-2 self-start rounded-xl bg-accent px-5 py-3.5 font-semibold text-accent-ink text-sm transition-all duration-150 hover:brightness-108"
        >
          Conectar mi banco
        </Link>
      </div>
    </Card>
  );
}
