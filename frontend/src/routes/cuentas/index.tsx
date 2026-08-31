import { useSuspenseQuery } from "@tanstack/react-query";
import { createFileRoute, Link, redirect } from "@tanstack/react-router";
import {
  ArrowRight,
  BookOpen,
  Check,
  ChevronDown,
  CreditCard,
  Landmark,
  Loader2,
  Plus,
  Radio,
  ShieldCheck,
  Sparkles,
  TrendingDown,
  Wallet,
} from "lucide-react";
import { type SubmitEvent, useState } from "react";
import { describeInstrument } from "@/accounts/instruments";
import { instrumentLabel, kindCopy } from "@/accounts/kinds";
import {
  type Account,
  accountsQuery,
  financialCatalogQuery,
  type InstrumentKind,
  useLinkInstrument,
} from "@/api/queries";
import { AppShell } from "@/components/AppShell";
import { CountUpMoney } from "@/components/CountUpMoney";
import { Money } from "@/components/Money";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { Field } from "@/components/ui/Field";
import { Select } from "@/components/ui/Select";
import { cn } from "@/lib/cn";
import { describeBalance, signOf, toChartValue } from "@/lib/money";

export const Route = createFileRoute("/cuentas/")({
  beforeLoad: ({ context }) => {
    if (!context.session) throw redirect({ to: "/login" });
  },
  loader: ({ context }) =>
    Promise.all([
      /*
       * `open`, the same scope the dashboard reports on. The net worth that
       * travels with this list is computed over the scope asked for, so
       * asking for `all` here would put a second, larger figure on a second
       * screen — two numbers for one question, one of them apparently wrong.
       */
      context.queryClient.query({ ...accountsQuery("open"), staleTime: "static" }),
      context.queryClient.query({ ...financialCatalogQuery, staleTime: "static" }),
    ]),
  component: AccountsScreen,
});

