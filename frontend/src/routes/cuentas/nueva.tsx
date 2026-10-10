import { useSuspenseQuery } from "@tanstack/react-query";
import {
  createFileRoute,
  Link,
  redirect,
  useNavigate,
  useRouter,
} from "@tanstack/react-router";
import {
  ArrowLeft,
  ArrowRight,
  Check,
  Loader2,
  Radio,
  ShieldCheck,
  Sparkles,
  TrendingDown,
} from "lucide-react";
import type { ReactNode } from "react";
import { useEffect, useState } from "react";
import {
  type AccountDraft,
  type Currency,
  emptyDraft,
  isLiability,
  issueFor,
  toPayload,
  validateDraft,
} from "@/accounts/draft";
import { canLinkAlerts, instrumentLabel, kindCopy } from "@/accounts/kinds";
import { ApiError } from "@/api/client";
import { type Account, financialCatalogQuery, useCreateAccount } from "@/api/queries";
import { AppShell } from "@/components/AppShell";
import { Money } from "@/components/Money";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { Field } from "@/components/ui/Field";
import { Select } from "@/components/ui/Select";
import { SuccessMark } from "@/components/ui/SuccessMark";
import { cn } from "@/lib/cn";

export const Route = createFileRoute("/cuentas/nueva")({
  beforeLoad: ({ context }) => {
    if (!context.session) throw redirect({ to: "/login" });
  },
  validateSearch: (raw: Record<string, unknown>): NewAccountSearch => {
    const paso = Number(raw.paso);
    return paso === 2 || paso === 3 ? { paso } : {};
  },
  loader: ({ context }) =>
    context.queryClient.query({ ...financialCatalogQuery, staleTime: "static" }),
  component: NewAccountScreen,
});

/**
 * The step on screen, in the address — the connect guide's lesson: the
 * browser's back walks the steps instead of leaving the form. Step one is the
 * bare address.
 */
type NewAccountSearch = { paso?: 2 | 3 };

const STEPS = ["Tipo", "Datos", "Confirmar"] as const;

/**
 * Declaring an account, in three steps and as few decisions as possible.
 *
 * The shortest honest path is three clicks — pick the kind, keep the name it
 * suggests, confirm — because that is a complete declaration: everything else
 * on the way is optional and says so. The last step is not a formality
 * either; it is where somebody is told what an account *does* here, which is
 * the part nobody guesses: it adopts what already arrived, it is not a
 * connection to a bank, and it can be corrected afterwards.
 */
