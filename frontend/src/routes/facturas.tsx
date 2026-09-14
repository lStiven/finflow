/**
 * What is going to be charged, before it is charged.
 *
 * The screen for the spending that has no email behind it: a domiciled gym,
 * the rent, a subscription the bank stopped announcing. Declaring it here is
 * what stops the balance this app shows from drifting away from the real one,
 * month after month, on the most predictable money a person spends.
 *
 * **Nothing on this screen is money that moved.** No balance changes, no
 * movement is recorded, and the two figures at the top are a forecast, not a
 * total from the ledger. Confirming a charge — which is what would make it
 * real — is the next deliverable and deliberately not here yet, so nothing on
 * this screen can be mistaken for it.
 *
 * Two figures and not one, which is the rule most worth keeping: "what this
 * month costs" and "what has not fallen due yet" are different questions and
 * a reader takes whichever is on screen to be the answer to both.
 */

import { useSuspenseQuery } from "@tanstack/react-query";
import { createFileRoute, redirect } from "@tanstack/react-router";
import {
  CalendarClock,
  Check,
  Loader2,
  Pause,
  Pencil,
  Play,
  Plus,
  Receipt,
  Trash2,
  TriangleAlert,
  Wallet,
  X,
} from "lucide-react";
import { useState } from "react";
import {
  accountsQuery,
  type Bill,
  type BillCadence,
  type BillOccurrence,
  type BillTotal,
  billsQuery,
  useAmendBill,
  useDeclareBill,
  useForgetBill,
  usePauseBill,
} from "@/api/queries";
import {
  type BillState,
  billState,
  CADENCES,
  cadenceLabel,
  formatAmountInput,
  groupByDay,
  parseAmount,
  singleTotal,
} from "@/bills/schedule";
import { AppShell } from "@/components/AppShell";
import { Money } from "@/components/Money";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { Field } from "@/components/ui/Field";
import { Select } from "@/components/ui/Select";
import { cn } from "@/lib/cn";
import { formatIsoDate, formatIsoDayMonth, todayIso } from "@/lib/dates";

/** The select hands back a string; this is where it becomes a cadence. */
function toCadence(value: string): BillCadence {
  return CADENCES.includes(value as BillCadence) ? (value as BillCadence) : "monthly";
}

export const Route = createFileRoute("/facturas")({
  beforeLoad: ({ context }) => {
    if (!context.session) throw redirect({ to: "/login" });
  },
  loader: ({ context }) =>
    Promise.all([
      context.queryClient.query(billsQuery),
      // The picker needs them, and a bill pointing at an account nobody
      // declared is refused by the server — better to offer only what exists.
      context.queryClient.query({ ...accountsQuery("open"), staleTime: "static" }),
    ]),
  component: BillsScreen,
});

function BillsScreen() {
  const { data: view } = useSuspenseQuery(billsQuery);
  const [declaring, setDeclaring] = useState(false);

  return (
    <AppShell>
      <div className="flex flex-col gap-6">
        <header className="flex flex-col gap-2">
          <h1 className="font-semibold text-2xl tracking-tight">Facturas</h1>
          <p className="max-w-prose text-muted text-sm">
            Los cobros que ya sabes que vienen y de los que el banco no te avisa por
            correo. Se declaran aquí y <strong>no mueven ningún saldo</strong> hasta que
            confirmes el pago.
          </p>
        </header>

        <Totals totals={view.totals} />

        {declaring ? (
          <BillForm onClose={() => setDeclaring(false)} />
        ) : (
          <Button onClick={() => setDeclaring(true)} full>
            <Plus className="size-4" />
            Declarar una factura
          </Button>
        )}

        {view.bills.length === 0 ? <Empty /> : <BillList bills={view.bills} />}

        {view.occurrences.length > 0 ? (
          <Upcoming occurrences={view.occurrences} bills={view.bills} />
        ) : null}
      </div>
    </AppShell>
  );
}

/**
 * The two figures.
 *
 * With one currency they sit side by side; with two or more each currency
 * gets its own row, because adding pesos to dollars needs a rate this app
 * does not have and will not invent.
 */
function Totals({ totals }: { totals: readonly BillTotal[] }) {
  if (totals.length === 0) return null;

  const only = singleTotal(totals);

  return (
    <Card glow="accent" lift={false} className="flex flex-col gap-4">
      {(only ? [only] : totals).map((total) => (
        <div key={total.currency} className="flex flex-col gap-2">
          {only ? null : (
            <span className="text-faint text-xs uppercase tracking-wider">
              {total.currency}
            </span>
          )}
          <div className="flex flex-wrap items-baseline gap-x-6 gap-y-2">
            <figure className="flex min-w-0 items-baseline gap-2">
              <figcaption className="text-faint text-xs uppercase tracking-wider">
                Este mes
              </figcaption>
              <Money amount={total.expected} currency={total.currency} size="md" />
            </figure>
            <figure className="flex min-w-0 items-baseline gap-2">
              <figcaption className="text-faint text-xs uppercase tracking-wider">
                Aún no vence
              </figcaption>
              <Money
                amount={total.upcoming}
                currency={total.currency}
                size="sm"
                tone="neutral"
              />
            </figure>
          </div>
        </div>
      ))}
      <p className="text-faint text-xs">
        Una previsión, no un gasto registrado. «Aún no vence» es lo que todavía no ha
        llegado a su fecha.
      </p>
    </Card>
  );
}

