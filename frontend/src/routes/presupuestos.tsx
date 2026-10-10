/**
 * What you said you would spend, against what you have.
 *
 * Its own screen rather than a row inside Resumen, which is a decision worth
 * writing down because the build plan said the opposite. The plan's argument
 * was sound — a ceiling belongs where the spending is visible, not in a
 * settings panel — and this screen satisfies it: the budgets *are* shown
 * against the month's spending. What it does not do is pile a second breakdown
 * onto a dashboard whose job is «cuánto tengo, cuánto gasté, cuánto debo,
 * cuánto entró». That screen gets a summary and a link.
 *
 * **Nothing here moves money.** A ceiling is a statement, not a transaction: no
 * balance changes, no movement is written, and nothing is blocked when one is
 * passed. The app is not the one spending.
 *
 * **And nothing here rings a phone.** The traffic light is on this screen and
 * only on this screen. A movement has no category at the moment it is
 * recorded — Financial keeps the bank's text and joins it to a merchant when
 * the answer is read, which is what makes correcting a merchant fix the past —
 * so nothing at write time knows which budget a purchase belongs to. The one
 * exception is a budget over *everything*, which needs no category to be
 * judged; that is what makes an alert possible at all, and it is not built yet.
 *
 * **A budget is a scope, not a category.** It carries a name, it may gather
 * several categories or none at all, and two of them may overlap on purpose.
 * That is why every budget has an id and why editing one changes everything
 * about it — the previous version could only ever change the ceiling, because
 * the category and the month *were* the cap's identity.
 */

import { useSuspenseQuery } from "@tanstack/react-query";
import { createFileRoute, redirect } from "@tanstack/react-router";
import {
  Check,
  ChevronLeft,
  ChevronRight,
  Layers,
  Loader2,
  Plus,
  Target,
  Trash2,
  TriangleAlert,
  Wallet,
  X,
} from "lucide-react";
import { useState } from "react";
import {
  type BudgetBody,
  type BudgetProgress,
  type BudgetState,
  type BudgetTotal,
  budgetsQuery,
  categoriesQuery,
  type UncappedCategory,
  useAmendBudget,
  useDeclareBudget,
  useForgetBudget,
} from "@/api/queries";
// Imported rather than copied: its own docstring warns that a second table of
// sixteen categories is how a gym ends up with a plane on it. The folder is
// called `bills/` because that is where it was first needed, not because it
// is about bills.
import { lookOf } from "@/bills/look";
import { formatAmountInput, parseAmount } from "@/bills/schedule";
import {
  captionOf,
  leftOf,
  monthLabel,
  overBy,
  scopeLabel,
  shiftMonth,
  tallyOf,
  usedShare,
  warningMark,
  worstOf,
} from "@/budgets/progress";
import { AppShell } from "@/components/AppShell";
import { Money } from "@/components/Money";
import { PageHeader, type PageHelp } from "@/components/PageHeader";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { Field } from "@/components/ui/Field";
import { cn } from "@/lib/cn";
import { categoryLabel, UNCATEGORIZED } from "@/merchants/categories";

/**
 * How each state is drawn. The only place these three colours are decided.
 *
 * Green, amber, red — and green really green, not the app's accent. The accent
 * is magenta at hue 349 and `outgoing` is red at hue 8, which are the same
 * colour to anybody glancing at a bar: the first draft of this table drew «te
 * pasaste» and «vas bien» identically, under a card that says «1 de 3 en
 * verde». A traffic light with one colour is not a traffic light.
 *
 * `warn` is the token, not `mid` — there is no `mid`, and `bg-mid` compiles to
 * nothing at all.
 */
const TONES: Record<BudgetState, { bar: string; text: string; ring: string }> = {
  ok: { bar: "bg-incoming/70", text: "text-muted", ring: "border-line" },
  warning: { bar: "bg-warn", text: "text-warn", ring: "border-warn/30" },
  over: { bar: "bg-outgoing/80", text: "text-outgoing", ring: "border-outgoing/30" },
};

