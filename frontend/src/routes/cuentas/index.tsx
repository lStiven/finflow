import { useSuspenseQuery } from "@tanstack/react-query";
import {
  createFileRoute,
  Link,
  redirect,
  useNavigate,
  useRouterState,
} from "@tanstack/react-router";
import {
  Archive,
  ArrowRight,
  BookOpen,
  Check,
  ChevronDown,
  CreditCard,
  Eye,
  Landmark,
  Loader2,
  Lock,
  Pencil,
  Percent,
  Plus,
  Radio,
  RotateCcw,
  Scale,
  ShieldCheck,
  Sparkles,
  Trash2,
  TrendingDown,
  TriangleAlert,
  Wallet,
} from "lucide-react";
import type { ComponentType, ReactNode } from "react";
import { type SubmitEvent, useState } from "react";
import { attentionOf, watchedCount } from "@/accounts/attention";
import { balanceIssue, creditLimitIssue, nameIssue } from "@/accounts/edits";
import { isFinanceable, RATE_BASIS_COPY, toPercent } from "@/accounts/financing";
import { describeInstrument, parseInstrument } from "@/accounts/instruments";
import { canLinkAlerts, instrumentLabel, kindCopy } from "@/accounts/kinds";
import {
  type Account,
  accountsQuery,
  financialCatalogQuery,
  type InstrumentKind,
  useCloseAccount,
  useLinkInstrument,
  useRenameAccount,
  useReopenAccount,
  useRestateBalance,
  useSetCreditLimit,
  useUnlinkInstrument,
} from "@/api/queries";
import { AppShell } from "@/components/AppShell";
import { CountUpMoney } from "@/components/CountUpMoney";
import { Money } from "@/components/Money";
import { PageHeader } from "@/components/PageHeader";
import { Button, buttonClass } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { Field } from "@/components/ui/Field";
import { Notice } from "@/components/ui/Notice";
import { Select } from "@/components/ui/Select";
import { cn } from "@/lib/cn";
import { formatDate } from "@/lib/dates";
import { describeBalance, signOf, toChartValue } from "@/lib/money";

/** Which accounts the list shows. The API's own vocabulary. */
type Scope = "open" | "closed" | "all";

const SCOPE_TABS: { value: Scope; label: string }[] = [
  { value: "open", label: "Abiertas" },
  { value: "closed", label: "Cerradas" },
  { value: "all", label: "Todas" },
];

/**
 * The tab, in the address: back undoes it, and closing an account lands on
 * «Cerradas» as a step somebody can take back. Absent means «Abiertas».
 */
type AccountsSearch = { ver?: "cerradas" | "todas" };

const SCOPE_OF = { cerradas: "closed", todas: "all" } as const;

function scopeOf(search: AccountsSearch): Scope {
  return search.ver ? SCOPE_OF[search.ver] : "open";
}

function searchOf(scope: Scope): AccountsSearch {
  return scope === "closed"
    ? { ver: "cerradas" }
    : scope === "all"
      ? { ver: "todas" }
      : {};
}

export const Route = createFileRoute("/cuentas/")({
  beforeLoad: ({ context }) => {
    if (!context.session) throw redirect({ to: "/login" });
  },
  validateSearch: (raw: Record<string, unknown>): AccountsSearch =>
    raw.ver === "cerradas" || raw.ver === "todas" ? { ver: raw.ver } : {},
  loaderDeps: ({ search }) => search,
  loader: ({ context, deps }) =>
    Promise.all([
      context.queryClient.query({
        ...accountsQuery(scopeOf(deps)),
        staleTime: "static",
      }),
      /*
       * `open`, the same scope the dashboard reports on. The net worth that
       * travels with this list is computed over the scope asked for, so the
       * figures above the list stay on this one however the list is filtered
       * — two numbers for one question, one of them apparently wrong, is
       * exactly what the scope tabs must not introduce.
       */
      context.queryClient.query({ ...accountsQuery("open"), staleTime: "static" }),
      /*
       * And `all`, which answers a different question: has this person ever
       * declared an account? Only that may decide between the first-run
       * explanation and the list, because somebody who has closed every
       * account they had is not a first-run — and showing them the pitch
       * would hide the tabs that are the only way back to what they closed.
       */
      context.queryClient.query({ ...accountsQuery("all"), staleTime: "static" }),
      context.queryClient.query({ ...financialCatalogQuery, staleTime: "static" }),
    ]),
  component: AccountsScreen,
});

