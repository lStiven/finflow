import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import {
  Ban,
  Check,
  ChevronDown,
  FileQuestion,
  Filter,
  Landmark,
  LifeBuoy,
  Loader2,
  TriangleAlert,
} from "lucide-react";
import type { ComponentType, ReactNode } from "react";
import {
  latestAlertMovementQuery,
  type Notification,
  recentMailQuery,
} from "@/api/queries";
import { Button, buttonClass } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { cn } from "@/lib/cn";
import { formatDate, formatRelative } from "@/lib/dates";
import { useNow } from "@/lib/useNow";
import { type Health, healthOf, type Outcome, outcomeOf } from "@/onboarding/activity";
import { DiscardedSenders } from "@/onboarding/BanksStep";
import {
  approvedBanks,
  bankOfSender,
  filterDrift,
  filterTerms,
  isApproved,
  withSender,
} from "@/onboarding/banks";
import { CopyField } from "@/onboarding/CopyField";
import { GMAIL_FILTERS_URL } from "@/onboarding/gmail";
import { MovementCard, MovementPlaceholder } from "@/onboarding/MovementCard";
import { ExternalButton } from "@/onboarding/parts";
import type { OnboardingState, StageId } from "@/onboarding/steps";
import { type SendersControl, useSenders } from "@/onboarding/useSenders";

/**
 * The connection, for somebody who already finished setting it up.
 *
 * Not the guide again: what is connected, when mail last arrived, and what
 * became of it. The headline is earned by the records — "connected" only
 * while they agree, something else the moment they do not — and every
 * problem it can see comes with the one thing to do about it. What it cannot
 * see (a Gmail filter quietly deleted) it does not pretend to: past a couple
 * of weeks of silence it stops saying all is well.
 */
export function ConnectionSummary({
  state,
  onOpenStage,
  onFilterUpdated,
}: {
  state: OnboardingState;
  onOpenStage: (id: StageId) => void;
  /** They say Gmail's filter now matches these terms. */
  onFilterUpdated: (terms: string[]) => void;
}) {
  const control = useSenders();
  const { senders } = control;
  const now = useNow(60_000);
  const mail = useQuery({ ...recentMailQuery, refetchInterval: 60_000 });
  const latest = useQuery(latestAlertMovementQuery);

  const notifications = mail.data?.notifications ?? [];
  const health = mail.data ? healthOf(notifications, senders, now) : null;
  const terms = filterTerms(senders);
  const drift = filterDrift(state.filterSenders, terms);
  const movement = latest.data?.transactions[0] ?? null;
  const headline = headlineOf(health, state, now);

  return (
    <div className="rise flex flex-col gap-6">
      <header className="flex items-start gap-4">
        <StatusOrb tone={headline.tone} />
        <div className="min-w-0">
          <p className="text-faint text-xs uppercase tracking-wider">
            Conexión con tu banco
          </p>
          <h1 className="mt-1 text-balance font-semibold text-2xl tracking-tight">
            {headline.title}
          </h1>
          <p className="mt-1.5 max-w-xl text-pretty text-muted text-sm leading-relaxed">
            {headline.detail}
          </p>
        </div>
      </header>

      {health?.kind === "discarding" ? (
        <DiscardedSenders senders={health.senders} control={control} />
      ) : null}

      {drift && (drift.added.length > 0 || drift.removed.length > 0) ? (
        <FilterUpdate
          added={drift.added}
          removed={drift.removed}
          filter={terms.join(" OR ")}
          onDone={() => onFilterUpdated(terms)}
        />
      ) : null}

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-5">
        <Card lift={false} className="flex min-w-0 flex-col gap-5 lg:col-span-3">
          <Row icon={Landmark} title="Tus bancos">
            <div className="flex flex-wrap items-center gap-2">
              {approvedBanks(senders).map((bank) => (
                <span
                  key={bank.name}
                  className={cn(
                    "inline-flex max-w-full items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs",
                    bank.partial
                      ? "border-warn/35 bg-warn/8 text-warn"
                      : "border-incoming/30 bg-incoming/8 text-incoming",
                  )}
                >
                  {bank.partial ? (
                    <TriangleAlert className="size-3 shrink-0" aria-hidden />
                  ) : (
                    <Check className="size-3 shrink-0" aria-hidden />
                  )}
                  <span className="truncate">{bank.name}</span>
                  {bank.partial ? <span className="sr-only">(incompleto)</span> : null}
                </span>
              ))}
              <Button
                variant="quiet"
                onClick={() => onOpenStage("banks")}
                className="px-2 py-1.5 text-cyan text-xs hover:text-cyan"
              >
                Administrar
              </Button>
            </div>
          </Row>

          <div className="border-line/70 border-t pt-5">
            <CopyField
              label="Tu dirección de Finflow"
              value={state.address}
              copyLabel="Copiar"
              copiedLabel="¡Copiada!"
              announce="Dirección copiada"
            />
            <p className="mt-2 text-faint text-xs">
              {state.forwardingConfirmedAt
                ? `Gmail la autorizó el ${formatDate(state.forwardingConfirmedAt)}.`
                : "Recibe los correos que le reenvías."}
            </p>
          </div>
        </Card>

        <div className="flex min-w-0 flex-col gap-3 lg:col-span-2">
          <p className="text-faint text-xs uppercase tracking-wider">
            Último movimiento automático
          </p>
          {movement ? (
            <MovementCard movement={movement} />
          ) : latest.isPending ? (
            <MovementPlaceholder />
          ) : (
            <p className="rounded-2xl border border-line border-dashed p-4 text-muted text-sm">
              Todavía no hay movimientos que hayan llegado de tu banco.
            </p>
          )}
          <Link to="/transacciones" className={buttonClass("primary", true)}>
            Ver mis movimientos
          </Link>
        </div>
      </div>

      <RecentMail notifications={notifications} control={control} now={now} />

      <div className="flex flex-wrap gap-2">
        <Button variant="ghost" onClick={() => onOpenStage("banks")}>
          <Landmark className="size-4" aria-hidden />
          Administrar bancos
        </Button>
        <Button variant="ghost" onClick={() => onOpenStage("address")}>
          <Filter className="size-4" aria-hidden />
          Repasar los pasos de Gmail
        </Button>
      </div>

      <Troubleshooting filter={terms.join(" OR ")} />
    </div>
  );
}

