import { useQuery } from "@tanstack/react-query";
import { createFileRoute, Link, redirect } from "@tanstack/react-router";
import {
  ArrowLeft,
  ArrowRight,
  Ban,
  Bell,
  BellOff,
  Check,
  ShieldCheck,
} from "lucide-react";
import { type ComponentType, type ReactNode, useEffect, useRef, useState } from "react";
import { ConnectTelegram } from "@/alerts/ConnectTelegram";
import { channelState, describeChat, linkedChannel } from "@/alerts/channels";
import { alertChannelsQuery } from "@/api/queries";
import { AppShell } from "@/components/AppShell";
import { PageHeader } from "@/components/PageHeader";
import { Card } from "@/components/ui/Card";
import { SuccessMark } from "@/components/ui/SuccessMark";
import { cn } from "@/lib/cn";
import { WaitingDot } from "@/onboarding/parts";

export const Route = createFileRoute("/guias/avisos")({
  beforeLoad: ({ context }) => {
    if (!context.session) throw redirect({ to: "/login" });
  },
  component: AlertsGuide,
});

/**
 * Por qué la app puede escribirte, y qué controlas de eso.
 *
 * Una guía que se hace, no solo se lee: arriba está el estado real del canal
 * y el botón para conectarlo, los mismos que en Perfil. Debajo, en una línea
 * cada una, las respuestas a lo que se pregunta antes de darle a una app de
 * finanzas permiso para escribirte. Cada afirmación corresponde a una regla
 * que el backend hace cumplir — una guía que promete algo que la API rechaza
 * es peor que no tener guía.
 */
function AlertsGuide() {
  return (
    <AppShell>
      <div className="mx-auto flex w-full max-w-3xl flex-col gap-6">
        <Link
          to="/guias"
          className="-my-2 flex min-h-11 items-center gap-1.5 self-start text-muted text-xs transition-colors hover:text-text"
        >
          <ArrowLeft className="size-3.5" />
          Guías
        </Link>

        <PageHeader
          title="Avisos en tu teléfono"
          lead="Finflow te escribe por Telegram cada vez que se mueve plata."
        />

        <Status />

        <Section icon={Bell} title="Qué te llega">
          <pre className="overflow-x-auto rounded-xl border border-line bg-ink p-3.5 font-mono text-text text-xs leading-relaxed">
            {
              "Gasto $84.300\nCOMPRA EN *PAYU*COL\nBancolombia · 13/09 04:46 p. m.\nSin cuenta asignada"
            }
          </pre>
          <Line>
            Cada gasto e ingreso que Finflow registra, del correo del banco o escrito a
            mano, segundos después. Y los lunes, el resumen de tu semana.
          </Line>
          <Line>
            El nombre es el texto del banco tal cual: el aviso no espera a que Finflow
            ordene nada.
          </Line>
          <Line icon={Ban}>
            Nunca los intereses que Finflow calcula de tus créditos: llegarían de a
            varios mientras los estás mirando.
          </Line>
        </Section>

        <Section icon={BellOff} title="Si te molesta">
          <Line>
            <strong className="text-text">Monto mínimo:</strong> no te avisa por debajo
            de esa cifra.
          </Line>
          <Line>
            <strong className="text-text">Movimientos apagado:</strong> el canal sigue
            conectado, en silencio.
          </Line>
          <Line>
            <strong className="text-text">Desvincular:</strong> no llega nada más, y ese
            Telegram queda libre.
          </Line>
          <Link
            to="/perfil"
            className="-my-2 inline-flex min-h-11 items-center gap-1.5 self-start text-cyan text-sm hover:text-text"
          >
            Todo eso está en Perfil
            <ArrowRight className="size-3.5" aria-hidden />
          </Link>
        </Section>

        <Section icon={ShieldCheck} title="Lo que nunca pasa">
          <Line>
            Un Telegram no se conecta a dos cuentas de Finflow: el bot lo rechaza ahí
            mismo, porque un mensaje que llegó no se puede deshacer.
          </Line>
          <Line>
            En el aviso viajan el monto, la descripción y la hora. Nunca un saldo ni el
            número de una tarjeta.
          </Line>
          <Line>
            No pedimos tu número ni tu usuario; del chat guardamos solo el final.
          </Line>
        </Section>

        <p className="text-faint text-xs leading-relaxed">
          ¿Conectaste y no te llega nada? Escríbele{" "}
          <code className="rounded bg-ink px-1.5 py-0.5 font-mono">/start</code> al bot:
          si responde, el canal está vivo y falta que entre un movimiento nuevo.
        </p>
      </div>
    </AppShell>
  );
}

