import { useQuery, useSuspenseQuery } from "@tanstack/react-query";
import { ArrowLeftRight } from "lucide-react";
import { useState } from "react";
import {
  type Account,
  accountsQuery,
  type Transaction,
  type TransferOptions,
  transferOptionsQuery,
  useDeclareTransfer,
} from "@/api/queries";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { Select } from "@/components/ui/Select";
import { cn } from "@/lib/cn";
import { formatDateTime } from "@/lib/dates";
import { stopsCounting, writtenSideEffect } from "@/lib/declaring";
import { formatMoney } from "@/lib/money";

type DeclareTransferBody = Parameters<
  ReturnType<typeof useDeclareTransfer>["mutate"]
>[0];

/**
 * Saying that a movement was money between two of your own balances.
 *
 * Paying a card at another bank arrives as an ordinary expense: the alert names
 * the account and the institution, never the card. So this screen asks — and
 * when the institution is the bank of one of your accounts, or the same money
 * already arrived somewhere else, it asks the specific question first.
 *
 * Three answers, and they are not interchangeable:
 * - **Pair** it with a movement already here, when both banks emailed. No
 *   balance moves, both already did.
 * - **Write** the other side on an account that never emailed. Its balance
 *   moves — on a card, the debt falls.
 * - **Outside Finflow**: it only stops counting as spending or income.
 *
 * Writing a side where the money already arrived would count it twice there,
 * which is why a matching movement on the chosen account turns the choice
 * back into pairing rather than being offered next to it.
 */
export function DeclareTransfer({ movement }: { movement: Transaction }) {
  // Not suspense: it reads the owner's whole history, and the movement is
  // worth showing while it does.
  const { data: options } = useQuery(transferOptionsQuery(movement.id));
  const { data: held } = useSuspenseQuery(accountsQuery("all"));
  const [open, setOpen] = useState(false);

  if (!options || options.refusal !== null) return null;

  const byId = new Map(held.accounts.map((account) => [account.id, account]));
  const suggested = options.accounts
    .filter((option) => option.suggested)
    .map((option) => byId.get(option.id))
    .filter((account): account is Account => account !== undefined);
  const counterpart = options.counterparts[0];

  if (open) {
    return (
      <DeclareForm
        movement={movement}
        options={options}
        accounts={byId}
        onClose={() => setOpen(false)}
      />
    );
  }

  if (counterpart) {
    return (
      <Proposal
        movement={movement}
        title="¿Es la otra mitad de este movimiento?"
        body={`El ${formatDateTime(counterpart.occurred_at)} ${
          counterpart.direction === "incoming" ? "llegaron" : "salieron"
        } ${formatMoney(counterpart.amount, counterpart.currency)} ${
          counterpart.account_id
            ? `${counterpart.direction === "incoming" ? "a" : "de"} ${byId.get(counterpart.account_id)?.name ?? "otra cuenta tuya"}`
            : `(${counterpart.counterparty})`
        }. Si es la misma plata, emparéjalos: dejan de contar como gasto e ingreso y ningún saldo cambia.`}
        confirm="Sí, es la misma plata"
        request={{ counterpart_movement_id: counterpart.id }}
        onMore={() => setOpen(true)}
      />
    );
  }

  const [only] = suggested;
  if (suggested.length === 1 && only) {
    return (
      <Proposal
        movement={movement}
        title={
          movement.direction === "outgoing"
            ? `¿Fue un pago a tu ${only.name}?`
            : `¿Vino de tu ${only.name}?`
        }
        body={`${movement.counterparty} es el banco de esa cuenta. Si lo marcas como traslado: ${stopsCounting(movement).toLowerCase()} ${writtenSideEffect(movement, only)}`}
        confirm="Sí, marcar como traslado"
        request={{ counterpart_account_id: only.id }}
        onMore={() => setOpen(true)}
      />
    );
  }

  return (
    <Button variant="quiet" full className="text-xs" onClick={() => setOpen(true)}>
      <ArrowLeftRight className="size-3.5" />
      ¿Fue un traslado entre tus cuentas?
    </Button>
  );
}

function Proposal({
  movement,
  title,
  body,
  confirm,
  request,
  onMore,
}: {
  movement: Transaction;
  title: string;
  body: string;
  confirm: string;
  request: DeclareTransferBody;
  onMore: () => void;
}) {
  const declare = useDeclareTransfer(movement.id);

  return (
    <Card glow="violet" lift={false} className="flex flex-col gap-3">
      <div className="flex items-start gap-3">
        <span
          aria-hidden
          className="grid size-9 shrink-0 place-items-center rounded-xl bg-violet/12 text-violet ring-1 ring-violet/25"
        >
          <ArrowLeftRight className="size-4" />
        </span>
        <div className="min-w-0">
          <h2 className="font-medium text-sm">{title}</h2>
          <p className="mt-1 text-muted text-sm leading-relaxed">{body}</p>
        </div>
      </div>

      {declare.error ? (
        <p role="alert" className="text-outgoing text-sm">
          {declare.error.message}
        </p>
      ) : null}

      <div className="flex flex-wrap gap-2">
        <Button
          className="py-2 text-xs"
          disabled={declare.isPending}
          onClick={() => declare.mutate(request)}
        >
          {declare.isPending ? "Guardando…" : confirm}
        </Button>
        <Button variant="quiet" className="py-2 text-xs" onClick={onMore}>
          Elegir otra opción
        </Button>
      </div>
    </Card>
  );
}

