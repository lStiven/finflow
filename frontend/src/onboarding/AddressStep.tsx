import { ArrowRight, Hourglass, MailCheck, ShieldAlert } from "lucide-react";
import { useState } from "react";
import { Button } from "@/components/ui/Button";
import { Notice } from "@/components/ui/Notice";
import { SuccessMark } from "@/components/ui/SuccessMark";
import { Tutorial } from "@/components/ui/Tutorial";
import { formatDateTime, formatRelative } from "@/lib/dates";
import { useNow } from "@/lib/useNow";
import { CopyField } from "@/onboarding/CopyField";
import { DesktopHint } from "@/onboarding/DesktopHint";
import {
  AddAddressMock,
  ForwardingChoiceMock,
  ForwardingTabMock,
} from "@/onboarding/GmailMocks";
import { GMAIL_FORWARDING_URL } from "@/onboarding/gmail";
import { ExternalButton, StepHeading } from "@/onboarding/parts";

/** Past this, a wait for Google is worth explaining rather than just showing. */
const SLOW_AFTER_SECONDS = 5 * 60;

/**
 * Step two: Gmail has to be told it may forward to this address.
 *
 * The only step here with a proof nobody has to report: Google mails a
 * confirmation link to the address, the ingest worker follows it, and the
 * setup endpoint says so — usually within a couple of minutes. So the step
 * never closes on "ya lo hice"; that button only starts the wait, and the
 * wait is the screen polling for the real answer.
 */
export function AddressStep({
  address,
  confirmedAt,
  receiving,
  requestedAt,
  onCopied,
  onRequested,
  onContinue,
}: {
  address: string;
  /** When Google's confirmation was recorded, if it was. */
  confirmedAt: number | null;
  /** Mail already arrives, confirmation or not (somebody forwarding by hand). */
  receiving: boolean;
  /** When they said the address was added in Gmail, if they did. */
  requestedAt: number | null;
  onCopied: () => void;
  onRequested: () => void;
  onContinue: () => void;
}) {
  // "Ver los pasos otra vez" from the wait, without forgetting the wait.
  const [rereading, setRereading] = useState(false);
  const confirmed = confirmedAt !== null || receiving;
  const waiting = !confirmed && requestedAt !== null && !rereading;

  return (
    <div>
      <StepHeading
        title="Autoriza tu dirección en Gmail"
        lead="Gmail solo reenvía a direcciones que tú autorizas. Se hace una sola vez."
      />

      {confirmed ? (
        <Confirmed confirmedAt={confirmedAt} onContinue={onContinue} />
      ) : waiting && requestedAt !== null ? (
        <Waiting
          address={address}
          requestedAt={requestedAt}
          onReread={() => setRereading(true)}
        />
      ) : (
        <Instructions
          address={address}
          onCopied={onCopied}
          onRequested={() => {
            setRereading(false);
            onRequested();
          }}
        />
      )}

      {/* Kept mounted across the three, so the change itself is announced. */}
      <p role="status" className="sr-only">
        {confirmed
          ? "Gmail confirmó tu dirección."
          : waiting
            ? "Esperando la confirmación de Gmail."
            : ""}
      </p>
    </div>
  );
}

function Instructions({
  address,
  onCopied,
  onRequested,
}: {
  address: string;
  onCopied: () => void;
  onRequested: () => void;
}) {
  return (
    <div className="flex flex-col gap-5">
      <DesktopHint />

      <CopyField
        label="Tu dirección de Finflow"
        value={address}
        copyLabel="Copiar dirección"
        copiedLabel="¡Copiada!"
        announce="Dirección copiada"
        emphasis="primary"
        onCopied={onCopied}
      />

      <div className="flex flex-wrap items-center gap-3">
        <ExternalButton href={GMAIL_FORWARDING_URL}>Abrir Gmail</ExternalButton>
        <span className="text-faint text-xs">
          Se abre directo en la pestaña de reenvío.
        </span>
      </div>

      <Tutorial
        label="Cómo autorizar tu dirección en Gmail"
        slides={[
          {
            title: "Ve a «Reenvío y correo POP/IMAP»",
            caption:
              "Es una pestaña de la configuración de Gmail. «Abrir Gmail» te lleva directo.",
            illustration: <ForwardingTabMock highlight="tab" />,
            help: {
              question: "¿Se abrió otra pantalla?",
              answer:
                "Arriba a la derecha pulsa Configuración (el engranaje), luego «Ver toda la configuración», y elige la pestaña «Reenvío y correo POP/IMAP».",
            },
          },
          {
            title: "Pulsa «Agregar una dirección de reenvío»",
            caption: "Está en la sección Reenvío, arriba de todo.",
            illustration: <ForwardingTabMock highlight="add" />,
          },
          {
            title: "Pega tu dirección y pulsa «Siguiente»",
            caption:
              "Después «Continuar» y «Aceptar». Gmail enviará un correo de verificación a tu dirección de Finflow, y Finflow lo confirma solo: no tienes que buscar nada.",
            illustration: <AddAddressMock address={address} />,
            help: {
              question: "¿Gmail te pide una contraseña?",
              answer:
                "Es la de tu cuenta de Google, para confirmar que eres tú. Finflow nunca la ve, y nunca te pedirá la de tu banco.",
            },
          },
        ]}
      />

      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <p className="text-faint text-xs">
          Cuando Gmail diga que envió la verificación:
        </p>
        <Button onClick={onRequested} className="sm:min-w-44">
          Ya la agregué
          <ArrowRight className="size-4" aria-hidden />
        </Button>
      </div>
    </div>
  );
}

