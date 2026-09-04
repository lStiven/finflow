import { useSuspenseQuery } from "@tanstack/react-query";
import { createFileRoute, Link, redirect, useNavigate } from "@tanstack/react-router";
import {
  ArrowLeft,
  ArrowRight,
  Check,
  ChevronDown,
  Combine,
  Eye,
  Hash,
  Loader2,
  Merge,
  Pencil,
  Scissors,
  Sparkles,
  Store,
  TriangleAlert,
  Wallet,
} from "lucide-react";
import type { ComponentType, ReactNode } from "react";
import { type SubmitEvent, useState } from "react";
import {
  categoriesQuery,
  type MerchantAlias,
  type MerchantDetail,
  merchantQuery,
  merchantsForFilterQuery,
  summaryQuery,
  useConfirmMerchant,
  useEditMerchant,
  useMergeMerchants,
  useMoveAlias,
  useSplitAlias,
} from "@/api/queries";
import { AppShell } from "@/components/AppShell";
import { Money } from "@/components/Money";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { Field } from "@/components/ui/Field";
import { type Option, Select } from "@/components/ui/Select";
import { cn } from "@/lib/cn";
import { formatDate } from "@/lib/dates";
import {
  countGuesses,
  originCopy,
  statusLabel,
  timesSeenLabel,
} from "@/merchants/aliases";
import { CategoryPicker } from "@/merchants/CategoryPicker";
import { categoryLabels, labelFrom } from "@/merchants/categories";
import {
  lastAliasBlocker,
  MAX_NAME_LENGTH,
  merchantNameIssue,
  newMerchantNameIssue,
} from "@/merchants/edits";

export const Route = createFileRoute("/comercios/$merchantId")({
  beforeLoad: ({ context }) => {
    if (!context.session) throw redirect({ to: "/login" });
  },
  loader: ({ context, params }) =>
    Promise.all([
      context.queryClient.query({
        ...merchantQuery(params.merchantId),
        staleTime: "static",
      }),
      context.queryClient.query(categoriesQuery),
      // The other merchants: what "mover a otro" and "fusionar" pick from.
      context.queryClient.query({ ...merchantsForFilterQuery, staleTime: "static" }),
      /*
       * What was actually spent here, which Merchant does not know: it counts
       * sightings of a name, never money. Grouped by merchant, the bucket key
       * *is* the merchant id, so one summary answers this screen.
       */
      context.queryClient.query({ ...summaryQuery("merchant"), staleTime: "static" }),
    ]),
  component: MerchantScreen,
});

function MerchantScreen() {
  const { merchantId } = Route.useParams();
  const navigate = useNavigate();
  const { data: merchant } = useSuspenseQuery(merchantQuery(merchantId));
  // The chip below holds a value and nothing else, so a category this person
  // wrote would render as `custom:mascotas` without the names beside it.
  const { data: categories } = useSuspenseQuery(categoriesQuery);
  const labels = categoryLabels(categories.categories);

  return (
    <AppShell>
      {/*
        Keyed on the merchant, not just rendered with it. Every form below is
        seeded from the merchant it opened on, and the router matches this
        screen by route id — so walking from one merchant to another (the
        links a move or a split hands out do exactly that) would reuse the
        same component and keep the previous one's drafts, right down to
        renaming B with A's name.
      */}
      <div key={merchant.id} className="mx-auto flex w-full max-w-2xl flex-col gap-6">
        <Button
          variant="quiet"
          className="self-start px-0 py-0 text-xs"
          onClick={() => void navigate({ to: "/comercios" })}
        >
          <ArrowLeft className="size-3.5" />
          Comercios
        </Button>

        <header className="flex items-start gap-4">
          <span
            aria-hidden
            className={cn(
              "grid size-12 shrink-0 place-items-center rounded-xl ring-1",
              merchant.needs_review
                ? "bg-accent/12 text-accent ring-accent/25"
                : "bg-cyan/12 text-cyan ring-cyan/25",
            )}
          >
            <Store className="size-5" />
          </span>
          <div className="min-w-0 flex-1">
            <h1 className="font-semibold text-2xl tracking-tight">
              {merchant.display_name}
            </h1>
            <p className="mt-1 flex flex-wrap items-center gap-2 text-faint text-xs">
              <span className="rounded-full border border-line px-2 py-0.5">
                {labelFrom(labels, merchant.category)}
              </span>
              <span
                className={cn(
                  "rounded-full px-2 py-0.5",
                  merchant.needs_review
                    ? "bg-accent/15 text-accent"
                    : "bg-incoming/12 text-incoming",
                )}
              >
                {statusLabel(merchant.status)}
              </span>
              <span>
                Desde {formatDate(merchant.first_seen)} · último{" "}
                {formatDate(merchant.last_seen)}
              </span>
            </p>
          </div>
        </header>

        <Figures merchant={merchant} />

        {merchant.needs_review ? <ReviewCard merchant={merchant} /> : null}

        <Link
          to="/transacciones"
          search={{ merchant: merchant.id }}
          className="-m-1 flex items-center gap-1.5 self-start rounded-lg p-1 text-cyan text-sm transition-colors hover:text-text"
        >
          Ver sus movimientos
          <ArrowRight className="size-3.5" />
        </Link>

        <EditSection merchant={merchant} />
        <AliasSection merchant={merchant} />
        <MergeSection merchant={merchant} />
      </div>
    </AppShell>
  );
}