function NewAccountScreen() {
  const navigate = useNavigate();
  const router = useRouter();
  const { paso } = Route.useSearch();
  const { data: catalog } = useSuspenseQuery(financialCatalogQuery);
  const create = useCreateAccount();

  const [draft, setDraft] = useState<AccountDraft>(emptyDraft);
  // A later step with no kind chosen is a reload or a pasted link: the draft
  // lives in memory, so there is nothing to show there but the first step.
  const step = paso !== undefined && draft.kind !== "" ? paso - 1 : 0;

  useEffect(() => {
    if (paso !== undefined && draft.kind === "") {
      void navigate({ to: "/cuentas/nueva", search: {}, replace: true });
    }
  }, [paso, draft.kind, navigate]);

  /** Forward pushes a step; going back is the browser's own back. */
  function setStep(next: number, replace = false) {
    void navigate({
      to: "/cuentas/nueva",
      search: next === 0 ? {} : { paso: next === 1 ? 2 : 3 },
      replace,
    });
  }
  /** Once somebody edits the name, the kind stops rewriting it under them. */
  const [namedByHand, setNamedByHand] = useState(false);
  /** Only after a failed attempt: nobody wants to be corrected mid-typing. */
  const [checked, setChecked] = useState(false);
  const [created, setCreated] = useState<Account | null>(null);

  const issues = validateDraft(draft);
  const shown = checked ? issues : [];
  /* What the catalogue calls the chosen kind — the last resort for one this
     build has no Spanish copy for, and better than the raw enum value. */
  const kindOption = catalog.account_kinds.find(
    (option) => option.value === draft.kind,
  );
  const kindLabel = kindOption?.label ?? draft.kind;
  /* Watched rather than counted, straight from the catalogue: which kinds are
     outside every total is the backend's rule, never a list repeated here. */
  const watched = kindOption?.informational ?? false;

  function edit(patch: Partial<AccountDraft>) {
    setDraft((current) => ({ ...current, ...patch }));
  }

  function chooseKind(value: string, category: string, label: string) {
    const copy = kindCopy(value, label);
    const changed = value !== draft.kind;
    edit({
      kind: value,
      category,
      name: namedByHand ? draft.name : copy.suggestedName,
      // A limit belongs to what is owed; changing the kind must not leave one
      // behind on an asset, which the API refuses outright.
      creditLimit: category === "liability" ? draft.creditLimit : "",
      // Both halves of the instrument go together, and both belong to the
      // kind that was chosen before: keeping the digits alone would block the
      // next step with an error about a field nobody touched.
      instrumentKind: changed ? "" : draft.instrumentKind,
      lastFour: changed ? "" : draft.lastFour,
    });
    setChecked(false);
    setStep(1);
  }

  function next() {
    if (issues.length > 0) {
      setChecked(true);
      return;
    }
    setChecked(false);
    setStep(2);
  }

  async function submit() {
    if (issues.length > 0) {
      setChecked(true);
      setStep(1, true);
      return;
    }
    try {
      setCreated(await create.mutateAsync(toPayload(draft)));
    } catch {
      // `create.error` carries it; the confirm step reports it in place.
    }
  }

  if (created) {
    return (
      <AppShell>
        <Done
          account={created}
          onAgain={() => {
            setCreated(null);
            setDraft(emptyDraft());
            setNamedByHand(false);
            setChecked(false);
            setStep(0, true);
          }}
        />
      </AppShell>
    );
  }

  return (
    <AppShell>
      <div className="mx-auto flex w-full max-w-2xl flex-col gap-6">
        <header className="flex flex-col gap-4">
          <Button
            variant="quiet"
            className="self-start px-0 py-0 text-xs"
            onClick={() =>
              step === 0 ? void navigate({ to: "/cuentas" }) : router.history.back()
            }
          >
            <ArrowLeft className="size-3.5" />
            {step === 0 ? "Cuentas" : STEPS[step - 1]}
          </Button>

          <div>
            <h1 className="font-semibold text-2xl tracking-tight">Nueva cuenta</h1>
            <p className="mt-1.5 text-muted text-sm">
              {step === 0
                ? "Empieza por lo único obligatorio: qué tipo de cuenta es."
                : step === 1
                  ? "Ponle nombre. Lo demás es opcional y se puede cambiar después."
                  : "Mira que esté bien y te contamos qué pasa al crearla."}
            </p>
          </div>

          <Stepper step={step} />
        </header>

        {/* Keyed on the step so each one plays its entrance instead of the
            words swapping in place under a heading that did not move. */}
        <div key={step} className="rise">
          {step === 0 ? (
            <KindStep
              kinds={catalog.account_kinds}
              selected={draft.kind}
              onChoose={chooseKind}
            />
          ) : null}

          {step === 1 ? (
            <DetailsStep
              draft={draft}
              kindLabel={kindLabel}
              issues={shown}
              currencies={catalog.currencies}
              instruments={catalog.instrument_kinds}
              onEdit={edit}
              onName={(name) => {
                setNamedByHand(true);
                edit({ name });
              }}
              onNext={next}
            />
          ) : null}

          {step === 2 ? (
            <ConfirmStep
              draft={draft}
              kindLabel={kindLabel}
              watched={watched}
              pending={create.isPending}
              error={create.error}
              onBack={() => router.history.back()}
              onConfirm={submit}
            />
          ) : null}
        </div>
      </div>
    </AppShell>
  );
}

/* -------------------------------------------------------------------- pasos */