function Waiting({
  address,
  requestedAt,
  onReread,
}: {
  address: string;
  requestedAt: number;
  onReread: () => void;
}) {
  const now = useNow(15_000);
  const slow = now - requestedAt > SLOW_AFTER_SECONDS;

  return (
    <div className="flex flex-col items-center text-center">
      <span className="relative mt-2 grid size-20 place-items-center">
        <span className="absolute inset-0 animate-ping rounded-full bg-cyan/15" />
        <span className="absolute inset-3 rounded-full bg-cyan/10 ring-1 ring-cyan/30" />
        <MailCheck className="relative size-7 text-cyan" aria-hidden />
      </span>
      <p className="mt-5 font-medium text-lg">Esperando confirmación de Gmail</p>
      <p className="mt-2 max-w-md text-pretty text-muted text-sm leading-relaxed">
        Finflow la confirma solo, sin que abras ningún correo. Suele tardar un par de
        minutos.
      </p>
      <p className="mt-3 text-faint text-xs">
        Esta pantalla se actualiza sola · empezaste {formatRelative(requestedAt, now)}
      </p>

      {slow ? (
        <div className="rise mt-6 w-full text-left">
          <Notice tone="warn" icon={Hourglass} title="¿Está tardando? Revisa esto">
            <ol className="mt-2 flex list-decimal flex-col gap-3 pl-5">
              <li>
                Que la dirección esté completa en Gmail, sin espacios.
                <CopyField
                  className="mt-2"
                  label="Tu dirección"
                  value={address}
                  copyLabel="Copiar otra vez"
                  copiedLabel="¡Copiada!"
                  announce="Dirección copiada"
                />
              </li>
              <li>
                En Gmail, junto a tu dirección, pulsa{" "}
                <strong className="text-text">
                  «Volver a enviar correo de verificación»
                </strong>
                .
              </li>
              <li>
                Que sea la cuenta de Gmail donde te llegan las alertas del banco, si
                usas más de una.
              </li>
            </ol>
          </Notice>
        </div>
      ) : null}

      <Button variant="quiet" onClick={onReread} className="mt-4">
        Ver los pasos otra vez
      </Button>
    </div>
  );
}

function Confirmed({
  confirmedAt,
  onContinue,
}: {
  confirmedAt: number | null;
  onContinue: () => void;
}) {
  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-col items-center text-center">
        <SuccessMark className="mt-2" />
        <p className="mt-5 font-medium text-lg">
          {confirmedAt
            ? "¡Gmail confirmó tu dirección!"
            : "Tu dirección ya recibe correos"}
        </p>
        <p className="mt-1.5 text-muted text-sm">
          {confirmedAt
            ? `Confirmada el ${formatDateTime(confirmedAt)}`
            : "Tus alertas ya están llegando a Finflow."}
        </p>
      </div>

      {confirmedAt ? (
        <div className="rounded-2xl border border-line bg-surface p-4 sm:p-5">
          <p className="flex items-center gap-2 font-medium text-sm">
            <ShieldAlert className="size-4 shrink-0 text-warn" aria-hidden />
            Un detalle importante en esa misma página de Gmail
          </p>
          <div className="mt-3 grid grid-cols-1 items-center gap-4 sm:grid-cols-[1fr_1.1fr]">
            <ForwardingChoiceMock />
            <p className="text-muted text-sm leading-relaxed">
              Deja marcado{" "}
              <strong className="text-text">«Inhabilitar el reenvío»</strong>. La otra
              opción enviaría <strong className="text-text">todo</strong> tu correo. En
              el siguiente paso eliges qué se reenvía: solo tus alertas.
            </p>
          </div>
        </div>
      ) : null}

      <Button onClick={onContinue} className="self-end sm:min-w-44">
        Continuar
        <ArrowRight className="size-4" aria-hidden />
      </Button>
    </div>
  );
}
