import { useSuspenseQuery } from "@tanstack/react-query";
import { createFileRoute, redirect } from "@tanstack/react-router";
import {
  ArrowRight,
  BadgeCheck,
  Ban,
  Check,
  ChevronDown,
  Copy,
  Loader2,
  Mail,
  Radio,
  ShieldCheck,
  Sparkles,
  Wallet,
  X,
} from "lucide-react";
import type { ReactNode } from "react";
import { type SubmitEvent, useState } from "react";
import { inboxQuery, setupQuery, useUpdateInbox } from "@/api/queries";
import { AppShell } from "@/components/AppShell";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { Field } from "@/components/ui/Field";
import { cn } from "@/lib/cn";
import { formatDateTime } from "@/lib/dates";
import { STAGE_COPY } from "@/onboarding/copy";
import type { AddressStatus, StageId } from "@/onboarding/steps";
import { useOnboarding } from "@/onboarding/useOnboarding";

export const Route = createFileRoute("/conectar")({
  beforeLoad: ({ context }) => {
    if (!context.session) throw redirect({ to: "/login" });
  },
  loader: ({ context }) =>
    Promise.all([
      context.queryClient.ensureQueryData(setupQuery),
      context.queryClient.ensureQueryData(inboxQuery),
    ]),
  component: ConnectScreen,
});

/**
 * One screen, two jobs.
 *
 * While anything is open it is the guide that walks somebody through
 * forwarding their first alert, resuming wherever they left off. Once
 * expenses are arriving it stops asking for anything and becomes the place to
 * look the address up and read the explanation again — which is why it is one
 * route and not two: the same five stages, told in the past tense.
 */
function ConnectScreen() {
  const { state, acknowledge } = useOnboarding();
  // The loader filled the cache, so this is the same object the hook read.
  useSuspenseQuery(setupQuery);
  /*
   * Which panel is open, when somebody has said so themselves.
   *
   * `null` means "follow the flow": the open panel is whichever stage is
   * current, so finishing one opens the next instead of leaving a ticked
   * stage expanded above a collapsed one. Acknowledging hands control back.
   */
  const [manual, setManual] = useState<StageId | "none" | null>(null);

  if (!state) return null;

  const { complete, current } = state;

  function isExpanded(id: StageId): boolean {
    if (manual !== null) return manual === id;
    return complete ? id === "intro" : current === id;
  }

  function acknowledgeAndAdvance(key: Parameters<typeof acknowledge>[0]) {
    acknowledge(key);
    setManual(null);
  }

  return (
    <AppShell>
      <header className="mb-8 flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="font-semibold text-2xl tracking-tight">
            {complete ? "Cómo funciona Finflow" : "Conecta tu banco"}
          </h1>
          <p className="mt-1.5 max-w-xl text-muted text-sm">
            {complete
              ? "Ya está todo andando. Esto queda por si quieres repasar el camino que hace un correo, o volver a ver tu dirección."
              : "Cuatro cosas tienen que ser ciertas para que tus gastos se registren solos. Vamos una por una."}
          </p>
        </div>
        <AddressBadge status={state.addressStatus} />
      </header>

      {!complete ? <ProgressBar done={state.doneCount} total={state.total} /> : null}

      <AddressCard address={state.address} status={state.addressStatus} />

      {state.unapprovedSenders.length > 0 ? (
        <DiscardedNotice senders={state.unapprovedSenders} />
      ) : null}

      <ol className="mt-6 flex flex-col gap-3">
        {state.stages.map((stage, index) => (
          <StagePanel
            key={stage.id}
            index={index}
            id={stage.id}
            done={stage.done}
            proof={stage.proof}
            current={current === stage.id}
            expanded={isExpanded(stage.id)}
            onToggle={() => setManual(isExpanded(stage.id) ? "none" : stage.id)}
          >
            <StageBody
              id={stage.id}
              address={state.address}
              status={state.addressStatus}
              complete={complete}
              onDone={acknowledgeAndAdvance}
            />
          </StagePanel>
        ))}
      </ol>
    </AppShell>
  );
}