function Stepper({ step }: { step: number }) {
  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-center gap-2 text-xs">
        {STEPS.map((label, index) => (
          <span
            key={label}
            className={cn(
              "transition-colors duration-300",
              index === step
                ? "font-medium text-text"
                : index < step
                  ? "text-cyan"
                  : "text-faint",
            )}
          >
            {index < step ? "✓ " : ""}
            {label}
          </span>
        ))}
      </div>
      <div className="h-1 overflow-hidden rounded-full bg-surface-raised">
        <div
          className="h-full rounded-full bg-gradient-to-r from-accent to-cyan transition-[width] duration-500 ease-out"
          style={{ width: `${((step + 1) / STEPS.length) * 100}%` }}
        />
      </div>
    </div>
  );
}

type KindOption = {
  value: string;
  label: string;
  category: string;
  informational: boolean;
};

/** Which of the three shelves a kind belongs on. */
function shelfOf(kind: KindOption): string {
  return kind.informational ? "informational" : kind.category;
}

/**
 * One click, and it advances. The kinds are grouped by what they do to the
 * figures, because that is the only thing about them somebody has to
 * understand — and there are three answers, not two: one adds to what you
 * have, one adds to what you owe, and a credit you are only following does
 * neither.
 */
function KindStep({
  kinds,
  selected,
  onChoose,
}: {
  kinds: KindOption[];
  selected: string;
  onChoose: (value: string, category: string, label: string) => void;
}) {
  const groups = [
    {
      category: "asset",
      title: "Lo que tienes",
      blurb: "Suma a tu patrimonio.",
    },
    {
      category: "liability",
      title: "Lo que debes",
      blurb: "Resta de tu patrimonio.",
    },
    {
      category: "informational",
      title: "Créditos que solo quieres seguir",
      blurb:
        "No entran en tu patrimonio ni en tus gastos: ya sabes lo que debes, y la cuota se te registra cuando sale de tu cuenta.",
    },
  ];

  return (
    <div className="flex flex-col gap-7">
      {groups.map(({ category, title, blurb }) => {
        const options = kinds.filter((kind) => shelfOf(kind) === category);
        if (options.length === 0) return null;

        return (
          <section key={category} className="flex flex-col gap-3">
            <h2 className="flex flex-wrap items-baseline gap-x-2 text-muted text-sm">
              {title}
              <span className="text-faint text-xs">{blurb}</span>
            </h2>

            <div className="grid gap-3 sm:grid-cols-2">
              {options.map((option, index) => {
                const copy = kindCopy(option.value, option.label);
                const Icon = copy.icon;
                const active = selected === option.value;

                return (
                  <button
                    key={option.value}
                    type="button"
                    onClick={() =>
                      onChoose(option.value, option.category, option.label)
                    }
                    aria-pressed={active}
                    className={cn(
                      "surface rise flex items-start gap-3 rounded-card border bg-surface p-4 text-left",
                      category === "asset" ? "glow-cyan" : "glow-accent",
                      active ? "border-accent/50" : "border-line",
                    )}
                    style={{ animationDelay: `${index * 45}ms` }}
                  >
                    <span
                      aria-hidden
                      className={cn(
                        "grid size-10 shrink-0 place-items-center rounded-xl ring-1 transition-transform duration-200",
                        category === "asset"
                          ? "bg-cyan/12 text-cyan ring-cyan/25"
                          : "bg-accent/12 text-accent ring-accent/25",
                      )}
                    >
                      <Icon className="size-5" />
                    </span>
                    <span className="min-w-0">
                      <span className="block font-medium text-sm">{copy.label}</span>
                      <span className="mt-0.5 block text-muted text-xs leading-relaxed">
                        {copy.blurb}
                      </span>
                    </span>
                  </button>
                );
              })}
            </div>
          </section>
        );
      })}
    </div>
  );
}

type Option = { value: string; label: string };