/* ------------------------------------------------------------------ cifras */

/**
 * Three figures, and only one of them is money.
 *
 * That separation is the point: `times_seen` counts appearances of the name
 * and would read as an amount next to an amount, so what was spent comes from
 * Financial's summary and is labelled as the different thing it is.
 */
function Figures({ merchant }: { merchant: MerchantDetail }) {
  const { data: summary } = useSuspenseQuery(summaryQuery("merchant"));
  const group = summary.groups.find((bucket) => bucket.key === merchant.id);
  /*
   * One figure per currency, all of them. The summary reports a total per
   * currency and there is no exchange rate anywhere in the backend, so
   * picking the first of several would show one number for a question with
   * two answers — and never say which one it picked.
   */
  const spent = group?.totals ?? [];

  return (
    <section aria-label="En números" className="grid gap-3 sm:grid-cols-3">
      <Figure icon={Wallet} label="Gastado aquí" hue="accent">
        {spent.length === 0 ? (
          <p className="text-faint text-sm">Nada todavía</p>
        ) : (
          spent.map((total) => (
            <Money
              key={total.currency}
              amount={total.outgoing}
              currency={total.currency}
              size="md"
            />
          ))
        )}
      </Figure>
      <Figure icon={Eye} label="Veces visto" hue="cyan">
        <p className="font-semibold text-xl tabular">{merchant.times_seen}</p>
      </Figure>
      <Figure icon={Hash} label="Formas de escribirse" hue="violet">
        <p className="font-semibold text-xl tabular">{merchant.alias_count}</p>
      </Figure>
    </section>
  );
}

function Figure({
  icon: Icon,
  label,
  hue,
  children,
}: {
  icon: ComponentType<{ className?: string }>;
  label: string;
  hue: "accent" | "cyan" | "violet";
  children: ReactNode;
}) {
  return (
    <div className="surface surface-static flex flex-col gap-2 rounded-card border border-line bg-surface p-4">
      <div className="flex items-start justify-between gap-3">
        <p className="text-muted text-sm">{label}</p>
        <span
          aria-hidden
          className={cn(
            "grid size-8 shrink-0 place-items-center rounded-lg",
            hue === "accent" && "bg-accent/12 text-accent",
            hue === "cyan" && "bg-cyan/12 text-cyan",
            hue === "violet" && "bg-violet/12 text-violet",
          )}
        >
          <Icon className="size-4" />
        </span>
      </div>
      {children}
    </div>
  );
}

/* ----------------------------------------------------------------- revisar */

/**
 * The one-tap way out of the review queue, with what it actually means.
 *
 * Confirming is not "this is correct" in the abstract — it is accepting the
 * guessed spellings below as belonging here. So the card counts them, and
 * points at the list rather than pretending the decision is free.
 */
