/**
 * What is going to be charged, before it is charged.
 *
 * The screen for the spending that has no email behind it: a domiciled gym,
 * the rent, a subscription the bank stopped announcing. Declaring it here is
 * what stops the balance this app shows from drifting away from the real one,
 * month after month, on the most predictable money a person spends.
 *
 * **Declaring a bill is not money.** No balance changes and no movement is
 * recorded: the cards and the figure at the top are a forecast.
 *
 * **Confirming a charge is.** That is the one control on this screen that
 * moves money, and it lives on the timeline rather than on the cards for a
 * reason: what gets paid is one charge of one month, not the bill. A button on
 * the card would have nothing to say about *which* month, and the two the
 * owner is most likely to mean — this one and the one they forgot — are
 * exactly the two a card cannot tell apart.
 *
 * Three pieces, in the order somebody actually reads them: **what is the month
 * going to cost**, then **what do I have declared**, then **when does each one
 * land, and is it paid**. The first is one card with a bar, because "what this
 * month costs" and "what is still to pay" are two numbers that only mean
 * something against each other. The second is a grid of cards, each carrying
 * its own state. The third is a dated rail — the one shape that makes "the
 * 1st, the 4th, the 18th" read as a month passing rather than as three
 * unrelated rows — and now the place where each of those days is answered for.
 */

import { useQuery, useSuspenseQuery } from "@tanstack/react-query";
import { createFileRoute, Link, redirect } from "@tanstack/react-router";
import {
  CalendarClock,
  Check,
  CircleSlash,
  Link2,
  Loader2,
  Pause,
  Pencil,
  Play,
  Plus,
  Receipt,
  RotateCcw,
  Snowflake,
  Sparkles,
  Trash2,
  Wallet,
  X,
  Zap,
  ZapOff,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";
import {
  accountsQuery,
  type Bill,
  type BillCadence,
  type BillOccurrence,
  type BillsSettlement,
  type BillTotal,
  billsQuery,
  type ChargeProposal,
  categoriesQuery,
  type RecurringSeries,
  recurringQuery,
  useAmendBill,
  useDeclareBill,
  useForgetBill,
  useLinkCharge,
  usePauseBill,
  useSetBillAutopay,
  useSettleCharge,
  useSettleDueCharges,
} from "@/api/queries";
import {
  asDeclaration,
  type Certainty,
  certaintyOf,
  evidenceLabel,
  nextChargeLabel,
  suggestions,
} from "@/bills/detected";
import { lookOf } from "@/bills/look";
import {
  type BillState,
  billState,
  CADENCES,
  cadenceLabel,
  chargedAmount,
  chargeLabel,
  chargeVerbs,
  formatAmountInput,
  groupByDay,
  isMatched,
  isSettled,
  parseAmount,
  settledShare,
  whenLabel,
} from "@/bills/schedule";
import { AppShell } from "@/components/AppShell";
import { Money } from "@/components/Money";
import { PageHeader, type PageHelp } from "@/components/PageHeader";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { Field } from "@/components/ui/Field";
import { Select } from "@/components/ui/Select";
import { cn } from "@/lib/cn";
import { formatIsoDate, formatIsoDayMonth, todayIso } from "@/lib/dates";
import { categoryLabel, UNCATEGORIZED } from "@/merchants/categories";

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
      context.queryClient.query({ ...categoriesQuery, staleTime: "static" }),
    ]),
  component: BillsScreen,
});

function BillsScreen() {
  const { data: view } = useSuspenseQuery(billsQuery);
  const [declaring, setDeclaring] = useState(false);
  const today = todayIso();
  const settlement = useSettleDueCharges();
  const asked = useRef(false);

  // Once per visit, as the screen opens. Nothing in this deployment can walk
  // every user on a schedule yet, so the charges that fell due while nobody
  // was looking are settled while somebody *is* — which is also the only
  // moment an undo is worth anything. The ref is what stops React's double
  // mount in development from asking twice.
  useEffect(() => {
    if (asked.current) return;
    asked.current = true;
    settlement.mutate();
  }, [settlement.mutate]);

  return (
    <AppShell>
      <div className="flex flex-col gap-7">
        <PageHeader
          title="Facturas"
          lead="Lo que se cobra solo y tu banco no avisa por correo."
          help={HELP}
        />

        <Forecast totals={view.totals} />

        {settlement.data ? <Settled view={settlement.data} /> : null}
        {settlement.data ? <Proposals view={settlement.data} /> : null}

        {declaring ? (
          <BillForm onClose={() => setDeclaring(false)} />
        ) : (
          <Button onClick={() => setDeclaring(true)} full>
            <Plus className="size-4" />
            Declarar una factura
          </Button>
        )}

        {view.bills.length === 0 ? (
          <Empty />
        ) : (
          <section className="flex flex-col gap-3">
            <SectionTitle count={view.bills.length}>Declaradas</SectionTitle>
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">
              {view.bills.map((bill) => (
                <BillCard key={bill.id} bill={bill} today={today} />
              ))}
            </div>
          </section>
        )}

        {view.occurrences.length > 0 ? (
          <Timeline occurrences={view.occurrences} bills={view.bills} today={today} />
        ) : null}

        <Detected today={today} />
      </div>
    </AppShell>
  );
}