function DetailsStep({
  draft,
  kindLabel,
  issues,
  currencies,
  instruments,
  onEdit,
  onName,
  onNext,
}: {
  draft: AccountDraft;
  kindLabel: string;
  issues: ReturnType<typeof validateDraft>;
  currencies: Option[];
  instruments: Option[];
  onEdit: (patch: Partial<AccountDraft>) => void;
  onName: (name: string) => void;
  onNext: () => void;
}) {
  const copy = kindCopy(draft.kind, kindLabel);
  const owed = isLiability(draft);
  /* Cash and a mortgage never email, so the block that links alerts to this
     account would be four fields nobody can fill in. */
  const emails = canLinkAlerts(draft.kind);

  return (
    <Card lift={false}>
      {/* A real form, so the phone keyboard offers "Ir" and Enter advances
          instead of doing nothing. */}
      <form
        onSubmit={(event) => {
          event.preventDefault();
          onNext();
        }}
        className="flex flex-col gap-6"
      >
        <Field
          label="¿Cómo se llama?"
          required
          maxLength={120}
          placeholder={copy.suggestedName}
          hint="Como la reconoces tú. Aparece así en tus movimientos."
          value={draft.name}
          onChange={(event) => onName(event.target.value)}
        />
        <Issue message={issueFor(issues, "name")} />

        <div className="grid gap-5 sm:grid-cols-[1fr_9rem]">
          <Field
            label={copy.openingLabel}
            inputMode="decimal"
            placeholder="0"
            hint={copy.openingHint}
            // A string all the way to the API: a float loses cents.
            value={draft.openingBalance}
            onChange={(event) => onEdit({ openingBalance: event.target.value })}
          />
          <Select
            label="Moneda"
            value={draft.currency}
            onChange={(event) => onEdit({ currency: event.target.value as Currency })}
            options={currencies}
          />
        </div>
        <Issue message={issueFor(issues, "openingBalance")} />

        {owed ? (
          <>
            <Field
              label="Cupo total (opcional)"
              inputMode="decimal"
              placeholder="0"
              hint="El cupo, no lo gastado. Con los dos, Finflow te muestra cuánto te queda."
              value={draft.creditLimit}
              onChange={(event) => onEdit({ creditLimit: event.target.value })}
            />
            <Issue message={issueFor(issues, "creditLimit")} />
          </>
        ) : null}

        {emails ? (
          <fieldset className="flex flex-col gap-5 rounded-xl border border-line bg-ink/60 p-4">
            <legend className="px-1 text-muted text-xs uppercase tracking-wider">
              Sus alertas (opcional)
            </legend>

            <p className="text-faint text-xs leading-relaxed">
              Esto es lo que hace que sus movimientos se le asignen solos, incluidos los
              que ya llegaron. Si no tienes los datos a la mano, sáltalo: se puede
              enlazar después desde la cuenta.
            </p>

            <Field
              label="Banco"
              placeholder="Bancolombia"
              maxLength={512}
              hint="Como aparece en el correo que te manda."
              value={draft.bank}
              onChange={(event) => onEdit({ bank: event.target.value })}
            />
            <Issue message={issueFor(issues, "bank")} />

            <Select
              label="¿Cómo llegan sus alertas?"
              placeholder="Todavía no la enlazo"
              hint="Es la palabra que usa el banco, no el tipo de cuenta. Una cuenta también manda alertas como tarjeta débito: ahí se enlazan las dos."
              value={draft.instrumentKind}
              onChange={(event) => onEdit({ instrumentKind: event.target.value })}
              options={instruments.map((option) => ({
                value: option.value,
                label:
                  option.value === copy.instrument
                    ? `${instrumentLabel(option.value, option.label)} — lo más común`
                    : instrumentLabel(option.value, option.label),
              }))}
            />
            <Issue message={issueFor(issues, "instrumentKind")} />

            <Field
              label="Últimos cuatro dígitos"
              inputMode="numeric"
              placeholder="0530"
              hint="Los de la tarjeta o cuenta que nombra la alerta."
              value={draft.lastFour}
              onChange={(event) => onEdit({ lastFour: event.target.value })}
            />
            <Issue message={issueFor(issues, "lastFour")} />
          </fieldset>
        ) : null}

        <Button type="submit" full className="py-3.5">
          Continuar
          <ArrowRight className="size-4" />
        </Button>
      </form>
    </Card>
  );
}

