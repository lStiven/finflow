/**
 * «¿Cuánto puedo gastar?» — at the top of the screen, or not at all.
 *
 * The figure the rest of this app was building towards, and the one that has
 * to be handled most carefully: **if it lies once, nobody looks at it again.**
 * Three decisions follow from that and they are the whole design of this card.
 *
 * **It is absent until somebody declares their month.** Not zero, not a
 * placeholder — a zero here reads as "you have nothing left to spend", which
 * is a real situation and not the one they are in. What stands in its place is
 * an invitation, because the thing being asked for is two numbers only they
 * know.
 *
 * **It shows its own arithmetic.** The breakdown opens in place: what you
 * expect to earn, what you are keeping, what you have spent, what you still
 * owe. A number somebody cannot check against their own head is a number they
 * abandon the first time it disagrees.
 *
 * **It never does the arithmetic itself.** `available` comes from the server
 * already computed. A second implementation here would be a second answer,
 * drifting from the first the day either one was fixed — and this is precisely
 * the figure where two answers is worse than none.
 */

import { useQuery } from "@tanstack/react-query";
import {
  Check,
  ChevronDown,
  Loader2,
  PiggyBank,
  Sparkles,
  Target,
  Wallet,
  X,
} from "lucide-react";
import { useState } from "react";
import {
  type Allowance,
  allowanceQuery,
  planQuery,
  recurringQuery,
  useDeclarePlan,
  useForgetPlan,
} from "@/api/queries";
// Lifted rather than copied a third time: both parse a price typed into a
// field, both refuse an empty one. A third implementation of "what did they
// mean by 1.200,50" is a third place for it to be wrong.
import { formatAmountInput, parseAmount } from "@/bills/schedule";
import { Money } from "@/components/Money";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { Field } from "@/components/ui/Field";
import { cn } from "@/lib/cn";
import {
  type AllowanceTone,
  breakdownOf,
  incomeSuggestions,
  monthlyEquivalent,
  parseKept,
  perDay,
  toneOf,
  usedShare,
} from "@/plan/allowance";

const TONES: Record<AllowanceTone, { number: "plain" | "negative"; bar: string }> = {
  healthy: { number: "plain", bar: "bg-accent/70" },
  tight: { number: "plain", bar: "bg-mid" },
  over: { number: "negative", bar: "bg-outgoing/80" },
};

export function AllowanceCard() {
  // Read rather than suspended, and the route prefetches both so this is
  // normally a cache hit with no flash. What the plain query buys is the bad
  // day: a 5xx here leaves the card out instead of taking the whole dashboard
  // down with it — the same reason the suggestions on `/facturas` are not
  // suspended either. `undefined` is "still coming"; `null` is the server
  // saying nothing has been declared, which is a real answer.
  const { data: plan, isPending: planPending } = useQuery(planQuery);
  const { data: allowance } = useQuery(allowanceQuery);
  const [editing, setEditing] = useState(false);

  if (planPending || plan === undefined) return null;

  if (editing || plan === null) {
    return (
      <PlanForm
        currency={plan?.currency ?? "COP"}
        income={plan?.expected_income ?? null}
        savings={plan?.savings_target ?? null}
        onClose={plan === null ? null : () => setEditing(false)}
      />
    );
  }

  if (allowance === null || allowance === undefined) return null;

  return <Available allowance={allowance} onEdit={() => setEditing(true)} />;
}

/**
 * The number, what is left of the month, and the subtraction behind it.
 *
 * The per-day figure is what turns the answer into the question actually
 * being asked. It disappears once there is nothing left: «-$40.000 por día»
 * is arithmetic, not an answer.
 */
function Available({
  allowance,
  onEdit,
}: {
  allowance: Allowance;
  onEdit: () => void;
}) {
  const forget = useForgetPlan();
  const tone = TONES[toneOf(allowance)];
  const daily = perDay(allowance);
  const rows = breakdownOf(allowance);

  return (
    <Card glow="accent" lift={false} className="flex flex-col gap-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <figure className="flex min-w-0 flex-col gap-1">
          <figcaption className="text-faint text-xs uppercase tracking-wider">
            Te queda para gastar
          </figcaption>
          <Money
            amount={allowance.available}
            currency={allowance.currency}
            size="lg"
            tone={tone.number}
          />
        </figure>

        <div className="flex shrink-0 flex-col items-end gap-1 text-right">
          <span className="text-muted text-xs">
            {allowance.days_left === 1
              ? "queda 1 día del mes"
              : `quedan ${allowance.days_left} días del mes`}
          </span>
          {daily ? (
            <span className="text-faint text-xs">
              ≈ <Money amount={daily} currency={allowance.currency} size="sm" /> por día
            </span>
          ) : null}
        </div>
      </div>

      <div
        role="presentation"
        className="h-1.5 w-full overflow-hidden rounded-full bg-surface-raised"
      >
        <div
          className={cn("h-full rounded-full", tone.bar)}
          style={{ width: `${Math.round(usedShare(allowance) * 100)}%` }}
        />
      </div>

      <details className="group/parts">
        <summary className="flex cursor-pointer list-none items-center gap-1.5 text-faint text-xs hover:text-muted">
          <ChevronDown className="size-3.5 transition-transform group-open/parts:rotate-180" />
          De qué está hecho
        </summary>

        <dl className="mt-3 flex flex-col gap-2">
          {rows.map((row) => (
            <div key={row.label} className="flex items-baseline justify-between gap-3">
              <dt className="min-w-0 truncate text-muted text-xs">
                {row.subtracted ? "− " : ""}
                {row.label}
              </dt>
              <dd className="shrink-0">
                <Money
                  amount={row.amount}
                  currency={allowance.currency}
                  size="sm"
                  tone={row.subtracted ? "neutral" : "plain"}
                />
              </dd>
            </div>
          ))}
        </dl>

        <div className="mt-4 flex flex-wrap gap-2">
          <Button variant="ghost" className="px-3 py-1.5 text-xs" onClick={onEdit}>
            Cambiar el mes
          </Button>
          <Button
            variant="quiet"
            className="px-3 py-1.5 text-xs"
            disabled={forget.isPending}
            onClick={() => forget.mutate()}
          >
            Quitar
          </Button>
        </div>
      </details>
    </Card>
  );
}

