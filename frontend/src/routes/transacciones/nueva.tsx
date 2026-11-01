import { useSuspenseQuery } from "@tanstack/react-query";
import { createFileRoute, Link, redirect, useNavigate } from "@tanstack/react-router";
import { ArrowLeft, ArrowLeftRight, Landmark, Mail, Wallet } from "lucide-react";
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
import { Notice } from "@/components/ui/Notice";
import { Select } from "@/components/ui/Select";
import { TextArea } from "@/components/ui/TextArea";
import { fromLocalInput, nowInSeconds, toLocalInput } from "@/lib/dates";
import { CategoryPicker } from "@/merchants/CategoryPicker";
import { OUTSIDE, resolveSides, sameBank, UNNAMED_OUTSIDE } from "@/transfers/sides";

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
 * out; the third is money its owner moved between their own balances — the
 * card paid from savings, savings topped up from Nequi. It goes to a different
 * endpoint and is neither spending nor income, which is the whole reason it
 * is not just a direction.
 */
type Kind = "outgoing" | "incoming" | "transfer";

function NewTransactionScreen() {
  const navigate = useNavigate();
  const { data: accounts } = useSuspenseQuery(accountsQuery("open"));
  const { data: catalog } = useSuspenseQuery(financialCatalogQuery);
  const create = useCreateTransaction();
  const createTransfer = useCreateTransferLeg();

  const [kind, setKind] = useState<Kind>("outgoing");
  // Each an account id, `OUTSIDE`, or empty until chosen.
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [outsideName, setOutsideName] = useState("");
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

    // Two endpoints, because the two facts are different. A transfer takes a
    // `role` instead of a direction — the role fixes it, and a form free to
    // pair `source` with an incoming movement is a form free to record a
    // payment that *raises* what is owed.
    function saveTransfer(occurred_at: number) {
      const sides = resolveSides(from, to, outsideName);
      if (!sides.ok) throw new Error(sides.error);

      return createTransfer.mutateAsync({
        ...sides.body,
        amount,
        occurred_at,
        currency: currency as "COP" | "USD",
        // A label, not part of which balance moved; a pair takes each
        // account's own.
        bank: "",
        note: note.trim() || null,
      });
    }

    try {
      const created =
        kind === "transfer"
          ? await saveTransfer(occurred)
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
  const noAccounts = accounts.accounts.length === 0;
  const accountOf = (id: string) =>
    accounts.accounts.find((account) => account.id === id);
  const nameOf = (id: string) => accountOf(id)?.name ?? "";
  // Watched, never counted: the API records the side leaving as spending.
  const paysWatchedDebt =
    accountOf(to)?.informational === true && accountOf(from)?.informational === false;

  /** Every account but the one on the other side, and «Otro» unless it is. */
  function sideOptions(other: string) {
    return [
      ...accounts.accounts
        .filter((account) => account.id !== other)
        .map((account) => ({ value: account.id, label: account.name })),
      ...(other === OUTSIDE
        ? []
        : [{ value: OUTSIDE, label: "Otro (fuera de Finflow)" }]),
    ];
  }

  // An account holds one currency and refuses any other, so choosing one
  // sets it rather than leaving a mismatch to be refused on save.
  function chooseSide(value: string, set: (value: string) => void) {
    set(value);
    const account = accountOf(value);
    if (account) setCurrency(account.currency);
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
                <Notice
                  icon={ArrowLeftRight}
                  title="No es gasto ni ingreso: la plata solo cambia de lugar"
                >
                  Elige de dónde salió y a dónde llegó. Pagar la tarjeta desde tus
                  ahorros baja las dos cosas: lo que tienes en la cuenta y lo que debes
                  en la tarjeta. Si un lado no está en Finflow —otro banco, Nequi,
                  efectivo—, elige «Otro».
                </Notice>

                {noAccounts ? (
                  <Notice tone="warn" icon={Wallet} title="Primero declara una cuenta">
                    Un traslado mueve el saldo de al menos una cuenta tuya, y todavía no
                    tienes ninguna.{" "}
                    <Link to="/cuentas/nueva" className="font-medium underline">
                      Declarar una cuenta
                    </Link>
                  </Notice>
                ) : null}

                <Select
                  label="¿De dónde salió la plata?"
                  required
                  placeholder="Elige el origen"
                  value={from}
                  onChange={(event) => chooseSide(event.target.value, setFrom)}
                  options={sideOptions(to)}
                />
                <Select
                  label="¿A dónde llegó?"
                  required
                  placeholder="Elige el destino"
                  hint="Si pagaste una tarjeta o un crédito, elígelo aquí: su deuda baja."
                  value={to}
                  onChange={(event) => chooseSide(event.target.value, setTo)}
                  options={sideOptions(from)}
                />

                {from === OUTSIDE || to === OUTSIDE ? (
                  <Field
                    label={
                      from === OUTSIDE
                        ? "¿Cómo se llama el origen? (opcional)"
                        : "¿Cómo se llama el destino? (opcional)"
                    }
                    maxLength={512}
                    placeholder="Nequi, efectivo, Davivienda…"
                    hint={`Así se verá en el movimiento. Si lo dejas vacío, queda como «${UNNAMED_OUTSIDE}».`}
                    value={outsideName}
                    onChange={(event) => setOutsideName(event.target.value)}
                  />
                ) : null}

                {/* What the API does with a cuota, said before it is saved:
                    the side leaving comes back as spending, not a traslado. */}
                {paysWatchedDebt ? (
                  <Notice icon={Landmark} title="La cuota cuenta como gasto">
                    Un crédito o una hipoteca se vigila pero no se suma, así que la
                    cuota se cuenta donde sale: en {nameOf(from)} queda como gasto, y en{" "}
                    {nameOf(to)} baja la deuda.
                  </Notice>
                ) : null}

                {/* The pair Finflow writes from that bank's own alert has an
                    identity of its own, so typing it here as well would count
                    the payment twice. Said, not blocked: the alert may never
                    come. */}
                {sameBank(from, to, accounts.accounts) ? (
                  <Notice
                    tone="warn"
                    icon={Mail}
                    title="Las dos cuentas son del mismo banco"
                  >
                    Si el banco te avisa este pago por correo, Finflow ya lo registra
                    solo, en las dos cuentas. Escríbelo aquí solo si ese correo no te
                    llega; si no, quedará contado dos veces.
                  </Notice>
                ) : null}
              </>
            ) : (
              <Field
                label="Descripción"
                required
                maxLength={512}
                placeholder="Supermercado La Torre"
                hint="Quién cobró o pagó. Es lo que se usa para reconocer el comercio."
                value={counterparty}
                onChange={(event) => setCounterparty(event.target.value)}
              />
            )}

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

            {/* Not on a traslado: its accounts are the two sides above, and it
                is deliberately neither spending nor income, so there is no
                category bucket for it to fall in and no comercio to create. */}
            {isTransfer ? null : (
              <>
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
                <CategoryPicker
                  label="Categoría (opcional)"
                  placeholder="Sin categoría"
                  hint="Se guarda en el comercio: sus otros movimientos también la toman."
                  value={category}
                  onChange={setCategory}
                />
                <Field
                  label="Banco (opcional)"
                  maxLength={512}
                  value={bank}
                  onChange={(event) => setBank(event.target.value)}
                />
              </>
            )}

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
              <Button
                type="submit"
                full
                disabled={saving || (isTransfer && noAccounts)}
              >
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
          ? "rounded-xl border border-accent bg-accent-soft px-3 py-2.5 text-center font-medium text-sm text-text"
          : "rounded-xl border border-line px-3 py-2.5 text-center text-muted text-sm hover:text-text"
      }
    >
      {label}
    </button>
  );
}