type Choice =
  | { kind: "pair"; movementId: string }
  | { kind: "account" }
  | { kind: "outside" };

function DeclareForm({
  movement,
  options,
  accounts,
  onClose,
}: {
  movement: Transaction;
  options: TransferOptions;
  accounts: Map<string, Account>;
  onClose: () => void;
}) {
  const declare = useDeclareTransfer(movement.id);
  const outgoing = movement.direction === "outgoing";
  const [choice, setChoice] = useState<Choice>(() => {
    const first = options.counterparts[0];
    if (first) return { kind: "pair", movementId: first.id };
    return options.accounts.length > 0 ? { kind: "account" } : { kind: "outside" };
  });
  const [accountId, setAccountId] = useState(options.accounts[0]?.id ?? "");
  const chosen = accounts.get(accountId);

  // The same money already sitting on the chosen account: writing another
  // side there would count it twice, so the form points at the pair instead.
  const already = options.counterparts.find(
    (candidate) => candidate.account_id === accountId,
  );

  function submit() {
    if (choice.kind === "pair") {
      declare.mutate({ counterpart_movement_id: choice.movementId });
    } else if (choice.kind === "account") {
      if (!accountId || already) return;
      declare.mutate({ counterpart_account_id: accountId });
    } else {
      declare.mutate({});
    }
  }

  return (
    <Card lift={false} className="flex flex-col gap-4">
      <div>
        <h2 className="font-medium">Marcar como traslado</h2>
        <p className="mt-1 text-faint text-xs leading-relaxed">
          {stopsCounting(movement)} El movimiento del banco se queda tal cual; si
          cambias de idea, se puede deshacer.
        </p>
      </div>

      <fieldset className="flex flex-col gap-2">
        <legend className="mb-2 text-muted text-sm">
          {outgoing ? "¿A dónde fue la plata?" : "¿De dónde vino la plata?"}
        </legend>

        {options.counterparts.map((candidate) => (
          <ChoiceButton
            key={candidate.id}
            active={choice.kind === "pair" && choice.movementId === candidate.id}
            onClick={() => setChoice({ kind: "pair", movementId: candidate.id })}
            label={`Es este: ${formatMoney(candidate.amount, candidate.currency)} · ${formatDateTime(candidate.occurred_at)}`}
            hint={`${
              candidate.account_id
                ? (accounts.get(candidate.account_id)?.name ?? "otra cuenta tuya")
                : candidate.counterparty
            }. Se emparejan y ningún saldo cambia.`}
          />
        ))}

        {options.accounts.length > 0 ? (
          <ChoiceButton
            active={choice.kind === "account"}
            onClick={() => setChoice({ kind: "account" })}
            label={
              outgoing ? "A una cuenta mía en Finflow" : "De una cuenta mía en Finflow"
            }
            hint="Finflow escribe ese lado en la cuenta que elijas y mueve su saldo."
          />
        ) : null}

        <ChoiceButton
          active={choice.kind === "outside"}
          onClick={() => setChoice({ kind: "outside" })}
          label={
            outgoing
              ? "A una cuenta mía que no llevo en Finflow"
              : "De una cuenta mía que no llevo en Finflow"
          }
          hint="Solo deja de contar en tus totales. Ningún saldo cambia."
        />
      </fieldset>

      {choice.kind === "account" ? (
        <>
          <Select
            label="Cuenta"
            value={accountId}
            onChange={(event) => setAccountId(event.target.value)}
            options={options.accounts.map((option) => ({
              value: option.id,
              label: option.suggested ? `${option.name} (sugerida)` : option.name,
            }))}
          />
          {already ? (
            <p
              role="status"
              className="rounded-xl border border-warn/30 bg-warn/8 p-3 text-sm"
            >
              En esa cuenta ya está este mismo monto el{" "}
              {formatDateTime(already.occurred_at)}. Emparéjalo arriba: escribir otro
              lado ahí contaría la plata dos veces.
            </p>
          ) : chosen ? (
            <p className="text-muted text-sm">{writtenSideEffect(movement, chosen)}</p>
          ) : null}
        </>
      ) : null}

      {declare.error ? (
        <p role="alert" className="text-outgoing text-sm">
          {declare.error.message}
        </p>
      ) : null}

      <div className="flex gap-3">
        <Button type="button" variant="ghost" full onClick={onClose}>
          Cancelar
        </Button>
        <Button
          type="button"
          full
          disabled={
            declare.isPending ||
            (choice.kind === "account" && (!accountId || !!already))
          }
          onClick={submit}
        >
          {declare.isPending ? "Guardando…" : "Marcar como traslado"}
        </Button>
      </div>
    </Card>
  );
}

function ChoiceButton({
  label,
  hint,
  active,
  onClick,
}: {
  label: string;
  hint: string;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className={cn(
        "rounded-xl border px-3 py-2.5 text-left text-sm",
        active
          ? "border-accent bg-accent-soft font-medium text-text"
          : "border-line text-muted hover:text-text",
      )}
    >
      <span className="block">{label}</span>
      <span
        className={cn("mt-0.5 block text-xs", active ? "text-text/70" : "text-faint")}
      >
        {hint}
      </span>
    </button>
  );
}
