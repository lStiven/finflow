import { ArrowRight, Check, Hourglass, Landmark, ShieldCheck } from "lucide-react";
import { Button } from "@/components/ui/Button";
import { approvedBanks, filterTerms, hasSenders } from "@/onboarding/banks";
import { CopyField } from "@/onboarding/CopyField";
import { DesktopHint } from "@/onboarding/DesktopHint";
import {
  FilterActionsMock,
  ResultsMock,
  SearchBarMock,
  SearchOptionsMock,
} from "@/onboarding/GmailMocks";
import { GmailTutorial } from "@/onboarding/GmailTutorial";
import { GMAIL_INBOX_URL } from "@/onboarding/gmail";
import { ExternalButton, Notice, StepHeading } from "@/onboarding/parts";
import { useSenders } from "@/onboarding/useSenders";

/**
 * Step three: the filter that forwards the bank's alerts and nothing else.
 *
 * The text that goes into Gmail's «De» is built here from the approved
 * senders — the very list the intake accepts — so nobody has to learn
 * Gmail's search syntax. Nothing outside Gmail can see a filter, so this step
 * closes on the person's word, labelled as such, until the first alert
 * proves it.
 */
export function FilterStep({
  address,
  addressConfirmed,
  done,
  onDone,
  onOpenBanks,
  onOpenAddress,
}: {
  address: string;
  /** Gmail lists the address under «Reenviarlo a» only once it is verified. */
  addressConfirmed: boolean;
  /** Whether this step is already settled (their word, or an alert). */
  done: boolean;
  onDone: (terms: string[]) => void;
  onOpenBanks: () => void;
  onOpenAddress: () => void;
}) {
  const { senders } = useSenders();
  const terms = filterTerms(senders);
  const filter = terms.join(" OR ");
  const banks = approvedBanks(senders).map((bank) => bank.name);

  if (!hasSenders(senders)) {
    return (
      <div>
        <StepHeading title="Reenvía solo tus alertas" />
        <Notice tone="warn" icon={Landmark} title="Primero elige tus bancos">
          El filtro se arma con los bancos que elijas: sin ninguno, no hay nada que
          reenviar.
          <div className="mt-3">
            <Button variant="ghost" onClick={onOpenBanks} className="px-3 py-2">
              Elegir mis bancos
            </Button>
          </div>
        </Notice>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-5">
      <StepHeading
        title="Reenvía solo tus alertas"
        lead="Un filtro de Gmail le enviará a Finflow solo los correos de tus bancos. El resto de tu correo se queda donde está."
      />

      {!addressConfirmed ? (
        <Notice
          tone="warn"
          icon={Hourglass}
          title="Gmail todavía no confirma tu dirección"
        >
          Hasta entonces no aparecerá en el filtro. Puedes ver los pasos mientras tanto.
          <div className="mt-3">
            <Button variant="ghost" onClick={onOpenAddress} className="px-3 py-2">
              Ir al paso anterior
            </Button>
          </div>
        </Notice>
      ) : null}

      <DesktopHint />

      <div className="rounded-2xl border border-line bg-surface p-4 sm:p-5">
        <CopyField
          label="Tu filtro"
          value={filter}
          copyLabel="Copiar filtro"
          copiedLabel="¡Copiado!"
          announce="Filtro copiado"
          emphasis="primary"
        />
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <span className="text-faint text-xs">Incluye:</span>
          {banks.map((name) => (
            <span
              key={name}
              className="inline-flex items-center gap-1 rounded-full border border-incoming/30 bg-incoming/8 px-2.5 py-0.5 text-incoming text-xs"
            >
              <Check className="size-3" aria-hidden />
              <span className="max-w-56 truncate">{name}</span>
            </span>
          ))}
        </div>
        <details className="group mt-3">
          <summary className="inline-flex min-h-9 cursor-pointer list-none items-center rounded-lg text-faint text-xs underline-offset-4 hover:text-muted hover:underline [&::-webkit-details-marker]:hidden">
            ¿Qué significa este texto?
          </summary>
          <p className="rise mt-1 text-muted text-xs leading-relaxed">
            Le dice a Gmail de quién son los correos que debe reenviar. Un remitente que
            empieza por @ cubre cualquier dirección de ese banco, y OR une varios bancos
            en un solo filtro. No hace falta editarlo: se actualiza aquí cuando cambias
            tus bancos.
          </p>
        </details>
      </div>

      <div className="flex flex-wrap items-center gap-3">
        <ExternalButton href={GMAIL_INBOX_URL}>Abrir Gmail</ExternalButton>
        <p className="flex items-center gap-1.5 text-faint text-xs">
          <ShieldCheck className="size-3.5 text-incoming" aria-hidden />
          Solo se reenvían los correos de tus bancos.
        </p>
      </div>

      <GmailTutorial
        label="Cómo crear el filtro en Gmail"
        slides={[
          {
            title: "Abre las opciones de búsqueda",
            caption: "Es el ícono a la derecha de la barra de búsqueda de Gmail.",
            illustration: <SearchBarMock />,
          },
          {
            title: "Pega el filtro en «De»",
            caption: "Deja los demás campos vacíos.",
            illustration: <SearchOptionsMock from={filter} highlight="from" />,
          },
          {
            title: "Pulsa «Buscar» y revisa",
            caption:
              "Deberían aparecer alertas de tu banco que ya tengas. Así sabes que el filtro las encuentra.",
            illustration: <ResultsMock banks={banks} />,
            help: {
              question: "¿No aparece ninguna?",
              answer: (
                <>
                  Abre una alerta de tu banco, copia el correo del remitente (el que va
                  entre «&lt;» y «&gt;») y agrégalo en{" "}
                  <button
                    type="button"
                    onClick={onOpenBanks}
                    className="font-medium text-cyan underline-offset-4 hover:underline"
                  >
                    Bancos
                  </button>
                  . El filtro de aquí se actualiza solo.
                </>
              ),
            },
          },
          {
            title: "Abre otra vez las opciones y pulsa «Crear filtro»",
            caption: "Lo que pegaste sigue ahí.",
            illustration: <SearchOptionsMock from={filter} highlight="create" />,
          },
          {
            title: "Marca «Reenviarlo a» y elige tu dirección de Finflow",
            caption: (
              <>
                Es esta: <code className="break-all text-text">{address}</code>
              </>
            ),
            illustration: <FilterActionsMock address={address} highlight="forward" />,
            help: {
              question: "¿Tu dirección no aparece en la lista?",
              answer: (
                <>
                  Recarga Gmail. Si sigue sin aparecer, Gmail aún no la ha verificado:{" "}
                  <button
                    type="button"
                    onClick={onOpenAddress}
                    className="font-medium text-cyan underline-offset-4 hover:underline"
                  >
                    vuelve al paso de tu dirección
                  </button>
                  .
                </>
              ),
            },
          },
          {
            title: "Pulsa «Crear filtro»",
            caption:
              "Listo. Desde ahora, cada alerta nueva de tus bancos llegará a Finflow. Las que ya tienes no se reenvían.",
            illustration: <FilterActionsMock address={address} highlight="create" />,
          },
        ]}
      />

      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <p className="text-faint text-xs">
          {addressConfirmed
            ? "Finflow no puede ver tus filtros: lo comprobará cuando llegue tu primera alerta."
            : "Disponible cuando Gmail confirme tu dirección."}
        </p>
        <Button
          onClick={() => onDone(terms)}
          disabled={!addressConfirmed}
          className="sm:min-w-44"
        >
          {done ? "Listo, seguir" : "Ya creé el filtro"}
          <ArrowRight className="size-4" aria-hidden />
        </Button>
      </div>
    </div>
  );
}