function AccountsScreen() {
  const search = Route.useSearch();
  const navigate = useNavigate({ from: Route.fullPath });
  const scope = scopeOf(search);
  // The router keeps this list on screen while the next tab loads; this is
  // what says so.
  const pending = useRouterState({ select: (state) => state.isLoading });
  const showScope = (next: Scope) => void navigate({ search: searchOf(next) });

  const { data: open } = useSuspenseQuery(accountsQuery("open"));
  const { data: every } = useSuspenseQuery(accountsQuery("all"));
  const { data } = useSuspenseQuery(accountsQuery(scope));
  const { data: catalog } = useSuspenseQuery(financialCatalogQuery);

  const accounts = data.accounts;
  const primary = open.net_worth[0] ?? null;
  const others = open.net_worth.slice(1);
  // Nothing at all, ever — the only case the first-run explanation is for. A
  // brand-new account holder also has nothing to filter, so the tabs go with
  // it rather than sitting above a paragraph about what an account is.
  const declaredAny = every.accounts.length > 0;

  /** The catalogue's own label, for a kind this build has no Spanish copy for. */
  const labelOf = (kind: string) =>
    catalog.account_kinds.find((option) => option.value === kind)?.label ?? kind;

  return (
    <AppShell>
      <div className="mb-8">
        <PageHeader
          title="Cuentas"
          lead={
            declaredAny
              ? "Lo que tienes y lo que debes."
              : "Dónde vive tu plata: la cuenta del banco, la tarjeta, el efectivo."
          }
          // Without any account the first-run card below is the explanation.
          help={{ id: "cuentas", openFirstTime: declaredAny, points: HELP_POINTS }}
          actions={
            declaredAny ? (
              <Link to="/cuentas/nueva" className={buttonClass("primary")}>
                <Plus className="size-4" />
                Nueva cuenta
              </Link>
            ) : null
          }
        />
      </div>

      {!declaredAny ? (
        <EmptyState />
      ) : (
        <div className="flex flex-col gap-8">
          {primary ? (
            <NetWorthStrip
              total={primary.total}
              assets={primary.assets}
              liabilities={primary.liabilities}
              currency={primary.currency}
              watched={watchedCount(open.accounts)}
            />
          ) : null}

          {others.length > 0 ? (
            <p className="-mt-5 text-faint text-xs">
              También tienes saldos en{" "}
              {others.map((figure) => figure.currency).join(", ")}. Se muestran aparte
              porque no hay tasa de cambio para sumarlos.
            </p>
          ) : null}

          <ScopeTabs scope={scope} pending={pending} onChange={showScope} />

          {accounts.length === 0 ? (
            <Card lift={false}>
              <p className="py-8 text-center text-muted text-sm">
                {scope === "closed"
                  ? "No has cerrado ninguna cuenta."
                  : "Cerraste todas tus cuentas. Míralas en «Cerradas», o declara una nueva."}
              </p>
            </Card>
          ) : (
            <section
              className={cn(
                "grid grid-cols-1 gap-4 [&>*]:min-w-0 md:grid-cols-2",
                pending && "opacity-60",
              )}
            >
              {accounts.map((account, index) => (
                <AccountCard
                  key={account.id}
                  account={account}
                  kindLabel={labelOf(account.kind)}
                  index={index}
                  onClosed={() => showScope("closed")}
                />
              ))}
            </section>
          )}

          <GuideLink />
        </div>
      )}
    </AppShell>
  );
}

const HELP_POINTS = [
  {
    icon: ShieldCheck,
    title: "Las declaras tú",
    body: "Finflow no entra a tu banco: una cuenta es una etiqueta tuya para ordenar lo que llega.",
  },
  {
    icon: Radio,
    title: "Enlaza sus tarjetas",
    body: "Las alertas traen el banco y los últimos cuatro dígitos. Enlázalos en «Alertas» y sus movimientos caen en esa cuenta.",
  },
  {
    icon: Scale,
    title: "Patrimonio",
    body: "Lo que tienes menos lo que debes. Préstamos e hipotecas se vigilan sin sumar.",
  },
  {
    icon: Pencil,
    title: "Todo se ajusta",
    body: "Nombre, saldo, cupo, cerrar o reabrir: en «Ajustes», dentro de cada cuenta.",
  },
];

function ScopeTabs({
  scope,
  pending,
  onChange,
}: {
  scope: Scope;
  pending: boolean;
  onChange: (next: Scope) => void;
}) {
  return (
    <div className="flex items-center gap-3">
      <div
        role="tablist"
        aria-label="Qué cuentas ver"
        className="flex flex-1 gap-1 rounded-xl border border-line bg-surface p-1 sm:max-w-sm"
      >
        {SCOPE_TABS.map((tab) => (
          <button
            key={tab.value}
            type="button"
            role="tab"
            aria-selected={scope === tab.value}
            onClick={() => onChange(tab.value)}
            className={
              scope === tab.value
                ? "flex-1 rounded-lg bg-accent-soft py-2 font-medium text-sm text-text transition-colors"
                : "flex-1 rounded-lg py-2 text-muted text-sm transition-colors hover:text-text"
            }
          >
            {tab.label}
          </button>
        ))}
      </div>
      {pending ? <Loader2 className="size-4 animate-spin text-faint" /> : null}
    </div>
  );
}

/* ------------------------------------------------------------- sin cuentas */

/**
 * What somebody sees before they have declared anything.
 *
 * The one screen where the explanation *is* the content: an account is not a
 * connection to a bank and declaring one is not required, which is the
 * opposite of what every other finance app has taught people to expect. It
 * disappears for good the moment there is one account, because after that the
 * balances say it better than any paragraph.
 */
function EmptyState() {
  return (
    <div className="flex flex-col gap-4">
      <Card glow="accent" lift={false} className="relative overflow-hidden p-7 sm:p-9">
        <span
          aria-hidden
          className="absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-accent/60 to-transparent"
        />

        <div className="rise flex flex-col gap-5">
          <span
            aria-hidden
            className="pulse-ring grid size-14 place-items-center rounded-2xl bg-accent/15 ring-1 ring-accent/30"
          >
            <Wallet className="size-6 text-accent" />
          </span>

          <div>
            <h2 className="font-semibold text-xl tracking-tight">
              Todavía no tienes cuentas
            </h2>
            <p className="mt-2 max-w-2xl text-muted text-sm leading-relaxed">
              Una cuenta es cada sitio donde vive tu plata: tu cuenta de ahorros, tu
              tarjeta de crédito, el efectivo que cargas.{" "}
              <strong className="text-text">Finflow funciona sin ninguna</strong> — todo
              lo que llegue se registra igual, solo queda sin asignar. Declararlas es lo
              que convierte esa lista en saldos y patrimonio.
            </p>
          </div>
        </div>

        <ul className="mt-8 grid gap-5 sm:grid-cols-2">
          {PITCH.map(({ icon: Icon, title, body }, index) => (
            <li
              key={title}
              className="rise flex items-start gap-3"
              style={{ animationDelay: `${90 + index * 80}ms` }}
            >
              <span
                aria-hidden
                className="mt-0.5 grid size-9 shrink-0 place-items-center rounded-xl border border-line bg-ink"
              >
                <Icon className="size-4 text-cyan" />
              </span>
              <span className="min-w-0">
                <span className="block font-medium text-sm">{title}</span>
                <span className="mt-0.5 block text-muted text-sm leading-relaxed">
                  {body}
                </span>
              </span>
            </li>
          ))}
        </ul>

        <div
          className="rise mt-9 flex flex-col gap-3 sm:flex-row sm:items-center"
          style={{ animationDelay: "420ms" }}
        >
          <Link
            to="/cuentas/nueva"
            className="inline-flex items-center justify-center gap-2 rounded-xl bg-accent px-5 py-3.5 font-semibold text-accent-ink text-sm transition-all duration-150 hover:brightness-108"
          >
            <Plus className="size-4" />
            Crear mi primera cuenta
          </Link>
          <Link
            to="/guias/cuentas-y-movimientos"
            className="inline-flex items-center justify-center gap-2 rounded-xl border border-line px-5 py-3.5 text-muted text-sm transition-colors duration-150 hover:border-cyan/40 hover:text-text"
          >
            <BookOpen className="size-4" />
            Leer la guía primero
          </Link>
        </div>
      </Card>
    </div>
  );
}