function ReviewCard({ merchant }: { merchant: MerchantDetail }) {
  const confirm = useConfirmMerchant(merchant.id);
  const guesses = countGuesses(merchant.aliases);

  return (
    <Card glow="accent" lift={false} className="relative overflow-hidden">
      <span
        aria-hidden
        className="absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-accent/60 to-transparent"
      />
      <div className="flex flex-col gap-4 sm:flex-row sm:items-start">
        <span
          aria-hidden
          className="grid size-10 shrink-0 place-items-center rounded-xl bg-accent/12 text-accent ring-1 ring-accent/25"
        >
          <Sparkles className="size-5" />
        </span>
        <div className="min-w-0 flex-1">
          <p className="font-medium">Nadie ha mirado este comercio</p>
          <p className="mt-1 text-muted text-sm leading-relaxed">
            El nombre y la categoría los dedujo Finflow del texto de tus alertas.
            {guesses > 0
              ? ` Además hay ${guesses === 1 ? "una grafía que es una conjetura" : `${guesses} grafías que son conjeturas`}: mira la lista de abajo antes de aceptar.`
              : " Nada aquí es una conjetura: las formas de abajo se agruparon por regla."}
          </p>
          {confirm.error ? (
            <p role="alert" className="mt-2 text-outgoing text-xs">
              {confirm.error.message}
            </p>
          ) : null}
        </div>
        <Button
          variant="ghost"
          className="shrink-0 py-2 text-xs"
          disabled={confirm.isPending}
          onClick={() => confirm.mutate()}
        >
          {confirm.isPending ? (
            <>
              <Loader2 className="size-3.5 animate-spin" />
              Guardando…
            </>
          ) : (
            <>
              <Check className="size-3.5" />
              Está bien así
            </>
          )}
        </Button>
      </div>
    </Card>
  );
}

/* -------------------------------------------------------------- piezas ui */

/** A section of the screen: a heading, why it is here, and the thing itself. */
function Section({
  icon: Icon,
  title,
  hint,
  children,
}: {
  icon: ComponentType<{ className?: string }>;
  title: string;
  hint: ReactNode;
  children: ReactNode;
}) {
  return (
    <Card lift={false} className="flex flex-col gap-4">
      <div className="flex items-start gap-3">
        <Icon className="mt-0.5 size-4 shrink-0 text-faint" aria-hidden />
        <div className="min-w-0">
          <h2 className="font-medium text-sm">{title}</h2>
          <p className="mt-1 text-faint text-xs leading-relaxed">{hint}</p>
        </div>
      </div>
      {children}
    </Card>
  );
}

/** One foldable block, closed until asked for. */
function Fold({
  summary,
  icon: Icon,
  children,
}: {
  summary: string;
  icon: ComponentType<{ className?: string }>;
  children: ReactNode;
}) {
  const [open, setOpen] = useState(false);

  return (
    <div className="rounded-xl border border-line bg-ink/60">
      <button
        type="button"
        onClick={() => setOpen(!open)}
        aria-expanded={open}
        className="flex w-full items-center gap-2.5 px-3.5 py-2.5 text-left"
      >
        <Icon className="size-3.5 shrink-0 text-faint" aria-hidden />
        <span className="min-w-0 flex-1 truncate text-muted text-xs">{summary}</span>
        <ChevronDown
          className={cn(
            "size-3.5 shrink-0 text-faint transition-transform duration-200",
            open && "rotate-180",
          )}
          aria-hidden
        />
      </button>
      {open ? (
        <div className="rise flex flex-col gap-4 border-line border-t p-3.5">
          {children}
        </div>
      ) : null}
    </div>
  );
}

function Failed({ error }: { error: Error | null }) {
  if (error === null) return null;
  return (
    <p role="alert" className="text-outgoing text-xs">
      {error.message}
    </p>
  );
}

function Saved({ children }: { children: ReactNode }) {
  return (
    <p role="status" className="rise flex items-start gap-1.5 text-incoming text-xs">
      <Check className="mt-0.5 size-3.5 shrink-0" />
      <span>{children}</span>
    </p>
  );
}

function Spinner({ label }: { label: string }) {
  return (
    <>
      <Loader2 className="size-3.5 animate-spin" />
      {label}
    </>
  );
}

