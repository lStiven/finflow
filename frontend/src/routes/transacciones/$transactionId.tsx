import { useSuspenseQuery } from "@tanstack/react-query";
import { createFileRoute, Link, redirect, useNavigate } from "@tanstack/react-router";
import {
  ArrowLeft,
  ArrowLeftRight,
  Landmark,
  Pencil,
  Trash2,
  TriangleAlert,
  Unlink,
} from "lucide-react";
import { type SubmitEvent, useEffect, useRef, useState } from "react";
import {
  type Account,
  accountsQuery,
  categoriesQuery,
  financialCatalogQuery,
  type Transaction,
  transactionQuery,
  useDeleteTransaction,
  useEditTransaction,
} from "@/api/queries";
import { AppShell } from "@/components/AppShell";
import { Money } from "@/components/Money";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { Field } from "@/components/ui/Field";
import { Select } from "@/components/ui/Select";
import { TextArea } from "@/components/ui/TextArea";
import { buildCorrection, DETACH, isEmpty } from "@/lib/correction";
import { formatDateTime, fromLocalInput, toLocalInput } from "@/lib/dates";
import { describeDeletion } from "@/lib/deletion";
import { transferBlurb, transferTitle } from "@/lib/transfers";
import { categoryLabels, labelFrom } from "@/merchants/categories";

export const Route = createFileRoute("/transacciones/$transactionId")({
  beforeLoad: ({ context }) => {
    if (!context.session) throw redirect({ to: "/login" });
  },
  loader: ({ context, params }) =>
    Promise.all([
      context.queryClient.query({
        ...transactionQuery(params.transactionId),
        staleTime: "static",
      }),
      context.queryClient.query({ ...accountsQuery("all"), staleTime: "static" }),
      context.queryClient.query({ ...financialCatalogQuery, staleTime: "static" }),
      context.queryClient.query(categoriesQuery),
    ]),
  component: TransactionScreen,
});

function TransactionScreen() {
  const { transactionId } = Route.useParams();
  const navigate = useNavigate();
  const { data: movement } = useSuspenseQuery(transactionQuery(transactionId));
  // The movement carries a category value and nothing else, so one this
  // person wrote would read as `custom:mascotas` without the names beside it.
  const { data: categories } = useSuspenseQuery(categoriesQuery);
  const labels = categoryLabels(categories.categories);
  const [editing, setEditing] = useState(false);

  const incoming = movement.direction === "incoming";
  const transfer = movement.transfer;

  return (
    <AppShell>
      <div className="mx-auto flex w-full max-w-xl flex-col gap-6">
        <Button
          variant="quiet"
          className="self-start px-0 py-0 text-xs"
          onClick={() => void navigate({ to: "/transacciones" })}
        >
          <ArrowLeft className="size-3.5" />
          Transacciones
        </Button>

        <header className="flex flex-col items-start gap-2">
          <p className="text-muted text-sm">
            {transfer
              ? transferTitle(transfer, movement.counterparty)
              : (movement.merchant?.display_name ?? movement.counterparty)}
          </p>
          <Money
            amount={incoming ? movement.amount : `-${movement.amount}`}
            currency={movement.currency}
            signed
            size="lg"
            tone={transfer ? "neutral" : incoming ? "positive" : "plain"}
          />
          <p className="text-faint text-xs">{formatDateTime(movement.occurred_at)}</p>
        </header>

        {transfer ? <TransferPanel movement={movement} /> : null}

        <Card lift={false} className="flex flex-col gap-0 p-0">
          <Row label="Tipo">
            {transfer ? "Traslado entre tus cuentas" : incoming ? "Ingreso" : "Gasto"}
          </Row>
          {/*
           * Hidden only on a pair, whose `counterparty` is machine text
           * (`credit_card *1234`) that feeds the movement's identity. On a
           * lone leg it is what its owner typed, and the only thing naming
           * the other side.
           */}
          {transfer && !transfer.external ? null : (
            <Row label={transfer ? "Otro lado" : "Contraparte"}>
              {movement.counterparty}
            </Row>
          )}
          {movement.merchant ? (
            <Row label="Comercio">
              {movement.merchant.display_name}
              {movement.merchant.needs_review ? (
                <span className="ml-2 rounded-full bg-warn/15 px-2 py-0.5 text-[0.625rem] text-warn">
                  En revisión
                </span>
              ) : null}
            </Row>
          ) : null}
          {movement.merchant?.category ? (
            <Row label="Categoría">{labelFrom(labels, movement.merchant.category)}</Row>
          ) : null}
          <Row label="Cuenta">
            {movement.account_id ? (
              <AccountName accountId={movement.account_id} />
            ) : (
              <span className="text-warn">Sin asignar</span>
            )}
          </Row>
          <Row label="Estado">
            {movement.status === "assigned" ? "En una cuenta" : "Sin asignar"}
          </Row>
          <Row label="Origen">
            {movement.origin === "manual" ? "Registrado a mano" : "Alerta del banco"}
          </Row>
          {movement.bank ? <Row label="Banco">{movement.bank}</Row> : null}
          {movement.note ? <Row label="Nota">{movement.note}</Row> : null}
        </Card>

        <Stated movement={movement} />

        {movement.account_id ? null : (
          <p className="text-faint text-xs">
            Este movimiento no está en ninguna cuenta, así que no mueve ningún saldo. Se
            adopta solo en cuanto declares la cuenta que le corresponde.
          </p>
        )}

        {editing ? (
          <EditForm movement={movement} onDone={() => setEditing(false)} />
        ) : (
          <>
            <Button variant="ghost" full onClick={() => setEditing(true)}>
              <Pencil className="size-4" />
              Corregir
            </Button>
            <DeleteSection movement={movement} />
          </>
        )}
      </div>
    </AppShell>
  );
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-start justify-between gap-4 border-line/60 border-b px-5 py-3 last:border-0">
      <span className="text-muted text-sm">{label}</span>
      <span className="min-w-0 text-right text-sm">{children}</span>
    </div>
  );
}