const PITCH = [
  {
    icon: Sparkles,
    title: "Ordena lo que ya llegó",
    body: "Al declararla adopta los movimientos que estaban esperando por ella. No hay que reenviar nada.",
  },
  {
    icon: TrendingDown,
    title: "Te dice cuánto tienes y cuánto debes",
    body: "Lo que tienes suma y lo que debes resta. Eso es tu patrimonio, y sale de aquí.",
  },
  {
    icon: ShieldCheck,
    title: "No conecta con tu banco",
    body: "No pedimos claves ni entramos a ningún lado. Una cuenta es una etiqueta tuya, nada más.",
  },
  {
    icon: Radio,
    title: "La declaras tú, siempre",
    body: "Finflow nunca crea una sola. Si llega una alerta de una tarjeta que no declaraste, queda sin asignar.",
  },
];

/* ------------------------------------------------------------- con cuentas */

/**
 * The three figures, each with the line that says what it is made of.
 *
 * «Debes» is the one that needed it: a loan watched rather than counted sits
 * in the list below with a debt on it, and a «Debes» that leaves it out reads
 * as a sum that does not add up until somebody says why.
 */
function NetWorthStrip({
  total,
  assets,
  liabilities,
  currency,
  watched,
}: {
  total: string;
  assets: string;
  liabilities: string;
  currency: string;
  /** Open accounts kept outside every total. */
  watched: number;
}) {
  const figures = [
    {
      label: "Patrimonio",
      amount: total,
      hue: "violet" as const,
      icon: Wallet,
      caption: "Lo que tienes menos lo que debes",
    },
    {
      label: "Tienes",
      amount: assets,
      hue: "green" as const,
      icon: Landmark,
      caption: "Cuentas, efectivo e inversiones",
    },
    {
      label: "Debes",
      amount: liabilities,
      hue: "accent" as const,
      icon: CreditCard,
      // Loans and mortgages are watched, never counted (the catalogue's
      // `informational`), so what is owed here is the cards.
      caption:
        watched === 0
          ? "Tus tarjetas de crédito"
          : `Tus tarjetas; sin ${watched === 1 ? "el crédito" : `los ${watched} créditos`} que solo vigilas`,
    },
  ];

  return (
    <section aria-label="Tu posición" className="grid gap-3 sm:grid-cols-3">
      {figures.map(({ label, amount, hue, icon: Icon, caption }, index) => (
        <div
          key={label}
          className={cn(
            "surface surface-static rise flex flex-col gap-2 rounded-card border border-line bg-surface p-5",
            hue === "violet" && "glow-violet",
            hue === "green" && "glow-green",
            hue === "accent" && "glow-accent",
          )}
          style={{ animationDelay: `${index * 70}ms` }}
        >
          <div className="flex items-start justify-between gap-3">
            <p className="text-muted text-sm">{label}</p>
            <span
              aria-hidden
              className={cn(
                "grid size-8 shrink-0 place-items-center rounded-lg",
                hue === "violet" && "bg-violet/12 text-violet",
                hue === "green" && "bg-incoming/12 text-incoming",
                hue === "accent" && "bg-accent/12 text-accent",
              )}
            >
              <Icon className="size-4" />
            </span>
          </div>
          <CountUpMoney
            amount={amount}
            currency={currency}
            size="md"
            tone={
              hue === "green" ? "positive" : hue === "accent" ? "negative" : "plain"
            }
          />
          <p className="text-faint text-xs">{caption}</p>
        </div>
      ))}
    </section>
  );
}