/** How many categories one budget may gather. The API refuses more. */
const MAX_SCOPE_CATEGORIES = 20;

export const Route = createFileRoute("/presupuestos")({
  beforeLoad: ({ context }) => {
    if (!context.session) throw redirect({ to: "/login" });
  },
  loader: ({ context }) =>
    Promise.all([
      // The current month, which is what the screen opens on. Paging to
      // another one is a fetch the month bar pays for.
      context.queryClient.query(budgetsQuery()),
      // The picker needs them, and so does every label on this screen: a
      // scope holds category *values* and only this list knows their names.
      context.queryClient.query({ ...categoriesQuery, staleTime: "static" }),
    ]),
  component: BudgetsScreen,
});

function BudgetsScreen() {
  const [month, setMonth] = useState<string | null>(null);
  const { data: view } = useSuspenseQuery(budgetsQuery(month));
  const { data: categories } = useSuspenseQuery(categoriesQuery);
  const [declaring, setDeclaring] = useState(false);

  const labels = Object.fromEntries(
    categories.categories.map((option) => [
      option.value,
      categoryLabel(option.value, option.label),
    ]),
  );

  return (
    <AppShell>
      <div className="flex flex-col gap-7">
        <PageHeader
          title="Presupuestos"
          lead="Un tope para lo que quieras, y cómo vas contra él."
          help={HELP}
        />

        <MonthBar
          month={view.month}
          onChange={(next) => {
            setMonth(next);
            setDeclaring(false);
          }}
        />

        <Overview totals={view.totals} />

        {declaring ? (
          <BudgetForm month={view.month} onClose={() => setDeclaring(false)} />
        ) : (
          <Button onClick={() => setDeclaring(true)} full>
            <Plus className="size-4" />
            Poner un tope
          </Button>
        )}

        {view.budgets.length === 0 ? (
          <Empty />
        ) : (
          <section className="flex flex-col gap-3">
            <SectionTitle count={view.budgets.length}>Tus topes</SectionTitle>
            <div className="grid gap-3 [&>*]:min-w-0 sm:grid-cols-2 lg:grid-cols-3">
              {view.budgets.map((budget) => (
                <BudgetCard
                  key={budget.id}
                  budget={budget}
                  labels={labels}
                  month={view.month}
                />
              ))}
            </div>
          </section>
        )}

        {view.suggestions.length > 0 ? (
          <Suggestions
            suggestions={view.suggestions}
            labels={labels}
            month={view.month}
          />
        ) : null}
      </div>
    </AppShell>
  );
}

/** What the header paragraph and the footnote used to say, now a tap away. */
const HELP: PageHelp = {
  id: "presupuestos",
  points: [
    {
      icon: Wallet,
      title: "Solo informa",
      body: "Un tope no mueve saldos ni bloquea compras: te avisa aquí y decides tú.",
    },
    {
      icon: Target,
      title: "Qué vigila",
      body: "Sin categorías, cuenta todo lo que gastes en el mes. También puedes elegir una o varias.",
    },
    {
      icon: TriangleAlert,
      title: "Ámbar y rojo",
      body: "Ámbar al llegar al punto de aviso que elijas, rojo al pasarte.",
    },
    {
      icon: Layers,
      title: "Pueden solaparse",
      body: "Dos topes pueden contar el mismo gasto, así que su suma no tiene que cuadrar con Resumen.",
    },
  ],
};

function SectionTitle({ children, count }: { children: string; count?: number }) {
  return (
    <h2 className="flex items-center gap-2 font-medium text-sm">
      {children}
      {count === undefined ? null : (
        <span className="rounded-full bg-surface-raised px-2 py-0.5 text-faint text-xs tabular-nums">
          {count}
        </span>
      )}
    </h2>
  );
}

/**
 * Which month is on screen, and the two arrows that change it.
 *
 * Worth having because a budget can govern one month only: without a way to
 * get to December, «este diciembre sí gasto más» would be a ceiling nobody
 * could ever see again.
 */