/* ------------------------------------------------------- nombre y categoría */

/**
 * The name and the category, in one form because the endpoint takes them
 * together and because editing either one is what marks the merchant
 * reviewed. Sending only what changed keeps a rename from silently
 * re-asserting a category the user never looked at.
 */
function EditSection({ merchant }: { merchant: MerchantDetail }) {
  const edit = useEditMerchant(merchant.id);
  const [name, setName] = useState(merchant.display_name);
  const [category, setCategory] = useState(merchant.category);
  /*
   * `null` until something is saved, and then whether that save is what took
   * the merchant out of the review queue. Reading `needs_review` afterwards
   * cannot answer it: by then it is false either way, so a merchant reviewed
   * last week would be told it just left a queue it was never in.
   */
  const [saved, setSaved] = useState<{ leftQueue: boolean } | null>(null);

  const issue = merchantNameIssue(name);
  const nameChanged = name.trim() !== merchant.display_name;
  const categoryChanged = category !== merchant.category;
  const changed = nameChanged || categoryChanged;
  const canSave = issue === undefined && changed && !edit.isPending;

  async function onSubmit(event: SubmitEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!canSave) return;
    const leftQueue = merchant.needs_review;
    setSaved(null);
    try {
      const next = await edit.mutateAsync({
        display_name: nameChanged ? name.trim() : undefined,
        category: categoryChanged ? category : undefined,
      });
      setName(next.display_name);
      setCategory(next.category);
      setSaved({ leftQueue });
    } catch {
      // `edit.error` carries it and `Failed` reports it below.
    }
  }

  return (
    <Section
      icon={Pencil}
      title="Cómo lo llamas"
      hint="Solo cambia lo que ves tú: el texto que manda el banco se queda como está, y sus movimientos no se mueven. La categoría es la que suma en tu resumen de gastos."
    >
      <form onSubmit={onSubmit} className="flex flex-col gap-3">
        <Field
          label="Nombre"
          value={name}
          maxLength={MAX_NAME_LENGTH}
          onChange={(event) => {
            setName(event.target.value);
            setSaved(null);
          }}
        />
        <CategoryPicker
          value={category}
          onChange={(value) => {
            setCategory(value);
            setSaved(null);
          }}
        />
        {nameChanged && issue ? <p className="text-outgoing text-xs">{issue}</p> : null}
        <Failed error={edit.error} />
        {saved && !changed ? (
          <Saved>
            Guardado.
            {saved.leftQueue ? " Este comercio ya cuenta como revisado." : ""}
          </Saved>
        ) : null}
        {merchant.needs_review ? (
          <p className="text-faint text-xs">
            Guardar aquí también lo saca de la cola de revisión.
          </p>
        ) : null}
        <Button
          type="submit"
          variant="ghost"
          className="self-start py-2 text-xs"
          disabled={!canSave}
        >
          {edit.isPending ? <Spinner label="Guardando…" /> : "Guardar cambios"}
        </Button>
      </form>
    </Section>
  );
}

/* ------------------------------------------------------------------ alias */

/**
 * Every spelling this merchant answers to, and the two ways one can be wrong.
 *
 * This is the screen's real content. A user has no reason to know the system
 * groups names at all, so the list has to say what it is showing — the raw
 * text as the bank writes it — before it offers to take one away.
 */
/**
 * Where a spelling ended up, once it is no longer on this merchant.
 *
 * Kept by the section rather than by the row that did it: moving or splitting
 * detaches the spelling, the mutation invalidates, and the row unmounts with
 * its own confirmation still inside it. A split in particular creates a
 * merchant whose only link would vanish with it.
 */
type Detached = {
  kind: "moved" | "split";
  /** The merchant it landed on — the target of a move, the one a split made. */
  merchant: MerchantDetail;
  rawText: string;
};

