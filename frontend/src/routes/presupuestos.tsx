/**
 * What you said you would spend, against what you have.
 *
 * Its own screen rather than a row inside Resumen, which is a decision worth
 * writing down because the build plan said the opposite. The plan's argument
 * was sound — a ceiling belongs where the spending is visible, not in a
 * settings panel — and this screen satisfies it: the caps *are* shown against
 * the month's spending, category by category. What it does not do is pile a
 * second breakdown onto a dashboard whose job is «cuánto tengo, cuánto gasté,
 * cuánto debo, cuánto entró». That screen gets a summary and a link.
 *
 * **Nothing here moves money.** A cap is a statement, not a transaction: no
 * balance changes, no movement is written, and nothing is blocked when a
 * ceiling is passed. The app is not the one spending.
 *
 * **And nothing here rings a phone.** The traffic light is on this screen and
 * only on this screen. A movement has no category at the moment it is
 * recorded — Financial keeps the bank's text and joins it to a merchant when
 * the answer is read, which is what makes correcting a merchant fix the past —
 * so nothing at write time knows which cap a purchase belongs to. That is why
 * the copy says «te avisa aquí» rather than promising Telegram.
 *
 * Three pieces, in the order somebody reads them: **how the month is going in
 * total**, then **each cap with its bar**, then **where the money actually
 * goes and nothing is watching** — which is the part that makes the screen
 * useful on the first visit, when there is nothing declared at all.
 */

import { useSuspenseQuery } from "@tanstack/react-query";
import { createFileRoute, redirect } from "@tanstack/react-router";
import {
  Check,
  ChevronLeft,
  ChevronRight,
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
  type BudgetProgress,
  type BudgetState,
  type BudgetTotal,
  budgetsQuery,
  categoriesQuery,
  type UncappedCategory,
  useForgetBudget,
  useSetBudget,
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
  shiftMonth,
  tallyOf,
  usedShare,
  warningMark,
  worstOf,
} from "@/budgets/progress";
import { AppShell } from "@/components/AppShell";
import { Money } from "@/components/Money";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { Field } from "@/components/ui/Field";
import { Select } from "@/components/ui/Select";
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