function MonthBar({
  month,
  onChange,
}: {
  month: string;
  onChange: (month: string) => void;
}) {
  return (
    <div className="flex items-center justify-between gap-3 rounded-xl border border-line bg-surface px-2 py-1.5">
      <StepButton
        label="Mes anterior"
        icon={ChevronLeft}
        onClick={() => onChange(shiftMonth(month, -1))}
      />
      <span className="min-w-0 truncate text-center text-sm first-letter:uppercase">
        {monthLabel(month)}
      </span>
      <StepButton
        label="Mes siguiente"
        icon={ChevronRight}
        onClick={() => onChange(shiftMonth(month, 1))}
      />
    </div>
  );
}

function StepButton({
  label,
  icon: Icon,
  onClick,
}: {
  label: string;
  icon: typeof ChevronLeft;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-label={label}
      title={label}
      className="grid size-8 shrink-0 place-items-center rounded-lg text-muted transition-colors hover:bg-surface-raised hover:text-text"
    >
      <Icon className="size-4" />
    </button>
  );
}

/**
 * The month against every budget at once, per currency.
 *
 * The bar first and the figures after it, for the reason the bills forecast
 * gives: two numbers side by side make a reader do the arithmetic, and the
 * proportion answers «¿voy bien?» before either one is read.
 *
 * The added-up ceiling is **not** what the month allows, because budgets may
 * overlap and two of them can count the same peso. It is a tally of what has
 * been declared, and the note at the foot of the screen says so.
 */
function Overview({ totals }: { totals: readonly BudgetTotal[] }) {
  if (totals.length === 0) return null;

  return (
    <Card glow="accent" lift={false} className="flex flex-col gap-5">
      {totals.map((total, index) => {
        const tally = tallyOf(total);
        const tone = TONES[worstOf(total)];
        const used =
          Number(total.limit) > 0 ? Number(total.spent) / Number(total.limit) : 1;

        return (
          <div
            key={total.currency}
            className={cn(
              "flex flex-col gap-3",
              index > 0 && "border-line border-t pt-5",
            )}
          >
            <div className="flex items-baseline justify-between gap-3">
              <figure className="flex min-w-0 flex-col gap-1">
                <figcaption className="text-faint text-xs uppercase tracking-wider">
                  Llevas gastado
                </figcaption>
                <Money amount={total.spent} currency={total.currency} size="md" />
              </figure>
              <span className={cn("shrink-0 text-right text-xs", tone.text)}>
                {tally.ok} de {tally.total} en verde
                {totals.length > 1 ? ` · ${total.currency}` : null}
              </span>
            </div>

            <div
              role="presentation"
              className="h-1.5 w-full overflow-hidden rounded-full bg-surface-raised"
            >
              <div
                className={cn("h-full rounded-full", tone.bar)}
                style={{
                  width: `${Math.round(Math.min(1, Math.max(0, used)) * 100)}%`,
                }}
              />
            </div>

            <figure className="flex items-baseline justify-between gap-3">
              <figcaption className="text-faint text-xs uppercase tracking-wider">
                De topes que suman
              </figcaption>
              <Money amount={total.limit} currency={total.currency} size="sm" />
            </figure>
          </div>
        );
      })}
    </Card>
  );
}

function Empty() {
  return (
    <Card lift={false} className="flex flex-col items-center gap-3 py-12 text-center">
      <span className="grid size-12 place-items-center rounded-2xl bg-surface-raised">
        <Target className="size-5 text-faint" />
      </span>
      <p className="max-w-xs text-muted text-sm leading-relaxed">
        Todavía no has puesto ningún tope. El más fácil de empezar es uno sobre todo el
        mes: no hay que elegir ninguna categoría.
      </p>
    </Card>
  );
}

/**
 * The drawing a budget gets, without a second table of categories.
 *
 * The icon slug is a *category value* — «usa el dibujo de esta categoría» —
 * so the one table in `look.ts` answers for both. A budget that named no icon
 * falls back to the first category it watches, and one over everything falls
 * back to the default, which is what `lookOf` already answers for `null`.
 */
