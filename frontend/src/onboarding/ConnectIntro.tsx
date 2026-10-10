import { Link } from "@tanstack/react-router";
import { ArrowRight, KeyRound, MailCheck, Monitor } from "lucide-react";
import type { ComponentType, ReactNode } from "react";
import { Button } from "@/components/ui/Button";
import { FlowAnimation } from "@/onboarding/FlowAnimation";

/**
 * The door to the setup, for somebody who has not started it.
 *
 * Three things to understand in a few seconds — what this gets them, that no
 * bank password is involved, that they only send what they choose — and one
 * thing to do. What they will need (Gmail on a computer) is said here rather
 * than discovered at step two, which is where people used to give up.
 */
export function ConnectIntro({ onStart }: { onStart: () => void }) {
  return (
    <section className="rise mx-auto flex max-w-2xl flex-col items-center pt-2 text-center sm:pt-6">
      <FlowAnimation size="lg" />

      <h1 className="mt-8 text-balance font-semibold text-3xl tracking-tight sm:text-4xl">
        Tus gastos, registrados solos
      </h1>
      <p className="mt-3 max-w-md text-balance text-muted leading-relaxed">
        Conecta las alertas de tu banco y Finflow organizará tus movimientos por ti.
      </p>

      <ul className="mt-8 grid w-full grid-cols-1 gap-3 text-left sm:grid-cols-2">
        <Point icon={KeyRound} title="Sin contraseñas del banco">
          Nunca te las pediremos.
        </Point>
        <Point icon={MailCheck} title="Tú decides qué llega">
          Solo las alertas de los bancos que elijas. Tu correo personal no sale de
          Gmail.
        </Point>
      </ul>

      <Button className="mt-8 px-6" onClick={onStart}>
        Comenzar configuración
        <ArrowRight className="size-4" aria-hidden />
      </Button>
      <p className="mt-4 flex items-center gap-2 text-faint text-xs">
        <Monitor className="size-3.5" aria-hidden />
        Toma unos minutos. Necesitarás Gmail abierto en un computador.
      </p>
      <Link
        to="/"
        className="mt-5 rounded-lg px-3 py-2 text-muted text-sm transition-colors hover:text-text"
      >
        Ahora no, explorar primero
      </Link>
    </section>
  );
}

function Point({
  icon: Icon,
  title,
  children,
}: {
  icon: ComponentType<{ className?: string }>;
  title: string;
  children: ReactNode;
}) {
  return (
    <li className="flex items-start gap-3 rounded-2xl border border-line bg-surface/80 p-4">
      <span
        aria-hidden
        className="grid size-9 shrink-0 place-items-center rounded-xl border border-line bg-ink text-cyan"
      >
        <Icon className="size-4" />
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
