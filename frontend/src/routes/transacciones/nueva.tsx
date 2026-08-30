import { useSuspenseQuery } from "@tanstack/react-query";
import { createFileRoute, redirect, useNavigate } from "@tanstack/react-router";
import { ArrowLeft } from "lucide-react";
import { type SubmitEvent, useState } from "react";
import {
  accountsQuery,
  financialCatalogQuery,
  useCreateTransaction,
} from "@/api/queries";
import { AppShell } from "@/components/AppShell";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { Field } from "@/components/ui/Field";
import { Select } from "@/components/ui/Select";
import { TextArea } from "@/components/ui/TextArea";
import { fromLocalInput, nowInSeconds, toLocalInput } from "@/lib/dates";

export const Route = createFileRoute("/transacciones/nueva")({
  beforeLoad: ({ context }) => {
    if (!context.session) throw redirect({ to: "/login" });
  },
  loader: ({ context }) =>
    Promise.all([
      context.queryClient.query({ ...accountsQuery("open"), staleTime: "static" }),
      context.queryClient.query({ ...financialCatalogQuery, staleTime: "static" }),
    ]),
  component: NewTransactionScreen,
});

function NewTransactionScreen() {
  const navigate = useNavigate();
  const { data: accounts } = useSuspenseQuery(accountsQuery("open"));
  const { data: catalog } = useSuspenseQuery(financialCatalogQuery);
  const create = useCreateTransaction();

  const [direction, setDirection] = useState<"outgoing" | "incoming">("outgoing");
  const [amount, setAmount] = useState("");
  const [counterparty, setCounterparty] = useState("");
  const [occurredAt, setOccurredAt] = useState(() => toLocalInput(nowInSeconds()));
  const [accountId, setAccountId] = useState("");
  const [currency, setCurrency] = useState("COP");
  const [bank, setBank] = useState("");
  const [note, setNote] = useState("");
  const [error, setError] = useState<string | null>(null);

  async function onSubmit(event: SubmitEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);

    const occurred = fromLocalInput(occurredAt);
    if (occurred === null) {
      setError("Revisa la fecha: no se pudo interpretar.");
      return;
    }

    try {
      const created = await create.mutateAsync({
        direction,
        amount,
        occurred_at: occurred,
        counterparty: counterparty.trim(),
        currency: currency as "COP" | "USD",
        // Omitted rather than sent empty: the backend treats an absent
        // account as "unassigned", which is a real state, and an empty
        // string is not a valid identifier.
        account_id: accountId || null,
        bank: bank.trim(),
        note: note.trim() || null,
      });
      void navigate({
        to: "/transacciones/$transactionId",
        params: { transactionId: created.id },
        replace: true,
      });
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "No se pudo guardar");
    }
  }

  return (
    <AppShell>
      <div className="mx-auto flex w-full max-w-xl flex-col gap-6">
        <header className="flex flex-col gap-3">
          <Button
            variant="quiet"
            className="self-start px-0 py-0 text-xs"
            onClick={() => void navigate({ to: "/transacciones" })}
          >
            <ArrowLeft className="size-3.5" />
            Transacciones
          </Button>
          <div>
            <h1 className="font-semibold text-2xl tracking-tight">Nueva transacción</h1>
            <p className="mt-1 text-muted text-sm">
              Para el efectivo y lo que el banco no avisa por correo. Todo lo demás
              llega solo.
            </p>
          </div>
        </header>

        <Card lift={false}>
          <form onSubmit={onSubmit} className="flex flex-col gap-5">
            <fieldset className="flex flex-col gap-2">
              <legend className="mb-2 text-muted text-sm">Tipo</legend>
              <div className="grid grid-cols-2 gap-2">
                <DirectionTab
                  label="Gasto"
                  active={direction === "outgoing"}
                  onClick={() => setDirection("outgoing")}
                />
                <DirectionTab
                  label="Ingreso"
                  active={direction === "incoming"}
                  onClick={() => setDirection("incoming")}
                />
              </div>
            </fieldset>

            <Field
              label="Descripción"
              required
              maxLength={512}
              placeholder="Supermercado La Torre"
              hint="Quién cobró o pagó. Es lo que se usa para reconocer el comercio."
              value={counterparty}
              onChange={(event) => setCounterparty(event.target.value)}
            />

            <div className="grid gap-5 sm:grid-cols-2">
              <Field
                label="Monto"
                required
                inputMode="decimal"
                placeholder="0"
                // Sent as a string: the API takes a decimal and a float would
                // lose cents on the way out.
                value={amount}
                onChange={(event) => setAmount(event.target.value)}
              />
              <Select
                label="Moneda"
                value={currency}
                onChange={(event) => setCurrency(event.target.value)}
                options={catalog.currencies}
              />
            </div>

            <Field
              label="Fecha y hora"
              type="datetime-local"
              required
              value={occurredAt}
              onChange={(event) => setOccurredAt(event.target.value)}
            />

            <Select
              label="Cuenta"
              placeholder="Sin asignar"
              hint="Dejarla sin asignar es válido: se adopta sola cuando declares la cuenta."
              value={accountId}
              onChange={(event) => setAccountId(event.target.value)}
              options={accounts.accounts.map((account) => ({
                value: account.id,
                label: account.name,
              }))}
            />

            <Field
              label="Banco (opcional)"
              maxLength={512}
              value={bank}
              onChange={(event) => setBank(event.target.value)}
            />

            <TextArea
              label="Nota (opcional)"
              maxLength={512}
              value={note}
              onChange={(event) => setNote(event.target.value)}
            />

            {error ? (
              <p role="alert" className="text-outgoing text-sm">
                {error}
              </p>
            ) : null}

            <div className="flex gap-3">
              <Button
                type="button"
                variant="ghost"
                full
                onClick={() => void navigate({ to: "/transacciones" })}
              >
                Cancelar
              </Button>
              <Button type="submit" full disabled={create.isPending}>
                {create.isPending ? "Guardando…" : "Guardar"}
              </Button>
            </div>
          </form>
        </Card>
      </div>
    </AppShell>
  );
}

function DirectionTab({
  label,
  active,
  onClick,
}: {
  label: string;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className={
        active
          ? "rounded-xl border border-accent bg-accent-soft py-2.5 font-medium text-sm text-text"
          : "rounded-xl border border-line py-2.5 text-muted text-sm hover:text-text"
      }
    >
      {label}
    </button>
  );
}