export const Route = createFileRoute("/presupuestos")({
  beforeLoad: ({ context }) => {
    if (!context.session) throw redirect({ to: "/login" });
  },
  loader: ({ context }) =>
    Promise.all([
      // The current month, which is what the screen opens on. Paging to
      // another one is a fetch the month bar pays for.
      context.queryClient.query(budgetsQuery()),
      // The picker needs them, and so does every label on this screen: a cap
      // is stored as a category *value* and only this list knows its name.
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
        <header className="flex flex-col gap-2">
          <h1 className="font-semibold text-2xl tracking-tight">Presupuestos</h1>
          <p className="max-w-prose text-muted text-sm leading-relaxed">
            Un tope por categoría y un semáforo contra lo que llevas gastado. Poner un
            tope <strong className="text-text">no mueve ningún saldo</strong> y no
            bloquea nada: te avisa aquí, y decides tú.
          </p>
        </header>

        <MonthBar
          month={view.month}
          onChange={(next) => {
            setMonth(next);
            setDeclaring(false);
          }}
        />

        <Overview totals={view.totals} />

        {declaring ? (
          <BudgetForm
            month={view.month}
            taken={view.budgets.map((budget) => budget.category)}
            onClose={() => setDeclaring(false)}
          />
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
                  key={`${budget.category}:${budget.month ?? "every"}`}
                  budget={budget}
                  label={labels[budget.category]}
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

        <p className="text-faint text-xs leading-relaxed">
          Los topes no suman el gasto del mes: un movimiento cuyo comercio todavía no
          está reconocido no cae en ninguna categoría, así que la suma de tus topes y lo
          que dice Resumen no tienen por qué coincidir.
        </p>
      </div>
    </AppShell>
  );
}

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
 * Worth having because a cap can be declared for one month only: without a way
 * to get to December, «este diciembre sí gasto más» would be a ceiling nobody
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
 * The month against every cap at once, per currency.
 *
 * The bar first and the figures after it, for the reason the bills forecast
 * gives: two numbers side by side make a reader do the arithmetic, and the
 * proportion answers «¿voy bien?» before either one is read.
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
                De un tope de
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
        Todavía no has puesto ningún tope. Empieza por donde más se te va: restaurantes,
        mercado, domicilios.
      </p>
    </Card>
  );
}

/**
 * One cap, its bar and what is left.
 *
 * The notch on the track is the point somebody chose to be warned at. Without
 * it the bar turns amber for no visible reason, which reads as the app
 * deciding for them — and this is a screen that informs rather than scolds.
 *
 * A cap whose category has since been deleted is not hidden: it is the record
 * of a decision, and hiding it would leave a row nothing could reach. It says
 * so and offers the only thing still worth doing with it.
 */
function BudgetCard({
  budget,
  label,
  month,
}: {
  budget: BudgetProgress;
  label: string | undefined;
  month: string;
}) {
  const [editing, setEditing] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const forget = useForgetBudget();
  const tone = TONES[budget.state];
  const look = lookOf({ category: budget.category, direction: "outgoing" });
  const name = label ?? (budget.retired ? "Categoría eliminada" : budget.category);
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
          ¿Quitar el tope de «{name}»? No borra ningún movimiento.
        </span>
        <div className="flex flex-wrap gap-2">
          <Button
            variant="ghost"
            className="px-3 py-1.5 text-outgoing text-xs"
            disabled={forget.isPending}
            aria-label={`Sí, quitar el tope de ${name}`}
            onClick={() =>
              forget.mutate({ category: budget.category, month: budget.month })
            }
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
            <span className="truncate font-medium text-sm" title={name}>
              {name}
            </span>
            {budget.recurring ? null : (
              <span className="truncate text-faint text-xs">Solo este mes</span>
            )}
          </span>
        </span>

        <div className="-mr-1 flex shrink-0 gap-0.5 opacity-60 transition-opacity duration-200 group-focus-within:opacity-100 group-hover:opacity-100">
          {budget.retired ? null : (
            <IconAction
              label="Cambiar el tope de"
              on={name}
              icon={Target}
              onClick={() => setEditing(true)}
            />
          )}
          <IconAction
            label="Quitar el tope de"
            on={name}
            icon={Trash2}
            tone="danger"
            onClick={() => setConfirming(true)}
          />
        </div>
      </div>

      {budget.retired ? (
        <p className="text-faint text-xs leading-relaxed">
          Esta categoría ya no existe, así que nada va a contar contra este tope.
        </p>
      ) : (
        <>
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
 * moment somebody is looking at the place a cap would go.
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
    return (
      <BudgetForm
        month={month}
        preset={chosen}
        taken={[]}
        onClose={() => setChosen(null)}
      />
    );
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
 * Three fields visible and two behind «Opciones», which is the whole design
 * of it: a cap has to be worth declaring without being a form somebody
 * abandons. The category and the ceiling are the answer; the month and the
 * warning point have defaults that are right almost every time.
 *
 * Correcting a cap moves neither the category nor the months it governs, and
 * that is one rule rather than two: a cap's identity **is** the pair, so
 * changing either half would not correct this cap — it would declare a second
 * one and leave the first standing. Worse than useless on the month, because
 * the exception it left behind goes on shadowing the recurring cap: the save
 * would look like it had done nothing at all, with an unreachable row behind
 * it. Both are changed by removing the cap and putting the other.
 */
function BudgetForm({
  budget,
  preset,
  month,
  taken = [],
  onClose,
}: {
  budget?: BudgetProgress;
  preset?: UncappedCategory;
  month: string;
  taken?: readonly string[];
  onClose: () => void;
}) {
  const { data: categories } = useSuspenseQuery(categoriesQuery);
  const save = useSetBudget();

  const [category, setCategory] = useState(budget?.category ?? preset?.category ?? "");
  const [limit, setLimit] = useState(budget ? formatAmountInput(budget.limit) : "");
  // Only ever moved while declaring. On a correction it is fixed to what the
  // cap already is, because it is half of the cap's identity.
  const [recurring, setRecurring] = useState(budget ? budget.recurring : true);
  const [warnAt, setWarnAt] = useState(String(budget?.warn_at ?? 80));
  const [error, setError] = useState<string | null>(null);
  const [options, setOptions] = useState(false);

  const currency = budget?.currency ?? preset?.currency ?? "COP";
  const editing = budget !== undefined;

  const submit = () => {
    const parsed = parseAmount(limit);
    const warn = Number(warnAt);

    if (category === "") return setError("Elige una categoría.");
    if (parsed === null) {
      return setError("El tope tiene que ser un número mayor que cero.");
    }
    if (!Number.isInteger(warn) || warn < 1 || warn > 99) {
      return setError("El aviso va entre 1 y 99 por ciento del tope.");
    }

    setError(null);
    save.mutate(
      {
        category,
        limit: parsed,
        currency: currency as "COP" | "USD",
        // The cap's own month when correcting one, never the month the screen
        // happens to be showing: they are the same today and are not the
        // moment somebody pages back to look at November.
        month: editing ? (budget.month ?? null) : recurring ? null : month,
        warn_at: warn,
      },
      { onSuccess: () => onClose() },
    );
  };

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

      {editing ? (
        <p className="text-faint text-xs leading-relaxed">
          Aquí solo se cambian el tope y el aviso. La categoría y los meses que cubre
          son lo que <em>es</em> este tope: para moverlos, quítalo y pon el otro.
        </p>
      ) : (
        <Select
          label="Categoría"
          value={category}
          onChange={(event) => setCategory(event.target.value)}
          placeholder="Elige una"
          options={categories.categories
            .filter(
              (option) =>
                option.value !== UNCATEGORIZED && !taken.includes(option.value),
            )
            .map((option) => ({
              value: option.value,
              label: categoryLabel(option.value, option.label),
            }))}
        />
      )}

      <Field
        label="Tope"
        icon={Wallet}
        inputMode="decimal"
        value={limit}
        placeholder="600.000"
        onChange={(event) => setLimit(event.target.value)}
      />

      {editing ? null : (
        <label className="flex items-center justify-between gap-3 rounded-xl border border-line bg-ink p-3.5">
          <span className="min-w-0">
            <span className="block font-medium text-sm">Se repite cada mes</span>
            <span className="block text-faint text-xs">
              Apágalo para que valga solo en {monthLabel(month)}, sin tocar el tope de
              siempre.
            </span>
          </span>
          <input
            type="checkbox"
            className="size-5 shrink-0 accent-violet"
            checked={recurring}
            onChange={(event) => setRecurring(event.target.checked)}
          />
        </label>
      )}

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