const STEPS = [
  "Pulsa «Conectar Telegram»",
  "En Telegram, pulsa Empezar",
  "El bot te responde y queda listo",
] as const;

/**
 * Where this person stands, from the server, with the next step in reach.
 *
 * The channel list polls while a link is out, so the moment «Empezar» is
 * pressed in Telegram this turns into the confirmation on its own — and if
 * that happened while somebody watched, it celebrates, the way the connect
 * guide does when the first movement lands.
 */
function Status() {
  const { data } = useQuery(alertChannelsQuery);
  const channels = data?.channels ?? [];
  const state = channelState(channels);
  const linked = linkedChannel(channels);
  const sawPending = useRef(false);
  const [celebrate, setCelebrate] = useState(false);

  useEffect(() => {
    if (state === "pending") sawPending.current = true;
    if (state === "linked" && sawPending.current) setCelebrate(true);
  }, [state]);

  if (!data) return null;

  if (state === "linked" && linked) {
    return (
      <Card
        glow="green"
        lift={false}
        className="rise flex flex-col items-center gap-4 p-6 text-center"
      >
        <SuccessMark celebrate={celebrate} />
        <div>
          <h2 className="font-semibold text-lg">Tus avisos están conectados</h2>
          <p className="mt-1 text-muted text-sm">
            Te escribimos a {describeChat(linked)}.
          </p>
        </div>
        <Link
          to="/perfil"
          className="-my-2 inline-flex min-h-11 items-center gap-1.5 text-cyan text-sm hover:text-text"
        >
          Ajustar el mínimo o el resumen
          <ArrowRight className="size-3.5" aria-hidden />
        </Link>
      </Card>
    );
  }

  // The first step is done once a link exists; the second is the wait.
  const current = state === "pending" ? 1 : 0;

  return (
    <Card glow="violet" lift={false} className="rise flex flex-col gap-5">
      <h2 className="font-medium">Conéctalo en un toque</h2>
      <ol className="flex flex-col gap-3">
        {STEPS.map((label, index) => (
          <li key={label} className="flex items-center gap-3 text-sm">
            <span
              aria-hidden
              className={cn(
                "grid size-7 shrink-0 place-items-center rounded-lg text-xs ring-1",
                index < current
                  ? "bg-incoming/15 text-incoming ring-incoming/30"
                  : index === current
                    ? "bg-violet/15 text-violet ring-violet/40"
                    : "bg-surface-raised text-faint ring-line",
              )}
            >
              {index < current ? <Check className="size-3.5" /> : index + 1}
            </span>
            <span
              className={cn(
                "min-w-0 flex-1",
                index < current
                  ? "text-muted line-through"
                  : index === current
                    ? ""
                    : "text-muted",
              )}
            >
              {label}
            </span>
            {index === current && current === 1 ? <WaitingDot /> : null}
            <span className="sr-only">
              {index < current ? "(hecho)" : index === current ? "(ahora)" : ""}
            </span>
          </li>
        ))}
      </ol>
      <ConnectTelegram />
      <p className="text-faint text-xs">
        Sin códigos y sin buscar tu identificador: la pantalla se entera sola.
      </p>
    </Card>
  );
}

function Section({
  icon: Icon,
  title,
  children,
}: {
  icon: ComponentType<{ className?: string }>;
  title: string;
  children: ReactNode;
}) {
  return (
    <Card lift={false} className="flex flex-col gap-4">
      <h2 className="flex items-center gap-2.5 font-medium">
        <span
          aria-hidden
          className="grid size-8 shrink-0 place-items-center rounded-lg border border-line bg-ink"
        >
          <Icon className="size-4 text-cyan" />
        </span>
        {title}
      </h2>
      <div className="flex flex-col gap-3 text-muted text-sm">{children}</div>
    </Card>
  );
}

function Line({
  icon: Icon = Check,
  children,
}: {
  icon?: ComponentType<{ className?: string }>;
  children: ReactNode;
}) {
  return (
    <p className="flex items-start gap-2.5">
      <Icon aria-hidden className="mt-0.5 size-4 shrink-0 text-faint" />
      <span className="min-w-0">{children}</span>
    </p>
  );
}