function AccountName({ accountId }: { accountId: string }) {
  const { data } = useSuspenseQuery(accountsQuery("all"));
  const account = data.accounts.find((candidate) => candidate.id === accountId);
  return <>{account?.name ?? accountId}</>;
}

/**
 * What the bank's own alert said, when it is no longer what the movement says.
 *
 * A correction overwrites the figure but never the record of what arrived —
 * this is the difference between a ledger and a notepad, and it is the only
 * place the original survives.
 */
function Stated({ movement }: { movement: Transaction }) {
  const stated = movement.stated;
  if (!stated) return null;

  const changed =
    stated.amount !== movement.amount ||
    stated.counterparty !== movement.counterparty ||
    stated.currency !== movement.currency ||
    stated.occurred_at !== movement.occurred_at;
  if (!changed) return null;

  return (
    <Card glow="cyan" lift={false} className="flex flex-col gap-3 border-dashed">
      <div className="flex items-center gap-2">
        <Landmark className="size-4 text-faint" aria-hidden />
        <h2 className="font-medium text-sm">Lo que dijo el banco</h2>
      </div>
      <p className="text-faint text-xs">
        Corregiste este movimiento. Esto es lo que llegó en la alerta original.
      </p>
      <dl className="flex flex-col gap-1 text-sm">
        <Pair label="Monto">
          <Money amount={stated.amount} currency={stated.currency} size="sm" />
        </Pair>
        <Pair label="Contraparte">{stated.counterparty}</Pair>
        <Pair label="Fecha">{formatDateTime(stated.occurred_at)}</Pair>
      </dl>
    </Card>
  );
}

function Pair({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex justify-between gap-4">
      <dt className="text-muted">{label}</dt>
      <dd className="text-right">{children}</dd>
    </div>
  );
}