function lookOfBudget(budget: BudgetProgress) {
  const source = budget.icon || budget.scope.categories[0] || null;

  return lookOf({ category: source, direction: "outgoing" });
}

/**
 * The line under a budget's name: what it watches, and for how long.
 *
 * The scope is dropped when it only repeats the name — a budget over
 * everything that its owner called «Todo el mes» would otherwise say it twice,
 * which reads like a rendering bug rather than emphasis. What is never dropped
 * is «solo este mes», because that one is not derivable from anything else on
 * the card.
 */
function subtitleOf(
  budget: BudgetProgress,
  labels: Record<string, string>,
  month: string,
): string {
  const scope = scopeLabel(budget.scope, labels);
  const parts = scope.toLowerCase() === budget.name.trim().toLowerCase() ? [] : [scope];

  if (!budget.recurring) parts.push(`solo ${monthLabel(month)}`);

  return parts.join(" · ");
}

/**
 * One budget, its bar and what is left.
 *
 * The notch on the track is the point somebody chose to be warned at. Without
 * it the bar turns amber for no visible reason, which reads as the app
 * deciding for them — and this is a screen that informs rather than scolds.
 *
 * A budget whose categories have since been deleted is not hidden: it is the
 * record of a decision, and hiding it would leave a row nothing could reach.
 * Losing *one* of several is a different thing and says so differently — the
 * ceiling is still a live decision about the ones that remain.
 */
function BudgetCard({
  budget,
  labels,
  month,
}: {
  budget: BudgetProgress;
  labels: Record<string, string>;
  month: string;
}) {
  const [editing, setEditing] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const forget = useForgetBudget();
  const tone = TONES[budget.state];
  const look = lookOfBudget(budget);
  const left = leftOf(budget);
  const over = overBy(budget);

  if (editing) {
    return (
      <div className="col-span-full">
        <BudgetForm budget={budget} month={month} onClose={() => setEditing(false)} />
      </div>
    );
  }

  if (confirming) {
    return (
      <Card lift={false} className={cn("flex flex-col justify-between gap-3 p-4")}>
        <span className="text-muted text-sm leading-relaxed">
          ¿Quitar «{budget.name}»? No borra ningún movimiento.
        </span>
        <div className="flex flex-wrap gap-2">
          <Button
            variant="ghost"
            className="px-3 py-1.5 text-outgoing text-xs"
            disabled={forget.isPending}
            aria-label={`Sí, quitar ${budget.name}`}
            onClick={() => forget.mutate(budget.id)}
          >
            Sí, quitar
          </Button>
          <Button
            variant="ghost"
            className="px-3 py-1.5 text-xs"
            onClick={() => setConfirming(false)}
          >
            Cancelar
          </Button>
        </div>
      </Card>
    );
  }

  return (
    <Card
      glow={budget.retired ? "none" : look.glow}
      lift={false}
      className={cn(
        "group flex flex-col gap-3 p-4",
        budget.retired && "opacity-70",
        budget.state !== "ok" && tone.ring,
      )}
    >
      <div className="flex items-start justify-between gap-2">
        <span className="flex min-w-0 items-center gap-2">
          <span
            aria-hidden
            className={cn(
              "grid size-9 shrink-0 place-items-center rounded-xl",
              budget.retired ? "bg-surface-raised text-faint" : look.badge,
            )}
          >
            <look.icon className="size-4" />
          </span>
          <span className="flex min-w-0 flex-col">
            <span className="truncate font-medium text-sm" title={budget.name}>
              {budget.name}
            </span>
            <span className="truncate text-faint text-xs">
              {subtitleOf(budget, labels, month)}
            </span>
          </span>
        </span>

        <div className="-mr-1 flex shrink-0 gap-0.5 opacity-60 transition-opacity duration-200 group-focus-within:opacity-100 group-hover:opacity-100">
          <IconAction
            label="Cambiar"
            on={budget.name}
            icon={Target}
            onClick={() => setEditing(true)}
          />
          <IconAction
            label="Quitar"
            on={budget.name}
            icon={Trash2}
            tone="danger"
            onClick={() => setConfirming(true)}
          />
        </div>
      </div>

      {budget.retired ? (
        <p className="text-faint text-xs leading-relaxed">
          {budget.scope.categories.length === 1
            ? "Esa categoría ya no existe, así que nada va a contar contra este tope."
            : "Ninguna de sus categorías existe ya, así que nada va a contar contra este tope."}
        </p>
      ) : (
        <>
          {budget.missing.length > 0 ? (
            <p className="text-warn text-xs leading-relaxed">
              {budget.missing.length === 1
                ? "Una de sus categorías ya no existe."
                : `${budget.missing.length} de sus categorías ya no existen.`}{" "}
              El tope sigue contando las demás.
            </p>
          ) : null}

          <div className="flex items-baseline justify-between gap-2">
            <Money amount={budget.spent} currency={budget.currency} size="sm" />
            <span className="shrink-0 text-faint text-xs">
              de <Money amount={budget.limit} currency={budget.currency} size="sm" />
            </span>
          </div>

          {/* The track carries two things: how much is used, and where the
              owner asked to be warned. */}
          <div
            role="presentation"
            className="relative h-1.5 w-full overflow-hidden rounded-full bg-surface-raised"
          >
            <div
              className={cn("h-full rounded-full", tone.bar)}
              style={{ width: `${Math.round(usedShare(budget) * 100)}%` }}
            />
            <span
              className="absolute inset-y-0 w-px bg-text/25"
              style={{ left: `${Math.round(warningMark(budget) * 100)}%` }}
            />
          </div>

          <div className="flex items-baseline justify-between gap-2">
            <span className={cn("truncate text-xs", tone.text)}>
              {captionOf(budget)}
            </span>
            <span className="shrink-0 text-faint text-xs">
              {over ? (
                <>
                  <Money amount={over} currency={budget.currency} size="sm" /> de más
                </>
              ) : left ? (
                <>
                  queda <Money amount={left} currency={budget.currency} size="sm" />
                </>
              ) : (
                "sin margen"
              )}
            </span>
          </div>
        </>
      )}
    </Card>
  );
}