type Headline = { title: string; detail: string; tone: "ok" | "warn" };

function headlineOf(
  health: Health | null,
  state: OnboardingState,
  now: number,
): Headline {
  const connected = "Tus bancos están conectados";

  if (health === null) {
    return { title: connected, detail: "Revisando los últimos correos…", tone: "ok" };
  }

  switch (health.kind) {
    case "quiet":
      return {
        title: connected,
        detail: state.firstAlertAt
          ? `Finflow recibe tus alertas desde el ${formatDate(state.firstAlertAt)}.`
          : "Finflow ya recibe tus alertas.",
        tone: "ok",
      };
    case "ok":
      return {
        title: connected,
        detail: `Finflow recibió tu último correo ${formatRelative(health.lastAt, now)}.`,
        tone: "ok",
      };
    case "unreadable":
      return health.count > 1
        ? {
            title: "Tus últimos correos no se pudieron leer",
            detail: `Llegaron bien, pero Finflow no encontró un movimiento en los últimos ${health.count}. Si eran compras, puedes registrarlas a mano.`,
            tone: "warn",
          }
        : {
            title: connected,
            detail: `Tu último correo llegó ${formatRelative(health.lastAt, now)}, aunque no traía un movimiento.`,
            tone: "ok",
          };
    case "stale":
      return {
        title: "No han llegado correos en un tiempo",
        detail: `El último llegó ${formatRelative(health.lastAt, now)}. Si usaste tus tarjetas desde entonces, revisa que tu filtro de Gmail siga activo.`,
        tone: "warn",
      };
    case "discarding":
      return {
        title: "Se están descartando correos",
        detail: "Llegan correos de un remitente que no está entre tus bancos.",
        tone: "warn",
      };
  }
}

function StatusOrb({ tone }: { tone: Headline["tone"] }) {
  return (
    <span
      aria-hidden
      className="relative mt-1 grid size-12 shrink-0 place-items-center"
    >
      {tone === "ok" ? (
        <>
          <span className="pulse-ring absolute inset-0 rounded-2xl bg-incoming/12 ring-1 ring-incoming/30" />
          <Check className="relative size-5 text-incoming" strokeWidth={3} />
        </>
      ) : (
        <>
          <span className="absolute inset-0 rounded-2xl bg-warn/12 ring-1 ring-warn/35" />
          <TriangleAlert className="relative size-5 text-warn" />
        </>
      )}
    </span>
  );
}

