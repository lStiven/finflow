import { createFileRoute, Link, redirect } from "@tanstack/react-router";
import {
  ArrowLeft,
  Ban,
  Bell,
  BellOff,
  Send,
  ShieldCheck,
  Sparkles,
  Users,
} from "lucide-react";
import type { ComponentType, ReactNode } from "react";
import { AppShell } from "@/components/AppShell";
import { Card } from "@/components/ui/Card";

export const Route = createFileRoute("/guias/avisos")({
  beforeLoad: ({ context }) => {
    if (!context.session) throw redirect({ to: "/login" });
  },
  component: AlertsGuide,
});

/**
 * Por qué la app puede escribirte, y qué controlas de eso.
 *
 * Escrita para alguien que está a punto de darle a una app de finanzas
 * permiso para escribirle al teléfono, así que el orden es el de las
 * preguntas con las que se llega: qué me va a llegar, cómo se conecta, qué
 * pasa si me molesta, y quién más puede ver esto. Cada afirmación de aquí
 * corresponde a una regla que el backend hace cumplir — una guía que promete
 * algo que la API rechaza es peor que no tener guía.
 */
function AlertsGuide() {
  return (
    <AppShell>
      <div className="mx-auto flex w-full max-w-3xl flex-col gap-6">
        <header className="flex flex-col gap-4">
          <Link
            to="/guias"
            className="flex items-center gap-1.5 self-start text-muted text-xs transition-colors hover:text-text"
          >
            <ArrowLeft className="size-3.5" />
            Guías
          </Link>

          <div>
            <h1 className="font-semibold text-2xl tracking-tight">
              Avisos en tu teléfono
            </h1>
            <p className="mt-1.5 text-muted text-sm leading-relaxed">
              Finflow te escribe por Telegram cada vez que se mueve plata. Es lo que
              hace que no tengas que acordarte de abrirla: si algo no cuadra, te enteras
              en el momento y no a fin de mes.
            </p>
          </div>
        </header>

        <Section
          icon={Bell}
          glow="violet"
          title="Qué te llega"
          lead="Tres o cuatro líneas, segundos después del movimiento"
          delay={0}
        >
          <pre className="overflow-x-auto rounded-xl border border-line bg-ink p-3.5 font-mono text-text text-xs leading-relaxed">
            {
              "Gasto $84.300\nCOMPRA EN *PAYU*COL\nBancolombia · 13/09 04:46 p. m.\nSin cuenta asignada"
            }
          </pre>

          <p>
            Entra todo lo que Finflow registra: lo que llega por el correo del banco y
            lo que escribes a mano, tanto gastos como ingresos.
          </p>

          <Note>
            El nombre que ves es el texto tal cual lo escribió tu banco, no el nombre
            bonito del comercio. Es a propósito: es lo mismo que diría la alerta del
            banco, y así el aviso no depende de que Finflow haya alcanzado a ordenar
            nada todavía.
          </Note>

          <p className="font-medium text-sm text-text">Lo que nunca te va a llegar</p>
          <ul className="flex flex-col gap-2">
            <Never>
              Los intereses y seguros que Finflow calcula de tus créditos. Aparecen de a
              varios cuando abres la pantalla del crédito, y avisártelos mientras los
              estás mirando es justo el ruido que hace que la gente apague todo.
            </Never>
            <Never>
              Nada mientras no hayas conectado un canal. La app no puede escribirte
              hasta que tú lo pidas.
            </Never>
          </ul>
        </Section>

        <Section
          icon={Send}
          glow="accent"
          title="Cómo se conecta"
          lead="Un toque, sin códigos y sin buscar tu identificador"
          delay={60}
        >
          <ol className="flex flex-col gap-3">
            <Step n={1}>
              En <strong className="text-text">Perfil</strong>, pulsa{" "}
              <strong className="text-text">Conectar Telegram</strong>.
            </Step>
            <Step n={2}>
              Se abre Telegram en el bot de Finflow. Pulsa{" "}
              <strong className="text-text">Empezar</strong>.
            </Step>
            <Step n={3}>
              El bot te responde «listo, te aviso por aquí». Eso es la confirmación de
              que de verdad puede escribirte.
            </Step>
            <Step n={4}>
              La pantalla lo detecta sola. No tienes que volver ni copiar nada.
            </Step>
          </ol>

          <Note>
            El enlace sirve <strong className="text-text">una sola vez</strong> y vence
            a los 15 minutos. Si se te pasa, pulsa Conectar otra vez: eso retira el
            anterior, así que nunca queda un enlace tuyo dando vueltas.
          </Note>

          <p>
            Finflow no te pide tu número ni tu usuario de Telegram, y no guarda la
            dirección entera: en la pantalla solo verás los últimos cuatro caracteres,
            que es todo lo que hace falta para reconocer cuál es.
          </p>
        </Section>

        <Section
          icon={BellOff}
          glow="cyan"
          title="Si te molesta, es tuyo"
          lead="Apagarlo, ponerle un piso, o desconectarlo del todo"
          delay={120}
        >
          <Case title="Bajarle el volumen">
            Ponle un <strong className="text-text">monto mínimo</strong> y no te avisará
            por debajo de esa cifra. Sirve para que un café de $3.000 no gaste la
            atención que necesita un cargo de $400.000.
          </Case>

          <Case title="Apagarlo sin desconectar">
            El interruptor de <strong className="text-text">Movimientos</strong> deja el
            canal conectado y en silencio. Volver a encenderlo es un toque.
          </Case>

          <Case title="Desconectar">
            <strong className="text-text">Desvincular</strong> y ya. No se manda nada
            más, y ese Telegram queda libre para conectarse otra vez —aquí o en otra
            cuenta— cuando quieras.
          </Case>
        </Section>

        <Section
          icon={Users}
          glow="none"
          title="Un Telegram, una cuenta"
          lead="Por qué no puedes conectar el mismo chat dos veces"
          delay={180}
        >
          <p>
            Si intentas conectar un Telegram que ya está en otra cuenta de Finflow, el
            bot te lo dice ahí mismo y no lo conecta. Desvincúlalo en la otra cuenta
            primero.
          </p>
          <p>
            No es una limitación técnica: dos personas recibiendo sus movimientos en la
            misma conversación es una fuga que ninguna de las dos aceptó, y no hay forma
            de deshacer un mensaje que ya llegó.
          </p>

          <Note>
            <ShieldCheck aria-hidden className="hidden" />
            Lo que viaja en el aviso es el monto, la descripción del banco y la hora.
            Nunca un saldo, ni el número de una tarjeta, ni nada con lo que se pueda
            mover plata.
          </Note>
        </Section>

        <p className="text-faint text-xs leading-relaxed">
          ¿No te llega nada después de conectar? Escríbele{" "}
          <code className="rounded bg-ink px-1.5 py-0.5 font-mono">/start</code> al bot
          una vez más: si te responde, el canal está vivo y lo que falta es que entre un
          movimiento nuevo.
        </p>
      </div>
    </AppShell>
  );
}