function EditForm({ movement, onDone }: { movement: Transaction; onDone: () => void }) {
  const { data: accounts } = useSuspenseQuery(accountsQuery("all"));
  const { data: catalog } = useSuspenseQuery(financialCatalogQuery);
  const edit = useEditTransaction(movement.id);

  const [amount, setAmount] = useState(movement.amount);
  const [counterparty, setCounterparty] = useState(movement.counterparty);
  const [currency, setCurrency] = useState(movement.currency);
  const [occurredAt, setOccurredAt] = useState(toLocalInput(movement.occurred_at));
  const [account, setAccount] = useState(movement.account_id ?? "");
  const [note, setNote] = useState(movement.note ?? "");
  const [error, setError] = useState<string | null>(null);

  async function onSubmit(event: SubmitEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);

    const occurred = fromLocalInput(occurredAt);
    if (occurred === null) {
      setError("Revisa la fecha: no se pudo interpretar.");
      return;
    }

    const body = buildCorrection(
      movement,
      { amount, counterparty, currency, occurredAt, account, note },
      toLocalInput(movement.occurred_at),
      occurred,
    );

    if (isEmpty(body)) {
      onDone();
      return;
    }

    try {
      await edit.mutateAsync(body);
      onDone();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "No se pudo guardar");
    }
  }

  /*
   * A *pair* states one movement across two rows, and this screen holds one of
   * them: correcting the amount or the date here would leave the two
   * describing different movements on two balances. The API refuses that with
   * a 409, so the fields are not offered rather than offered and rejected.
   *
   * A lone leg is not that case and the API knows it — there is no second row
   * to fall out of step with, so it answers 200 and moves the one balance.
   * Gating on `external` rather than on being a transfer at all is what keeps
   * this screen from hiding fields the backend would have accepted.
   */
  const leg = movement.transfer ?? null;
  const pairedTransfer = leg !== null && !leg.external;

  /*
   * Detaching a lone leg is refused with a 409: it states that a balance
   * moved, and it names no instrument, so nothing would ever adopt it back.
   * Moving it to another account stays allowed, which is what a leg entered
   * against the wrong card actually needs.
   */
  const canDetach = Boolean(movement.account_id) && leg?.external !== true;

  return (
    <Card lift={false}>
      <form onSubmit={onSubmit} className="flex flex-col gap-5">
        <h2 className="font-medium">Corregir</h2>

        {pairedTransfer ? (
          <p className="rounded-xl border border-line bg-ink p-3.5 text-faint text-xs leading-relaxed">
            Este movimiento es una mitad de un traslado entre tus cuentas, así que el
            monto y la fecha no se corrigen por separado: las dos mitades dicen lo
            mismo. Lo que sí puedes cambiar es en qué cuenta queda y la nota.
          </p>
        ) : null}

        {pairedTransfer ? null : (
          <>
            <Field
              label="Contraparte"
              required
              // The endpoint refuses an empty one, and would reject the whole
              // correction with it.
              minLength={1}
              maxLength={512}
              value={counterparty}
              onChange={(event) => setCounterparty(event.target.value)}
            />

            <div className="grid gap-5 sm:grid-cols-2">
              <Field
                label="Monto"
                inputMode="decimal"
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
              value={occurredAt}
              onChange={(event) => setOccurredAt(event.target.value)}
            />
          </>
        )}

        <Select
          label="Cuenta"
          hint={
            leg?.external === true
              ? "Cambiarla mueve el saldo de las dos. Un traslado no puede quedarse sin cuenta."
              : movement.account_id
                ? "Quitarla la deja sin asignar y ajusta el saldo de la cuenta actual."
                : "Asignarla es retroactivo: mueve el saldo de esa cuenta."
          }
          value={account}
          onChange={(event) => setAccount(event.target.value)}
          placeholder={movement.account_id ? undefined : "Sin asignar"}
          options={[
            ...accounts.accounts.map((candidate) => ({
              value: candidate.id,
              label: candidate.name,
            })),
            ...(canDetach ? [{ value: DETACH, label: "Quitar de la cuenta" }] : []),
          ]}
        />

        <TextArea
          label="Nota"
          maxLength={512}
          value={note}
          onChange={(event) => setNote(event.target.value)}
        />

        {account === DETACH ? (
          <p className="flex items-start gap-2 text-faint text-xs">
            <Unlink className="mt-0.5 size-3.5 shrink-0" aria-hidden />
            Quedará registrado y visible, fuera de todo saldo, hasta que lo asignes a
            otra cuenta.
          </p>
        ) : null}

        {error ? (
          <p role="alert" className="text-outgoing text-sm">
            {error}
          </p>
        ) : null}

        <div className="flex gap-3">
          <Button type="button" variant="ghost" full onClick={onDone}>
            Cancelar
          </Button>
          <Button type="submit" full disabled={edit.isPending}>
            {edit.isPending ? "Guardando…" : "Guardar"}
          </Button>
        </div>
      </form>
    </Card>
  );
}

/**
 * Erasing a movement, and saying what that does before it does it.
 *
 * Deleting is the only action on this screen that cannot be undone, and the
 * only one whose effect is not visible from the button: what happens depends
 * on where the movement sits and what kind of movement it is. So the
 * confirmation is not a generic "¿seguro?" — it is the specific consequence,
 * built by `describeDeletion` from this movement, and the button itself
 * promises the right number of rows so nobody confirms one and loses two.
 *
 * Navigating away on success rather than reporting it here: the row is gone,
 * so there is no screen left to report onto, and the list behind it has
 * already been invalidated.
 */
