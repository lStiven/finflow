/**
 * What a credit really costs, and what an investment really earns.
 *
 * This screen exists because a ledger alone lies about a loan. Somebody owing
 * 60 000 000 who pays 2 000 000 does not then owe 58 000 000: the month
 * charged interest on what was owed and the bank added the insurance the
 * credit carries, and only what survived those reduced the debt. Nothing in a
 * bank alert says any of that — an alert says a payment was made, never what
 * the payment was made of — so it is asked for here, once, and everything
 * else follows from it.
 *
 * The three things it has to answer, in this order:
 *
 * 1. **¿Cuánto debo hoy de verdad?** The balance plus the days since the cut
 *    that nobody has been charged for yet.
 * 2. **¿De qué se compone mi cuota?** The table, where the only column that
 *    matters is the one that actually lowers the debt.
 * 3. **¿Qué me están cobrando aparte?** Each insurance by name, because they
 *    are the part nobody reads and the part that makes a 19 % credit cost 23.
 */

import { useQuery, useSuspenseQuery } from "@tanstack/react-query";
import { createFileRoute, Link, redirect } from "@tanstack/react-router";
import {
  ArrowLeft,
  BookOpen,
  CalendarClock,
  Check,
  ChevronDown,
  Loader2,
  Percent,
  Plus,
  RefreshCw,
  Scale,
  Trash2,
  TrendingUp,
  TriangleAlert,
  Wallet,
} from "lucide-react";
import type { ReactNode } from "react";
import { type SubmitEvent, useState } from "react";
import {
  AMORTIZATION_COPY,
  CHARGE_BASIS_COPY,
  type ChargeDraft,
  emptyCharge,
  emptyInvestmentDraft,
  emptyLoanDraft,
  type FinancingIssue,
  type InvestmentDraft,
  investmentPayload,
  isFinanceable,
  type LoanDraft,
  loanPayload,
  RATE_BASIS_COPY,
  toPercent,
  validateInvestmentDraft,
  validateLoanDraft,
} from "@/accounts/financing";
import { kindCopy } from "@/accounts/kinds";
import {
  type Account,
  accountQuery,
  type ChargeAmount,
  type Financing,
  financialCatalogQuery,
  financingQuery,
  type RateBasis,
  useAccrue,
  useClearFinancing,
  useRevalue,
  useSetInvestmentTerms,
  useSetLoanTerms,
} from "@/api/queries";
import { AppShell } from "@/components/AppShell";
import { Money } from "@/components/Money";
import { PageHeader, type PageHelp } from "@/components/PageHeader";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { Field } from "@/components/ui/Field";
import { Select } from "@/components/ui/Select";
import { StatTile } from "@/components/ui/StatTile";
import { storyOf } from "@/guides/stories";
import { cn } from "@/lib/cn";
import { formatIsoDate, formatIsoDayMonth, todayIso } from "@/lib/dates";
import { isZero, signOf } from "@/lib/money";

export const Route = createFileRoute("/cuentas/$accountId/financiacion")({
  beforeLoad: ({ context }) => {
    if (!context.session) throw redirect({ to: "/login" });
  },
  loader: ({ context, params }) =>
    context.queryClient.ensureQueryData(accountQuery(params.accountId)),
  component: FinancingScreen,
});

function FinancingScreen() {
  const { accountId } = Route.useParams();
  const { data: account } = useSuspenseQuery(accountQuery(accountId));
  const shape = isFinanceable(account.kind);

  return (
    <AppShell>
      <Link
        to="/cuentas"
        className="-m-1 mb-6 inline-flex items-center gap-1.5 rounded-lg p-1 text-muted text-sm transition-colors hover:text-text"
      >
        <ArrowLeft className="size-3.5" />
        Cuentas
      </Link>

      <div className="mb-8">
        <PageHeader
          title={account.name}
          lead={
            shape === "loan"
              ? "Lo que este crédito cuesta de verdad, mes a mes."
              : shape === "investment"
                ? "Lo que esta inversión rinde, mes a mes."
                : undefined
          }
          help={
            shape === "loan"
              ? LOAN_HELP
              : shape === "investment"
                ? INVESTMENT_HELP
                : undefined
          }
        />
      </div>

      {shape === null ? <NotFinanceable account={account} /> : null}
      {shape === "loan" ? <LoanPanel account={account} /> : null}
      {shape === "investment" ? <InvestmentPanel account={account} /> : null}
    </AppShell>
  );
}

const LOAN_HELP: PageHelp = {
  id: "financiacion-credito",
  story: storyOf("cuota"),
  points: [
    {
      icon: Percent,
      title: "Pagar no baja la deuda lo mismo",
      body: "Cada mes, primero se cobran intereses y seguros; solo el resto baja lo que debes.",
    },
    {
      icon: RefreshCw,
      title: "Cada corte, un movimiento",
      body: "«Actualizar» registra los cortes pendientes, así el saldo es la suma de cosas que ves.",
    },
    {
      icon: Wallet,
      title: "Fuera de tus totales",
      body: "No suma a tu patrimonio ni a tus gastos: el gasto es la cuota que sale de tu cuenta.",
    },
  ],
};

const INVESTMENT_HELP: PageHelp = {
  id: "financiacion-inversion",
  points: [
    {
      icon: TrendingUp,
      title: "Rinde en cada corte",
      body: "Con la tasa pactada, Finflow abona el rendimiento y descuenta lo que te retienen.",
    },
    {
      icon: Scale,
      title: "Sin tasa, lo valoras tú",
      body: "Un fondo que sube y baja se actualiza a mano; la diferencia queda como movimiento.",
    },
    {
      icon: Wallet,
      title: "Suma a tu patrimonio",
      body: "A diferencia de un crédito, lo que vale una inversión sí cuenta en tus totales.",
    },
  ],
};

/**
 * The honest answer for a card, which is the kind most likely to land here.
 *
 * A credit card does charge interest, and it is deliberately not computed:
 * the interest falls on whatever part of the statement went unpaid, which
 * nothing in this app knows. Somebody who pays theirs in full owes nothing,
 * and inventing a month of interest for them would be worse than saying so.
 */