function AliasSection({ merchant }: { merchant: MerchantDetail }) {
  const [detached, setDetached] = useState<Detached | null>(null);
  const guesses = countGuesses(merchant.aliases);
  // Guesses first: they are the only ones the user is being asked about, and
  // a queue that buries them under twenty certainties is not a queue.
  const aliases = [...merchant.aliases].sort((left, right) => {
    const byGuess =
      Number(originCopy(right.origin).guess) - Number(originCopy(left.origin).guess);
    return byGuess !== 0 ? byGuess : right.times_seen - left.times_seen;
  });

  return (
    <Section
      icon={Combine}
      title="Cómo llega su nombre"
      hint={
        <>
          Un mismo negocio se escribe de muchas formas en las alertas —otra sede, otro
          número, otra abreviatura—. Todas estas caen en{" "}
          <strong className="text-muted">{merchant.display_name}</strong>.
          {guesses > 0
            ? " Las marcadas como conjetura son las que conviene mirar."
            : ""}
        </>
      }
    >
      {detached ? (
        <Saved>
          <span className="font-mono">{detached.rawText}</span>
          {detached.kind === "moved" ? " ahora es de " : " ya es "}
          <Link
            to="/comercios/$merchantId"
            params={{ merchantId: detached.merchant.id }}
            className="text-cyan underline underline-offset-2"
          >
            {detached.merchant.display_name}
          </Link>
          {detached.kind === "moved"
            ? ". Los movimientos que traían ese texto ya cuentan allá."
            : ", un comercio aparte. Lo que llegue con ese texto contará allá."}
        </Saved>
      ) : null}

      <ul className="flex flex-col gap-2">
        {aliases.map((alias) => (
          <li key={alias.fingerprint}>
            <AliasRow merchant={merchant} alias={alias} onDetached={setDetached} />
          </li>
        ))}
      </ul>
    </Section>
  );
}

function AliasRow({
  merchant,
  alias,
  onDetached,
}: {
  merchant: MerchantDetail;
  alias: MerchantAlias;
  onDetached: (detached: Detached | null) => void;
}) {
  const copy = originCopy(alias.origin);
  // Both ways of taking a spelling away are refused by the same rule, so the
  // row asks once and the fold below says it once.
  const blocker = lastAliasBlocker(merchant.alias_count);

  return (
    <div className="rounded-xl border border-line bg-ink/40 p-3.5">
      <div className="flex flex-wrap items-start justify-between gap-2">
        {/* The bank's own text, in mono: it is a machine's string, not prose. */}
        <p className="min-w-0 break-words font-mono text-sm">{alias.raw_text}</p>
        <span
          className={cn(
            "shrink-0 rounded-full px-2 py-0.5 text-[0.625rem] uppercase tracking-wider",
            copy.guess ? "bg-warn/15 text-warn" : "bg-surface-raised text-faint",
          )}
        >
          {copy.label}
        </span>
      </div>
      <p className="mt-1 text-faint text-xs">
        {timesSeenLabel(alias.times_seen)} · último {formatDate(alias.last_seen)}
      </p>
      <p className="mt-1.5 text-faint text-xs leading-relaxed">{copy.hint}</p>

      <div className="mt-3">
        <Fold summary="No es de este comercio" icon={Scissors}>
          {blocker === undefined ? (
            <>
              <p className="text-faint text-xs leading-relaxed">
                Dos salidas, según lo que sea. Cualquiera de las dos es{" "}
                <strong className="text-muted">definitiva</strong>: desde entonces esta
                grafía se resuelve por coincidencia exacta y ninguna regla vuelve a
                decidir por ella. Para deshacerla hay que traerla de vuelta a mano.
              </p>
              <MoveAliasForm
                merchant={merchant}
                alias={alias}
                onDetached={onDetached}
              />
              <SplitAliasForm
                merchant={merchant}
                alias={alias}
                onDetached={onDetached}
              />
            </>
          ) : (
            /*
             * Said once, not twice. The rule refuses moving *and* splitting
             * for the same reason, so two disabled forms under one
             * explanation would be the same sentence said in two places.
             */
            <p className="text-faint text-xs leading-relaxed">{blocker}</p>
          )}
        </Fold>
      </div>
    </div>
  );
}

/**
 * The merchants this one can hand a spelling to, or be merged with.
 *
 * A window over the hundred most recently seen, which is what the filter
 * query asks for. When it is a window rather than the whole list, the caller
 * says so — an option that is simply not there reads as a bug.
 */