function AccountCard({
  account,
  kindLabel,
  index,
  onClosed,
}: {
  account: Account;
  kindLabel: string;
  index: number;
  onClosed: () => void;
}) {
  // Tone and caption both from the one place allowed to decide what a balance
  // means: a paid-off card must not read "Debes" beside a zero.
  const { tone, label } = describeBalance(
    account.balance,
    account.currency,
    account.category,
  );
  const owed = account.category === "liability";
  const closed = account.closed_at !== null;
  const copy = kindCopy(account.kind, kindLabel);
  const Icon = copy.icon;
  const attention = attentionOf(account);
  // One section at a time: three open at once is the wall this replaced.
  const [open, setOpen] = useState<Section | null>(null);
  const toggle = (section: Section) =>
    setOpen((current) => (current === section ? null : section));

  return (
    <Card
      glow={closed ? "none" : owed ? "accent" : "cyan"}
      lift={false}
      className={cn(
        "rise relative flex flex-col gap-5 overflow-hidden",
        closed && "border-dashed",
      )}
      style={{ animationDelay: `${index * 60}ms` }}
    >
      <span
        aria-hidden
        className={cn(
          "absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent to-transparent",
          closed ? "via-line" : owed ? "via-accent/50" : "via-cyan/50",
        )}
      />

      <div className="flex items-start gap-4">
        <span
          aria-hidden
          className={cn(
            "grid size-11 shrink-0 place-items-center rounded-xl ring-1",
            closed
              ? "bg-surface-raised text-faint ring-line"
              : owed
                ? "bg-accent/12 text-accent ring-accent/25"
                : "bg-cyan/12 text-cyan ring-cyan/25",
          )}
        >
          <Icon className="size-5" />
        </span>

        <div className="min-w-0 flex-1">
          <p className="flex flex-wrap items-center gap-2 font-medium">
            {/* Two lines before it gives up: this is the card's title, and
                "Ahorros Bancolo…" on a phone is the one truncation nobody
                should have to accept. */}
            <span className="line-clamp-2 break-words">{account.name}</span>
            {closed ? (
              <span className="rounded-full border border-line px-2 py-0.5 text-[0.625rem] text-faint uppercase tracking-wider">
                Cerrada
              </span>
            ) : null}
          </p>
          <p className="mt-0.5 line-clamp-2 text-faint text-xs">
            {copy.label}
            {account.bank ? ` · ${account.bank}` : ""}
          </p>
        </div>

        <div className="text-right">
          <Money
            amount={account.balance}
            currency={account.currency}
            tone={tone}
            size="sm"
          />
          <p className="mt-0.5 text-faint text-xs">
            {account.informational ? "Saldo del crédito" : label}
          </p>
        </div>
      </div>

      {owed && !account.informational ? <CreditBar account={account} /> : null}

      {/* On the card and not folded away: right above this sits a «Debes»
          that leaves this figure out, and the two read as a contradiction
          until somebody is told which question each answers. One line —
          «Cómo funciona» and the financing screen say the rest. */}
      {account.informational ? (
        <p className="flex items-center gap-2 text-faint text-xs">
          <Eye aria-hidden className="size-3.5 shrink-0" />
          Solo la vigilas: no suma ni resta en tu patrimonio.
        </p>
      ) : null}

      <p className="text-faint text-xs">
        {account.movements_applied === 0
          ? "Todavía no se le ha asignado ningún movimiento."
          : `${account.movements_applied} ${
              account.movements_applied === 1
                ? "movimiento asignado"
                : "movimientos asignados"
            }.`}
        {closed && account.closed_at !== null
          ? ` Cerrada el ${formatDate(account.closed_at)}.`
          : ""}
      </p>

      {attention ? (
        <AttentionNotice
          account={account}
          attention={attention}
          onLink={() => setOpen("alerts")}
        />
      ) : null}

      <CardSections account={account} open={open} onToggle={toggle} />

      {open === "alerts" ? (
        <div
          id={`${account.id}-alerts`}
          className="rise flex flex-col gap-4 rounded-xl border border-line bg-ink/60 p-3.5"
        >
          <Instruments account={account} />
        </div>
      ) : null}
      {open === "financing" ? (
        <div
          id={`${account.id}-financing`}
          className="rise flex flex-col gap-4 rounded-xl border border-line bg-ink/60 p-3.5"
        >
          <Financing account={account} />
        </div>
      ) : null}
      {open === "settings" ? (
        <div
          id={`${account.id}-settings`}
          className="rise flex flex-col gap-4 rounded-xl border border-line bg-ink/60 p-3.5"
        >
          <Settings account={account} onClosed={onClosed} />
        </div>
      ) : null}

      <Link
        to="/transacciones"
        search={{ account: account.id }}
        className="-m-1 mt-auto flex items-center gap-1.5 self-start rounded-lg p-1 text-cyan text-sm transition-colors hover:text-text"
      >
        Ver sus movimientos
        <ArrowRight className="size-3.5" />
      </Link>
    </Card>
  );
}

/**
 * What a loan or an investment costs or yields, and the way to the screen
 * that keeps it.
 *
 * The gap it names is invisible otherwise: a mortgage declared with a balance
 * and no terms looks complete, its number falls by exactly what is paid, and
 * it is wrong every month by the interest nobody charged. Once the terms
 * exist it says the rate, the one figure worth checking against a contract.
 */
function Financing({ account }: { account: Account }) {
  const shape = isFinanceable(account.kind);
  if (shape === null) return null;

  const terms = account.loan ?? account.investment ?? null;
  const rate = account.loan?.rate ?? account.investment?.rate ?? null;

  return (
    <>
      <p className="text-muted text-sm">
        {terms === null
          ? shape === "loan"
            ? "Sin la tasa y los seguros, este saldo baja exactamente lo que pagas, y una deuda no funciona así."
            : "Con la tasa pactada, Finflow abona los rendimientos en cada corte."
          : rate === null
            ? `Su valor lo registras tú. Corte el ${terms.statement_day}.`
            : `${toPercent(rate.value)} % ${
                RATE_BASIS_COPY[rate.basis]?.label ?? rate.basis
              }, corte el ${terms.statement_day}. Cada corte queda como movimientos.`}
      </p>

      <Link
        to="/cuentas/$accountId/financiacion"
        params={{ accountId: account.id }}
        className="-my-2 inline-flex min-h-11 items-center gap-1.5 self-start text-cyan text-sm transition-colors hover:text-text"
      >
        {terms === null ? "Registrar las condiciones" : "Ver la tabla y actualizar"}
        <ArrowRight className="size-3.5" />
      </Link>
    </>
  );
}

/**
 * How much of the card is spent, when both figures exist.
 *
 * `available` is signed and may be null: null means no limit was declared,
 * and zero would mean "nothing left", which is a different fact.
 */