function NotFinanceable({ account }: { account: Account }) {
  const copy = kindCopy(account.kind, account.kind);

  return (
    <Card glow="none" className="flex flex-col gap-3">
      <p className="font-medium">Esta cuenta no lleva intereses calculados</p>
      <p className="text-muted text-sm leading-relaxed">
        Solo los préstamos, las hipotecas y las inversiones tienen una fórmula que
        Finflow pueda seguir mes a mes. {copy.label} no: lo que{" "}
        {account.category === "liability" ? "debes" : "tienes"} es exactamente la suma
        de sus movimientos.
      </p>
      {account.kind === "credit_card" ? (
        <p className="text-faint text-xs leading-relaxed">
          Con la tarjeta de crédito los intereses dependen de cuánto del extracto
          dejaste sin pagar, y eso no llega en ninguna alerta. Si te cobran intereses,
          regístralos como un movimiento a mano.
        </p>
      ) : null}
      <Link to="/cuentas" className="mt-2 text-cyan text-sm">
        Volver a cuentas
      </Link>
    </Card>
  );
}

/* -------------------------------------------------------------- préstamos */

function LoanPanel({ account }: { account: Account }) {
  // A plain query, not a suspense one: an account nobody has priced answers
  // 409, and that is the form below rather than an error page.
  const financing = useQuery(financingQuery(account.id, 24));
  const terms = account.loan ?? null;

  if (terms === null) return <LoanForm account={account} />;
  if (financing.data === undefined) return <Loading />;

  return (
    <div className="flex flex-col gap-6">
      <LoanFigures account={account} financing={financing.data} />
      <AccrueCard account={account} due={financing.data.periods_due} shape="loan" />
      <Schedule financing={financing.data} account={account} />
      <TermsSummary account={account} />
      <FoldedForm summary="Corregir las condiciones del crédito">
        <LoanForm account={account} compact />
      </FoldedForm>
      <ClearRow account={account} />
      <GuideLink />
    </div>
  );
}

/**
 * The three numbers somebody came here for.
 *
 * `payoff` is the one that does not exist anywhere else: the balance plus the
 * days since the cut that have accrued and not yet been charged. It is what
 * cancelling today would take, and it is always larger than the balance the
 * accounts screen shows — which is why it says so out loud.
 */
function LoanFigures({
  account,
  financing,
}: {
  account: Account;
  financing: Financing;
}) {
  const next = financing.schedule?.payments[0];

  return (
    <>
      <section aria-label="Tu deuda hoy" className="grid gap-3 sm:grid-cols-3">
        <StatTile
          label="Saldo registrado"
          icon={Wallet}
          hue="accent"
          caption={
            <span className="text-faint">
              Fuera de tu patrimonio, a propósito: esto lo sigues, no lo suma Finflow.
            </span>
          }
        >
          <Money amount={account.balance} currency={account.currency} tone="negative" />
        </StatTile>

        <StatTile
          label="Para cancelarlo hoy"
          icon={Scale}
          hue="violet"
          caption={
            isZero(financing.pending_interest) ? (
              <span className="text-faint">Sin intereses pendientes de cobrar.</span>
            ) : (
              <span className="text-faint">
                Incluye{" "}
                <Money
                  amount={financing.pending_interest}
                  currency={account.currency}
                  size="sm"
                  className="text-text text-xs"
                />{" "}
                de intereses corridos desde el último corte.
              </span>
            )
          }
        >
          {financing.payoff === null ? (
            <p className="text-muted">Nada pendiente</p>
          ) : (
            <Money
              amount={financing.payoff}
              currency={account.currency}
              tone="negative"
            />
          )}
        </StatTile>

        <StatTile
          label="Próxima cuota"
          icon={CalendarClock}
          hue="cyan"
          caption={
            financing.next_due_on === null ? undefined : (
              <span className="text-faint">
                Se paga el {formatIsoDate(financing.next_due_on)}. Corte el{" "}
                {formatIsoDate(financing.next_statement_on)}.
              </span>
            )
          }
        >
          {next === undefined ? (
            <p className="text-muted">Sin cuotas pendientes</p>
          ) : (
            <Money amount={next.due} currency={account.currency} />
          )}
        </StatTile>
      </section>

      {financing.schedule?.negatively_amortizing ? (
        <Card glow="accent" className="flex items-start gap-3">
          <TriangleAlert className="mt-0.5 size-4 shrink-0 text-accent" />
          <div className="text-sm leading-relaxed">
            <p className="font-medium">La cuota no alcanza a cubrir los intereses.</p>
            <p className="mt-1 text-muted">
              Con esta cuota la deuda sube cada mes en vez de bajar, por mucho que la
              pagues. Revisa la cuota o la tasa que registraste.
            </p>
          </div>
        </Card>
      ) : null}
    </>
  );
}

/**
 * The button that turns a closed month into rows.
 *
 * Safe to press twice, unlike almost everything else that writes: each charge
 * is identified by its account and its period, so a second press lands on a
 * key the ledger already holds. That is why it is a plain button with no
 * confirmation — and why the answer says how many it skipped rather than
 * pretending it did nothing.
 */