function DeleteSection({ movement }: { movement: Transaction }) {
  const navigate = useNavigate();
  const { data: accounts } = useSuspenseQuery(accountsQuery("all"));
  const remove = useDeleteTransaction(movement.id);
  const [confirming, setConfirming] = useState(false);
  const actions = useRef<HTMLDivElement>(null);

  /*
   * The confirmation opens below the button that opened it, and on a phone
   * that puts its two buttons off the bottom of the screen — under a fixed
   * nav bar, on a short movement. A destructive choice whose buttons cannot
   * be seen reads as a screen that did nothing, so the actions are brought
   * into view. Centred rather than aligned to the bottom, which the nav
   * covers.
   */
  useEffect(() => {
    if (confirming) {
      actions.current?.scrollIntoView({ block: "center", behavior: "smooth" });
    }
  }, [confirming]);

  const account: Account | undefined = accounts.accounts.find(
    (candidate) => candidate.id === movement.account_id,
  );
  const consequence = describeDeletion(movement, account);

  async function onConfirm() {
    if (remove.isPending) return;
    try {
      await remove.mutateAsync();
      await navigate({ to: "/transacciones" });
    } catch {
      // `remove.error` carries it, and it is reported below. Staying put is
      // the point: nothing was erased, so the movement is still here.
    }
  }

  if (!confirming) {
    return (
      <Button
        variant="quiet"
        full
        className="text-outgoing text-xs hover:brightness-110"
        onClick={() => setConfirming(true)}
      >
        <Trash2 className="size-3.5" />
        Eliminar movimiento
      </Button>
    );
  }

  return (
    <Card
      lift={false}
      className="flex flex-col gap-3 border-outgoing/30 bg-outgoing/[0.06]"
    >
      <h2 className="flex items-center gap-2 font-medium text-sm">
        <TriangleAlert className="size-4 shrink-0 text-outgoing" aria-hidden />
        {consequence.rows === 2
          ? "Eliminar las dos mitades del traslado"
          : "Eliminar este movimiento"}
      </h2>

      <ul className="flex flex-col gap-2 text-sm leading-relaxed">
        <Consequence>{consequence.balance}</Consequence>
        <Consequence>{consequence.totals}</Consequence>
        {consequence.transfer ? (
          <Consequence>{consequence.transfer}</Consequence>
        ) : null}
      </ul>

      <p className="text-faint text-xs leading-relaxed">{consequence.instead}</p>

      {remove.error ? (
        <p role="alert" className="text-outgoing text-sm">
          {remove.error.message}
        </p>
      ) : null}

      <div ref={actions} className="flex flex-wrap gap-2">
        <Button
          variant="ghost"
          className="py-2 text-xs"
          disabled={remove.isPending}
          onClick={() => void onConfirm()}
        >
          <Trash2 className="size-3.5" />
          {remove.isPending ? "Eliminando…" : consequence.confirm}
        </Button>
        <Button
          variant="quiet"
          className="py-2 text-xs"
          disabled={remove.isPending}
          onClick={() => {
            // Cleared with the panel: reopening it later must not show the
            // reason a previous attempt failed as though this one had.
            remove.reset();
            setConfirming(false);
          }}
        >
          Mejor no
        </Button>
      </div>
    </Card>
  );
}

function Consequence({ children }: { children: React.ReactNode }) {
  return (
    <li className="flex items-start gap-2">
      <span aria-hidden className="mt-2 size-1 shrink-0 rounded-full bg-outgoing" />
      <span className="min-w-0">{children}</span>
    </li>
  );
}

/**
 * The half of the screen that only a transfer has.
 *
 * It answers the two questions this movement raises and nothing else does:
 * why the amount is not spending, and where its other half is. The link is
 * the point — a balance that fell and a debt that fell are one event, and
 * being able to step between them is what makes that legible.
 */
function TransferPanel({ movement }: { movement: Transaction }) {
  const transfer = movement.transfer;
  if (!transfer) return null;

  return (
    <Card glow="violet" lift={false} className="flex items-start gap-3.5">
      <span
        aria-hidden
        className="grid size-10 shrink-0 place-items-center rounded-xl bg-violet/12 text-violet ring-1 ring-violet/25"
      >
        <ArrowLeftRight className="size-4" />
      </span>

      <div className="min-w-0 flex-1">
        <p className="font-medium text-sm">
          {transfer.external
            ? "Traslado desde fuera de Finflow"
            : "Traslado entre tus cuentas"}
        </p>
        <p className="mt-1 text-muted text-sm leading-relaxed">
          {transferBlurb(transfer)} Tu patrimonio no cambió: la plata sigue siendo tuya,
          solo cambió de lado.
        </p>
        {/*
         * Only a pair has a row to step to. On a lone leg the other side is
         * not a movement here at all, so the link would go nowhere — and
         * `counterpart_movement_id` is null, which is what `external` is for.
         */}
        {transfer.counterpart_movement_id === null ? (
          <p className="mt-2 text-faint text-xs leading-relaxed">
            La otra mitad está en {movement.counterparty}, que no lleva Finflow. Por eso
            solo ves este lado.
          </p>
        ) : (
          <Link
            to="/transacciones/$transactionId"
            params={{ transactionId: transfer.counterpart_movement_id }}
            className="mt-2 inline-flex items-center gap-1.5 text-sm text-violet transition-colors hover:text-text"
          >
            Ver la otra mitad
            <ArrowLeftRight className="size-3.5" />
          </Link>
        )}
      </div>
    </Card>
  );
}