function Row({
  icon: Icon,
  title,
  children,
}: {
  icon: ComponentType<{ className?: string }>;
  title: string;
  children: ReactNode;
}) {
  return (
    <div className="flex items-start gap-3">
      <span
        aria-hidden
        className="grid size-9 shrink-0 place-items-center rounded-xl border border-line bg-ink text-cyan"
      >
        <Icon className="size-4" />
      </span>
      <div className="min-w-0 flex-1">
        <p className="mb-1.5 text-faint text-xs uppercase tracking-wider">{title}</p>
        {children}
      </div>
    </div>
  );
}

function termsToNames(terms: string[]): string {
  const names = [
    ...new Set(
      terms.map((term) => {
        const sender = term.startsWith("@") ? `x${term}` : term;
        return bankOfSender(sender)?.name ?? term;
      }),
    ),
  ];
  if (names.length <= 1) return names.join("");
  return `${names.slice(0, -1).join(", ")} y ${names.at(-1)}`;
}

/**
 * The filter in Gmail and the banks here have drifted apart since this
 * browser saw it made. A bank added since is urgent — its alerts are not
 * being forwarded at all; one removed since is not, the intake already
 * throws its mail away.
 */
function FilterUpdate({
  added,
  removed,
  filter,
  onDone,
}: {
  added: string[];
  removed: string[];
  filter: string;
  onDone: () => void;
}) {
  const urgent = added.length > 0;

  return (
    <div
      className={cn(
        "rise rounded-2xl border p-4 sm:p-5",
        urgent ? "border-warn/30 bg-warn/7" : "border-line bg-surface",
      )}
    >
      <p
        className={cn(
          "flex items-center gap-2 font-medium text-sm",
          urgent ? "text-warn" : "text-text",
        )}
      >
        <Filter className="size-4 shrink-0" aria-hidden />
        {urgent
          ? `Tu filtro de Gmail no incluye ${termsToNames(added)}`
          : `Tu filtro de Gmail todavía reenvía ${termsToNames(removed)}`}
      </p>
      <p className="mt-1 text-muted text-sm leading-relaxed">
        {urgent
          ? "Sus alertas no llegarán hasta que actualices el filtro."
          : "Finflow ya descarta esos correos, así que no es urgente."}
      </p>
      <ol className="mt-4 flex flex-col gap-4 text-sm">
        <li>
          <CopyField
            label="1 · Copia el filtro nuevo"
            value={filter}
            copyLabel="Copiar filtro"
            announce="Filtro copiado"
            emphasis={urgent ? "primary" : "ghost"}
          />
        </li>
        <li className="flex flex-col gap-2">
          <p className="text-faint text-xs uppercase tracking-wider">
            2 · Edítalo en Gmail
          </p>
          <p className="text-muted leading-relaxed">
            En «Filtros y direcciones bloqueadas», pulsa «Editar» en tu filtro,
            reemplaza el campo «De» por el texto nuevo y guárdalo.
          </p>
          <ExternalButton href={GMAIL_FILTERS_URL} className="self-start">
            Abrir mis filtros de Gmail
          </ExternalButton>
        </li>
      </ol>
      <Button variant="ghost" onClick={onDone} className="mt-4">
        <Check className="size-4" aria-hidden />
        Ya lo actualicé
      </Button>
    </div>
  );
}

const OUTCOME_LOOK: Record<
  Outcome,
  { label: string; icon: ComponentType<{ className?: string }>; className: string }
> = {
  registered: { label: "Registrado", icon: Check, className: "text-incoming" },
  reading: { label: "Leyendo…", icon: Loader2, className: "text-cyan" },
  unreadable: { label: "Sin movimiento", icon: FileQuestion, className: "text-faint" },
  discarded: { label: "Descartado", icon: Ban, className: "text-warn" },
};

/**
 * What arrived lately and what became of each — the place to find out why a
 * movement is missing without asking anybody.
 */
