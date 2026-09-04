import { useSuspenseQuery } from "@tanstack/react-query";
import { createFileRoute, redirect, useNavigate } from "@tanstack/react-router";
import { ArrowLeft } from "lucide-react";
import { type SubmitEvent, useState } from "react";
import {
  accountsQuery,
  categoriesQuery,
  financialCatalogQuery,
  useCreateTransaction,
  useCreateTransferLeg,
} from "@/api/queries";
import { AppShell } from "@/components/AppShell";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { Field } from "@/components/ui/Field";
import { Select } from "@/components/ui/Select";
import { TextArea } from "@/components/ui/TextArea";
import { cn } from "@/lib/cn";
import { fromLocalInput, nowInSeconds, toLocalInput } from "@/lib/dates";
import { CategoryPicker } from "@/merchants/CategoryPicker";

export const Route = createFileRoute("/transacciones/nueva")({
  beforeLoad: ({ context }) => {
    if (!context.session) throw redirect({ to: "/login" });
  },
  loader: ({ context }) =>
    Promise.all([
      context.queryClient.query({ ...accountsQuery("open"), staleTime: "static" }),
      context.queryClient.query({ ...financialCatalogQuery, staleTime: "static" }),
      context.queryClient.query(categoriesQuery),
    ]),
  component: NewTransactionScreen,
});

/**
 * What the form is recording. The first two are one movement of money in or
 * out; the third is a payment between two of the owner's own balances whose
 * other side this app does not hold — a card paid from another bank, from a
 * wallet, or in cash. It goes to a different endpoint and is neither spending
 * nor income, which is the whole reason it is not just a direction.
 */
type Kind = "outgoing" | "incoming" | "transfer";

/** Which side of the transfer the chosen account is. */
type Role = "source" | "destination";