function Section({
  icon: Icon,
  glow,
  title,
  lead,
  delay,
  children,
}: {
  icon: ComponentType<{ className?: string }>;
  glow: "cyan" | "accent" | "violet" | "none";
  title: string;
  lead: string;
  delay: number;
  children: ReactNode;
}) {
  return (
    <Card
      glow={glow}
      lift={false}
      className="rise flex flex-col gap-5"
      style={{ animationDelay: `${delay}ms` }}
    >
      <div className="flex items-start gap-3.5">
        <span
          aria-hidden
          className="grid size-10 shrink-0 place-items-center rounded-xl border border-line bg-ink"
        >
          <Icon className="size-4 text-cyan" />
        </span>
        <div className="min-w-0">
          <h2 className="font-medium">{title}</h2>
          <p className="mt-0.5 text-faint text-xs">{lead}</p>
        </div>
      </div>

      <div className="flex flex-col gap-3.5 text-muted text-sm leading-relaxed">
        {children}
      </div>
    </Card>
  );
}

/** The aside that carries the thing people get wrong most often. */
function Note({ children }: { children: ReactNode }) {
  return (
    <p className="flex items-start gap-2.5 rounded-xl border border-line bg-ink p-3.5">
      <Sparkles aria-hidden className="mt-0.5 size-4 shrink-0 text-cyan" />
      <span className="min-w-0">{children}</span>
    </p>
  );
}

function Case({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="border-line/70 border-l-2 pl-4">
      <p className="font-medium text-sm text-text">{title}</p>
      <p className="mt-1">{children}</p>
    </div>
  );
}

function Never({ children }: { children: ReactNode }) {
  return (
    <li className="flex items-start gap-2.5">
      <Ban aria-hidden className="mt-0.5 size-4 shrink-0 text-faint" />
      <span className="min-w-0">{children}</span>
    </li>
  );
}

function Step({ n, children }: { n: number; children: ReactNode }) {
  return (
    <li className="flex items-start gap-3">
      <span
        aria-hidden
        className="grid size-6 shrink-0 place-items-center rounded-lg bg-violet/15 font-medium text-violet text-xs ring-1 ring-violet/30"
      >
        {n}
      </span>
      <span className="min-w-0">{children}</span>
    </li>
  );
}