function CreditBar({ account }: { account: Account }) {
  if (account.credit_limit === null || account.available === null) return null;

  const limit = toChartValue(account.credit_limit);
  if (limit === 0) return null;

  /*
   * An overpaid card carries a negative balance — money in your favour, and
   * `toChartValue` takes the absolute value. Without this the bar would fill
   * as if the card were drawn, right above a caption saying the opposite.
   */
  const used =
    signOf(account.balance) < 0
      ? 0
      : Math.min(1, toChartValue(account.balance) / limit);

  const percent = Math.round(used * 100);

  return (
    <div className="flex flex-col gap-2">
      <div
        role="progressbar"
        aria-label="Cupo usado"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={percent}
        aria-valuetext={`${percent} % del cupo usado`}
        className="h-1.5 overflow-hidden rounded-full bg-surface-raised"
      >
        <div
          className="h-full rounded-full bg-gradient-to-r from-accent to-violet transition-[width] duration-700 ease-out"
          style={{ width: `${percent}%` }}
        />
      </div>
      <p className="flex flex-wrap items-baseline gap-x-1.5 text-faint text-xs">
        Te quedan
        <Money
          amount={account.available}
          currency={account.currency}
          size="sm"
          className="text-text text-xs"
        />
        de un cupo de
        <Money
          amount={account.credit_limit}
          currency={account.currency}
          size="sm"
          className="text-xs"
        />
      </p>
    </div>
  );
}

type Section = "alerts" | "financing" | "settings";

/**
 * The card's three drawers as one row, one open at a time.
 *
 * They used to be three full-width folds stacked under every card, each the
 * same size as the next, so the card's actual content was a third of it.
 * A row of labels says the same three things in one line.
 */
function CardSections({
  account,
  open,
  onToggle,
}: {
  account: Account;
  open: Section | null;
  onToggle: (section: Section) => void;
}) {
  const shape = isFinanceable(account.kind);
  const count = account.instruments.length;
  const sections: { id: Section; label: string; badge?: number }[] = [
    ...(canLinkAlerts(account.kind)
      ? [{ id: "alerts" as const, label: "Alertas", badge: count }]
      : []),
    ...(shape === null
      ? []
      : [
          {
            id: "financing" as const,
            label: shape === "loan" ? "Intereses" : "Rendimiento",
          },
        ]),
    { id: "settings", label: "Ajustes" },
  ];

  return (
    <div className="flex flex-wrap gap-1.5">
      {sections.map((section) => (
        <button
          key={section.id}
          type="button"
          onClick={() => onToggle(section.id)}
          aria-expanded={open === section.id}
          aria-controls={`${account.id}-${section.id}`}
          className={cn(
            "inline-flex min-h-11 items-center gap-1.5 rounded-xl border px-3 text-xs transition-colors",
            open === section.id
              ? "border-cyan/40 bg-cyan/10 text-text"
              : "border-line text-muted hover:border-cyan/30 hover:text-text",
          )}
        >
          {section.label}
          {section.badge === undefined ? null : (
            <span className="rounded-full bg-surface-raised px-1.5 text-[0.625rem] tabular-nums">
              {section.badge}
            </span>
          )}
          <ChevronDown
            aria-hidden
            className={cn(
              "size-3.5 transition-transform duration-200",
              open === section.id && "rotate-180",
            )}
          />
        </button>
      ))}
    </div>
  );
}

/**
 * The one thing this account still needs, said first and with its action.
 *
 * Without it, an account nothing reaches looks exactly like one that works:
 * the alerts that should land here wait unassigned, and nothing on the card
 * says so.
 */
function AttentionNotice({
  account,
  attention,
  onLink,
}: {
  account: Account;
  attention: NonNullable<ReturnType<typeof attentionOf>>;
  onLink: () => void;
}) {
  if (attention.kind === "terms") {
    return (
      <Notice
        tone="warn"
        icon={Percent}
        title={
          attention.shape === "loan"
            ? "Falta decir qué intereses te cobran"
            : "Falta decir cómo rinde"
        }
      >
        <Link
          to="/cuentas/$accountId/financiacion"
          params={{ accountId: account.id }}
          className="-my-2 inline-flex min-h-11 items-center gap-1.5 text-cyan hover:underline"
        >
          Registrar las condiciones
          <ArrowRight className="size-3.5" aria-hidden />
        </Link>
      </Notice>
    );
  }

  return (
    <Notice tone="warn" icon={Radio} title="Ninguna alerta cae aquí todavía">
      <button
        type="button"
        onClick={onLink}
        className="-my-2 inline-flex min-h-11 items-center gap-1.5 text-cyan hover:underline"
      >
        Enlazar su tarjeta o cuenta
        <ArrowRight className="size-3.5" aria-hidden />
      </button>
    </Notice>
  );
}

/**
 * The names this account's alerts arrive under, and the way to add another.
 *
 * The silent failure this exists for: one real account emails as a card for
 * purchases and as an account number for transfers, under different last four
 * digits. Link only one and half its movements wait forever, with nothing on
 * screen to say why. So the panel says what it is *for* rather than counting
 * the keys — "3 formas de llegar" named the mechanism to somebody who has no
 * reason to know there is one.
 */
function Instruments({ account }: { account: Account }) {
  const count = account.instruments.length;

  return (
    <>
      <p className="text-muted text-sm">
        Las alertas solo traen el banco, si fue tarjeta o cuenta y los últimos cuatro
        dígitos. Enlázalos y sus movimientos caen en{" "}
        <strong className="text-text">{account.name}</strong>.
      </p>

      {count > 0 ? (
        <ul className="flex flex-col gap-2">
          {account.instruments.map((instrument) => (
            <InstrumentRow key={instrument} account={account} instrument={instrument} />
          ))}
        </ul>
      ) : null}

      {account.closed_at === null ? (
        <LinkInstrumentForm account={account} />
      ) : (
        <p className="text-faint text-xs">
          Está cerrada, así que no adopta movimientos nuevos y no tiene sentido
          enlazarle más alertas.
        </p>
      )}
    </>
  );
}

