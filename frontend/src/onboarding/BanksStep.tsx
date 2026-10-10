import {
  ArrowRight,
  Check,
  ChevronDown,
  Filter,
  Loader2,
  MailWarning,
  Plus,
  SlidersHorizontal,
  TriangleAlert,
  X,
} from "lucide-react";
import { type SubmitEvent, useCallback, useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/Button";
import { Field } from "@/components/ui/Field";
import { cn } from "@/lib/cn";
import { useDismissOnEscape } from "@/lib/useDismissOnEscape";
import {
  type BankState,
  bankOfSender,
  bankState,
  type CustomSender,
  customSenders,
  hasSenders,
  isApproved,
  KNOWN_BANKS,
  type KnownBank,
  parseSender,
  type Senders,
  senderErrorMessage,
  withBank,
  withoutBank,
  withoutSender,
  withSender,
} from "@/onboarding/banks";
import { StepHeading } from "@/onboarding/parts";
import { type SendersControl, useSenders } from "@/onboarding/useSenders";

const BANK_TONES: Record<string, string> = {
  bancolombia: "bg-cyan/12 text-cyan ring-cyan/30",
  lulo: "bg-violet/12 text-violet ring-violet/30",
};
const CUSTOM_TONE = "bg-accent/12 text-accent ring-accent/30";

/**
 * Step one: which banks Finflow may read.
 *
 * Cards rather than a list of domains, because the person thinks in banks
 * and the domains are the app's business. They are one tap away, under
 * «Detalles avanzados», for whoever needs to see or prune them. Approving is
 * one tap and is drawn only once the server has it; taking a bank away asks
 * first, because it silently stops its alerts from that moment on.
 */
export function BanksStep({
  unapprovedSenders,
  filterDone,
  continueLabel,
  onContinue,
  onOpenFilter,
}: {
  /** From the setup endpoint: senders whose mail was thrown away. */
  unapprovedSenders: string[];
  /** Whether a Gmail filter exists already, so a new bank needs adding to it. */
  filterDone: boolean;
  continueLabel: string;
  onContinue: () => void;
  onOpenFilter: () => void;
}) {
  const control = useSenders();
  const { senders, save, busy, savingKey, error } = control;
  const [confirming, setConfirming] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const [addedSince, setAddedSince] = useState<string | null>(null);
  const [announcement, setAnnouncement] = useState("");

  const custom = customSenders(senders);
  const any = hasSenders(senders);
  // Approved a moment ago and still listed until the setup is asked again.
  const discarded = unapprovedSenders.filter((sender) => !isApproved(sender, senders));

  function added(name: string) {
    setAnnouncement(`Listo: Finflow aceptará las alertas de ${name}.`);
    if (filterDone) setAddedSince(name);
  }

  function toggleBank(bank: KnownBank, state: BankState) {
    if (state === "on") {
      setConfirming(bank.id);
      return;
    }
    save(withBank(senders, bank), bank.id, () => added(bank.name));
  }

  function remove(next: Senders, key: string, name: string) {
    save(next, key, () => {
      setConfirming(null);
      setAnnouncement(`Quitado: ya no se aceptan los correos de ${name}.`);
    });
  }

  function addCustom(sender: CustomSender) {
    save(withSender(senders, sender), `custom:${sender.value}`, () => {
      setAdding(false);
      added(sender.value);
    });
  }

  return (
    <div>
      <StepHeading
        title="Elige tus bancos"
        lead="Finflow solo aceptará los correos de los bancos que elijas. Todo lo demás se descarta sin leerse."
      />

      {discarded.length > 0 ? (
        <DiscardedSenders senders={discarded} control={control} className="mb-5" />
      ) : null}

      <ul className="grid grid-cols-1 gap-3 min-[440px]:grid-cols-2" aria-busy={busy}>
        {KNOWN_BANKS.map((bank) => {
          const state = bankState(bank, senders);
          return (
            <li key={bank.id}>
              <ChoiceCard
                name={bank.name}
                monogram={bank.name.charAt(0)}
                tone={BANK_TONES[bank.id] ?? CUSTOM_TONE}
                state={state}
                detail={
                  state === "on"
                    ? "Aceptando sus alertas"
                    : state === "partial"
                      ? "Incompleto · toca para completar"
                      : "Toca para elegir"
                }
                saving={savingKey === bank.id}
                disabled={busy}
                confirming={confirming === bank.id}
                leavesNothing={!hasSenders(withoutBank(senders, bank))}
                onActivate={() => toggleBank(bank, state)}
                onConfirm={() => remove(withoutBank(senders, bank), bank.id, bank.name)}
                onCancel={() => setConfirming(null)}
              />
            </li>
          );
        })}

        {custom.map((sender) => {
          const key = `custom:${sender.value}`;
          return (
            <li key={key}>
              <ChoiceCard
                name={sender.type === "domain" ? `@${sender.value}` : sender.value}
                monogram={(
                  bankOfSender(`x@${domainOf(sender)}`)?.name ?? sender.value
                ).charAt(0)}
                tone={CUSTOM_TONE}
                state="on"
                detail="Agregado por ti"
                saving={savingKey === key}
                disabled={busy}
                confirming={confirming === key}
                leavesNothing={!hasSenders(withoutSender(senders, sender))}
                onActivate={() => setConfirming(key)}
                onConfirm={() =>
                  remove(withoutSender(senders, sender), key, sender.value)
                }
                onCancel={() => setConfirming(null)}
              />
            </li>
          );
        })}

        <li>
          <button
            type="button"
            onClick={() => setAdding((open) => !open)}
            aria-expanded={adding}
            className="flex min-h-22 w-full items-center gap-3 rounded-2xl border border-line border-dashed p-4 text-left text-muted transition-colors duration-150 hover:border-accent/45 hover:text-text"
          >
            <span
              aria-hidden
              className="grid size-11 shrink-0 place-items-center rounded-xl border border-line border-dashed"
            >
              <Plus className="size-4" />
            </span>
            <span className="min-w-0">
              <span className="block font-medium text-sm text-text">Otro banco</span>
              <span className="mt-0.5 block text-xs">
                Agrega el correo desde el que te escribe
              </span>
            </span>
          </button>
        </li>
      </ul>

      {adding ? (
        <OtherBankForm
          senders={senders}
          busy={busy}
          saving={savingKey?.startsWith("custom:") ?? false}
          onAdd={addCustom}
          onCancel={() => setAdding(false)}
        />
      ) : null}

      {error ? (
        <p role="alert" className="mt-4 text-outgoing text-sm">
          {error.message}
        </p>
      ) : null}

      {addedSince ? (
        <div className="rise mt-4 flex items-start gap-3 rounded-2xl border border-warn/30 bg-warn/7 p-4">
          <Filter className="mt-0.5 size-4 shrink-0 text-warn" aria-hidden />
          <div className="min-w-0 text-sm">
            <p className="font-medium text-warn">
              Agrégalo también a tu filtro de Gmail
            </p>
            <p className="mt-1 text-muted leading-relaxed">
              Tu filtro se creó antes de agregar {addedSince}: hasta que lo actualices,
              sus alertas no llegarán.
            </p>
            <button
              type="button"
              onClick={onOpenFilter}
              className="mt-2 font-medium text-cyan underline-offset-4 hover:underline"
            >
              Ver el filtro nuevo
            </button>
          </div>
        </div>
      ) : null}

      <ApprovedDetails control={control} />

      <div className="mt-8 flex flex-col-reverse gap-3 sm:flex-row sm:items-center sm:justify-between">
        <p className="text-faint text-xs">
          {any
            ? "Puedes cambiar tus bancos cuando quieras."
            : "Elige al menos un banco para seguir."}
        </p>
        <Button onClick={onContinue} disabled={!any || busy} className="sm:min-w-40">
          {continueLabel}
          <ArrowRight className="size-4" aria-hidden />
        </Button>
      </div>

      <p role="status" className="sr-only">
        {announcement}
      </p>
    </div>
  );
}

function domainOf(sender: CustomSender): string {
  return sender.type === "domain"
    ? sender.value
    : sender.value.slice(sender.value.lastIndexOf("@") + 1);
}

/**
 * One bank, as a single big target.
 *
 * Asked to let go of an approved bank, the card turns into the question in
 * place — the same spot, the same size — instead of a dialog over the page.
 */
function ChoiceCard({
  name,
  monogram,
  tone,
  state,
  detail,
  saving,
  disabled,
  confirming,
  leavesNothing,
  onActivate,
  onConfirm,
  onCancel,
}: {
  name: string;
  monogram: string;
  tone: string;
  state: BankState;
  detail: string;
  saving: boolean;
  disabled: boolean;
  confirming: boolean;
  /** Removing this one leaves the address accepting nothing at all. */
  leavesNothing: boolean;
  onActivate: () => void;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  const box = useRef<HTMLDivElement>(null);
  const cancel = useCallback(() => onCancel(), [onCancel]);
  useDismissOnEscape(cancel, confirming);

  useEffect(() => {
    // The safe answer gets the focus: Enter on an open question keeps the bank.
    if (confirming)
      box.current?.querySelector<HTMLButtonElement>("[data-cancel]")?.focus();
  }, [confirming]);

  if (confirming) {
    return (
      <div
        ref={box}
        className="rise flex min-h-22 flex-col justify-center gap-3 rounded-2xl border border-outgoing/40 bg-outgoing/6 p-4"
      >
        <div>
          <p className="text-sm">
            ¿Dejar de aceptar los correos de{" "}
            <strong className="wrap-anywhere">{name}</strong>?
          </p>
          <p className={cn("mt-1 text-xs", leavesNothing ? "text-warn" : "text-faint")}>
            {leavesNothing
              ? "Es el único: Finflow dejará de registrar movimientos nuevos."
              : "Lo que ya se registró se queda."}
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button variant="ghost" data-cancel onClick={onCancel} className="py-2">
            Cancelar
          </Button>
          <Button
            variant="ghost"
            onClick={onConfirm}
            disabled={disabled}
            className="border-outgoing/40 py-2 text-outgoing hover:bg-outgoing/10"
          >
            {saving ? <Loader2 className="size-4 animate-spin" aria-hidden /> : null}
            Quitar
          </Button>
        </div>
      </div>
    );
  }

  const on = state === "on";

  return (
    <button
      type="button"
      onClick={onActivate}
      disabled={disabled}
      aria-pressed={on}
      className={cn(
        "group relative flex min-h-22 w-full items-center gap-3 rounded-2xl border p-4 text-left transition-all duration-200",
        "disabled:cursor-wait",
        on
          ? "border-incoming/45 bg-incoming/7"
          : state === "partial"
            ? "border-warn/40 bg-warn/6 hover:border-warn/60"
            : "border-line bg-surface hover:-translate-y-0.5 hover:border-accent/45 hover:bg-surface-raised",
      )}
    >
      <span
        aria-hidden
        className={cn(
          "grid size-11 shrink-0 place-items-center rounded-xl font-semibold uppercase ring-1",
          tone,
        )}
      >
        {monogram}
      </span>
      <span className="min-w-0 flex-1">
        <span className="block font-medium text-sm wrap-anywhere">{name}</span>
        <span
          className={cn(
            "mt-0.5 block text-xs",
            on ? "text-incoming" : state === "partial" ? "text-warn" : "text-muted",
          )}
        >
          {saving ? "Guardando…" : detail}
        </span>
      </span>
      <span aria-hidden className="shrink-0">
        {saving ? (
          <Loader2 className="size-5 animate-spin text-faint" />
        ) : on ? (
          <span
            key="on"
            className="pop grid size-6 place-items-center rounded-full bg-incoming text-ink"
          >
            <Check className="size-3.5" strokeWidth={3} />
          </span>
        ) : state === "partial" ? (
          <TriangleAlert className="size-5 text-warn" />
        ) : (
          <span className="block size-6 rounded-full border-2 border-line transition-colors group-hover:border-accent/60" />
        )}
      </span>
    </button>
  );
}

function OtherBankForm({
  senders,
  busy,
  saving,
  onAdd,
  onCancel,
}: {
  senders: Senders;
  busy: boolean;
  saving: boolean;
  onAdd: (sender: CustomSender) => void;
  onCancel: () => void;
}) {
  const [value, setValue] = useState("");
  const [problem, setProblem] = useState<string | null>(null);
  const box = useRef<HTMLFormElement>(null);

  useEffect(() => {
    box.current?.querySelector("input")?.focus();
  }, []);

  function submit(event: SubmitEvent<HTMLFormElement>) {
    event.preventDefault();
    const parsed = parseSender(value, senders);
    if (!parsed.ok) {
      setProblem(senderErrorMessage(parsed.error));
      return;
    }
    setProblem(null);
    onAdd(parsed.sender);
  }

  return (
    <form
      ref={box}
      onSubmit={submit}
      noValidate
      className="rise mt-3 rounded-2xl border border-line bg-surface p-4 sm:p-5"
    >
      <Field
        label="¿Desde qué correo te escribe tu banco?"
        placeholder="alertas@tubanco.com"
        hint="Míralo en cualquier alerta de tu banco: es la dirección junto al nombre del remitente."
        value={value}
        onChange={(event) => {
          setValue(event.target.value);
          if (problem) setProblem(null);
        }}
        autoComplete="off"
        autoCapitalize="none"
        spellCheck={false}
        inputMode="email"
        aria-invalid={problem ? true : undefined}
      />
      {problem ? (
        <p role="alert" className="mt-2 text-outgoing text-sm">
          {problem}
        </p>
      ) : null}
      <div className="mt-4 flex flex-wrap gap-2">
        <Button type="submit" disabled={busy}>
          {saving ? (
            <Loader2 className="size-4 animate-spin" aria-hidden />
          ) : (
            <Plus className="size-4" aria-hidden />
          )}
          Agregar
        </Button>
        <Button variant="quiet" onClick={onCancel}>
          Cancelar
        </Button>
      </div>
    </form>
  );
}

/**
 * The raw list, for whoever wants it: every domain and address that is
 * approved, each removable on its own. Closed by default — choosing a bank
 * does not need any of it.
 */
function ApprovedDetails({ control }: { control: SendersControl }) {
  const { senders, save, busy, savingKey } = control;
  const [confirming, setConfirming] = useState<string | null>(null);
  const entries: CustomSender[] = [
    ...senders.domains.map((value) => ({ type: "domain" as const, value })),
    ...senders.addresses.map((value) => ({ type: "address" as const, value })),
  ];

  return (
    <details className="group mt-6 rounded-2xl border border-line bg-surface/60">
      <summary className="flex min-h-12 cursor-pointer list-none items-center gap-3 rounded-2xl px-4 py-3 text-muted text-sm transition-colors hover:text-text [&::-webkit-details-marker]:hidden">
        <SlidersHorizontal className="size-4 shrink-0" aria-hidden />
        Detalles avanzados
        <span className="ml-auto text-faint text-xs tabular">
          {entries.length} {entries.length === 1 ? "remitente" : "remitentes"}
        </span>
        <ChevronDown
          className="size-4 shrink-0 transition-transform duration-200 group-open:rotate-180"
          aria-hidden
        />
      </summary>
      <div className="border-line border-t p-4">
        <p className="text-faint text-xs leading-relaxed">
          Finflow acepta los correos que lleguen exactamente desde estos remitentes. Uno
          que empieza por @ es un dominio: acepta cualquier dirección que termine así.
        </p>
        {entries.length === 0 ? (
          <p className="mt-3 text-muted text-sm">
            Ninguno todavía: tu dirección no acepta nada.
          </p>
        ) : (
          <ul className="mt-3 flex flex-col gap-2">
            {entries.map((entry) => {
              const key = `${entry.type}:${entry.value}`;
              const shown = entry.type === "domain" ? `@${entry.value}` : entry.value;
              const bank = bankOfSender(
                entry.type === "domain" ? `x@${entry.value}` : entry.value,
              );
              const asking = confirming === key;
              const leavesNothing = !hasSenders(withoutSender(senders, entry));

              return (
                <li
                  key={key}
                  className="flex flex-wrap items-center gap-2 rounded-xl border border-line bg-ink px-3 py-2"
                >
                  <code className="min-w-0 flex-1 break-all text-xs">{shown}</code>
                  {bank ? (
                    <span className="text-faint text-xs">{bank.name}</span>
                  ) : null}
                  {asking ? (
                    <span className="flex items-center gap-1.5">
                      <span
                        className={cn(
                          "text-xs",
                          leavesNothing ? "text-warn" : "text-muted",
                        )}
                      >
                        {leavesNothing ? "Es el último. ¿Quitar?" : "¿Quitar?"}
                      </span>
                      <button
                        type="button"
                        onClick={() =>
                          save(withoutSender(senders, entry), `raw:${key}`, () =>
                            setConfirming(null),
                          )
                        }
                        disabled={busy}
                        className="min-h-8 rounded-lg px-2 text-outgoing text-xs hover:bg-outgoing/10 disabled:opacity-45"
                      >
                        {savingKey === `raw:${key}` ? "Quitando…" : "Sí, quitar"}
                      </button>
                      <button
                        type="button"
                        onClick={() => setConfirming(null)}
                        className="min-h-8 rounded-lg px-2 text-muted text-xs hover:bg-surface-raised"
                      >
                        No
                      </button>
                    </span>
                  ) : (
                    <button
                      type="button"
                      onClick={() => setConfirming(key)}
                      disabled={busy}
                      aria-label={`Quitar ${shown}`}
                      title={`Quitar ${shown}`}
                      className="grid size-8 shrink-0 place-items-center rounded-lg text-faint transition-colors hover:bg-outgoing/15 hover:text-outgoing disabled:opacity-45"
                    >
                      <X className="size-3.5" aria-hidden />
                    </button>
                  )}
                </li>
              );
            })}
          </ul>
        )}
      </div>
    </details>
  );
}

/**
 * Mail that reached the address and was thrown away for its sender — the one
 * failure that looks exactly like nothing arriving. Approving from here takes
 * the exact address, never its whole domain: the narrowest thing that lets
 * the bank in.
 */
export function DiscardedSenders({
  senders,
  control,
  className,
}: {
  senders: string[];
  control: SendersControl;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "rise rounded-2xl border border-warn/30 bg-warn/7 p-4 sm:p-5",
        className,
      )}
    >
      <p className="flex items-center gap-2 font-medium text-sm text-warn">
        <MailWarning className="size-4 shrink-0" aria-hidden />
        Llegaron correos de un remitente que no elegiste
      </p>
      <p className="mt-1 text-muted text-sm leading-relaxed">
        Finflow no los leyó. Si son de tu banco, acéptalo; si no lo reconoces, déjalo
        así.
      </p>
      <ul className="mt-3 flex flex-col gap-2">
        {senders.map((sender) => {
          const key = `discarded:${sender}`;
          return (
            <li
              key={sender}
              className="flex flex-wrap items-center gap-2 rounded-xl border border-line bg-ink px-3 py-2"
            >
              <code className="min-w-0 flex-1 break-all text-xs">{sender}</code>
              <Button
                variant="ghost"
                onClick={() =>
                  control.save(
                    withSender(control.senders, { type: "address", value: sender }),
                    key,
                  )
                }
                disabled={control.busy}
                className="px-3 py-2 text-xs"
              >
                {control.savingKey === key ? (
                  <Loader2 className="size-3.5 animate-spin" aria-hidden />
                ) : (
                  <Check className="size-3.5" aria-hidden />
                )}
                Es de mi banco
              </Button>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