/* ------------------------------------------------------------------ pieces */

const STATUS_COPY: Record<AddressStatus, { label: string; className: string }> = {
  unverified: {
    label: "Sin verificar",
    className: "border-line bg-surface-raised text-muted",
  },
  confirmed: {
    label: "Correo verificado",
    className: "border-cyan/30 bg-cyan/10 text-cyan",
  },
  receiving: {
    label: "Recibiendo movimientos",
    className: "border-incoming/30 bg-incoming/10 text-incoming",
  },
};

/** The one indicator that says whether the address itself is settled. */
function AddressBadge({ status }: { status: AddressStatus }) {
  const { label, className } = STATUS_COPY[status];

  return (
    <span
      className={cn(
        "inline-flex items-center gap-2 rounded-full border px-3 py-1.5 text-xs",
        className,
      )}
    >
      {status === "unverified" ? (
        <Radio className="size-3.5" />
      ) : (
        <BadgeCheck className="size-3.5" />
      )}
      {label}
    </span>
  );
}

function ProgressBar({ done, total }: { done: number; total: number }) {
  return (
    <div className="mb-6">
      <div className="mb-2 flex items-baseline justify-between text-sm">
        <span className="text-muted">Tu progreso</span>
        <span className="tabular text-faint text-xs">
          {done} de {total}
        </span>
      </div>
      <div className="h-1.5 overflow-hidden rounded-full bg-surface-raised">
        <div
          className="h-full rounded-full bg-gradient-to-r from-accent to-cyan transition-[width] duration-500 ease-out"
          style={{ width: `${(done / total) * 100}%` }}
        />
      </div>
    </div>
  );
}

/** The address, always in reach — during the setup and long after it. */
function AddressCard({ address, status }: { address: string; status: AddressStatus }) {
  return (
    <Card glow="cyan" lift={false} className="flex flex-col gap-4">
      <div className="flex items-center gap-2 text-muted text-xs uppercase tracking-wider">
        <Mail className="size-3.5" />
        Tu dirección de reenvío
      </div>

      <div className="flex flex-wrap items-center gap-3">
        <code className="min-w-0 flex-1 break-all rounded-xl border border-line bg-ink px-4 py-3 text-sm">
          {address}
        </code>
        <CopyButton value={address} />
      </div>

      <p className="text-faint text-xs">
        Es tuya y no cambia nunca. Solo lee los correos que llegan a ella, y solo de los
        remitentes que apruebes.{" "}
        {status === "receiving"
          ? "Ahora mismo está recibiendo."
          : "Todavía no ha recibido nada tuyo."}
      </p>
    </Card>
  );
}

function CopyButton({ value, onCopied }: { value: string; onCopied?: () => void }) {
  const [copied, setCopied] = useState(false);

  async function copy() {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
      onCopied?.();
      window.setTimeout(() => setCopied(false), 2000);
    } catch {
      // Clipboard access can be refused outright (an insecure origin, a
      // permission prompt denied). The address is on screen and selectable,
      // so there is nothing to recover — only nothing to promise.
      setCopied(false);
    }
  }

  return (
    <Button variant="ghost" onClick={copy} className="shrink-0">
      {copied ? (
        <Check className="size-4 text-incoming" />
      ) : (
        <Copy className="size-4" />
      )}
      {copied ? "Copiada" : "Copiar"}
    </Button>
  );
}

/**
 * The one failure that looks exactly like nothing happening: mail arriving
 * and being thrown away for coming from somebody who was never approved.
 */