function AccrueCard({
  account,
  due,
  shape,
}: {
  account: Account;
  due: number;
  shape: "loan" | "investment";
}) {
  const accrue = useAccrue(account.id);
  const posted = accrue.data?.posted.length ?? 0;
  const owed = shape === "loan";

  return (
    <Card glow="none" lift={false} className="flex flex-col gap-4">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          <p className="font-medium">
            {owed
              ? "Intereses y seguros del mes"
              : "Rendimientos y retenciones del mes"}
          </p>
          <p className="mt-1 max-w-lg text-muted text-sm">
            {owed
              ? "Cada corte queda como movimientos: los intereses y cada seguro por su nombre."
              : "Cada corte queda como movimientos: el rendimiento y lo que te retengan."}
          </p>
          <p className="mt-2 text-faint text-xs">
            {account.accrued_through == null
              ? owed
                ? "Todavía no se ha cobrado ningún periodo."
                : "Todavía no se ha abonado ningún periodo."
              : `${owed ? "Cobrado" : "Abonado"} hasta el ${formatIsoDate(
                  account.accrued_through,
                )}.`}
          </p>

          {/* The one thing that makes every figure above too low, said where
              the button that fixes it already is. Prorating those months into
              the "intereses corridos" line instead would put six months under
              a label that reads as a few days — and understate them, because
              they compound. */}
          {due > 0 ? (
            <p className="mt-2 flex items-start gap-2 text-accent text-xs leading-relaxed">
              <TriangleAlert className="mt-0.5 size-3.5 shrink-0" />
              {owed
                ? due === 1
                  ? "Hay un corte sin registrar, así que el saldo de arriba está por debajo de lo real."
                  : `Hay ${due} cortes sin registrar, así que el saldo de arriba está por debajo de lo real.`
                : due === 1
                  ? "Hay un corte sin abonar, así que el valor de arriba está por debajo de lo real."
                  : `Hay ${due} cortes sin abonar, así que el valor de arriba está por debajo de lo real.`}
            </p>
          ) : null}
        </div>

        <Button
          onClick={() => accrue.mutate()}
          disabled={accrue.isPending}
          className="shrink-0"
        >
          {accrue.isPending ? (
            <Loader2 className="size-4 animate-spin" />
          ) : (
            <RefreshCw className="size-4" />
          )}
          Actualizar
        </Button>
      </div>

      {accrue.isSuccess ? (
        <p className="flex items-center gap-2 text-incoming text-sm">
          <Check className="size-3.5 shrink-0" />
          {posted === 0
            ? (accrue.data?.reason ?? "Ya estaba al día.")
            : `Se registraron ${posted} ${posted === 1 ? "cobro" : "cobros"}.`}
        </p>
      ) : null}
      {accrue.isError ? <Failed error={accrue.error} /> : null}
    </Card>
  );
}

/**
 * La tabla de amortización, and the only column anybody should read twice.
 *
 * `Capital` is what actually lowers the debt, and it is the whole reason the
 * table is here: everything else in the row is the month taking what it is
 * owed first. The table scrolls inside itself rather than shrinking the page,
 * and every figure is also in the row's own accessible label.
 */