function Issue({ message }: { message?: string }) {
  if (!message) return null;
  return (
    <p role="alert" className="rise -mt-4 text-outgoing text-sm">
      {message}
    </p>
  );
}

/**
 * The last step, and the one that earns its place: the recap is short and the
 * explanation is the point. Confirming is a deliberate act here, not the
 * fourth "next" in a row.
 */
function ConfirmStep({
  draft,
  kindLabel,
  watched,
  pending,
  error,
  onBack,
  onConfirm,
}: {
  draft: AccountDraft;
  kindLabel: string;
  /** A loan or a mortgage: kept, and left out of every total. */
  watched: boolean;
  pending: boolean;
  error: Error | null;
  onBack: () => void;
  onConfirm: () => void;
}) {
  const copy = kindCopy(draft.kind, kindLabel);
  const Icon = copy.icon;
  const owed = isLiability(draft);
  const linked = draft.instrumentKind !== "" && draft.lastFour.trim() !== "";
  const duplicate = error instanceof ApiError && error.status === 409;

  return (
    <div className="flex flex-col gap-4">
      <Card
        glow={owed ? "accent" : "cyan"}
        lift={false}
        className="flex flex-col gap-5"
      >
        <div className="flex items-center gap-3">
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
          <div className="min-w-0">
            <p className="truncate font-medium">{draft.name.trim()}</p>
            <p className="mt-0.5 text-faint text-xs">
              {copy.label} · {draft.currency}
            </p>
          </div>
        </div>

        <dl className="flex flex-col divide-y divide-line/70 border-line/70 border-t text-sm">
          <Row label={owed ? "Ya gastado" : "Saldo de hoy"}>
            {draft.openingBalance.trim() === "" ? (
              <span className="text-faint">Arranca en cero</span>
            ) : (
              <Money
                amount={draft.openingBalance.trim()}
                currency={draft.currency}
                size="sm"
              />
            )}
          </Row>

          {owed ? (
            <Row label="Cupo">
              {draft.creditLimit.trim() === "" ? (
                <span className="text-faint">Sin declarar</span>
              ) : (
                <Money
                  amount={draft.creditLimit.trim()}
                  currency={draft.currency}
                  size="sm"
                />
              )}
            </Row>
          ) : null}

          <Row label="Sus alertas">
            {linked ? (
              <span>
                {draft.bank.trim()} · {instrumentLabel(draft.instrumentKind)} ····{" "}
                {draft.lastFour.trim()}
              </span>
            ) : (
              <span className="text-faint">Sin enlazar por ahora</span>
            )}
          </Row>
        </dl>
      </Card>

      <Card lift={false} className="flex flex-col gap-5">
        <h2 className="font-medium text-sm">Qué hace una cuenta en Finflow</h2>

        <ul className="flex flex-col gap-4">
          <Point icon={Sparkles} title="Recoge lo que ya llegó">
            {linked
              ? "En cuanto exista, adopta los movimientos que estaban esperando por ese banco y esos cuatro dígitos, y recalcula su saldo con ellos."
              : "Cuando la enlaces con las alertas de tu banco, adoptará también los movimientos que ya habían llegado. Nada se pierde mientras tanto."}
          </Point>
          <Point
            icon={TrendingDown}
            title={watched ? "No entra en ningún total" : "Entra en tu patrimonio"}
          >
            {watched
              ? "Su saldo se queda aquí: no resta de tu patrimonio ni cuenta como gasto. La cuota sí se registra, cuando sale de la cuenta que la paga."
              : owed
                ? "Lo que debes en ella resta de tu patrimonio, y su saldo es la deuda: gastar lo sube."
                : "Lo que tiene suma a tu patrimonio, junto con las demás cuentas de la misma moneda."}
          </Point>
          <Point icon={ShieldCheck} title="No toca tu banco">
            No pedimos claves ni entramos a ningún lado. Es una etiqueta tuya para
            ordenar lo que ya te llega por correo.
          </Point>
          <Point icon={Radio} title="Todo se puede corregir">
            El nombre, el cupo y a qué cuenta pertenece cada movimiento. Nada de esto
            queda escrito en piedra.
          </Point>
        </ul>

        {error ? (
          <div
            role="alert"
            className="rise rounded-xl border border-warn/30 bg-warn/10 p-4"
          >
            <p className="text-sm text-warn">
              {duplicate
                ? "Ya tienes una cuenta declarada con ese banco y esos últimos cuatro dígitos."
                : error.message}
            </p>
            {duplicate ? (
              <Link
                to="/cuentas"
                className="mt-2 inline-flex items-center gap-1.5 text-sm text-warn underline underline-offset-4"
              >
                Ver la que ya existe
                <ArrowRight className="size-3.5" />
              </Link>
            ) : null}
          </div>
        ) : null}

        <div className="flex flex-col gap-3 sm:flex-row-reverse">
          <Button full onClick={onConfirm} disabled={pending} className="py-3.5">
            {pending ? (
              <>
                <Loader2 className="size-4 animate-spin" />
                Creando…
              </>
            ) : (
              <>
                <Check className="size-4" />
                Sí, crear la cuenta
              </>
            )}
          </Button>
          <Button variant="ghost" full onClick={onBack} disabled={pending}>
            Volver a editar
          </Button>
        </div>
      </Card>
    </div>
  );
}

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-4 py-2.5">
      <dt className="text-muted">{label}</dt>
      <dd className="min-w-0 truncate text-right">{children}</dd>
    </div>
  );
}