function DiscardedNotice({ senders }: { senders: string[] }) {
  return (
    <div className="mt-4 flex items-start gap-3 rounded-xl border border-warn/30 bg-warn/10 p-4">
      <Ban className="mt-0.5 size-4 shrink-0 text-warn" />
      <div className="min-w-0">
        <p className="font-medium text-sm text-warn">
          Está llegando correo que se está descartando
        </p>
        <p className="mt-1 text-muted text-sm">
          Estos remitentes escribieron a tu dirección y no están aprobados, así que no
          se leyó nada de ellos. Si es tu banco, apruébalo en el paso 3.
        </p>
        <ul className="mt-2 flex flex-wrap gap-2">
          {senders.map((sender) => (
            <li
              key={sender}
              className="rounded-lg border border-line bg-ink px-2 py-1 text-xs"
            >
              {sender}
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}

/** One stage, collapsed unless it is the one being pointed at. */
function StagePanel({
  index,
  id,
  done,
  proof,
  current,
  expanded,
  onToggle,
  children,
}: {
  index: number;
  id: StageId;
  done: boolean;
  proof: "you" | "verified";
  /** The stage being pointed at, which is what gets the lit border. */
  current: boolean;
  expanded: boolean;
  onToggle: () => void;
  children: ReactNode;
}) {
  const copy = STAGE_COPY[id];

  return (
    <li>
      <Card
        lift={false}
        glow={current ? "accent" : "none"}
        className={cn("p-0", current && "border-accent/30")}
      >
        <button
          type="button"
          onClick={onToggle}
          aria-expanded={expanded}
          className="flex w-full items-center gap-4 p-5 text-left"
        >
          <span
            aria-hidden
            className={cn(
              "grid size-8 shrink-0 place-items-center rounded-full text-sm transition-colors",
              done
                ? "bg-incoming/15 text-incoming ring-1 ring-incoming/30"
                : current
                  ? "bg-accent/15 text-accent ring-1 ring-accent/30"
                  : "bg-surface-raised text-faint",
            )}
          >
            {done ? <Check className="size-4" /> : index + 1}
          </span>

          <span className="min-w-0 flex-1">
            <span className="flex flex-wrap items-center gap-2">
              <span className="font-medium">{copy.title}</span>
              {done ? (
                <span className="rounded-full border border-line px-2 py-0.5 text-[0.625rem] text-faint uppercase tracking-wider">
                  {proof === "verified" ? "Verificado" : "Hecho"}
                </span>
              ) : null}
            </span>
            <span className="mt-0.5 block text-muted text-sm">{copy.blurb}</span>
          </span>

          <ChevronDown
            className={cn(
              "size-4 shrink-0 text-faint transition-transform duration-200",
              expanded && "rotate-180",
            )}
          />
        </button>

        {expanded ? (
          <div className="rise border-line border-t p-5">{children}</div>
        ) : null}
      </Card>
    </li>
  );
}

function StageBody({
  id,
  address,
  status,
  complete,
  onDone,
}: {
  id: StageId;
  address: string;
  status: AddressStatus;
  complete: boolean;
  onDone: (key: "introSeen" | "addressCopied" | "gmailSubmitted") => void;
}) {
  switch (id) {
    case "intro":
      return <IntroBody complete={complete} onDone={() => onDone("introSeen")} />;
    case "address":
      return (
        <AddressBody
          address={address}
          complete={complete}
          onDone={() => onDone("addressCopied")}
        />
      );
    case "senders":
      return <SendersBody />;
    case "forwarding":
      return (
        <ForwardingBody
          address={address}
          status={status}
          complete={complete}
          onDone={() => onDone("gmailSubmitted")}
        />
      );
    case "first-alert":
      return <FirstAlertBody complete={complete} />;
  }
}

function IntroBody({ complete, onDone }: { complete: boolean; onDone: () => void }) {
  return (
    <div className="flex flex-col gap-5">
      <p className="text-sm leading-relaxed">
        Tu banco ya te avisa por correo cada vez que compras, pagas o recibes plata.
        Finflow no entra a tu banco ni te pide claves: <strong>lee esos correos</strong>{" "}
        y arma con ellos tus movimientos, tus cuentas y tu saldo.
      </p>

      <ul className="flex flex-col gap-4">
        <IntroPoint icon={Mail} title="Te damos una dirección solo tuya">
          Es una dirección de correo que este despliegue posee. Tú configuras tu correo
          para que le reenvíe las alertas de tu banco, y nada más.
        </IntroPoint>
        <IntroPoint icon={ShieldCheck} title="Nadie entra a tu correo personal">
          No hay permisos de Google, no hay acceso a tu bandeja. El único buzón que
          Finflow lee es el suyo, y ahí solo llega lo que tú le reenvíes.
        </IntroPoint>
        <IntroPoint icon={Ban} title="Solo se acepta a quien tú apruebes">
          Cualquier correo que llegue de un remitente que no aprobaste se descarta sin
          leerse. Mientras no apruebes a nadie, tu dirección no acepta nada.
        </IntroPoint>
        <IntroPoint icon={Wallet} title="El resto pasa solo">
          De cada alerta salen el monto, el comercio y la cuenta. Tú no escribes ni un
          movimiento a mano — aunque puedes corregir cualquiera.
        </IntroPoint>
      </ul>

      {!complete ? (
        <div>
          <Button onClick={onDone}>
            Entendido, seguir
            <ArrowRight className="size-4" />
          </Button>
        </div>
      ) : null}
    </div>
  );
}

function IntroPoint({
  icon: Icon,
  title,
  children,
}: {
  icon: typeof Mail;
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

function AddressBody({
  address,
  complete,
  onDone,
}: {
  address: string;
  complete: boolean;
  onDone: () => void;
}) {
  return (
    <div className="flex flex-col gap-5">
      <p className="text-sm leading-relaxed">
        Esta es tu dirección. Sale de tu cuenta, no la elige nadie y no cambia. Lo que
        llegue ahí se guarda como una alerta tuya y se procesa; lo que llegue de un
        remitente sin aprobar se descarta.
      </p>

      <div className="flex flex-wrap items-center gap-3">
        <code className="min-w-0 flex-1 break-all rounded-xl border border-line bg-ink px-4 py-3 text-sm">
          {address}
        </code>
        {!complete ? <CopyButton value={address} onCopied={onDone} /> : null}
      </div>

      {!complete ? (
        <div>
          <Button variant="ghost" onClick={onDone}>
            Ya la tengo
            <ArrowRight className="size-4" />
          </Button>
        </div>
      ) : null}
    </div>
  );
}

/**
 * The banks with a deterministic parser today, and the domains each one
 * actually sends from.
 *
 * One button per bank rather than one list: approving a sender is approving
 * what may be read, and somebody who only banks with one of these should not
 * have to accept the other to get started. Any bank missing here still works
 * through the field below — it is typed instead of clicked, and read by the
 * LLM instead of a template.
 *
 * Lulo sends from `lulobank.com`. Its message ids come from Amazon SES, which
 * is shared with every other SES customer and is why that domain is not here.
 */
const KNOWN_BANKS = [
  {
    label: "Bancolombia",
    // The three domains the parser registry knows: the two alert domains, plus
    // `bancolombia.com.co`, which is the one a transfer between the owner's own
    // accounts arrives from. Approving only the first two accepts the card and
    // purchase alerts while silently dropping every transfer.
    domains: [
      "an.notificacionesbancolombia.com",
      "notificacionesbancolombia.com",
      "bancolombia.com.co",
    ],
  },
  { label: "Lulo bank", domains: ["lulobank.com"] },
];

/**
 * The approved-sender list, and the only screen that edits it.
 *
 * Available whether or not the setup is finished: banks change the domain
 * they send from, somebody approves the wrong one by pasting it, and a list
 * that could only ever grow would leave the wrong entry accepted forever.
 * Removing is not destructive — it is re-approved by typing it again — so it
 * costs one click, with the consequence spelled out where it matters: taking
 * the last one out means the address accepts nothing at all.
 */
function SendersBody() {
  const { data: inbox } = useSuspenseQuery(inboxQuery);
  const update = useUpdateInbox();
  const [custom, setCustom] = useState("");

  const domains = inbox.allowed_domains;
  const addresses = inbox.allowed_addresses;

  function save(next: { domains?: string[]; addresses?: string[] }) {
    // The endpoint replaces the whole list rather than merging, so both sides
    // travel on every call — sending one alone would silently clear the other.
    update.mutate({
      allowed_domains: next.domains ?? domains,
      allowed_addresses: next.addresses ?? addresses,
    });
  }

  function addCustom(event: SubmitEvent<HTMLFormElement>) {
    event.preventDefault();
    const value = custom.trim().toLowerCase();
    if (!value) return;

    // An "@" means somebody pasted a whole address; anything else is a domain.
    if (value.includes("@")) {
      if (!addresses.includes(value)) save({ addresses: [...addresses, value] });
    } else if (!domains.includes(value)) {
      save({ domains: [...domains, value] });
    }
    setCustom("");
  }

  function remove(sender: string, kind: "domain" | "address") {
    if (kind === "domain") {
      save({ domains: domains.filter((value) => value !== sender) });
    } else {
      save({ addresses: addresses.filter((value) => value !== sender) });
    }
  }

  const approved: { value: string; kind: "domain" | "address" }[] = [
    ...domains.map((value) => ({ value, kind: "domain" as const })),
    ...addresses.map((value) => ({ value, kind: "address" as const })),
  ];
  const last = approved.length === 1;

  return (
    <div className="flex flex-col gap-5">
      <p className="text-sm leading-relaxed">
        Aprueba las direcciones o dominios desde los que te escribe tu banco. Es lo
        único que decide qué se lee: <strong>una lista vacía no acepta nada</strong>,
        que es el valor seguro por defecto.
      </p>

      <div className="flex flex-wrap items-center gap-3">
        {KNOWN_BANKS.map((bank) => {
          const isApproved = bank.domains.every((domain) => domains.includes(domain));

          return (
            <Button
              key={bank.label}
              variant={isApproved ? "ghost" : "primary"}
              disabled={isApproved || update.isPending}
              onClick={() =>
                save({ domains: [...new Set([...domains, ...bank.domains])] })
              }
            >
              {isApproved ? <Check className="size-4 text-incoming" /> : null}
              {isApproved ? `${bank.label} aprobado` : `Aprobar ${bank.label}`}
            </Button>
          );
        })}
        {update.isPending ? (
          <Loader2 className="size-4 animate-spin text-faint" />
        ) : null}
      </div>

      <form
        onSubmit={addCustom}
        className="flex flex-col gap-3 sm:flex-row sm:items-end"
      >
        <Field
          label="Otro banco"
          className="w-full"
          placeholder="dominio.com o alertas@banco.com"
          value={custom}
          onChange={(e) => setCustom(e.target.value)}
        />
        {/*
          Disabled while a write is in flight like every other control here:
          `save` rebuilds the whole list from the cached one, and the endpoint
          replaces rather than merges — a second submit before the first
          response lands would drop the sender the first one added.
        */}
        <Button
          type="submit"
          variant="ghost"
          disabled={!custom.trim() || update.isPending}
        >
          Aprobar
        </Button>
      </form>

      {update.error ? (
        <p role="alert" className="text-outgoing text-sm">
          {update.error.message}
        </p>
      ) : null}

      <div>
        <p className="mb-2 text-muted text-xs uppercase tracking-wider">Aprobados</p>
        {approved.length === 0 ? (
          <p className="text-faint text-sm">
            Nadie todavía. Tu dirección no acepta nada.
          </p>
        ) : (
          <>
            <ul className="flex flex-wrap gap-2">
              {approved.map(({ value, kind }) => (
                <li
                  key={value}
                  className="flex items-center gap-1.5 rounded-lg border border-incoming/25 bg-incoming/10 py-1 pr-1 pl-2.5 text-incoming text-xs"
                >
                  <Check className="size-3 shrink-0" />
                  <span className="min-w-0 break-all">{value}</span>
                  <button
                    type="button"
                    onClick={() => remove(value, kind)}
                    disabled={update.isPending}
                    aria-label={`Quitar ${value} de los remitentes aprobados`}
                    title={`Quitar ${value}`}
                    className="-my-1 grid size-6 shrink-0 place-items-center rounded-md text-incoming/70 transition-colors hover:bg-outgoing/15 hover:text-outgoing disabled:cursor-not-allowed disabled:opacity-45"
                  >
                    <X className="size-3" />
                  </button>
                </li>
              ))}
            </ul>
            <p className="mt-2 text-faint text-xs leading-relaxed">
              {last
                ? "Si quitas el último, tu dirección deja de aceptar correo y no se registrará ningún movimiento nuevo."
                : "Quitar uno deja de aceptar sus correos de aquí en adelante; los movimientos que ya se registraron se quedan."}
            </p>
          </>
        )}
      </div>
    </div>
  );
}

function ForwardingBody({
  address,
  status,
  complete,
  onDone,
}: {
  address: string;
  status: AddressStatus;
  complete: boolean;
  onDone: () => void;
}) {
  const { data: setup } = useSuspenseQuery(setupQuery);
  const confirmedAt = setup.steps.find(
    (step) => step.key === "forwarding_confirmed",
  )?.at;

  return (
    <div className="flex flex-col gap-5">
      <ol className="flex flex-col gap-3 text-sm leading-relaxed">
        <Instruction n={1}>
          En Gmail: <strong>Configuración</strong> (⚙️) →{" "}
          <strong>Ver toda la configuración</strong> → pestaña{" "}
          <strong>Reenvío y correo POP/IMAP</strong>.
        </Instruction>
        <Instruction n={2}>
          <strong>Agregar una dirección de reenvío</strong> y pega la tuya:
          <code className="mt-2 block break-all rounded-lg border border-line bg-ink px-3 py-2 text-xs">
            {address}
          </code>
        </Instruction>
        <Instruction n={3}>
          Google manda un correo de confirmación a esa dirección.{" "}
          <strong>No tienes que hacer nada</strong>: Finflow lo recibe y lo confirma
          solo, en su siguiente pasada (aproximadamente un minuto).
        </Instruction>
        <Instruction n={4}>
          Crea un filtro: <strong>De</strong> = la dirección de tu banco →{" "}
          <strong>Reenviar a</strong> la dirección de arriba. Así solo se reenvía lo del
          banco, no tu correo personal.
        </Instruction>
      </ol>

      {status === "unverified" && !complete ? (
        <div className="flex flex-col gap-4 rounded-xl border border-line bg-ink p-4">
          <div className="flex items-center gap-2.5 text-muted text-sm">
            <Loader2 className="size-4 animate-spin text-cyan" />
            {STAGE_COPY.forwarding.waiting}
          </div>
          <p className="text-faint text-xs">
            Esta pantalla se entera sola cuando llegue. Si pasa un rato largo, revisa
            que la dirección quedó bien pegada en Gmail.
          </p>
          <div>
            <Button variant="ghost" onClick={onDone}>
              Ya lo configuré
            </Button>
          </div>
        </div>
      ) : (
        <p className="flex items-center gap-2 text-incoming text-sm">
          <BadgeCheck className="size-4" />
          {confirmedAt
            ? `Google confirmó el reenvío el ${formatDateTime(confirmedAt)}.`
            : "El correo ya está llegando, así que el camino funciona."}
        </p>
      )}
    </div>
  );
}

function Instruction({ n, children }: { n: number; children: ReactNode }) {
  return (
    <li className="flex items-start gap-3">
      <span
        aria-hidden
        className="mt-0.5 grid size-6 shrink-0 place-items-center rounded-lg border border-line bg-ink text-faint text-xs"
      >
        {n}
      </span>
      <span className="min-w-0">{children}</span>
    </li>
  );
}

function FirstAlertBody({ complete }: { complete: boolean }) {
  return (
    <div className="flex flex-col gap-4">
      <p className="text-sm leading-relaxed">
        Con el reenvío activo, la próxima alerta de tu banco llega sola. Puedes esperar
        a tu siguiente compra o hacer una pequeña para probar.
      </p>

      {complete ? (
        <p className="flex items-center gap-2 text-incoming text-sm">
          <Sparkles className="size-4" />
          Ya llegó la primera. Desde ahí, cada movimiento se registra solo.
        </p>
      ) : (
        <div className="flex items-center gap-2.5 rounded-xl border border-line bg-ink p-4 text-muted text-sm">
          <Loader2 className="size-4 animate-spin text-cyan" />
          {STAGE_COPY["first-alert"].waiting}
        </div>
      )}
    </div>
  );
}