/**
 * One linked card, and the way to take it back off this account.
 *
 * Unlinking is not a tidy-up: the movements that arrived under this card go
 * back to unassigned, so the warning says so before anything happens. That is
 * also what makes it useful — it is the first half of moving a card to the
 * account it should have been on, and the second half is linking it there.
 *
 * The three parts are read back out of the stored key rather than kept
 * separately, because that key is what the API published. One it cannot read
 * is shown without the control instead of guessing at it.
 */
function InstrumentRow({
  account,
  instrument,
}: {
  account: Account;
  instrument: string;
}) {
  const unlink = useUnlinkInstrument(account.id);
  const [confirming, setConfirming] = useState(false);
  const parts = parseInstrument(instrument);

  return (
    <li className="rounded-lg border border-line bg-surface px-2.5 py-1.5 text-xs">
      <div className="flex items-center gap-2">
        <span className="min-w-0 flex-1">{describeInstrument(instrument)}</span>
        {parts !== null && !confirming ? (
          <button
            type="button"
            aria-label={`Desenlazar ${describeInstrument(instrument)}`}
            className="-my-2 grid size-11 shrink-0 place-items-center rounded-lg text-faint hover:bg-outgoing/10 hover:text-outgoing"
            onClick={() => setConfirming(true)}
          >
            <Trash2 className="size-3.5" />
          </button>
        ) : null}
      </div>

      {confirming && parts !== null ? (
        <div className="mt-2 flex flex-col gap-2 rounded-lg border border-warn/30 bg-warn/10 p-2.5">
          <p className="flex items-start gap-2 leading-relaxed">
            <TriangleAlert className="mt-0.5 size-3.5 shrink-0 text-warn" />
            <span>
              Sus movimientos vuelven a quedar sin asignar y el saldo de{" "}
              <strong>{account.name}</strong> baja lo que ellos aportaban. Para moverlos
              a otra cuenta, enlaza ahí la misma tarjeta.
            </span>
          </p>
          <Failed error={unlink.error} />
          <div className="flex flex-wrap gap-2">
            <Button
              variant="ghost"
              className="py-1.5 text-xs"
              disabled={unlink.isPending}
              onClick={() => {
                unlink.mutate(
                  {
                    bank: parts.bank,
                    instrument_kind: parts.kind as InstrumentKind,
                    last_four: parts.lastFour,
                  },
                  { onSuccess: () => setConfirming(false) },
                );
              }}
            >
              {unlink.isPending ? (
                <>
                  <Loader2 className="size-3.5 animate-spin" />
                  Desenlazando…
                </>
              ) : (
                "Sí, desenlazar"
              )}
            </Button>
            <Button
              variant="quiet"
              className="py-1.5 text-xs"
              disabled={unlink.isPending}
              onClick={() => setConfirming(false)}
            >
              Mejor no
            </Button>
          </div>
        </div>
      ) : null}
    </li>
  );
}

function LinkInstrumentForm({ account }: { account: Account }) {
  // Already in the cache: the route loads it, so this suspends on nothing.
  const { data: catalog } = useSuspenseQuery(financialCatalogQuery);
  const link = useLinkInstrument(account.id);
  const [bank, setBank] = useState(account.bank ?? "");
  const [kind, setKind] = useState("");
  const [lastFour, setLastFour] = useState("");
  const [applied, setApplied] = useState<number | null>(null);

  const ready = bank.trim() !== "" && kind !== "" && /^\d{4,}$/.test(lastFour.trim());

  async function onSubmit(event: SubmitEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!ready || link.isPending) return;

    // Cleared first: a failed retry must not sit beside the previous
    // attempt's "adopted N movements".
    setApplied(null);
    const before = account.movements_applied;
    try {
      const updated = await link.mutateAsync({
        bank: bank.trim(),
        instrument_kind: kind as InstrumentKind,
        last_four: lastFour.trim(),
      });
      setApplied(updated.movements_applied - before);
      setKind("");
      setLastFour("");
    } catch {
      // `link.error` carries it and the form reports it below.
    }
  }

  return (
    <form onSubmit={onSubmit} className="flex flex-col gap-3">
      <div className="grid gap-3 sm:grid-cols-2">
        <Field
          label="Banco"
          placeholder="Bancolombia"
          maxLength={512}
          value={bank}
          onChange={(event) => setBank(event.target.value)}
        />
        <Field
          label="Últimos cuatro"
          inputMode="numeric"
          placeholder="0530"
          value={lastFour}
          onChange={(event) => setLastFour(event.target.value)}
        />
      </div>

      <Select
        label="¿Cómo llegan esas alertas?"
        placeholder="Elige una"
        value={kind}
        onChange={(event) => setKind(event.target.value)}
        // The vocabulary comes from the API — these are the only words that
        // ever appear in an alert, and a list copied here would drift.
        options={catalog.instrument_kinds.map((option) => ({
          value: option.value,
          label: instrumentLabel(option.value, option.label),
        }))}
      />

      {link.error ? (
        <p role="alert" className="text-outgoing text-sm">
          {link.error.message}
        </p>
      ) : null}

      {applied !== null ? (
        <p
          role="status"
          className="rise flex items-center gap-1.5 text-incoming text-xs"
        >
          <Check className="size-3.5" />
          {applied > 0
            ? `Enlazada. Adoptó ${applied} ${applied === 1 ? "movimiento" : "movimientos"} que estaban esperando.`
            : "Enlazada. Desde ahora sus alertas caen en esta cuenta."}
        </p>
      ) : null}

      <Button
        type="submit"
        variant="ghost"
        className="self-start py-2 text-xs"
        disabled={!ready || link.isPending}
      >
        {link.isPending ? (
          <>
            <Loader2 className="size-3.5 animate-spin" />
            Enlazando…
          </>
        ) : (
          `Enlazar ${kind === "" ? "otra tarjeta o cuenta" : instrumentLabel(kind).toLowerCase()}`
        )}
      </Button>
    </form>
  );
}

/* ------------------------------------------------------------------ ajustes */