function Empty() {
  return (
    <Card lift={false} className="flex flex-col items-center gap-3 py-10 text-center">
      <Receipt className="size-8 text-faint" />
      <p className="max-w-sm text-muted text-sm">
        Todavía no has declarado ninguna. El gimnasio, el arriendo, el streaming: lo que
        se cobra solo y no llega por correo.
      </p>
    </Card>
  );
}

function BillList({ bills }: { bills: readonly Bill[] }) {
  return (
    <section className="flex flex-col gap-3">
      <h2 className="text-muted text-sm">Declaradas</h2>
      {bills.map((bill) => (
        <BillCard key={bill.id} bill={bill} />
      ))}
    </section>
  );
}

const STATE_NOTE: Record<BillState, { label: string; className: string } | null> = {
  active: null,
  paused: { label: "En pausa", className: "text-faint" },
  frozen: { label: "Cuenta cerrada", className: "text-violet" },
  overdue: { label: "Ya pasó su fecha", className: "text-outgoing" },
};

function BillCard({ bill }: { bill: Bill }) {
  const [editing, setEditing] = useState(false);
  const pause = usePauseBill();
  const forget = useForgetBill();
  const state = billState(bill);
  const note = STATE_NOTE[state];
  const paused = bill.status === "paused";

  if (editing) return <BillForm bill={bill} onClose={() => setEditing(false)} />;

  return (
    <Card lift={false} className="flex flex-col gap-3">
      <div className="flex items-start gap-3">
        <span
          aria-hidden
          className={cn(
            "grid size-9 shrink-0 place-items-center rounded-xl",
            paused ? "bg-surface-raised text-faint" : "bg-accent/12 text-accent",
          )}
        >
          {state === "frozen" ? (
            <TriangleAlert className="size-4" />
          ) : (
            <Receipt className="size-4" />
          )}
        </span>

        <div className="flex min-w-0 flex-1 flex-col gap-0.5">
          <div className="flex min-w-0 items-baseline justify-between gap-3">
            <span className="truncate font-medium">{bill.name}</span>
            <Money
              amount={bill.amount}
              currency={bill.currency}
              size="sm"
              tone={bill.direction === "incoming" ? "positive" : "plain"}
            />
          </div>
          <span className="text-faint text-xs">
            {cadenceLabel(bill.cadence)}
            {bill.next_occurrence
              ? ` · próximo ${formatIsoDayMonth(bill.next_occurrence.due_on)}`
              : ""}
          </span>
          {note ? (
            <span className={cn("text-xs", note.className)}>{note.label}</span>
          ) : null}
        </div>
      </div>

      {/* Three actions on one row at 390px. The labels stay — an icon-only
          button here would be three unlabelled squares, and one of them
          deletes. What gives way instead is the padding. */}
      <div className="flex gap-2">
        <Button
          variant="ghost"
          className="flex-1 px-2 text-sm"
          onClick={() => setEditing(true)}
        >
          <Pencil className="size-4" />
          Editar
        </Button>
        <Button
          variant="ghost"
          className="flex-1 px-2 text-sm"
          disabled={pause.isPending}
          onClick={() => pause.mutate({ billId: bill.id, paused: !paused })}
        >
          {paused ? <Play className="size-4" /> : <Pause className="size-4" />}
          {paused ? "Reanudar" : "Pausar"}
        </Button>
        <Button
          variant="ghost"
          className="flex-1 px-2 text-outgoing text-sm"
          disabled={forget.isPending}
          onClick={() => forget.mutate(bill.id)}
        >
          <Trash2 className="size-4" />
          Borrar
        </Button>
      </div>

      {state === "frozen" ? (
        <p className="text-faint text-xs">
          La cuenta de la que salía está cerrada, así que esta factura queda congelada.
          Asígnale otra cuenta o reabre la que tenía.
        </p>
      ) : null}
    </Card>
  );
}

/**
 * Declaring and correcting, in one form.
 *
 * Two forms would be two places for the same eight fields to disagree; what
 * changes between them is only which mutation runs and what the fields start
 * as.
 */