function useOtherMerchants(merchantId: string): {
  options: Option[];
  capped: boolean;
} {
  const { data } = useSuspenseQuery(merchantsForFilterQuery);
  const others = data.merchants.filter((merchant) => merchant.id !== merchantId);
  return {
    options: others.map((merchant) => ({
      value: merchant.id,
      label: merchant.display_name,
    })),
    capped: data.total > data.merchants.length,
  };
}

function MoveAliasForm({
  merchant,
  alias,
  onDetached,
}: {
  merchant: MerchantDetail;
  alias: MerchantAlias;
  onDetached: (detached: Detached | null) => void;
}) {
  const move = useMoveAlias(merchant.id);
  const { options, capped } = useOtherMerchants(merchant.id);
  const [target, setTarget] = useState("");

  async function onSubmit(event: SubmitEvent<HTMLFormElement>) {
    event.preventDefault();
    if (target === "" || move.isPending) return;
    onDetached(null);
    try {
      // The response is the merchant it moved *to*, and the section above is
      // what reports it: this row is about to be unmounted by the refetch.
      const moved = await move.mutateAsync({
        fingerprint: alias.fingerprint,
        target_merchant_id: target,
      });
      onDetached({ kind: "moved", merchant: moved, rawText: alias.raw_text });
      setTarget("");
    } catch {
      // `move.error` carries it and `Failed` reports it below.
    }
  }

  if (options.length === 0) {
    return (
      <p className="border-line border-t pt-3 text-faint text-xs leading-relaxed">
        Para mover esta grafía a otro comercio hace falta que exista otro, y por ahora
        este es el único que tienes.
      </p>
    );
  }

  return (
    <form onSubmit={onSubmit} className="flex flex-col gap-3 border-line border-t pt-3">
      <Select
        label="Es de otro comercio que ya tienes"
        placeholder="Elige cuál"
        value={target}
        onChange={(event) => setTarget(event.target.value)}
        options={options}
        hint={
          capped ? "Se ofrecen los cien comercios vistos más recientemente." : undefined
        }
      />
      <Failed error={move.error} />
      <Button
        type="submit"
        variant="ghost"
        className="self-start py-2 text-xs"
        disabled={target === "" || move.isPending}
      >
        {move.isPending ? <Spinner label="Moviendo…" /> : "Moverla allá"}
      </Button>
    </form>
  );
}

function SplitAliasForm({
  merchant,
  alias,
  onDetached,
}: {
  merchant: MerchantDetail;
  alias: MerchantAlias;
  onDetached: (detached: Detached | null) => void;
}) {
  const split = useSplitAlias(merchant.id);
  const [name, setName] = useState("");
  const [category, setCategory] = useState("");

  const issue = newMerchantNameIssue(name);

  async function onSubmit(event: SubmitEvent<HTMLFormElement>) {
    event.preventDefault();
    if (issue !== undefined || split.isPending) return;
    onDetached(null);
    try {
      // The merchant this just created. Reported by the section above, which
      // outlives this row — otherwise the only link to it would unmount with
      // the spelling that is no longer here.
      const created = await split.mutateAsync({
        fingerprint: alias.fingerprint,
        display_name: name.trim() === "" ? undefined : name.trim(),
        category: category === "" ? undefined : category,
      });
      onDetached({ kind: "split", merchant: created, rawText: alias.raw_text });
      setName("");
      setCategory("");
    } catch {
      // `split.error` carries it and `Failed` reports it below.
    }
  }

  return (
    <form onSubmit={onSubmit} className="flex flex-col gap-3 border-line border-t pt-3">
      <p className="text-muted text-xs leading-relaxed">
        O es un negocio distinto que se llama parecido. Sácalo a comercio propio y todo
        lo que llegue con ese texto contará aparte.
      </p>
      <Field
        label="Nombre del comercio nuevo"
        placeholder={alias.raw_text}
        maxLength={MAX_NAME_LENGTH}
        value={name}
        hint="Si lo dejas vacío se queda con el texto del banco."
        onChange={(event) => setName(event.target.value)}
      />
      <CategoryPicker
        placeholder="Decidir después"
        value={category}
        onChange={setCategory}
      />
      {issue ? <p className="text-outgoing text-xs">{issue}</p> : null}
      <Failed error={split.error} />
      <Button
        type="submit"
        variant="ghost"
        className="self-start py-2 text-xs"
        disabled={issue !== undefined || split.isPending}
      >
        {split.isPending ? <Spinner label="Separando…" /> : "Sacarla a comercio propio"}
      </Button>
    </form>
  );
}