function AccountsScreen() {
  const { data } = useSuspenseQuery(accountsQuery("open"));
  const { data: catalog } = useSuspenseQuery(financialCatalogQuery);

  const accounts = data.accounts;
  const primary = data.net_worth[0] ?? null;
  const others = data.net_worth.slice(1);

  /** The catalogue's own label, for a kind this build has no Spanish copy for. */
  const labelOf = (kind: string) =>
    catalog.account_kinds.find((option) => option.value === kind)?.label ?? kind;

  return (
    <AppShell>
      <header className="mb-8 flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="font-semibold text-2xl tracking-tight">Cuentas</h1>
          <p className="mt-1.5 max-w-xl text-muted text-sm">
            {accounts.length === 0
              ? "Dónde vive tu plata: la cuenta del banco, la tarjeta, el efectivo."
              : "Lo que tienes y lo que debes, con los movimientos que ya se les asignaron."}
          </p>
        </div>

        {accounts.length > 0 ? (
          <Link
            to="/cuentas/nueva"
            className="inline-flex items-center gap-2 rounded-xl bg-accent px-4 py-3 font-semibold text-accent-ink text-sm transition-all duration-150 hover:brightness-108"
          >
            <Plus className="size-4" />
            Nueva cuenta
          </Link>
        ) : null}
      </header>

      {accounts.length === 0 ? (
        <EmptyState />
      ) : (
        <div className="flex flex-col gap-8">
          {primary ? (
            <NetWorthStrip
              total={primary.total}
              assets={primary.assets}
              liabilities={primary.liabilities}
              currency={primary.currency}
            />
          ) : null}

          {others.length > 0 ? (
            <p className="-mt-5 text-faint text-xs">
              También tienes saldos en{" "}
              {others.map((figure) => figure.currency).join(", ")}. Se muestran aparte
              porque no hay tasa de cambio para sumarlos.
            </p>
          ) : null}

          <section className="grid gap-4 md:grid-cols-2">
            {accounts.map((account, index) => (
              <AccountCard
                key={account.id}
                account={account}
                kindLabel={labelOf(account.kind)}
                index={index}
              />
            ))}
          </section>

          <GuideLink />
        </div>
      )}
    </AppShell>
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

function NetWorthStrip({
  total,
  assets,
  liabilities,
  currency,
}: {
  total: string;
  assets: string;
  liabilities: string;
  currency: string;
}) {
  const figures = [
    { label: "Patrimonio", amount: total, hue: "violet" as const, icon: Wallet },
    { label: "Tienes", amount: assets, hue: "green" as const, icon: Landmark },
    { label: "Debes", amount: liabilities, hue: "accent" as const, icon: CreditCard },
  ];

  return (
    <section aria-label="Tu posición" className="grid gap-3 sm:grid-cols-3">
      {figures.map(({ label, amount, hue, icon: Icon }, index) => (
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
        </div>
      ))}
    </section>
  );
}

function AccountCard({
  account,
  kindLabel,
  index,
}: {
  account: Account;
  kindLabel: string;
  index: number;
}) {
  // Tone and caption both from the one place allowed to decide what a balance
  // means: a paid-off card must not read "Debes" beside a zero.
  const { tone, label } = describeBalance(
    account.balance,
    account.currency,
    account.category,
  );
  const owed = account.category === "liability";
  const copy = kindCopy(account.kind, kindLabel);
  const Icon = copy.icon;

  return (
    <Card
      glow={owed ? "accent" : "cyan"}
      lift={false}
      className="rise relative flex flex-col gap-5 overflow-hidden"
      style={{ animationDelay: `${index * 60}ms` }}
    >
      <span
        aria-hidden
        className={cn(
          "absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent to-transparent",
          owed ? "via-accent/50" : "via-cyan/50",
        )}
      />

      <div className="flex items-start gap-4">
        <span
          aria-hidden
          className={cn(
            "grid size-11 shrink-0 place-items-center rounded-xl ring-1",
            owed
              ? "bg-accent/12 text-accent ring-accent/25"
              : "bg-cyan/12 text-cyan ring-cyan/25",
          )}
        >
          <Icon className="size-5" />
        </span>

        <div className="min-w-0 flex-1">
          <p className="truncate font-medium">{account.name}</p>
          <p className="mt-0.5 truncate text-faint text-xs">
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
          <p className="mt-0.5 text-faint text-xs">{label}</p>
        </div>
      </div>

      {owed ? <CreditBar account={account} /> : null}

      <p className="text-faint text-xs">
        {account.movements_applied === 0
          ? "Todavía no se le ha asignado ningún movimiento."
          : `${account.movements_applied} ${
              account.movements_applied === 1
                ? "movimiento asignado"
                : "movimientos asignados"
            }.`}
      </p>

      <Instruments account={account} />

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

  return (
    <div className="flex flex-col gap-2">
      <div className="h-1.5 overflow-hidden rounded-full bg-surface-raised">
        <div
          className="h-full rounded-full bg-gradient-to-r from-accent to-violet transition-[width] duration-700 ease-out"
          style={{ width: `${Math.round(used * 100)}%` }}
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

/**
 * The names this account's alerts arrive under, and the way to add another.
 *
 * The silent failure this exists for: one real account emails as a card for
 * purchases and as an account number for transfers, under different last four
 * digits. Link only one and half its movements wait forever, with nothing on
 * screen to say why.
 */
function Instruments({ account }: { account: Account }) {
  const [open, setOpen] = useState(false);
  const count = account.instruments.length;

  return (
    <div className="rounded-xl border border-line bg-ink/60">
      <button
        type="button"
        onClick={() => setOpen(!open)}
        aria-expanded={open}
        className="flex w-full items-center gap-2.5 px-3.5 py-2.5 text-left"
      >
        <Radio className="size-3.5 shrink-0 text-faint" />
        <span className="min-w-0 flex-1 truncate text-muted text-xs">
          {count === 0
            ? "No está enlazada a ninguna alerta"
            : `${count} ${count === 1 ? "forma de llegar" : "formas de llegar"}`}
        </span>
        <ChevronDown
          className={cn(
            "size-3.5 shrink-0 text-faint transition-transform duration-200",
            open && "rotate-180",
          )}
        />
      </button>

      {open ? (
        <div className="rise flex flex-col gap-4 border-line border-t p-3.5">
          {count === 0 ? (
            <p className="text-faint text-xs leading-relaxed">
              Ninguna alerta se le asigna sola todavía. Enlaza la tarjeta o la cuenta
              cuyos correos deben caer aquí y se adoptan también los que ya llegaron.
            </p>
          ) : (
            <ul className="flex flex-wrap gap-2">
              {account.instruments.map((instrument) => (
                <li
                  key={instrument}
                  className="rounded-lg border border-line bg-surface px-2.5 py-1 text-xs"
                >
                  {describeInstrument(instrument)}
                </li>
              ))}
            </ul>
          )}

          <LinkInstrumentForm account={account} />
        </div>
      ) : null}
    </div>
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
          Qué es cada cosa, cómo se juntan y qué hacer cuando algo no cuadra.
        </span>
      </span>
      <ArrowRight className="size-4 shrink-0 text-faint" />
    </Link>
  );
}