/** The three things about an account that are the owner's to change, and the end of it. */
function Settings({ account, onClosed }: { account: Account; onClosed: () => void }) {
  const owed = account.category === "liability";

  return (
    <>
      <RenameForm account={account} />
      <BalanceForm account={account} />
      {owed ? <CreditLimitForm account={account} /> : null}
      {account.closed_at === null ? (
        <CloseForm account={account} onClosed={onClosed} />
      ) : (
        <ReopenForm account={account} />
      )}
    </>
  );
}

/** Shared shell for the little one-field forms below. */
function EditRow({
  icon: Icon,
  title,
  hint,
  children,
}: {
  icon: ComponentType<{ className?: string }>;
  title: string;
  hint: string;
  children: ReactNode;
}) {
  return (
    <div className="flex flex-col gap-2 border-line border-t pt-4 first:border-0 first:pt-0">
      <p className="flex items-center gap-2 font-medium text-xs">
        <Icon className="size-3.5 shrink-0 text-faint" />
        {title}
      </p>
      <p className="text-faint text-xs leading-relaxed">{hint}</p>
      {children}
    </div>
  );
}

function Saved({ children }: { children: ReactNode }) {
  return (
    <p role="status" className="rise flex items-center gap-1.5 text-incoming text-xs">
      <Check className="size-3.5" />
      {children}
    </p>
  );
}

function Failed({ error }: { error: Error | null }) {
  if (error === null) return null;
  return (
    <p role="alert" className="text-outgoing text-xs">
      {error.message}
    </p>
  );
}

function RenameForm({ account }: { account: Account }) {
  const rename = useRenameAccount(account.id);
  const [name, setName] = useState(account.name);
  const [saved, setSaved] = useState(false);

  const issue = nameIssue(name);
  const changed = name.trim() !== account.name;
  const canSave = issue === undefined && changed && !rename.isPending;

  async function onSubmit(event: SubmitEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!canSave) return;
    setSaved(false);
    try {
      const next = await rename.mutateAsync({ name: name.trim() });
      setName(next.name);
      setSaved(true);
    } catch {
      // `rename.error` carries it and `Failed` reports it below.
    }
  }

  return (
    <EditRow
      icon={Pencil}
      title="Nombre"
      hint="Solo cómo la llamas. No mueve saldos ni movimientos."
    >
      <form onSubmit={onSubmit} className="flex flex-col gap-2">
        <Field
          label="Nombre de la cuenta"
          value={name}
          maxLength={120}
          onChange={(event) => {
            setName(event.target.value);
            setSaved(false);
          }}
        />
        {changed && issue ? <p className="text-outgoing text-xs">{issue}</p> : null}
        <Failed error={rename.error} />
        {saved && !changed ? <Saved>Nombre guardado.</Saved> : null}
        <Button
          type="submit"
          variant="ghost"
          className="self-start py-2 text-xs"
          disabled={!canSave}
        >
          {rename.isPending ? (
            <>
              <Loader2 className="size-3.5 animate-spin" />
              Guardando…
            </>
          ) : (
            "Guardar nombre"
          )}
        </Button>
      </form>
    </EditRow>
  );
}

/**
 * Restating the balance: the figure the bank shows today, not a movement.
 *
 * The backend solves the opening balance backwards from the movements already
 * on record, so nothing in the transaction list changes and every row still
 * counts exactly once. That is the whole point, and it is also the thing that
 * needs saying on screen — otherwise this reads like a way to invent money.
 */
function BalanceForm({ account }: { account: Account }) {
  const restate = useRestateBalance(account.id);
  const [balance, setBalance] = useState(account.balance);
  const [saved, setSaved] = useState(false);

  const issue = balanceIssue(balance);
  const changed = balance.trim() !== account.balance;
  const canSave = issue === undefined && changed && !restate.isPending;
  const owed = account.category === "liability";

  async function onSubmit(event: SubmitEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!canSave) return;
    setSaved(false);
    try {
      const next = await restate.mutateAsync({ balance: balance.trim() });
      setBalance(next.balance);
      setSaved(true);
    } catch {
      // `restate.error` carries it and `Failed` reports it below.
    }
  }

  return (
    <EditRow
      icon={Scale}
      title="Saldo"
      hint={
        owed
          ? "Lo que la tarjeta debe hoy, según tu banco. No crea ningún movimiento: Finflow recalcula el saldo de partida para que las compras que ya tiene sigan cuadrando."
          : "Lo que la cuenta tiene hoy, según tu banco. No crea ningún movimiento: Finflow recalcula el saldo de partida para que lo que ya tiene siga cuadrando."
      }
    >
      <form onSubmit={onSubmit} className="flex flex-col gap-2">
        <Field
          label={owed ? "¿Cuánto debe hoy?" : "¿Cuánto tiene hoy?"}
          inputMode="decimal"
          placeholder="0"
          value={balance}
          onChange={(event) => {
            setBalance(event.target.value);
            setSaved(false);
          }}
        />
        {changed && issue ? <p className="text-outgoing text-xs">{issue}</p> : null}
        <Failed error={restate.error} />
        {saved && !changed ? (
          <Saved>Saldo corregido. Ningún movimiento se tocó.</Saved>
        ) : null}
        <Button
          type="submit"
          variant="ghost"
          className="self-start py-2 text-xs"
          disabled={!canSave}
        >
          {restate.isPending ? (
            <>
              <Loader2 className="size-3.5 animate-spin" />
              Guardando…
            </>
          ) : (
            "Corregir saldo"
          )}
        </Button>
      </form>
    </EditRow>
  );
}