/** What the header paragraph used to say, now a tap away. */
const HELP: PageHelp = {
  id: "facturas",
  points: [
    {
      icon: Receipt,
      title: "Declarar no mueve plata",
      body: "Una factura avisa lo que viene. Ningún saldo cambia al declararla.",
    },
    {
      icon: Check,
      title: "Pagada es cuando cuenta",
      body: "Al marcar un cobro como pagado se registra el gasto y baja el saldo. Se puede deshacer.",
    },
    {
      icon: Zap,
      title: "Cobrar sola",
      body: "Espera unos días por si tu banco avisa. Si encuentra el movimiento, no agrega nada.",
    },
    {
      icon: Sparkles,
      title: "Propuestas",
      body: "Lo que se repite cada mes aparece abajo para declararlo con un toque. Nada se declara sin ti.",
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
 * What the month is committed to, as one picture.
 *
 * The bar is the point. Two figures side by side make a reader do the
 * arithmetic — is 310.000 out of 2.008.900 most of it or hardly any? — and the
 * proportion answers that before either number is read.
 *
 * What the filled part *means* changed with this delivery, and for the better:
 * it used to be days gone past, which nobody can act on. Now it is money that
 * has actually left. A month nearly over with an empty bar is somebody who has
 * paid none of their bills, which is exactly the thing worth seeing.
 */
function Forecast({ totals }: { totals: readonly BillTotal[] }) {
  if (totals.length === 0) return null;

  return (
    <Card glow="accent" lift={false} className="flex flex-col gap-5">
      {totals.map((total, index) => (
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
                Este mes
              </figcaption>
              <Money amount={total.expected} currency={total.currency} size="md" />
            </figure>
            {totals.length > 1 ? (
              <span className="shrink-0 rounded-full bg-surface-raised px-2.5 py-1 text-faint text-xs">
                {total.currency}
              </span>
            ) : null}
          </div>

          <div
            role="presentation"
            className="h-1.5 w-full overflow-hidden rounded-full bg-surface-raised"
          >
            <div
              className="h-full rounded-full bg-accent/70"
              style={{
                width: `${Math.round(
                  settledShare(total.expected, total.outstanding) * 100,
                )}%`,
              }}
            />
          </div>

          <figure className="flex items-baseline justify-between gap-3">
            <figcaption className="text-faint text-xs uppercase tracking-wider">
              Falta por pagar
            </figcaption>
            <Money
              amount={total.outstanding}
              currency={total.currency}
              size="sm"
              tone={total.outstanding === "0" ? "neutral" : "plain"}
            />
          </figure>
        </div>
      ))}

      <p className="text-faint text-xs leading-relaxed">
        La barra es lo que ya confirmaste que salió. Lo que falta sigue siendo una
        previsión hasta que lo marques.
      </p>
    </Card>
  );
}

function Empty() {
  return (
    <Card lift={false} className="flex flex-col items-center gap-3 py-12 text-center">
      <span className="grid size-12 place-items-center rounded-2xl bg-surface-raised">
        <Receipt className="size-5 text-faint" />
      </span>
      <p className="max-w-xs text-muted text-sm leading-relaxed">
        Todavía no has declarado ninguna. El gimnasio, el arriendo, el streaming: lo que
        se cobra solo y no llega por correo.
      </p>
    </Card>
  );
}

/**
 * What this visit settled on its own, with the way back.
 *
 * Only ever drawn when something happened, and it says **which** of the two
 * things happened, because they are not the same event and their undos are
 * not the same undo. «Se cobró sola» wrote a movement and moved a balance;
 * «Ya estaba pagada» recognised money that was already there and wrote
 * nothing at all.
 *
 * It is on the screen rather than only in the Telegram alert because the
 * alert can be off, and an automatic charge nobody was told about is exactly
 * the kind of surprise that makes somebody stop trusting a balance.
 */
function Settled({ view }: { view: BillsSettlement }) {
  const settle = useSettleCharge();

  if (view.settled.length === 0) return null;

  return (
    <Card lift={false} className="flex flex-col gap-3">
      <SectionTitle count={view.settled.length}>Se resolvió solo</SectionTitle>
      <ul className="flex flex-col gap-2">
        {view.settled.map((each) => (
          <li
            key={`${each.bill.id}-${each.occurrence.due_on}`}
            className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1"
          >
            <span className="flex min-w-0 flex-col">
              <span className="truncate text-sm">
                {each.bill.name}
                <span className="text-faint"> · </span>
                <span className="text-muted">
                  {formatIsoDayMonth(each.occurrence.due_on)}
                </span>
              </span>
              <span className="text-faint text-xs">
                {each.action === "charged"
                  ? "Se cobró sola: quedó un movimiento nuevo."
                  : "Ya estaba pagada por un movimiento tuyo. No se escribió nada."}
              </span>
            </span>
            <span className="flex items-center gap-2">
              <Money
                amount={chargedAmount(each.occurrence)}
                currency={each.occurrence.currency}
                size="sm"
                tone={each.occurrence.direction === "incoming" ? "positive" : "plain"}
              />
              <ChargeAction
                label={each.action === "charged" ? "Deshacer" : "No es este"}
                on={each.bill.name}
                icon={RotateCcw}
                disabled={settle.isPending}
                onClick={() =>
                  settle.mutate({
                    billId: each.bill.id,
                    period: each.occurrence.due_on,
                    action: each.action === "charged" ? "unpay" : "unlink",
                    currency: each.occurrence.currency,
                  })
                }
              />
            </span>
          </li>
        ))}
      </ul>
      {settle.isError ? (
        <span className="text-outgoing text-xs">{settle.error.message}</span>
      ) : null}
    </Card>
  );
}

/**
 * The charges the app refused to decide on its own.
 *
 * Two movements that could both be the gym, or one whose figure is nowhere
 * near what the bill says. Linking one **writes nothing**: it says that the
 * money already recorded is what this charge cost, which is the whole point —
 * confirming instead would record the same money twice.
 */
function Proposals({ view }: { view: BillsSettlement }) {
  if (view.proposals.length === 0) return null;

  return (
    <section className="flex flex-col gap-3">
      <SectionTitle count={view.proposals.length}>¿Es este el cobro?</SectionTitle>
      <p className="max-w-prose text-muted text-xs leading-relaxed">
        Estos movimientos ya están en tu historial y se parecen a un cobro que nadie ha
        respondido. Enlazarlo{" "}
        <strong className="text-text">no escribe ningún movimiento</strong>: solo dice
        que ese dinero es el de esta factura.
      </p>
      <div className="flex flex-col gap-3">
        {view.proposals.map((proposal) => (
          <Proposal
            key={`${proposal.bill_id}-${proposal.occurrence.due_on}`}
            proposal={proposal}
          />
        ))}
      </div>
    </section>
  );
}

function Proposal({ proposal }: { proposal: ChargeProposal }) {
  const link = useLinkCharge();
  const [linked, setLinked] = useState<string | null>(null);

  // The question is answered, and the card has to stop asking it. The list it
  // was drawn from is the one-shot answer of the sweep, which nothing
  // invalidates — so without this the buttons stay live and a second tap
  // quietly re-points the charge at a different movement.
  if (linked !== null) {
    const chosen = proposal.candidates.find((each) => each.movement_id === linked);

    return (
      <Card lift={false} className="flex flex-col gap-1">
        <span className="text-sm">
          {proposal.bill_name}
          <span className="text-faint"> · </span>
          <span className="text-muted">
            {formatIsoDayMonth(proposal.occurrence.due_on)}
          </span>
        </span>
        <span className="text-accent text-xs">
          Enlazado con {chosen?.counterparty ?? "ese movimiento"}. No se escribió nada.
        </span>
      </Card>
    );
  }

  return (
    <Card lift={false} className="flex flex-col gap-2">
      <span className="text-sm">
        {proposal.bill_name}
        <span className="text-faint"> · </span>
        <span className="text-muted">
          {formatIsoDayMonth(proposal.occurrence.due_on)}
        </span>
        <span className="text-faint"> · </span>
        <span className="text-muted">
          {chargeVerbs(proposal.occurrence).settle.toLowerCase()} pendiente
        </span>
      </span>

      <ul className="flex flex-col gap-1.5">
        {proposal.candidates.map((candidate) => (
          <li
            key={candidate.movement_id}
            className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1"
          >
            <span className="flex min-w-0 flex-col">
              <span className="truncate text-sm" title={candidate.counterparty}>
                {candidate.counterparty}
              </span>
              <span className="text-faint text-xs">
                {formatIsoDayMonth(candidate.occurred_on)}
                {candidate.quality === "certain" ? " · cuadra" : " · se parece"}
              </span>
            </span>
            <span className="flex items-center gap-2">
              <Money
                amount={candidate.amount}
                currency={candidate.currency}
                size="sm"
                tone="neutral"
              />
              <ChargeAction
                label="Es este"
                on={proposal.bill_name}
                icon={Link2}
                tone="accent"
                disabled={link.isPending}
                onClick={() =>
                  link.mutate(
                    {
                      billId: proposal.bill_id,
                      period: proposal.occurrence.due_on,
                      movementId: candidate.movement_id,
                    },
                    { onSuccess: () => setLinked(candidate.movement_id) },
                  )
                }
              />
            </span>
          </li>
        ))}
      </ul>

      {link.isError ? (
        <span className="text-outgoing text-xs">{link.error.message}</span>
      ) : null}
    </Card>
  );
}

/** What each state adds on top of the category's own look. */
const STATE: Record<BillState, { label: string | null; chip: string }> = {
  active: { label: null, chip: "text-faint" },
  paused: { label: "En pausa", chip: "text-faint" },
  frozen: { label: "Cuenta cerrada", chip: "text-violet" },
  overdue: { label: "Ya pasó", chip: "text-outgoing" },
};

/**
 * One bill, as a tile.
 *
 * Square-ish and in a mosaic rather than a stack of rows, because the question
 * this screen answers — "what is coming, and how much of it" — is a shape
 * question before it is a reading question. A column of identical rows has to
 * be read line by line; a grid is scanned.
 *
 * The icon and the hue come from the category (`lookOf`), so the same bill
 * looks the same every time and the eye has something to aim at. The lift and
 * the glow on hover are the app's own `.surface` behaviour, which every other
 * clickable card here already has — this one had it switched off, which is
 * what made the first version feel dead.
 *
 * The three actions are hidden until the pointer is on the tile, and **only
 * where a pointer exists**: below `sm` they stay visible, because hiding a
 * control behind a hover on a phone is hiding it for good. `focus-within`
 * brings them back for the keyboard.
 */
function BillCard({ bill, today }: { bill: Bill; today: string }) {
  const [editing, setEditing] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [arming, setArming] = useState(false);
  const pause = usePauseBill();
  const forget = useForgetBill();
  const autopay = useSetBillAutopay();
  const state = billState(bill);
  const note = STATE[state];
  const paused = bill.status === "paused";
  const look = lookOf({ category: bill.category, direction: bill.direction });
  const Icon = state === "frozen" ? Snowflake : look.icon;

  if (editing) {
    return (
      <div className="col-span-full">
        <BillForm bill={bill} onClose={() => setEditing(false)} />
      </div>
    );
  }

  return (
    <Card
      glow={paused ? "none" : look.glow}
      className={cn(
        "group flex min-h-48 flex-col gap-3 p-4",
        paused && "opacity-70",
        state === "overdue" && "border-outgoing/25",
      )}
    >
      <div className="flex items-start justify-between gap-2">
        <span
          aria-hidden
          className={cn(
            "grid size-10 shrink-0 place-items-center rounded-xl",
            "transition-transform duration-200 group-hover:scale-110",
            paused ? "bg-surface-raised text-faint" : look.badge,
          )}
        >
          <Icon className="size-[1.125rem]" />
        </span>

        <span className="flex shrink-0 flex-col items-end gap-1">
          {note.label ? (
            <span
              className={cn(
                "rounded-full bg-surface-raised px-2 py-0.5 text-[0.6875rem]",
                note.chip,
              )}
            >
              {note.label}
            </span>
          ) : null}
          {bill.autopay ? (
            <span
              className="flex items-center gap-1 rounded-full bg-surface-raised px-2 py-0.5 text-[0.6875rem] text-accent"
              title="Se cobra sola unos días después de cada fecha, si ningún movimiento tuyo cuadra con el cobro."
            >
              <Zap className="size-3" />
              Se cobra sola
            </span>
          ) : null}
        </span>
      </div>

      {arming ? (
        <div className="flex flex-1 flex-col justify-end gap-2">
          {/* The warning is the point of the step. Arming is the only switch
              on this screen that ends in money moving without anybody
              pressing anything, so it says what it will do before it does
              it. */}
          <span className="text-muted text-xs leading-relaxed">
            Cada cobro <strong className="text-text">escribirá un movimiento</strong>:
            mueve el saldo y cuenta como gasto del mes. Espera unos días por si el banco
            te avisa, y empieza desde el próximo cobro.
          </span>
          <div className="flex flex-wrap gap-2">
            <Button
              variant="ghost"
              className="px-3 py-1.5 text-accent text-xs"
              disabled={autopay.isPending}
              aria-label={`Sí, cobrar sola ${bill.name}`}
              onClick={() =>
                autopay.mutate(
                  { billId: bill.id, enabled: true },
                  { onSuccess: () => setArming(false) },
                )
              }
            >
              Sí, que se cobre sola
            </Button>
            <Button
              variant="ghost"
              className="px-3 py-1.5 text-xs"
              onClick={() => setArming(false)}
            >
              Cancelar
            </Button>
          </div>
        </div>
      ) : confirming ? (
        <div className="flex flex-1 flex-col justify-end gap-2">
          <span className="text-muted text-xs leading-relaxed">
            ¿Borrar «{bill.name}»? No borra ningún movimiento.
          </span>
          <div className="flex flex-wrap gap-2">
            <Button
              variant="ghost"
              className="px-3 py-1.5 text-outgoing text-xs"
              disabled={forget.isPending}
              aria-label={`Sí, borrar ${bill.name}`}
              onClick={() => forget.mutate(bill.id)}
            >
              Sí, borrar
            </Button>
            <Button
              variant="ghost"
              className="px-3 py-1.5 text-xs"
              onClick={() => setConfirming(false)}
            >
              Cancelar
            </Button>
          </div>
        </div>
      ) : (
        <>
          <div className="flex min-w-0 flex-1 flex-col gap-1">
            <span className="truncate font-medium text-sm" title={bill.name}>
              {bill.name}
            </span>
            <Money
              amount={bill.amount}
              currency={bill.currency}
              size="sm"
              tone={bill.direction === "incoming" ? "positive" : "plain"}
            />
            <span className="truncate text-faint text-xs">
              {cadenceLabel(bill.cadence)}
            </span>
            {bill.next_occurrence ? (
              <span
                className={cn(
                  "truncate text-xs",
                  state === "overdue" ? "text-outgoing" : "text-muted",
                )}
              >
                {formatIsoDayMonth(bill.next_occurrence.due_on)},{" "}
                {whenLabel(bill.next_occurrence.due_on, today)}
              </span>
            ) : (
              <span className="truncate text-faint text-xs">Sin próximo cobro</span>
            )}
          </div>

          {/* At the foot and faint: the tile is about what is coming, not
              about its own buttons. They come up to full strength under the
              pointer, and stay legible without one — hiding a control behind
              a hover on a phone is hiding it for good. */}
          <div className="-mr-1 flex justify-end gap-0.5 opacity-60 transition-opacity duration-200 group-hover:opacity-100 group-focus-within:opacity-100">
            <IconAction
              label="Editar"
              on={bill.name}
              icon={Pencil}
              onClick={() => setEditing(true)}
            />
            <IconAction
              label={bill.autopay ? "No cobrar sola" : "Cobrar sola"}
              on={bill.name}
              icon={bill.autopay ? ZapOff : Zap}
              disabled={autopay.isPending || paused}
              onClick={() =>
                bill.autopay
                  ? autopay.mutate({ billId: bill.id, enabled: false })
                  : setArming(true)
              }
            />
            <IconAction
              label={paused ? "Reanudar" : "Pausar"}
              on={bill.name}
              icon={paused ? Play : Pause}
              disabled={pause.isPending}
              onClick={() => pause.mutate({ billId: bill.id, paused: !paused })}
            />
            <IconAction
              label="Borrar"
              on={bill.name}
              icon={Trash2}
              tone="danger"
              onClick={() => setConfirming(true)}
            />
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
  disabled = false,
  tone = "plain",
}: {
  label: string;
  on: string;
  icon: typeof Pencil;
  onClick: () => void;
  disabled?: boolean;
  tone?: "plain" | "danger";
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      aria-label={`${label} ${on}`}
      title={label}
      className={cn(
        "grid size-8 place-items-center rounded-lg transition-colors",
        "hover:bg-surface-raised disabled:cursor-not-allowed disabled:opacity-45",
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
 * Declaring and correcting, in one form.
 *
 * Two forms would be two places for the same fields to disagree; what changes
 * between them is only which mutation runs and what the fields start as.
 */
function BillForm({ bill, onClose }: { bill?: Bill; onClose: () => void }) {
  const { data: accounts } = useSuspenseQuery(accountsQuery("open"));
  const { data: categories } = useSuspenseQuery(categoriesQuery);
  const declare = useDeclareBill();
  const amend = useAmendBill(bill?.id ?? "");
  const saving = bill ? amend : declare;

  const [name, setName] = useState(bill?.name ?? "");
  const [amount, setAmount] = useState(bill ? formatAmountInput(bill.amount) : "");
  const [cadence, setCadence] = useState<BillCadence>(bill?.cadence ?? "monthly");
  const [startsOn, setStartsOn] = useState(bill?.starts_on ?? todayIso());
  const [accountId, setAccountId] = useState(bill?.account_id ?? "");
  // The category is what the tile draws its icon and its hue from, and what a
  // confirmed charge will carry into the reports. Empty is a real answer.
  const [category, setCategory] = useState(bill?.category ?? "");
  const [error, setError] = useState<string | null>(null);

  const submit = () => {
    const parsed = parseAmount(amount);

    if (name.trim() === "") return setError("Ponle un nombre.");
    if (parsed === null) {
      return setError("El monto tiene que ser un número mayor que cero.");
    }

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
          category: category === "" ? null : category,
          // Absence means "leave it alone" on a correction, so taking the
          // account off has to say so out loud.
          account_id: accountId === "" ? null : accountId,
          clear_account: accountId === "" && bill.account_id !== null,
          clear_category: category === "" && bill.category !== null,
        },
        { onSuccess },
      );

      return;
    }

    declare.mutate(
      {
        ...shared,
        direction: "outgoing",
        category: category === "" ? null : category,
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
          aria-label="Cerrar"
          className="grid size-8 place-items-center rounded-lg text-faint hover:bg-surface-raised hover:text-text"
        >
          <X className="size-4" />
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
        label="Categoría"
        value={category}
        onChange={(event) => setCategory(event.target.value)}
        placeholder="Sin categoría"
        hint="Decide el icono de la ficha, y la categoría del gasto cuando se confirme."
        options={categories.categories
          .filter((option) => option.value !== UNCATEGORIZED)
          .map((option) => ({
            value: option.value,
            label: categoryLabel(option.value, option.label),
          }))}
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

      <Button onClick={submit} disabled={saving.isPending} full>
        {saving.isPending ? (
          <Loader2 className="size-4 animate-spin" />
        ) : (
          <Check className="size-4" />
        )}
        {bill ? "Guardar" : "Declarar"}
      </Button>
    </Card>
  );
}

/**
 * The month as a dated rail, and where each charge is answered for.
 *
 * A flat list makes "the 1st, the 4th, the 18th" read as unrelated rows. A
 * rail with a node per day reads as time passing, which is what "when does
 * this land" is actually asking. Today's node is marked, so the split between
 * what has gone and what is coming is visible without reading a date.
 *
 * The buttons live here rather than on the cards because **what gets paid is
 * one charge of one month, not the bill**. A card knows nothing about which
 * month somebody means, and the two they are most likely to mean — this one
 * and the one they forgot — are exactly the two it cannot tell apart.
 */
function Timeline({
  occurrences,
  bills,
  today,
}: {
  occurrences: readonly BillOccurrence[];
  bills: readonly Bill[];
  today: string;
}) {
  const names = new Map(bills.map((bill) => [bill.id, bill.name]));

  return (
    <section className="flex flex-col gap-3">
      <SectionTitle>Cobros de este mes</SectionTitle>

      <Card lift={false} className="flex flex-col gap-0">
        {groupByDay(occurrences).map((group, index) => {
          const past = group.day < today;
          const isToday = group.day === today;

          return (
            <div
              key={group.day}
              className={cn(
                "flex gap-4 pl-1",
                index === 0 ? "pb-5" : "py-5",
                index > 0 && "border-line/60 border-t",
              )}
            >
              <span
                aria-hidden
                className={cn(
                  "mt-1.5 size-2.5 shrink-0 rounded-full",
                  isToday
                    ? "bg-accent ring-4 ring-accent/20"
                    : past
                      ? "bg-line"
                      : "bg-muted/60",
                )}
              />

              <div className="flex min-w-0 flex-1 flex-col gap-3">
                <span
                  className={cn(
                    "text-xs uppercase tracking-wider",
                    isToday ? "text-accent" : "text-faint",
                  )}
                >
                  {formatIsoDate(group.day)}
                  {isToday ? " · hoy" : ""}
                </span>

                {group.occurrences.map((occurrence) => (
                  <Charge
                    key={`${occurrence.bill_id}-${occurrence.due_on}`}
                    occurrence={occurrence}
                    name={names.get(occurrence.bill_id) ?? "Factura"}
                    past={past}
                  />
                ))}
              </div>
            </div>
          );
        })}
      </Card>
    </section>
  );
}

/**
 * One charge of one day: what it is, what it costs, and what happened to it.
 *
 * Three shapes, one per answer. Unanswered it offers **Pagar** and **Saltar**;
 * paid or skipped it says so and offers to undo. Undo is not a nicety here —
 * "Pagar" moves a balance, and a tap that cannot be taken back on a money
 * screen is how somebody stops trusting the screen.
 *
 * The figure shown once it is paid is what actually moved, not what the bill
 * projects. The two differ whenever a price went up, and showing the
 * projection back at somebody after the money left would put the screen and
 * their bank statement in disagreement.
 */
function Charge({
  occurrence,
  name,
  past,
}: {
  occurrence: BillOccurrence;
  name: string;
  past: boolean;
}) {
  const [paying, setPaying] = useState(false);
  const settle = useSettleCharge();
  const settled = isSettled(occurrence);
  const label = chargeLabel(occurrence);
  const paid = occurrence.state === "paid";
  const verbs = chargeVerbs(occurrence);

  const matched = isMatched(occurrence);
  const run = (
    action: "pay" | "unpay" | "skip" | "unskip" | "unlink",
    amount?: string,
  ) =>
    settle.mutate(
      {
        billId: occurrence.bill_id,
        period: occurrence.due_on,
        action,
        amount,
        // The charge's own currency, never a constant: the server refuses an
        // amount in a currency the bill is not in rather than converting it.
        currency: occurrence.currency,
      },
      { onSuccess: () => setPaying(false) },
    );

  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-baseline justify-between gap-3">
        <span className="flex min-w-0 items-baseline gap-2">
          <span
            className={cn(
              "min-w-0 truncate text-sm",
              settled || past ? "text-muted" : "text-text",
              occurrence.state === "skipped" && "line-through",
            )}
          >
            {name}
          </span>
          {label ? (
            <span
              className={cn(
                "shrink-0 text-[0.6875rem] uppercase tracking-wide",
                paid ? "text-accent" : "",
                occurrence.state === "overdue" ? "text-outgoing" : "",
                occurrence.state === "skipped" ? "text-faint" : "",
              )}
            >
              {label}
            </span>
          ) : null}
        </span>
        <Money
          amount={chargedAmount(occurrence)}
          currency={occurrence.currency}
          size="sm"
          tone={
            occurrence.direction === "incoming"
              ? "positive"
              : settled || past
                ? "neutral"
                : "plain"
          }
        />
      </div>

      {matched ? (
        // Worth a line of its own: this charge was answered by money that was
        // already in the ledger, so there is no movement of this app's making
        // behind it and «deshacer» does not erase anything.
        <span className="text-faint text-xs">
          Pagada con un movimiento tuyo. No se escribió nada.
        </span>
      ) : null}

      {settle.isError ? (
        <span className="text-outgoing text-xs">{settle.error.message}</span>
      ) : null}

      {paying ? (
        <PayForm
          occurrence={occurrence}
          pending={settle.isPending}
          onCancel={() => setPaying(false)}
          onConfirm={(amount) => run("pay", amount)}
        />
      ) : (
        <div className="flex flex-wrap gap-1">
          {settled ? (
            <ChargeAction
              label={paid ? verbs.undo : "Ya no saltarlo"}
              on={name}
              icon={RotateCcw}
              disabled={settle.isPending}
              onClick={() => run(paid ? (matched ? "unlink" : "unpay") : "unskip")}
            />
          ) : (
            <>
              <ChargeAction
                label={verbs.settle}
                on={name}
                icon={Check}
                tone="accent"
                disabled={settle.isPending}
                onClick={() => setPaying(true)}
              />
              <ChargeAction
                label="Saltar"
                on={name}
                icon={CircleSlash}
                disabled={settle.isPending}
                onClick={() => run("skip")}
              />
            </>
          )}
        </div>
      )}
    </div>
  );
}

/**
 * Confirming, with the chance to correct the figure.
 *
 * Prefilled with what the bill says, because that is right most months. The
 * field exists for the month it is not — and it is the *amount* and not the
 * date, because the date is the charge's identity and correcting that would
 * be a different charge. When the money moved is recorded as the charge's own
 * day; somebody who needs to move it edits the movement, where every other
 * correction in this app is made.
 */
function PayForm({
  occurrence,
  pending,
  onCancel,
  onConfirm,
}: {
  occurrence: BillOccurrence;
  pending: boolean;
  onCancel: () => void;
  onConfirm: (amount: string | undefined) => void;
}) {
  const [amount, setAmount] = useState(formatAmountInput(occurrence.amount));
  const [error, setError] = useState<string | null>(null);

  const submit = () => {
    const parsed = parseAmount(amount);

    if (parsed === null) {
      setError("El monto tiene que ser un número mayor que cero.");

      return;
    }

    setError(null);
    onConfirm(parsed === occurrence.amount ? undefined : parsed);
  };

  const incoming = occurrence.direction === "incoming";

  return (
    <div className="flex flex-col gap-2 rounded-lg bg-surface-raised/60 p-3">
      {/* Said here rather than once at the top of the screen, because this is
          the moment it matters: the difference between «anoté cuánto me va a
          costar» and «se movió mi plata» is exactly what somebody gets wrong
          the first time they press this. And if planning is what they were
          after, the screen that does it has a name. */}
      <p className="text-faint text-xs leading-relaxed">
        {incoming ? "Confirmar" : "Pagar"}{" "}
        <strong className="text-text">escribe un movimiento nuevo</strong>: mueve el
        saldo de la cuenta, {incoming ? "cuenta como ingreso" : "cuenta como gasto"} del
        mes y te llega el aviso.{" "}
        {incoming ? null : (
          <>
            ¿Solo querías apartar la plata del mes? Eso son los{" "}
            <Link to="/presupuestos" className="text-accent hover:underline">
              Presupuestos
            </Link>
            , y ahí nada se mueve.
          </>
        )}
      </p>

      <label className="flex items-center gap-2 text-faint text-xs">
        <span className="shrink-0">{chargeVerbs(occurrence).amount}</span>
        <input
          inputMode="decimal"
          value={amount}
          aria-label={`Monto cobrado el ${occurrence.due_on}`}
          onChange={(event) => setAmount(event.target.value)}
          className="w-full min-w-0 rounded-md border border-line bg-surface px-2 py-1 text-sm text-text tabular-nums"
        />
      </label>

      {error ? <span className="text-outgoing text-xs">{error}</span> : null}

      <div className="flex flex-wrap gap-2">
        <Button onClick={submit} disabled={pending} className="px-3 py-1.5 text-xs">
          {pending ? (
            <Loader2 className="size-3.5 animate-spin" />
          ) : (
            <Check className="size-3.5" />
          )}
          Confirmar
        </Button>
        <Button variant="ghost" className="px-3 py-1.5 text-xs" onClick={onCancel}>
          Cancelar
        </Button>
      </div>
    </div>
  );
}

/**
 * One action on one charge.
 *
 * The accessible name carries the bill, like the card's own buttons: a month
 * of charges is a dozen buttons called "Pagar" to anybody reading with a
 * screen reader, and that is exactly how the wrong one gets pressed.
 */
function ChargeAction({
  label,
  on,
  icon: Icon,
  onClick,
  disabled = false,
  tone = "plain",
}: {
  label: string;
  on: string;
  icon: typeof Check;
  onClick: () => void;
  disabled?: boolean;
  tone?: "plain" | "accent";
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      aria-label={`${label} ${on}`}
      className={cn(
        "flex items-center gap-1.5 rounded-lg px-2 py-1 text-xs transition-colors",
        "hover:bg-surface-raised disabled:cursor-not-allowed disabled:opacity-45",
        tone === "accent" ? "text-accent" : "text-muted hover:text-text",
      )}
    >
      <Icon className="size-3.5" />
      {label}
    </button>
  );
}

/**
 * What looks like it repeats, and is not declared yet.
 *
 * The other half of this feature, and the weaker one by design: everything
 * above is somebody's own statement about their money, and this is the app
 * guessing from what the ledger already holds. So it sits at the bottom, it
 * proposes rather than does, and **accepting one is declaring a bill** —
 * literally the same call the form above makes, with the figures filled in.
 *
 * It is an enrichment and it behaves like one. While it is loading there is
 * nothing here, and if it fails there is nothing here either: a screen whose
 * point is the month must not show an error about a suggestion.
 *
 * Empty means "not enough history yet" far more often than "you have no
 * subscriptions" — three charges at one merchant is what it takes — so
 * nothing is rendered rather than a line claiming there is nothing to find.
 */
function Detected({ today }: { today: string }) {
  const { data } = useQuery(recurringQuery);
  const found = suggestions(data?.series ?? []);

  if (found.length === 0) return null;

  return (
    <section className="flex flex-col gap-3">
      <SectionTitle count={found.filter((each) => each.bill_id === null).length}>
        Parece que se repiten
      </SectionTitle>

      <p className="max-w-prose text-muted text-sm leading-relaxed">
        Cobros que ya están en tu historial y vuelven cada cierto tiempo. Esto es una
        lectura de lo que ya pasó:{" "}
        <strong className="text-text">no declara nada por su cuenta</strong> y no mueve
        ningún saldo.
      </p>

      <Card lift={false} className="flex flex-col gap-0 p-0">
        {found.map((series, index) => (
          <Suggestion
            key={series.key}
            series={series}
            today={today}
            first={index === 0}
          />
        ))}
      </Card>
    </section>
  );
}

/** How sure the detector is, in the one word a reader acts on. */
const CERTAINTY: Record<Certainty, { label: string; tone: string }> = {
  high: { label: "Muy probable", tone: "text-accent" },
  medium: { label: "Probable", tone: "text-muted" },
  low: { label: "Puede ser", tone: "text-faint" },
};

/**
 * One suggestion: what it looks like, what it costs, and the evidence.
 *
 * The evidence line is the part that earns the trust. "4 cobros, ninguno
 * faltó" is something somebody can check against their own bank in ten
 * seconds; a percentage is a number they have to take on faith, which on a
 * money screen is the same as ignoring it.
 *
 * A variable charge says so out loud — «≈ $88.900» — because "about ninety
 * thousand" and "ninety thousand" are different promises, and declaring the
 * second when the app meant the first puts a wrong figure into the month's
 * forecast every month.
 */
function Suggestion({
  series,
  today,
  first,
}: {
  series: RecurringSeries;
  today: string;
  first: boolean;
}) {
  const declare = useDeclareBill();
  const look = lookOf({ category: series.category, direction: series.direction });
  const Icon = look.icon;
  const certainty = CERTAINTY[certaintyOf(series)];
  const declared = series.bill_id !== null;

  return (
    <div
      className={cn(
        "flex flex-col gap-3 p-4 sm:flex-row sm:items-center",
        !first && "border-line/60 border-t",
        declared && "opacity-60",
      )}
    >
      <span
        aria-hidden
        className={cn(
          "grid size-10 shrink-0 place-items-center rounded-xl",
          declared ? "bg-surface-raised text-faint" : look.badge,
        )}
      >
        <Icon className="size-[1.125rem]" />
      </span>

      <div className="flex min-w-0 flex-1 flex-col gap-1">
        <span className="flex min-w-0 flex-wrap items-baseline gap-x-2 gap-y-1">
          <span className="min-w-0 truncate font-medium text-sm" title={series.name}>
            {series.name}
          </span>
          <span className={cn("shrink-0 text-[0.6875rem]", certainty.tone)}>
            {certainty.label}
          </span>
        </span>
        <span className="text-faint text-xs leading-relaxed">
          {cadenceLabel(series.cadence)} ·{" "}
          {nextChargeLabel(series, formatIsoDayMonth(series.next_due_on), today)} ·{" "}
          {evidenceLabel(series)}
        </span>
      </div>

      <div className="flex shrink-0 items-center justify-between gap-3 sm:justify-end">
        <span className="flex items-baseline gap-1">
          {series.variable ? (
            <span className="text-muted text-sm" title="El monto cambia cada vez">
              ≈
            </span>
          ) : null}
          <Money amount={series.amount} currency={series.currency} size="sm" />
        </span>

        {declared ? (
          <span className="shrink-0 rounded-full bg-surface-raised px-2 py-0.5 text-faint text-[0.6875rem]">
            Ya declarada
          </span>
        ) : (
          <Button
            variant="ghost"
            className="shrink-0 px-3 py-1.5 text-xs"
            aria-label={`Declarar ${series.name} como factura`}
            disabled={declare.isPending}
            onClick={() => declare.mutate(asDeclaration(series))}
          >
            {declare.isPending ? (
              <Loader2 className="size-3.5 animate-spin" />
            ) : (
              <Sparkles className="size-3.5" />
            )}
            Declarar
          </Button>
        )}
      </div>

      {declare.isError ? (
        <span className="text-outgoing text-xs">{declare.error.message}</span>
      ) : null}
    </div>
  );
}