function Point({
  icon: Icon,
  title,
  children,
}: {
  icon: typeof Sparkles;
  title: string;
  children: ReactNode;
}) {
  return (
    <li className="flex items-start gap-3">
      <span
        aria-hidden
        className="mt-0.5 grid size-8 shrink-0 place-items-center rounded-xl border border-line bg-ink"
      >
        <Icon className="size-4 text-cyan" />
      </span>
      <span className="min-w-0">
        <span className="block font-medium text-sm">{title}</span>
        <span className="mt-0.5 block text-muted text-sm leading-relaxed">
          {children}
        </span>
      </span>
    </li>
  );
}

/**
 * What the account did on the way in.
 *
 * `movements_applied` comes back already updated, so this is the one moment
 * the app can prove the retroactive part instead of promising it.
 */
function Done({ account, onAgain }: { account: Account; onAgain: () => void }) {
  const adopted = account.movements_applied;

  return (
    <div className="mx-auto flex w-full max-w-lg flex-col gap-6">
      <Card
        glow="green"
        lift={false}
        className="rise flex flex-col items-center gap-5 p-8 text-center"
      >
        {/* The connect guide's one celebration, for the other moment that
            earns it: something the owner declared now exists and is counted. */}
        <SuccessMark celebrate />

        <div>
          <h1 className="font-semibold text-xl tracking-tight">
            Listo, ya tienes {account.name}
          </h1>
          <p className="mt-2 text-muted text-sm leading-relaxed">
            {adopted > 0 ? (
              <>
                Adoptó{" "}
                <strong className="text-text">
                  {adopted} {adopted === 1 ? "movimiento" : "movimientos"}
                </strong>{" "}
                que estaban esperando por ella, y su saldo ya los cuenta.
              </>
            ) : (
              "Todavía no tenía movimientos esperando. Los que lleguen de aquí en adelante caerán solos, y los que ya estén sin asignar puedes moverlos a mano."
            )}
          </p>
        </div>

        <Money
          amount={account.balance}
          currency={account.currency}
          size="md"
          tone={account.category === "liability" ? "negative" : "positive"}
        />

        <div className="mt-2 flex w-full flex-col gap-2.5">
          <Link
            to="/cuentas"
            className="inline-flex w-full items-center justify-center gap-2 rounded-xl bg-accent px-4 py-3.5 font-semibold text-accent-ink text-sm transition-all duration-150 hover:brightness-108"
          >
            Ver mis cuentas
          </Link>
          <Button variant="quiet" onClick={onAgain}>
            Declarar otra
          </Button>
        </div>
      </Card>
    </div>
  );
}