function RecentMail({
  notifications,
  control,
  now,
}: {
  notifications: Notification[];
  control: SendersControl;
  now: number;
}) {
  if (notifications.length === 0) return null;

  return (
    <section aria-labelledby="recent-mail-title">
      <h2 id="recent-mail-title" className="mb-3 font-medium text-sm">
        Últimos correos recibidos
      </h2>
      <ul className="divide-y divide-line/70 rounded-2xl border border-line bg-surface">
        {notifications.map((notification) => {
          const outcome = outcomeOf(notification.status);
          const look = OUTCOME_LOOK[outcome];
          const Icon = look.icon;
          const bank = bankOfSender(notification.sender);
          const turnedAway =
            outcome === "discarded" &&
            !isApproved(notification.sender, control.senders);
          const key = `recent:${notification.id}`;

          return (
            <li
              key={notification.id}
              className="flex flex-wrap items-center gap-x-3 gap-y-2 px-4 py-3"
            >
              <span className="min-w-0 flex-1">
                <span className="block truncate text-sm">
                  {bank?.name ?? notification.sender}
                </span>
                <span className="block truncate text-faint text-xs">
                  {notification.subject || "(sin asunto)"} ·{" "}
                  {formatRelative(notification.received_at, now)}
                </span>
              </span>
              <span
                className={cn(
                  "flex shrink-0 items-center gap-1.5 text-xs",
                  look.className,
                )}
              >
                <Icon
                  className={cn("size-3.5", outcome === "reading" && "animate-spin")}
                  aria-hidden
                />
                {look.label}
              </span>
              {turnedAway ? (
                <Button
                  variant="ghost"
                  onClick={() =>
                    control.save(
                      withSender(control.senders, {
                        type: "address",
                        value: notification.sender.toLowerCase(),
                      }),
                      key,
                    )
                  }
                  disabled={control.busy}
                  className="w-full px-3 py-2 text-xs sm:w-auto"
                >
                  {control.savingKey === key ? (
                    <Loader2 className="size-3.5 animate-spin" aria-hidden />
                  ) : null}
                  Es de mi banco
                </Button>
              ) : null}
            </li>
          );
        })}
      </ul>
    </section>
  );
}

function Troubleshooting({ filter }: { filter: string }) {
  return (
    <details className="group rounded-2xl border border-line bg-surface">
      <summary className="flex min-h-12 cursor-pointer list-none items-center gap-3 rounded-2xl px-4 py-3 text-sm transition-colors hover:bg-surface-raised/50 [&::-webkit-details-marker]:hidden">
        <LifeBuoy className="size-4 shrink-0 text-cyan" aria-hidden />
        ¿Algo no funciona?
        <ChevronDown
          className="ml-auto size-4 shrink-0 text-faint transition-transform duration-200 group-open:rotate-180"
          aria-hidden
        />
      </summary>
      <div className="flex flex-col gap-5 border-line border-t p-4 sm:p-5">
        <Case title="No llega ningún movimiento">
          Revisa en Gmail que tu filtro exista y que, al buscar con su campo «De»,
          aparezcan las alertas de tu banco. Un filtro solo reenvía lo que llega después
          de crearlo.
          <ExternalButton href={GMAIL_FILTERS_URL} className="mt-3 self-start">
            Abrir mis filtros de Gmail
          </ExternalButton>
        </Case>
        <Case title="Agregué otro banco">
          Pon este texto en el campo «De» de tu filtro de Gmail, para que también
          reenvíe sus alertas.
          <CopyField
            className="mt-3"
            label="Tu filtro"
            value={filter}
            copyLabel="Copiar filtro"
            announce="Filtro copiado"
          />
        </Case>
        <Case title="Mi banco empezó a escribir desde otro correo">
          Aparecerá como «Descartado» en tus últimos correos. Pulsa «Es de mi banco»
          para aceptarlo.
        </Case>
        <Case title="Quiero dejar de reenviar">
          Borra el filtro en Gmail, en «Filtros y direcciones bloqueadas». Si además
          quitas tus bancos aquí, Finflow dejará de aceptar sus correos.
        </Case>
      </div>
    </details>
  );
}

function Case({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="flex flex-col">
      <p className="font-medium text-sm">{title}</p>
      <div className="mt-1 flex flex-col text-muted text-sm leading-relaxed">
        {children}
      </div>
    </div>
  );
}