/** Only a liability has one; sending a limit on an asset is a 422, not a no-op. */
function CreditLimitForm({ account }: { account: Account }) {
  const setLimit = useSetCreditLimit(account.id);
  const [limit, setLimit_] = useState(account.credit_limit ?? "");
  const [saved, setSaved] = useState(false);

  const issue = creditLimitIssue(limit);
  const changed = limit.trim() !== (account.credit_limit ?? "");
  const canSave = issue === undefined && changed && !setLimit.isPending;

  async function onSubmit(event: SubmitEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!canSave) return;
    setSaved(false);
    try {
      // Empty clears it: the endpoint takes the whole fact, so "no limit" is
      // a value that has to be sent rather than a field left out.
      const next = await setLimit.mutateAsync({
        credit_limit: limit.trim() === "" ? null : limit.trim(),
      });
      setLimit_(next.credit_limit ?? "");
      setSaved(true);
    } catch {
      // `setLimit.error` carries it and `Failed` reports it below.
    }
  }

  return (
    <EditRow
      icon={CreditCard}
      title="Cupo"
      hint="El máximo que el banco te deja deber. Es lo que permite mostrar cuánto te queda disponible. Déjalo vacío para quitarlo."
    >
      <form onSubmit={onSubmit} className="flex flex-col gap-2">
        <Field
          label="Cupo total"
          inputMode="decimal"
          placeholder="Sin cupo declarado"
          value={limit}
          onChange={(event) => {
            setLimit_(event.target.value);
            setSaved(false);
          }}
        />
        {changed && issue ? <p className="text-outgoing text-xs">{issue}</p> : null}
        <Failed error={setLimit.error} />
        {saved && !changed ? <Saved>Cupo guardado.</Saved> : null}
        <Button
          type="submit"
          variant="ghost"
          className="self-start py-2 text-xs"
          disabled={!canSave}
        >
          {setLimit.isPending ? (
            <>
              <Loader2 className="size-3.5 animate-spin" />
              Guardando…
            </>
          ) : (
            "Guardar cupo"
          )}
        </Button>
      </form>
    </EditRow>
  );
}

/**
 * Closing, behind a confirmation, because there is no way back.
 *
 * The API has no reopen and no delete, deliberately: a closed account still
 * explains the movements it already holds. What the confirmation has to say is
 * the consequence somebody would not guess — alerts from its card keep
 * arriving and land unassigned, because a closed account takes no movements.
 */
function CloseForm({ account, onClosed }: { account: Account; onClosed: () => void }) {
  const close = useCloseAccount(account.id);
  const [confirming, setConfirming] = useState(false);

  async function onConfirm() {
    try {
      await close.mutateAsync();
      onClosed();
    } catch {
      // `close.error` carries it and `Failed` reports it below.
    }
  }

  return (
    <EditRow
      icon={Archive}
      title="Cerrar la cuenta"
      hint="Deja de recibir movimientos nuevos. No se borra: sus movimientos y su saldo siguen ahí, explicando lo que ya pasó."
    >
      {confirming ? (
        <div className="flex flex-col gap-3 rounded-xl border border-warn/30 bg-warn/10 p-3">
          <p className="flex items-start gap-2 text-xs leading-relaxed">
            <TriangleAlert className="mt-0.5 size-3.5 shrink-0 text-warn" />
            <span>
              Vas a cerrar <strong>{account.name}</strong>. Si su tarjeta sigue enviando
              alertas, esos movimientos quedarán sin asignar. Puedes volver a abrirla
              cuando quieras.
            </span>
          </p>
          <Failed error={close.error} />
          <div className="flex flex-wrap gap-2">
            <Button
              variant="ghost"
              className="py-2 text-xs"
              disabled={close.isPending}
              onClick={onConfirm}
            >
              {close.isPending ? (
                <>
                  <Loader2 className="size-3.5 animate-spin" />
                  Cerrando…
                </>
              ) : (
                "Sí, cerrarla"
              )}
            </Button>
            <Button
              variant="quiet"
              className="py-2 text-xs"
              disabled={close.isPending}
              onClick={() => setConfirming(false)}
            >
              Mejor no
            </Button>
          </div>
        </div>
      ) : (
        <Button
          variant="ghost"
          className="self-start py-2 text-xs"
          onClick={() => setConfirming(true)}
        >
          <Archive className="size-3.5" />
          Cerrar cuenta
        </Button>
      )}
    </EditRow>
  );
}

/** The way back from a closure that turned out to be wrong. */
function ReopenForm({ account }: { account: Account }) {
  const reopen = useReopenAccount(account.id);

  return (
    <EditRow
      icon={Lock}
      title="Cuenta cerrada"
      hint="No recibe movimientos nuevos, pero sigue explicando los que ya tiene y su saldo sigue contando en tu patrimonio."
    >
      <p className="text-faint text-xs leading-relaxed">
        Al reabrirla vuelve a la lista de cuentas abiertas y sus alertas caen otra vez
        aquí. Nada de lo que ya tiene cambia: la historia nunca se fue.
      </p>
      <Failed error={reopen.error} />
      <Button
        variant="ghost"
        className="self-start py-2 text-xs"
        disabled={reopen.isPending}
        onClick={() => reopen.mutate()}
      >
        {reopen.isPending ? (
          <>
            <Loader2 className="size-3.5 animate-spin" />
            Reabriendo…
          </>
        ) : (
          <>
            <RotateCcw className="size-3.5" />
            Volver a abrirla
          </>
        )}
      </Button>
    </EditRow>
  );
}

function GuideLink() {
  return (
    <Link
      to="/guias/cuentas-y-movimientos"
      className="surface flex items-center gap-4 rounded-card border border-line bg-surface p-4 glow-cyan"
    >
      <span
        aria-hidden
        className="grid size-10 shrink-0 place-items-center rounded-xl border border-line bg-ink"
      >
        <BookOpen className="size-4 text-cyan" />
      </span>
      <span className="min-w-0 flex-1">
        <span className="block font-medium text-sm">Cuentas y movimientos</span>
        <span className="mt-0.5 block text-muted text-sm">
          Qué es cada cosa, cómo se juntan y qué hacer cuando algo no cuadre.
        </span>
      </span>
      <ArrowRight className="size-4 shrink-0 text-faint" />
    </Link>
  );
}