function NewTransactionScreen() {
  const navigate = useNavigate();
  const { data: accounts } = useSuspenseQuery(accountsQuery("open"));
  const { data: catalog } = useSuspenseQuery(financialCatalogQuery);
  const create = useCreateTransaction();
  const createTransfer = useCreateTransferLeg();

  const [kind, setKind] = useState<Kind>("outgoing");
  const [role, setRole] = useState<Role>("destination");
  const [amount, setAmount] = useState("");
  const [counterparty, setCounterparty] = useState("");
  const [occurredAt, setOccurredAt] = useState(() => toLocalInput(nowInSeconds()));
  const [accountId, setAccountId] = useState("");
  const [currency, setCurrency] = useState("COP");
  const [bank, setBank] = useState("");
  const [category, setCategory] = useState("");
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
      // Two endpoints, because the two facts are different. A transfer leg
      // takes a `role` instead of a direction — the role fixes it, and a form
      // free to pair `source` with an incoming movement is a form free to
      // record a payment that *raises* what is owed.
      const created =
        kind === "transfer"
          ? await createTransfer.mutateAsync({
              role,
              amount,
              occurred_at: occurred,
              counterparty: counterparty.trim(),
              currency: currency as "COP" | "USD",
              // Required here, unlike below: this says a balance moved, and a
              // leg names no instrument, so nothing would ever adopt it later.
              account_id: accountId,
              bank: bank.trim(),
              note: note.trim() || null,
            })
          : await create.mutateAsync({
              direction: kind,
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
              // What this counterparty is, not what this one movement is.
              // Sending it is what keeps the movement out of the bucket
              // every breakdown by category leaves out; omitting it leaves
              // the old behaviour, where it shows a merchant only if some
              // bank email has already taught Finflow that name.
              category: category || null,
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

  const isTransfer = kind === "transfer";
  const saving = create.isPending || createTransfer.isPending;

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
              <div className="grid grid-cols-3 gap-2">
                <KindTab
                  label="Gasto"
                  active={kind === "outgoing"}
                  onClick={() => setKind("outgoing")}
                />
                <KindTab
                  label="Ingreso"
                  active={kind === "incoming"}
                  onClick={() => setKind("incoming")}
                />
                <KindTab
                  label="Traslado"
                  active={isTransfer}
                  onClick={() => setKind("transfer")}
                />
              </div>
            </fieldset>

            {isTransfer ? (
              <>
                <p className="rounded-xl border border-violet/25 bg-violet/8 p-3.5 text-muted text-xs leading-relaxed">
                  Para pagar una tarjeta o un crédito con plata que no salió de una
                  cuenta que lleves aquí — otro banco, Nequi, efectivo. No cuenta como
                  gasto ni como ingreso: solo mueve el saldo.
                  <br />
                  <span className="text-faint">
                    Si pagas una tarjeta desde una cuenta del mismo banco, no registres
                    nada: ese correo llega solo y Finflow escribe las dos mitades.
                  </span>
                </p>

                <fieldset className="flex flex-col gap-2">
                  <legend className="mb-2 text-muted text-sm">¿Qué hiciste?</legend>
                  <div className="grid gap-2 sm:grid-cols-2">
                    <KindTab
                      label="Abonar a una deuda"
                      hint="Elige la tarjeta o el crédito que pagaste: su deuda baja."
                      active={role === "destination"}
                      onClick={() => setRole("destination")}
                    />
                    <KindTab
                      label="Pagar desde una cuenta"
                      hint="Elige la cuenta de donde salió la plata: su saldo baja."
                      active={role === "source"}
                      onClick={() => setRole("source")}
                    />
                  </div>
                </fieldset>
              </>
            ) : null}

            <Field
              label={
                isTransfer
                  ? role === "destination"
                    ? "¿De dónde salió la plata?"
                    : "¿Qué pagaste?"
                  : "Descripción"
              }
              required
              maxLength={512}
              placeholder={
                isTransfer
                  ? role === "destination"
                    ? "Nequi"
                    : "Tarjeta Nu"
                  : "Supermercado La Torre"
              }
              hint={
                isTransfer
                  ? "El otro lado, como lo llames tú. No crea un comercio."
                  : "Quién cobró o pagó. Es lo que se usa para reconocer el comercio."
              }
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
              required={isTransfer}
              placeholder={isTransfer ? "Elige una cuenta" : "Sin asignar"}
              hint={
                isTransfer
                  ? "Obligatoria: es el saldo que se mueve. Un traslado no puede quedar sin asignar."
                  : "Dejarla sin asignar es válido: se adopta sola cuando declares la cuenta."
              }
              value={accountId}
              onChange={(event) => setAccountId(event.target.value)}
              options={accounts.accounts.map((account) => ({
                value: account.id,
                label: account.name,
              }))}
            />

            {/* Not on a traslado: that side names something this app does
                not hold — "Nequi", "efectivo" — it creates no comercio, and
                it is deliberately neither spending nor income, so there is
                no bucket for it to fall in. */}
            {isTransfer ? null : (
              <CategoryPicker
                label="Categoría (opcional)"
                placeholder="Sin categoría"
                hint="Se la queda el comercio, no este movimiento: los anteriores con ese mismo nombre también la toman. Sin ella, este gasto no aparece en «Gastos por categoría»."
                value={category}
                onChange={setCategory}
              />
            )}

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
              <Button type="submit" full disabled={saving}>
                {saving ? "Guardando…" : "Guardar"}
              </Button>
            </div>
          </form>
        </Card>
      </div>
    </AppShell>
  );
}

function KindTab({
  label,
  hint,
  active,
  onClick,
}: {
  label: string;
  hint?: string;
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
          ? "rounded-xl border border-accent bg-accent-soft px-3 py-2.5 text-left font-medium text-sm text-text"
          : "rounded-xl border border-line px-3 py-2.5 text-left text-muted text-sm hover:text-text"
      }
    >
      <span className={hint ? "block" : "block text-center"}>{label}</span>
      {hint ? (
        // `faint` is calibrated against the dark surface, not against the
        // accent fill an active tab carries — on that background it is barely
        // there, which a screenshot showed and a type check could not.
        <span
          className={cn("mt-0.5 block text-xs", active ? "text-text/70" : "text-faint")}
        >
          {hint}
        </span>
      ) : null}
    </button>
  );
}