function Schedule({ financing, account }: { financing: Financing; account: Account }) {
  const schedule = financing.schedule;
  if (schedule === null || schedule.payments.length === 0) return null;

  return (
    <Card glow="none" lift={false} className="flex flex-col gap-4">
      <div>
        <p className="font-medium">De qué se compone cada cuota</p>
        <p className="mt-1 text-muted text-sm leading-relaxed">
          Proyección desde el saldo de hoy, suponiendo que pagas puntual y no abonas de
          más. Solo la columna <span className="text-text">capital</span> baja la deuda.
        </p>
      </div>

      <div className="-mx-5 overflow-x-auto px-5">
        <table className="w-full min-w-[34rem] border-collapse text-sm">
          <thead>
            <tr className="border-line border-b text-faint text-xs">
              <th className="py-2 pr-4 text-left font-normal">Se paga</th>
              <th className="py-2 pr-4 text-right font-normal">Cuota</th>
              {/* Third, not fifth. On a phone the table scrolls sideways and
                  only the first three columns are visible without moving it —
                  and this is the one the whole screen is about. */}
              <th className="py-2 pr-4 text-right font-normal">Capital</th>
              <th className="py-2 pr-4 text-right font-normal">Interés</th>
              <th className="py-2 pr-4 text-right font-normal">Seguros</th>
              <th className="py-2 text-right font-normal">Queda debiendo</th>
            </tr>
          </thead>
          <tbody>
            {schedule.payments.map((payment) => (
              <tr
                key={payment.due_on}
                className="border-line/60 border-b last:border-0"
              >
                <td className="py-2.5 pr-4 text-muted">
                  {formatIsoDayMonth(payment.due_on)}
                </td>
                <td className="py-2.5 pr-4 text-right">
                  <Money
                    amount={payment.due}
                    currency={account.currency}
                    size="sm"
                    className="text-sm"
                  />
                </td>
                <td className="py-2.5 pr-4 text-right">
                  <Money
                    amount={payment.principal}
                    currency={account.currency}
                    size="sm"
                    className="text-cyan text-sm"
                  />
                </td>
                <td className="py-2.5 pr-4 text-right text-muted">
                  <Money
                    amount={payment.interest}
                    currency={account.currency}
                    size="sm"
                    className="text-muted text-sm"
                  />
                </td>
                <td className="py-2.5 pr-4 text-right text-muted">
                  <ChargesCell charges={payment.charges} currency={account.currency} />
                </td>
                <td className="py-2.5 text-right text-muted">
                  <Money
                    amount={payment.closing_balance}
                    currency={account.currency}
                    size="sm"
                    className="text-muted text-sm"
                  />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <dl className="grid gap-3 border-line border-t pt-4 text-sm sm:grid-cols-3">
        <Total label="Intereses en el periodo" account={account}>
          {schedule.total_interest}
        </Total>
        <Total label="Seguros y cobros" account={account}>
          {schedule.total_charges}
        </Total>
        <div>
          <dt className="text-muted text-xs">Se termina de pagar</dt>
          <dd className="mt-0.5">
            {schedule.settles_on === null
              ? "Después de lo que se ve aquí"
              : formatIsoDate(schedule.settles_on)}
          </dd>
        </div>
      </dl>
    </Card>
  );
}

function Total({
  label,
  account,
  children,
}: {
  label: string;
  account: Account;
  children: string;
}) {
  return (
    <div>
      <dt className="text-muted text-xs">{label}</dt>
      <dd className="mt-0.5">
        <Money amount={children} currency={account.currency} size="sm" />
      </dd>
    </div>
  );
}

/** Every charge of the row, named — the part of a cuota nobody reads. */
function ChargesCell({
  charges,
  currency,
}: {
  charges: ChargeAmount[];
  currency: string;
}) {
  if (charges.length === 0) return <span className="text-faint">—</span>;

  const total = charges.reduce((sum, charge) => sum + Number(charge.amount), 0);

  return (
    <span title={charges.map((charge) => charge.name).join(" · ")}>
      <Money
        amount={total.toFixed(2)}
        currency={currency}
        size="sm"
        className="text-muted text-sm"
      />
    </span>
  );
}

/* ------------------------------------------------------------ inversiones */

function InvestmentPanel({ account }: { account: Account }) {
  const financing = useQuery(financingQuery(account.id, 24));
  const terms = account.investment ?? null;

  if (terms === null) return <InvestmentForm account={account} />;
  if (financing.data === undefined) return <Loading />;

  return (
    <div className="flex flex-col gap-6">
      <InvestmentFigures account={account} financing={financing.data} />
      {terms.rate === null ? (
        <RevalueCard account={account} />
      ) : (
        <AccrueCard
          account={account}
          due={financing.data.periods_due}
          shape="investment"
        />
      )}
      <Projection financing={financing.data} account={account} />
      <TermsSummary account={account} />
      <FoldedForm summary="Corregir las condiciones de la inversión">
        <InvestmentForm account={account} compact />
      </FoldedForm>
      <ClearRow account={account} />
      <GuideLink />
    </div>
  );
}

function InvestmentFigures({
  account,
  financing,
}: {
  account: Account;
  financing: Financing;
}) {
  const performance = financing.performance;
  const gained = performance === null ? 0 : signOf(performance.earned);
  const nextReturn = financing.projection?.periods[0];
  const terms = account.investment ?? null;
  const stated = terms?.rate == null;
  const matured = terms?.matures_on != null && terms.matures_on <= financing.as_of;

  return (
    <section aria-label="Tu inversión" className="grid gap-3 sm:grid-cols-3">
      <StatTile label="Vale hoy" icon={Wallet} hue="cyan">
        <Money amount={account.balance} currency={account.currency} tone="positive" />
      </StatTile>

      <StatTile
        label="Llevas ganado"
        icon={TrendingUp}
        hue={gained < 0 ? "accent" : "green"}
        caption={
          performance === null ? undefined : isZero(performance.contributed) ? (
            <span className="text-faint">
              Desde que la registraste. Ya descontados retenciones y comisiones.
            </span>
          ) : (
            <span className="text-faint">
              Sobre{" "}
              <Money
                amount={performance.contributed}
                currency={account.currency}
                size="sm"
                className="text-text text-xs"
              />{" "}
              que has aportado. Ya descontados retenciones y comisiones.
            </span>
          )
        }
      >
        {performance === null ? (
          <p className="text-muted">—</p>
        ) : (
          <Money
            amount={performance.earned}
            currency={account.currency}
            signed
            tone={gained < 0 ? "negative" : "positive"}
          />
        )}
      </StatTile>

      <StatTile
        label={stated ? "Valor registrado" : matured ? "Ya venció" : "Próximo abono"}
        icon={CalendarClock}
        hue="violet"
        caption={
          <span className="text-faint">
            {terms?.matures_on
              ? `Vence el ${formatIsoDate(terms.matures_on)}.`
              : `Corte el ${formatIsoDate(financing.next_statement_on)}.`}
          </span>
        }
      >
        {nextReturn === undefined ? (
          <p className="text-muted">
            {stated ? "Lo registras tú" : matured ? "Ya no rinde" : "—"}
          </p>
        ) : (
          <Money
            amount={nextReturn.earned}
            currency={account.currency}
            tone="positive"
          />
        )}
      </StatTile>
    </section>
  );
}

/**
 * The only honest way to value renta variable.
 *
 * Deliberately not the same call as "corregir el saldo" on the accounts
 * screen. That one solves the opening balance backwards so the movements
 * still add up, which is right for a savings account whose history is
 * incomplete — and here it would bury the gain inside the opening balance,
 * leaving a position whose return reads as exactly zero forever.
 */
function RevalueCard({ account }: { account: Account }) {
  const revalue = useRevalue(account.id);
  const [value, setValue] = useState("");

  function onSubmit(event: SubmitEvent<HTMLFormElement>) {
    event.preventDefault();
    if (value.trim() === "") return;

    // `mutate`, not `mutateAsync`: a 400 here is shown by `isError` below,
    // and awaiting it without a catch would also raise it as an unhandled
    // rejection. Clearing the field belongs to the success path anyway.
    revalue.mutate({ market_value: value.trim() }, { onSuccess: () => setValue("") });
  }

  return (
    <Card glow="none" lift={false} className="flex flex-col gap-4">
      <div>
        <p className="font-medium">¿Cuánto vale hoy?</p>
        <p className="mt-1 max-w-lg text-muted text-sm leading-relaxed">
          Para acciones o fondos cuyo valor cambia solo. La diferencia contra lo que
          Finflow tenía registrado queda como un movimiento de valoración, para que
          puedas ver cuánto ganó o perdió y no se confunda con lo que aportaste.
        </p>
      </div>

      <form onSubmit={onSubmit} className="flex flex-wrap items-end gap-3">
        <Field
          label="Valor de hoy"
          inputMode="decimal"
          placeholder="10450000"
          value={value}
          onChange={(event) => setValue(event.target.value)}
          className="min-w-40"
        />
        <Button type="submit" disabled={revalue.isPending || value.trim() === ""}>
          {revalue.isPending ? <Loader2 className="size-4 animate-spin" /> : null}
          Registrar
        </Button>
      </form>

      {revalue.isSuccess ? (
        <p className="flex items-center gap-2 text-incoming text-sm">
          <Check className="size-3.5 shrink-0" />
          {revalue.data.posted.length === 0
            ? "Ya estaba en ese valor."
            : "Valoración registrada."}
        </p>
      ) : null}
      {revalue.isError ? <Failed error={revalue.error} /> : null}
    </Card>
  );
}

function Projection({
  financing,
  account,
}: {
  financing: Financing;
  account: Account;
}) {
  const projection = financing.projection;
  if (projection === null || projection.periods.length === 0) return null;

  return (
    <Card glow="none" lift={false} className="flex flex-col gap-4">
      <div>
        <p className="font-medium">Lo que va a rendir</p>
        <p className="mt-1 text-muted text-sm leading-relaxed">
          A la tasa que registraste, sin contar aportes nuevos.
        </p>
      </div>

      <div className="-mx-5 overflow-x-auto px-5">
        <table className="w-full min-w-[26rem] border-collapse text-sm">
          <thead>
            <tr className="border-line border-b text-faint text-xs">
              <th className="py-2 pr-4 text-left font-normal">Corte</th>
              <th className="py-2 pr-4 text-right font-normal">Rendimiento</th>
              <th className="py-2 pr-4 text-right font-normal">Retenciones</th>
              <th className="py-2 text-right font-normal">Queda en</th>
            </tr>
          </thead>
          <tbody>
            {projection.periods.map((period) => (
              <tr
                key={period.ends_on}
                className="border-line/60 border-b last:border-0"
              >
                <td className="py-2.5 pr-4 text-muted">
                  {formatIsoDayMonth(period.ends_on)}
                </td>
                <td className="py-2.5 pr-4 text-right">
                  <Money
                    amount={period.earned}
                    currency={account.currency}
                    size="sm"
                    className="text-incoming text-sm"
                  />
                </td>
                <td className="py-2.5 pr-4 text-right text-muted">
                  <ChargesCell charges={period.charges} currency={account.currency} />
                </td>
                <td className="py-2.5 text-right text-muted">
                  <Money
                    amount={period.closing_balance}
                    currency={account.currency}
                    size="sm"
                    className="text-muted text-sm"
                  />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <dl className="grid gap-3 border-line border-t pt-4 text-sm sm:grid-cols-3">
        <Total label="Rendimiento del periodo" account={account}>
          {projection.total_earned}
        </Total>
        <Total label="Retenciones y comisiones" account={account}>
          {projection.total_charges}
        </Total>
        <Total label="Quedaría en" account={account}>
          {projection.value_at_end}
        </Total>
      </dl>
    </Card>
  );
}

/* ------------------------------------------------------------ condiciones */

/** What was declared, in the words the bank used, so it can be checked. */
function TermsSummary({ account }: { account: Account }) {
  const loan = account.loan ?? null;
  const investment = account.investment ?? null;
  const rate = loan?.rate ?? investment?.rate ?? null;
  const charges = loan?.charges ?? investment?.charges ?? [];

  return (
    <Card glow="none" lift={false} className="flex flex-col gap-4">
      <p className="font-medium">Lo que registraste</p>

      <dl className="grid gap-4 text-sm sm:grid-cols-2 lg:grid-cols-3">
        {rate === null ? (
          <Detail label="Tasa">Sin tasa: el valor lo registras tú</Detail>
        ) : (
          <Detail label="Tasa">
            {toPercent(rate.value)} % {RATE_BASIS_COPY[rate.basis]?.label ?? rate.basis}
            <span className="mt-0.5 block text-faint text-xs">
              Equivale a {toPercent(rate.monthly)} % mensual ·{" "}
              {toPercent(rate.effective_annual)} % E.A.
            </span>
          </Detail>
        )}

        <Detail label="Fecha de corte">
          Día {loan?.statement_day ?? investment?.statement_day}
        </Detail>

        {loan !== null ? (
          <>
            <Detail label="Día de pago">Día {loan.payment_day}</Detail>
            <Detail label="Plazo">
              {loan.term_months} meses
              <span className="mt-0.5 block text-faint text-xs">
                Desembolsado el {formatIsoDate(loan.disbursed_on)} · termina el{" "}
                {formatIsoDate(loan.matures_on)}
              </span>
            </Detail>
            <Detail label="Forma de pago">
              {AMORTIZATION_COPY[loan.style]?.label ?? loan.style}
            </Detail>
            <Detail label="Cuota">
              {loan.installment === null ? (
                "Calculada por Finflow"
              ) : (
                <>
                  <Money
                    amount={loan.installment}
                    currency={account.currency}
                    size="sm"
                  />
                  <span className="mt-0.5 block text-faint text-xs">
                    {loan.installment_covers_charges
                      ? "Ya incluye los seguros."
                      : "Los seguros se suman aparte."}
                  </span>
                </>
              )}
            </Detail>
          </>
        ) : null}

        {investment !== null ? (
          <Detail label="Vencimiento">
            {investment.matures_on === null
              ? "Sin vencimiento"
              : formatIsoDate(investment.matures_on)}
          </Detail>
        ) : null}
      </dl>

      {charges.length > 0 ? (
        <div className="border-line border-t pt-4">
          <p className="text-muted text-xs">Cobros de cada mes</p>
          <ul className="mt-2 flex flex-col gap-2">
            {charges.map((charge) => (
              <li
                key={charge.name}
                className="flex flex-wrap items-baseline gap-x-2 text-sm"
              >
                <span>{charge.name}</span>
                <span className="text-faint text-xs">
                  {charge.rate === null
                    ? `${charge.amount} fijos cada mes`
                    : `${toPercent(charge.rate)} % ${
                        CHARGE_BASIS_COPY[charge.basis]?.short ?? charge.basis
                      }`}
                  {charge.charged_to_balance ? "" : " · lo cobran por fuera"}
                </span>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </Card>
  );
}

function Detail({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="min-w-0">
      <dt className="text-muted text-xs">{label}</dt>
      <dd className="mt-0.5">{children}</dd>
    </div>
  );
}

function FoldedForm({ summary, children }: { summary: string; children: ReactNode }) {
  const [open, setOpen] = useState(false);

  return (
    <div className="rounded-card border border-line bg-surface">
      <button
        type="button"
        onClick={() => setOpen(!open)}
        aria-expanded={open}
        className="flex w-full items-center gap-2.5 px-5 py-4 text-left"
      >
        <Percent className="size-3.5 shrink-0 text-faint" />
        <span className="min-w-0 flex-1 text-muted text-sm">{summary}</span>
        <ChevronDown
          className={cn(
            "size-3.5 shrink-0 text-faint transition-transform duration-200",
            open && "rotate-180",
          )}
        />
      </button>
      {open ? <div className="border-line border-t p-5">{children}</div> : null}
    </div>
  );
}

function ClearRow({ account }: { account: Account }) {
  const clear = useClearFinancing(account.id);
  const [confirming, setConfirming] = useState(false);

  return (
    <div className="flex flex-wrap items-center gap-3 text-sm">
      {confirming ? (
        <>
          <span className="text-muted">
            Se dejan de calcular intereses. Los meses ya cobrados quedan como están.
          </span>
          <Button
            variant="ghost"
            onClick={() => clear.mutate()}
            disabled={clear.isPending}
          >
            {clear.isPending ? <Loader2 className="size-4 animate-spin" /> : null}
            Confirmar
          </Button>
          <Button variant="quiet" onClick={() => setConfirming(false)}>
            Cancelar
          </Button>
        </>
      ) : (
        <Button variant="quiet" onClick={() => setConfirming(true)}>
          <Trash2 className="size-3.5" />
          Dejar de calcular intereses
        </Button>
      )}
      {clear.isError ? <Failed error={clear.error} /> : null}
    </div>
  );
}

function GuideLink() {
  return (
    <Link
      to="/guias/prestamos-e-inversiones"
      className="-m-1 flex items-center gap-2 self-start rounded-lg p-1 text-cyan text-sm transition-colors hover:text-text"
    >
      <BookOpen className="size-3.5" />
      Qué significa cada dato que se pide
    </Link>
  );
}

function Loading() {
  return (
    <p className="flex items-center gap-2 text-muted text-sm">
      <Loader2 className="size-4 animate-spin" />
      Calculando…
    </p>
  );
}

function Failed({ error }: { error: Error | null }) {
  if (error === null) return null;

  return (
    <p className="flex items-start gap-2 text-outgoing text-sm">
      <TriangleAlert className="mt-0.5 size-3.5 shrink-0" />
      {error.message}
    </p>
  );
}

/* ---------------------------------------------------------------- el form */

function rateBasisOptions(values: string[]): { value: string; label: string }[] {
  return values.map((value) => ({
    value,
    label: RATE_BASIS_COPY[value]?.label ?? value,
  }));
}

/**
 * The form, which is the only place the person is asked for anything.
 *
 * Every field carries what it is *for* rather than what it is called: "la
 * tasa que sale en el contrato" beats "tasa", because the failure this form
 * exists to prevent is somebody typing a number in the wrong unit and finding
 * out three months later that their debt is wrong.
 */
function LoanForm({
  account,
  compact = false,
}: {
  account: Account;
  compact?: boolean;
}) {
  const { data: catalog } = useSuspenseQuery(financialCatalogQuery);
  const save = useSetLoanTerms(account.id);
  const [draft, setDraft] = useState<LoanDraft>(() => fromLoan(account));
  const [issues, setIssues] = useState<FinancingIssue[]>([]);
  const [rebuild, setRebuild] = useState(false);

  function set<K extends keyof LoanDraft>(key: K, value: LoanDraft[K]): void {
    setDraft((current) => ({ ...current, [key]: value }));
  }

  function onSubmit(event: SubmitEvent<HTMLFormElement>) {
    event.preventDefault();
    const found = validateLoanDraft(draft);
    setIssues(found);
    if (found.length > 0) return;

    // Nothing here needs the answer, and `mutateAsync` without a catch turns
    // a refusal the form already renders into an unhandled rejection.
    save.mutate(
      loanPayload({
        ...draft,
        accrueFrom: rebuild ? draft.disbursedOn : "",
      }),
    );
  }

  return (
    <form onSubmit={onSubmit} className="flex flex-col gap-6">
      {compact ? null : (
        <Card glow="cyan" className="flex flex-col gap-3">
          <p className="font-medium">Falta decirle a Finflow cuánto cuesta</p>
          <p className="text-muted text-sm leading-relaxed">
            Ninguna alerta del banco dice de qué se compone tu cuota: dice que pagaste,
            no cuánto de eso fue interés. Con estos datos Finflow puede cobrar cada mes
            lo que corresponde y decirte cuánto debes de verdad.
          </p>
          <Link to="/guias/prestamos-e-inversiones" className="text-cyan text-sm">
            Qué significa cada campo
          </Link>
        </Card>
      )}

      <div className="grid gap-4 sm:grid-cols-2">
        <Field
          label="Tasa de interés"
          inputMode="decimal"
          placeholder="19.56"
          hint="En porcentaje, tal como aparece en tu contrato."
          value={draft.ratePercent}
          onChange={(event) => set("ratePercent", event.target.value)}
        />
        <Select
          label="¿Cómo está expresada?"
          options={rateBasisOptions(catalog.rate_bases.map((option) => option.value))}
          hint={RATE_BASIS_COPY[draft.rateBasis]?.hint}
          value={draft.rateBasis}
          onChange={(event) => set("rateBasis", event.target.value as RateBasis)}
        />
        <Field
          label="Fecha de desembolso"
          type="date"
          hint="Cuándo te entregaron la plata."
          value={draft.disbursedOn}
          onChange={(event) => set("disbursedOn", event.target.value)}
        />
        <Field
          label="Plazo en meses"
          inputMode="numeric"
          placeholder="60"
          hint="A cuántos meses lo tomaste."
          value={draft.termMonths}
          onChange={(event) => set("termMonths", event.target.value)}
        />
        <Field
          label="Día de corte"
          inputMode="numeric"
          placeholder="15"
          hint="El día del mes en que cierra el extracto y te cobran intereses."
          value={draft.statementDay}
          onChange={(event) => set("statementDay", event.target.value)}
        />
        <Field
          label="Día de pago"
          inputMode="numeric"
          placeholder="20"
          hint="Cuándo vence la cuota. Si es el mismo del corte, déjalo vacío."
          value={draft.paymentDay}
          onChange={(event) => set("paymentDay", event.target.value)}
        />
        <Field
          label="Monto desembolsado"
          inputMode="decimal"
          placeholder="60000000"
          hint="Opcional. Lo que te prestaron al principio."
          value={draft.principal}
          onChange={(event) => set("principal", event.target.value)}
        />
        <Field
          label="Cuota mensual"
          inputMode="decimal"
          placeholder="2000000"
          hint="Opcional. Si la dejas vacía, Finflow la calcula."
          value={draft.installment}
          onChange={(event) => set("installment", event.target.value)}
        />
        <Select
          label="Forma de pago"
          options={catalog.amortization_styles.map((option) => ({
            value: option.value,
            label: AMORTIZATION_COPY[option.value]?.label ?? option.label,
          }))}
          hint={AMORTIZATION_COPY[draft.style]?.hint}
          value={draft.style}
          onChange={(event) => set("style", event.target.value)}
        />
      </div>

      <Toggle
        checked={draft.installmentCoversCharges}
        onChange={(next) => set("installmentCoversCharges", next)}
        label="La cuota ya incluye los seguros"
        hint="Casi siempre sí: el número del extracto suele traerlos dentro. Si te los cobran aparte, desmárcalo."
      />

      <Charges
        charges={draft.charges}
        issues={issues}
        currency={account.currency}
        onChange={(charges) => set("charges", charges)}
      />

      <Toggle
        checked={rebuild}
        onChange={setRebuild}
        label="Reconstruir desde el desembolso"
        hint="Solo si el saldo que registraste es el monto original. Si escribiste el saldo que te muestra el banco hoy, déjalo apagado: ese número ya trae todos los intereses cobrados hasta ahora."
      />

      <Issues issues={issues} />
      {save.isError ? <Failed error={save.error} /> : null}
      {save.isSuccess && issues.length === 0 ? (
        <p className="flex items-center gap-2 text-incoming text-sm">
          <Check className="size-3.5" />
          Guardado.
        </p>
      ) : null}

      <Button type="submit" disabled={save.isPending} className="self-start">
        {save.isPending ? <Loader2 className="size-4 animate-spin" /> : null}
        Guardar condiciones
      </Button>
    </form>
  );
}

function InvestmentForm({
  account,
  compact = false,
}: {
  account: Account;
  compact?: boolean;
}) {
  const { data: catalog } = useSuspenseQuery(financialCatalogQuery);
  const save = useSetInvestmentTerms(account.id);
  const [draft, setDraft] = useState<InvestmentDraft>(() => fromInvestment(account));
  const [issues, setIssues] = useState<FinancingIssue[]>([]);

  function set<K extends keyof InvestmentDraft>(
    key: K,
    value: InvestmentDraft[K],
  ): void {
    setDraft((current) => ({ ...current, [key]: value }));
  }

  function onSubmit(event: SubmitEvent<HTMLFormElement>) {
    event.preventDefault();
    const found = validateInvestmentDraft(draft);
    setIssues(found);
    if (found.length > 0) return;

    save.mutate(investmentPayload(draft));
  }

  return (
    <form onSubmit={onSubmit} className="flex flex-col gap-6">
      {compact ? null : (
        <Card glow="cyan" className="flex flex-col gap-3">
          <p className="font-medium">¿Esta inversión tiene una tasa pactada?</p>
          <p className="text-muted text-sm leading-relaxed">
            Un CDT o una cuenta remunerada sí: con la tasa, Finflow abona los
            rendimientos cada corte y descuenta la retención. Unas acciones o un fondo
            no — ahí el valor lo registras tú cuando quieras, y Finflow guarda la
            diferencia como ganancia o pérdida.
          </p>
        </Card>
      )}

      <div className="grid gap-4 sm:grid-cols-2">
        <Field
          label="Tasa"
          inputMode="decimal"
          placeholder="10.5"
          hint="En porcentaje. Déjala vacía si el valor cambia solo (acciones, fondos)."
          value={draft.ratePercent}
          onChange={(event) => set("ratePercent", event.target.value)}
        />
        <Select
          label="¿Cómo está expresada?"
          options={rateBasisOptions(catalog.rate_bases.map((option) => option.value))}
          hint={RATE_BASIS_COPY[draft.rateBasis]?.hint}
          value={draft.rateBasis}
          onChange={(event) => set("rateBasis", event.target.value as RateBasis)}
        />
        <Field
          label="Fecha de apertura"
          type="date"
          value={draft.openedOn}
          onChange={(event) => set("openedOn", event.target.value)}
        />
        <Field
          label="Día de corte"
          inputMode="numeric"
          placeholder="10"
          hint="El día en que te abonan los rendimientos."
          value={draft.statementDay}
          onChange={(event) => set("statementDay", event.target.value)}
        />
        <Field
          label="Vencimiento"
          type="date"
          hint="Opcional. Un CDT deja de rendir ese día."
          value={draft.maturesOn}
          onChange={(event) => set("maturesOn", event.target.value)}
        />
      </div>

      <Charges
        charges={draft.charges}
        issues={issues}
        currency={account.currency}
        onChange={(charges) => set("charges", charges)}
      />

      <Issues issues={issues} />
      {save.isError ? <Failed error={save.error} /> : null}

      <Button type="submit" disabled={save.isPending} className="self-start">
        {save.isPending ? <Loader2 className="size-4 animate-spin" /> : null}
        Guardar condiciones
      </Button>
    </form>
  );
}

/** The insurances and fees, which is where a credit's real cost hides. */
function Charges({
  charges,
  issues,
  currency,
  onChange,
}: {
  charges: ChargeDraft[];
  issues: FinancingIssue[];
  currency: string;
  onChange: (charges: ChargeDraft[]) => void;
}) {
  function update(index: number, patch: Partial<ChargeDraft>): void {
    onChange(
      charges.map((charge, at) => (at === index ? { ...charge, ...patch } : charge)),
    );
  }

  return (
    <div className="flex flex-col gap-4">
      <div>
        <p className="font-medium text-sm">Seguros y cobros de cada mes</p>
        <p className="mt-1 max-w-2xl text-muted text-sm leading-relaxed">
          Lo que te cobran además del interés. En una hipoteca casi siempre son dos: el
          seguro de vida deudores, que es un porcentaje del saldo, y el de incendio y
          terremoto, que se calcula sobre el valor asegurado de la vivienda y no baja
          aunque pagues.
        </p>
      </div>

      {charges.map((charge, index) => {
        const copy = CHARGE_BASIS_COPY[charge.basis];

        return (
          <Card
            key={charge.id}
            glow="none"
            lift={false}
            className="flex flex-col gap-4"
          >
            <div className="grid gap-4 sm:grid-cols-2">
              <Field
                label="Nombre"
                placeholder="Seguro de vida deudores"
                hint="Como aparece en tu extracto."
                value={charge.name}
                onChange={(event) => update(index, { name: event.target.value })}
              />
              <Select
                label="¿Sobre qué se calcula?"
                options={Object.entries(CHARGE_BASIS_COPY).map(([value, entry]) => ({
                  value,
                  label: entry.label,
                }))}
                hint={copy?.hint}
                value={charge.basis}
                onChange={(event) =>
                  update(index, {
                    basis: event.target.value as ChargeDraft["basis"],
                  })
                }
              />
              {copy?.needs === "rate" ? (
                <Field
                  label="Porcentaje mensual"
                  inputMode="decimal"
                  placeholder="0.0345"
                  hint="El del mes, no el del año."
                  value={charge.rate}
                  onChange={(event) => update(index, { rate: event.target.value })}
                />
              ) : (
                <Field
                  label={`Valor mensual (${currency})`}
                  inputMode="decimal"
                  placeholder="18500"
                  value={charge.amount}
                  onChange={(event) => update(index, { amount: event.target.value })}
                />
              )}
              {copy?.base ? (
                <Field
                  label={`Valor asegurado (${currency})`}
                  inputMode="decimal"
                  placeholder="350000000"
                  hint="El avalúo por el que está asegurada la vivienda."
                  value={charge.base}
                  onChange={(event) => update(index, { base: event.target.value })}
                />
              ) : null}
            </div>

            <Toggle
              checked={charge.chargedToBalance}
              onChange={(next) => update(index, { chargedToBalance: next })}
              label="Lo suman a la deuda"
              hint="Apágalo si te lo debitan de otra cuenta: entonces ya llega como su propio movimiento y sumarlo aquí lo cobraría dos veces."
            />

            <ChargeIssues issues={issues} index={index} />

            <Button
              variant="quiet"
              className="self-start"
              onClick={() => onChange(charges.filter((_, at) => at !== index))}
            >
              <Trash2 className="size-3.5" />
              Quitar
            </Button>
          </Card>
        );
      })}

      <Button
        variant="ghost"
        className="self-start"
        onClick={() => onChange([...charges, emptyCharge()])}
      >
        <Plus className="size-3.5" />
        Agregar un cobro
      </Button>
    </div>
  );
}

function ChargeIssues({ issues, index }: { issues: FinancingIssue[]; index: number }) {
  const mine = issues.filter((issue) =>
    String(issue.field).startsWith(`charges.${index}.`),
  );
  if (mine.length === 0) return null;

  return (
    <ul className="flex flex-col gap-1 text-outgoing text-sm">
      {mine.map((issue) => (
        <li key={String(issue.field)}>{issue.message}</li>
      ))}
    </ul>
  );
}

function Issues({ issues }: { issues: FinancingIssue[] }) {
  const general = issues.filter((issue) => !String(issue.field).startsWith("charges."));
  if (general.length === 0) return null;

  return (
    <ul className="flex flex-col gap-1.5 text-outgoing text-sm">
      {general.map((issue) => (
        <li key={String(issue.field)} className="flex items-start gap-2">
          <TriangleAlert className="mt-0.5 size-3.5 shrink-0" />
          {issue.message}
        </li>
      ))}
    </ul>
  );
}

function Toggle({
  checked,
  onChange,
  label,
  hint,
}: {
  checked: boolean;
  onChange: (next: boolean) => void;
  label: string;
  hint: string;
}) {
  return (
    <label className="flex cursor-pointer items-start gap-3">
      <input
        type="checkbox"
        checked={checked}
        onChange={(event) => onChange(event.target.checked)}
        className="mt-1 size-4 shrink-0 accent-[var(--color-accent)]"
      />
      <span className="min-w-0">
        <span className="block text-sm">{label}</span>
        <span className="mt-0.5 block text-faint text-xs leading-relaxed">{hint}</span>
      </span>
    </label>
  );
}

/* ------------------------------------------------ filling the form back in */

function fromLoan(account: Account): LoanDraft {
  const terms = account.loan ?? null;
  if (terms === null) {
    return { ...emptyLoanDraft(), disbursedOn: todayIso(), accrueFrom: "" };
  }

  return {
    ratePercent: toPercent(terms.rate.value),
    rateBasis: terms.rate.basis,
    disbursedOn: terms.disbursed_on,
    termMonths: String(terms.term_months),
    statementDay: String(terms.statement_day),
    paymentDay: String(terms.payment_day),
    style: terms.style,
    principal: terms.principal ?? "",
    installment: terms.installment ?? "",
    installmentCoversCharges: terms.installment_covers_charges,
    charges: terms.charges.map((charge) => ({
      ...emptyCharge(),
      name: charge.name,
      basis: charge.basis,
      rate: charge.rate === null ? "" : toPercent(charge.rate),
      amount: charge.amount ?? "",
      base: charge.base ?? "",
      chargedToBalance: charge.charged_to_balance,
    })),
    // Never prefilled from the stored cursor: re-saving the terms must not
    // silently re-anchor the arithmetic somewhere in the past.
    accrueFrom: "",
  };
}

function fromInvestment(account: Account): InvestmentDraft {
  const terms = account.investment ?? null;
  if (terms === null) {
    return { ...emptyInvestmentDraft(), openedOn: todayIso() };
  }

  return {
    ratePercent: terms.rate === null ? "" : toPercent(terms.rate.value),
    rateBasis: terms.rate?.basis ?? "effective_annual",
    openedOn: terms.opened_on,
    statementDay: String(terms.statement_day),
    maturesOn: terms.matures_on ?? "",
    charges: terms.charges.map((charge) => ({
      ...emptyCharge(),
      name: charge.name,
      basis: charge.basis,
      rate: charge.rate === null ? "" : toPercent(charge.rate),
      amount: charge.amount ?? "",
      base: charge.base ?? "",
      chargedToBalance: charge.charged_to_balance,
    })),
    accrueFrom: "",
  };
}