/* --------------------------------------------------------------- fusionar */

/**
 * Two records, one business — and the only action here that cannot be undone.
 *
 * Moving a spelling can be reversed by moving it back; a merge cannot, because
 * the absorbed merchant stops existing. That is what the confirmation step is
 * for, and what it has to name: which one survives.
 */
function MergeSection({ merchant }: { merchant: MerchantDetail }) {
  const mergeMerchants = useMergeMerchants(merchant.id);
  const { options, capped } = useOtherMerchants(merchant.id);
  const [absorbed, setAbsorbed] = useState("");
  const [confirming, setConfirming] = useState(false);
  const [done, setDone] = useState<string | null>(null);

  const chosen = options.find((option) => option.value === absorbed);

  async function onConfirm() {
    if (chosen === undefined || mergeMerchants.isPending) return;
    try {
      await mergeMerchants.mutateAsync({ absorbed_merchant_id: absorbed });
      setDone(chosen.label);
      setAbsorbed("");
      setConfirming(false);
    } catch {
      // `mergeMerchants.error` carries it and `Failed` reports it below.
    }
  }

  return (
    <Section
      icon={Merge}
      title="Fusionar con otro comercio"
      hint="Cuando el mismo negocio quedó partido en dos fichas. Se quedan aquí todas sus formas de escribirse y todos sus movimientos."
    >
      {options.length === 0 ? (
        <p className="text-faint text-xs leading-relaxed">
          Este es el único comercio que tienes, así que no hay con qué fusionarlo.
        </p>
      ) : (
        <div className="flex flex-col gap-3">
          <Select
            label="¿Cuál es el mismo negocio que este?"
            placeholder="Elige el comercio que desaparece"
            value={absorbed}
            onChange={(event) => {
              setAbsorbed(event.target.value);
              setConfirming(false);
              setDone(null);
            }}
            options={options}
            hint={
              capped
                ? "Se ofrecen los cien comercios vistos más recientemente."
                : undefined
            }
          />

          <Failed error={mergeMerchants.error} />
          {done ? (
            <Saved>
              {done} ya es parte de {merchant.display_name}.
            </Saved>
          ) : null}

          {confirming && chosen ? (
            <div className="flex flex-col gap-3 rounded-xl border border-warn/30 bg-warn/10 p-3">
              <p className="flex items-start gap-2 text-xs leading-relaxed">
                <TriangleAlert className="mt-0.5 size-3.5 shrink-0 text-warn" />
                <span>
                  <strong>{chosen.label}</strong> deja de existir y todo lo suyo pasa a{" "}
                  <strong>{merchant.display_name}</strong>, que es el que se queda.{" "}
                  <strong>Esto no se puede deshacer</strong> — no hay forma de volver a
                  separarlos.
                </span>
              </p>
              <div className="flex flex-wrap gap-2">
                <Button
                  variant="ghost"
                  className="py-2 text-xs"
                  disabled={mergeMerchants.isPending}
                  onClick={onConfirm}
                >
                  {mergeMerchants.isPending ? (
                    <Spinner label="Fusionando…" />
                  ) : (
                    "Sí, fusionarlos"
                  )}
                </Button>
                <Button
                  variant="quiet"
                  className="py-2 text-xs"
                  disabled={mergeMerchants.isPending}
                  onClick={() => setConfirming(false)}
                >
                  Mejor no
                </Button>
              </div>
            </div>
          ) : (
            <Button
              variant="ghost"
              className="self-start py-2 text-xs"
              disabled={absorbed === ""}
              onClick={() => setConfirming(true)}
            >
              <Merge className="size-3.5" />
              Fusionar
            </Button>
          )}
        </div>
      )}
    </Section>
  );
}