function IconAction({
  label,
  on,
  icon: Icon,
  onClick,
  tone = "plain",
}: {
  label: string;
  on: string;
  icon: typeof Target;
  onClick: () => void;
  tone?: "plain" | "danger";
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-label={`${label} ${on}`}
      title={label}
      className={cn(
        "grid size-8 place-items-center rounded-lg transition-colors hover:bg-surface-raised",
        tone === "danger"
          ? "text-faint hover:text-outgoing"
          : "text-muted hover:text-text",
      )}
    >
      <Icon className="size-4" />
    </button>
  );
}

/**
 * Where the money goes and nothing is watching.
 *
 * Offered, never created — the same rule the recurring detector follows. It is
 * also what makes this screen worth opening the first time, when there is
 * nothing declared: an empty state that only says «no hay nada» wastes the one
 * moment somebody is looking at the place a budget would go.
 */
function Suggestions({
  suggestions,
  labels,
  month,
}: {
  suggestions: readonly UncappedCategory[];
  labels: Record<string, string>;
  month: string;
}) {
  const [chosen, setChosen] = useState<UncappedCategory | null>(null);

  if (chosen) {
    return <BudgetForm month={month} preset={chosen} onClose={() => setChosen(null)} />;
  }

  return (
    <section className="flex flex-col gap-3">
      <SectionTitle>Donde más se te va, y sin tope</SectionTitle>
      <ul className="flex flex-col gap-2">
        {suggestions.map((offer) => (
          <li key={`${offer.category}:${offer.currency}`}>
            <button
              type="button"
              onClick={() => setChosen(offer)}
              className="flex w-full items-center justify-between gap-3 rounded-xl border border-line bg-surface px-3.5 py-3 text-left transition-colors hover:border-accent/40"
            >
              <span className="min-w-0 truncate text-sm">
                {labels[offer.category] ?? offer.category}
              </span>
              <span className="flex shrink-0 items-center gap-3">
                <Money amount={offer.spent} currency={offer.currency} size="sm" />
                <span className="text-faint text-xs">Poner tope</span>
              </span>
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}

/**
 * Declaring and correcting, in one form.
 *
 * **Everything is editable now**, and that is the change this iteration paid
 * for. The previous form could only ever move the ceiling: a cap's identity
 * *was* its category and its month, so touching either would have declared a
 * second cap and left the first standing — a save that looked like it had done
 * nothing, with an unreachable row behind it. A budget has an id, so correcting
 * one is one request that keeps it.
 *
 * The scope is a list of checkboxes and **none ticked is the default**, which
 * is «todo el mes» and the budget most people want first. It is not an empty
 * selection waiting to be filled: the line under the boxes says what it means,
 * because a form whose valid state looks unfinished is one people abandon.
 */
function BudgetForm({
  budget,
  preset,
  month,
  onClose,
}: {
  budget?: BudgetProgress;
  preset?: UncappedCategory;
  month: string;
  onClose: () => void;
}) {
  const { data: categories } = useSuspenseQuery(categoriesQuery);
  const declare = useDeclareBudget();
  const amend = useAmendBudget();
  const editing = budget !== undefined;
  const save = editing ? amend : declare;

  const [name, setName] = useState(
    budget?.name ?? (preset ? categoryNameOf(preset, categories) : ""),
  );
  const [chosen, setChosen] = useState<readonly string[]>(
    budget?.scope.categories ?? (preset ? [preset.category] : []),
  );
  // Carried, not dropped. `PUT` restates the budget whole, so an edit that
  // sent `[]` here would silently widen a card-scoped ceiling to every
  // account — the save looks like it changed nothing and changes the most
  // important thing about it. The screen cannot yet pick accounts; what it
  // can do is not lose the ones that are there.
  const accounts = budget?.scope.accounts ?? [];
  const icon = budget?.icon ?? "";
  const [limit, setLimit] = useState(budget ? formatAmountInput(budget.limit) : "");
  const [recurring, setRecurring] = useState(budget ? budget.recurring : true);
  const [warnAt, setWarnAt] = useState(String(budget?.warn_at ?? 80));
  const [error, setError] = useState<string | null>(null);
  const [options, setOptions] = useState(false);

  const currency = budget?.currency ?? preset?.currency ?? "COP";

  const toggle = (value: string) => {
    setChosen((current) =>
      current.includes(value)
        ? current.filter((each) => each !== value)
        : [...current, value],
    );
  };

  const submit = () => {
    const parsed = parseAmount(limit);
    const warn = Number(warnAt);

    if (name.trim() === "") return setError("Ponle un nombre.");
    if (parsed === null) {
      return setError("El tope tiene que ser un número mayor que cero.");
    }
    if (chosen.length > MAX_SCOPE_CATEGORIES) {
      return setError(
        `Un tope cubre hasta ${MAX_SCOPE_CATEGORIES} categorías. Déjalo sin ninguna para cubrir todo el mes.`,
      );
    }
    if (!Number.isInteger(warn) || warn < 1 || warn > 99) {
      return setError("El aviso va entre 1 y 99 por ciento del tope.");
    }

    setError(null);

    const body: BudgetBody = {
      name: name.trim(),
      limit: parsed,
      currency: currency as "COP" | "USD",
      categories: [...chosen],
      accounts: [...accounts],
      icon,
      // The budget's own month when correcting one, never the month the screen
      // happens to be showing: they are the same today and are not the moment
      // somebody pages back to look at November.
      month: recurring ? null : editing ? (budget.month ?? month) : month,
      warn_at: warn,
    };

    if (editing) {
      amend.mutate({ id: budget.id, body }, { onSuccess: () => onClose() });
    } else {
      declare.mutate(body, { onSuccess: () => onClose() });
    }
  };

  const pickable = categories.categories.filter(
    (option) => option.value !== UNCATEGORIZED,
  );

  return (
    <Card glow="cyan" lift={false} className="flex flex-col gap-4">
      <div className="flex items-center justify-between gap-3">
        <h2 className="font-medium">{editing ? "Cambiar el tope" : "Nuevo tope"}</h2>
        <button
          type="button"
          onClick={onClose}
          aria-label="Cerrar"
          className="grid size-8 place-items-center rounded-lg text-faint hover:bg-surface-raised hover:text-text"
        >
          <X className="size-4" />
        </button>
      </div>

      <Field
        label="Nombre"
        icon={Target}
        value={name}
        placeholder="Salidas"
        onChange={(event) => setName(event.target.value)}
      />

      <Field
        label="Tope"
        icon={Wallet}
        inputMode="decimal"
        value={limit}
        placeholder="600.000"
        onChange={(event) => setLimit(event.target.value)}
      />

      <fieldset className="flex flex-col gap-2">
        <legend className="mb-1 font-medium text-sm">Qué vigila</legend>
        <p className="text-faint text-xs leading-relaxed">
          {chosen.length === 0
            ? "Sin marcar ninguna, vigila todo lo que gastes este mes."
            : `Vigila ${chosen.length} ${chosen.length === 1 ? "categoría" : "categorías"}.`}
        </p>
        <div className="flex flex-wrap gap-2">
          {pickable.map((option) => {
            const active = chosen.includes(option.value);

            return (
              <button
                key={option.value}
                type="button"
                aria-pressed={active}
                onClick={() => toggle(option.value)}
                className={cn(
                  "rounded-full border px-3 py-1.5 text-xs transition-colors",
                  active
                    ? "border-accent bg-accent/12 text-accent"
                    : "border-line text-muted hover:border-accent/40 hover:text-text",
                )}
              >
                {categoryLabel(option.value, option.label)}
              </button>
            );
          })}
        </div>
      </fieldset>

      <label className="flex items-center justify-between gap-3 rounded-xl border border-line bg-ink p-3.5">
        <span className="min-w-0">
          <span className="block font-medium text-sm">Se repite cada mes</span>
          <span className="block text-faint text-xs">
            Apágalo para que valga solo en {monthLabel(month)}. El tope de siempre se
            queda donde está.
          </span>
        </span>
        <input
          type="checkbox"
          className="size-5 shrink-0 accent-violet"
          checked={recurring}
          onChange={(event) => setRecurring(event.target.checked)}
        />
      </label>

      <details
        open={options}
        onToggle={(event) => setOptions(event.currentTarget.open)}
        className="group/options"
      >
        <summary className="cursor-pointer list-none text-faint text-xs hover:text-muted">
          Opciones
        </summary>
        <div className="mt-3">
          <Field
            label="Avísame al"
            icon={TriangleAlert}
            inputMode="numeric"
            value={warnAt}
            placeholder="80"
            hint="Por ciento del tope. A partir de ahí la barra se pone ámbar en esta pantalla; todavía no llega a Telegram."
            onChange={(event) => setWarnAt(event.target.value)}
          />
        </div>
      </details>

      {error ? (
        <p role="alert" className="text-outgoing text-sm">
          {error}
        </p>
      ) : null}
      {save.isError ? (
        <p role="alert" className="text-outgoing text-sm">
          {save.error.message}
        </p>
      ) : null}

      <Button onClick={submit} disabled={save.isPending} full>
        {save.isPending ? (
          <Loader2 className="size-4 animate-spin" />
        ) : (
          <Check className="size-4" />
        )}
        {editing ? "Guardar" : "Poner el tope"}
      </Button>
    </Card>
  );
}

/** The name a suggestion arrives with, so the form opens already filled. */
function categoryNameOf(
  preset: UncappedCategory,
  categories: { categories: readonly { value: string; label: string }[] },
): string {
  const found = categories.categories.find(
    (option) => option.value === preset.category,
  );

  return found ? categoryLabel(found.value, found.label) : preset.category;
}