function BillForm({ bill, onClose }: { bill?: Bill; onClose: () => void }) {
  const { data: accounts } = useSuspenseQuery(accountsQuery("open"));
  const declare = useDeclareBill();
  const amend = useAmendBill(bill?.id ?? "");
  const saving = bill ? amend : declare;

  const [name, setName] = useState(bill?.name ?? "");
  const [amount, setAmount] = useState(bill ? formatAmountInput(bill.amount) : "");
  const [cadence, setCadence] = useState<BillCadence>(bill?.cadence ?? "monthly");
  const [startsOn, setStartsOn] = useState(bill?.starts_on ?? todayIso());
  const [accountId, setAccountId] = useState(bill?.account_id ?? "");
  const [error, setError] = useState<string | null>(null);

  const submit = () => {
    const parsed = parseAmount(amount);

    if (name.trim() === "") return setError("Ponle un nombre.");
    if (parsed === null)
      return setError("El monto tiene que ser un número mayor que cero.");

    setError(null);
    const onSuccess = () => onClose();
    const shared = {
      name: name.trim(),
      amount: parsed,
      currency: "COP" as const,
      cadence,
      starts_on: startsOn,
    };

    if (bill) {
      amend.mutate(
        {
          ...shared,
          direction: bill.direction,
          category: null,
          // Absence means "leave it alone" on a correction, so taking the
          // account off has to say so out loud.
          account_id: accountId === "" ? null : accountId,
          clear_account: accountId === "" && bill.account_id !== null,
          clear_category: false,
        },
        { onSuccess },
      );

      return;
    }

    declare.mutate(
      {
        ...shared,
        direction: "outgoing",
        category: null,
        account_id: accountId === "" ? null : accountId,
      },
      { onSuccess },
    );
  };

  return (
    <Card glow="cyan" lift={false} className="flex flex-col gap-4">
      <div className="flex items-center justify-between gap-3">
        <h2 className="font-medium">{bill ? "Editar factura" : "Nueva factura"}</h2>
        <button
          type="button"
          onClick={onClose}
          className="grid size-8 place-items-center rounded-lg text-faint hover:bg-surface-raised hover:text-text"
        >
          <X className="size-4" />
          <span className="sr-only">Cerrar</span>
        </button>
      </div>

      <Field
        label="Nombre"
        icon={Receipt}
        value={name}
        maxLength={120}
        placeholder="Gimnasio"
        onChange={(event) => setName(event.target.value)}
      />

      <Field
        label="Monto"
        icon={Wallet}
        inputMode="decimal"
        value={amount}
        placeholder="120.000"
        onChange={(event) => setAmount(event.target.value)}
      />

      <Select
        label="Cada cuánto"
        value={cadence}
        onChange={(event) => setCadence(toCadence(event.target.value))}
        options={CADENCES.map((value) => ({ value, label: cadenceLabel(value) }))}
      />

      <Field
        label="Primer cobro"
        icon={CalendarClock}
        type="date"
        value={startsOn}
        hint="De aquí sale el día de todos los siguientes."
        onChange={(event) => setStartsOn(event.target.value)}
      />

      <Select
        label="Sale de"
        value={accountId}
        onChange={(event) => setAccountId(event.target.value)}
        placeholder="Ninguna cuenta"
        hint="Opcional: si se paga en efectivo o no quieres atarla, déjala sin cuenta."
        options={accounts.accounts.map((account) => ({
          value: account.id,
          label: account.name,
        }))}
      />

      {error ? <p className="text-outgoing text-sm">{error}</p> : null}
      {saving.isError ? (
        <p className="text-outgoing text-sm">{saving.error.message}</p>
      ) : null}

      <div className="flex gap-2">
        <Button onClick={submit} disabled={saving.isPending} full>
          {saving.isPending ? (
            <Loader2 className="size-4 animate-spin" />
          ) : (
            <Check className="size-4" />
          )}
          {bill ? "Guardar" : "Declarar"}
        </Button>
      </div>
    </Card>
  );
}

/** Everything falling inside the month, by the day it falls on. */
function Upcoming({
  occurrences,
  bills,
}: {
  occurrences: readonly BillOccurrence[];
  bills: readonly Bill[];
}) {
  const names = new Map(bills.map((bill) => [bill.id, bill.name]));

  return (
    <section className="flex flex-col gap-3">
      <h2 className="text-muted text-sm">Cobros de este mes</h2>
      <Card lift={false} className="flex flex-col divide-y divide-line">
        {groupByDay(occurrences).map((group) => (
          <div
            key={group.day}
            className="flex flex-col gap-2 py-3 first:pt-0 last:pb-0"
          >
            <span className="text-faint text-xs uppercase tracking-wider">
              {formatIsoDate(group.day)}
            </span>
            {group.occurrences.map((occurrence) => (
              <div
                key={`${occurrence.bill_id}-${occurrence.due_on}`}
                className="flex items-center justify-between gap-3"
              >
                <span className="min-w-0 truncate text-sm">
                  {names.get(occurrence.bill_id) ?? "Factura"}
                  {occurrence.state === "overdue" ? (
                    <span className="ml-2 text-outgoing text-xs">ya pasó</span>
                  ) : null}
                </span>
                <Money
                  amount={occurrence.amount}
                  currency={occurrence.currency}
                  size="sm"
                  tone={occurrence.direction === "incoming" ? "positive" : "neutral"}
                />
              </div>
            ))}
          </div>
        ))}
      </Card>
    </section>
  );
}