/**
 * The two numbers only its owner knows.
 *
 * Shown in place of the card while there is no plan, because an empty state
 * that only says "no hay nada" wastes the one moment somebody is looking at
 * the place the number will be.
 *
 * The income field can be filled from what the detector already found: a
 * salary is a recurring series like any other, and it is the link between
 * this delivery and the one before it. It is a **suggestion** — the figure
 * lands in an editable field, and a fortnightly salary is converted to what a
 * month of it is worth, which is an approximation nobody is asked to trust.
 */
function PlanForm({
  currency,
  income,
  savings,
  onClose,
}: {
  currency: string;
  income: string | null;
  savings: string | null;
  onClose: (() => void) | null;
}) {
  const declare = useDeclarePlan();
  // Not suspended and not blocking: the form works with no suggestions at
  // all, and a detector that is slow or down must not hold up the one screen
  // somebody opened to state their month.
  const { data: detected } = useQuery(recurringQuery);
  const suggestions = incomeSuggestions(detected?.series ?? [], currency);

  const [expected, setExpected] = useState(income ? formatAmountInput(income) : "");
  const [target, setTarget] = useState(
    savings && Number(savings) > 0 ? formatAmountInput(savings) : "",
  );
  const [error, setError] = useState<string | null>(null);

  const submit = () => {
    const parsedIncome = parseAmount(expected);
    // Empty *and* zero are real answers here, unlike the income: most people
    // are not setting anything aside, and both ways of saying so have to work.
    const parsedTarget = parseKept(target);

    if (parsedIncome === null) {
      return setError("Escribe cuánto esperas que entre este mes.");
    }
    if (parsedTarget === null) {
      return setError("Lo que quieres guardar tiene que ser un número.");
    }
    if (Number(parsedTarget) > Number(parsedIncome)) {
      return setError("No puedes guardar más de lo que esperas que entre.");
    }

    setError(null);
    declare.mutate(
      {
        expected_income: parsedIncome,
        currency: currency as "COP" | "USD",
        savings_target: parsedTarget,
      },
      { onSuccess: () => onClose?.() },
    );
  };

  return (
    <Card glow="cyan" lift={false} className="flex flex-col gap-4">
      <div className="flex items-start justify-between gap-3">
        <div className="flex flex-col gap-1">
          <h2 className="font-medium">
            {income === null ? "¿Cuánto puedes gastar este mes?" : "Cambiar el mes"}
          </h2>
          <p className="max-w-prose text-muted text-sm leading-relaxed">
            Dinos cuánto esperas que entre y cuánto quieres guardar. Con eso la app
            descuenta lo que ya gastaste y lo que tus facturas todavía deben, y te dice
            qué queda. <strong className="text-text">No mueve ningún saldo.</strong>
          </p>
        </div>
        {onClose ? (
          <button
            type="button"
            onClick={onClose}
            aria-label="Cerrar"
            className="grid size-8 shrink-0 place-items-center rounded-lg text-faint hover:bg-surface-raised hover:text-text"
          >
            <X className="size-4" />
          </button>
        ) : null}
      </div>

      <Field
        label="Esperas que entren"
        icon={Wallet}
        inputMode="decimal"
        value={expected}
        placeholder="5.000.000"
        onChange={(event) => setExpected(event.target.value)}
      />

      {suggestions.length > 0 ? (
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-faint text-xs">Lo que se repite en tu historial:</span>
          {suggestions.map((found) => (
            <button
              key={found.key}
              type="button"
              onClick={() => setExpected(formatAmountInput(monthlyEquivalent(found)))}
              aria-label={`Usar ${found.name} como ingreso esperado`}
              className="flex items-center gap-1.5 rounded-lg border border-line px-2 py-1 text-muted text-xs hover:bg-surface-raised hover:text-text"
            >
              <Sparkles className="size-3" />
              <span className="max-w-32 truncate">{found.name}</span>
              <Money amount={monthlyEquivalent(found)} currency={currency} size="sm" />
            </button>
          ))}
        </div>
      ) : null}

      <Field
        label="Quieres guardar"
        icon={PiggyBank}
        inputMode="decimal"
        value={target}
        placeholder="0"
        hint="Opcional. Sale de lo que puedes gastar, pero no mueve la plata a ninguna parte."
        onChange={(event) => setTarget(event.target.value)}
      />

      {error ? <p className="text-outgoing text-sm">{error}</p> : null}
      {declare.isError ? (
        <p className="text-outgoing text-sm">{declare.error.message}</p>
      ) : null}

      <Button onClick={submit} disabled={declare.isPending} full>
        {declare.isPending ? (
          <Loader2 className="size-4 animate-spin" />
        ) : income === null ? (
          <Target className="size-4" />
        ) : (
          <Check className="size-4" />
        )}
        {income === null ? "Calcular lo que me queda" : "Guardar"}
      </Button>
    </Card>
  );
}
