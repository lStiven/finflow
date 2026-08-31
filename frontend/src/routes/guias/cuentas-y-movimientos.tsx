import { createFileRoute, Link, redirect } from "@tanstack/react-router";
import {
  ArrowLeft,
  ArrowUpRight,
  Ban,
  CreditCard,
  Link2,
  Plus,
  Search,
  ShieldCheck,
  Sparkles,
  Wallet,
} from "lucide-react";
import type { ComponentType, ReactNode } from "react";
import { AppShell } from "@/components/AppShell";
import { Card } from "@/components/ui/Card";

export const Route = createFileRoute("/guias/cuentas-y-movimientos")({
  beforeLoad: ({ context }) => {
    if (!context.session) throw redirect({ to: "/login" });
  },
  component: AccountsGuide,
});

/**
 * The two words the whole app is built out of, explained once and properly.
 *
 * Written to be read by somebody who is confused right now, so the order is
 * the order of the questions people actually arrive with: what is this thing
 * in my list, what is a cuenta, why did they not join up, and what do I do
 * about it. Every claim here matches a rule the backend enforces — a guide
 * that promises something the API refuses is worse than no guide.
 */
function AccountsGuide() {
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
              Cuentas y movimientos
            </h1>
            <p className="mt-1.5 text-muted text-sm leading-relaxed">
              Finflow tiene dos piezas y nada más. Un movimiento es cada vez que se
              mueve plata; una cuenta es dónde vive esa plata. Todo lo demás —tu saldo,
              tu patrimonio, tus reportes— sale de juntar las dos.
            </p>
          </div>
        </header>

        <Section
          icon={ArrowUpRight}
          glow="cyan"
          title="Un movimiento"
          lead="Cada vez que entra o sale plata. Es la unidad de todo lo que ves."
          delay={0}
        >
          <p>
            Nacen de dos formas, y solo de esas dos:{" "}
            <strong className="text-text">de una alerta de tu banco</strong>, que llega
            por correo y se lee sola, o <strong className="text-text">a mano</strong>,
            para lo que el banco no anuncia: el efectivo, un cobro sin correo.
          </p>
          <p>
            Cada uno trae el monto, la fecha, si fue ingreso o gasto, y quién cobró o
            pagó. Detrás del texto del banco, Finflow reconoce el comercio: “PAGO PSE
            RAPPI*BOG” y “RAPPI COLOMBIA” son el mismo sitio, y se agrupan como uno.
          </p>
          <Note>
            <strong className="text-text">“Sin asignar” no es un error.</strong> Es un
            movimiento que se registró bien y todavía no pertenece a ninguna cuenta.
            Puedes vivir así para siempre: verás lo que entra y lo que sale, aunque no
            un saldo.
          </Note>
        </Section>

        <Section
          icon={Wallet}
          glow="accent"
          title="Una cuenta"
          lead="Dónde vive la plata: tu cuenta de ahorros, tu tarjeta, el efectivo."
          delay={70}
        >
          <p>
            <strong className="text-text">Las declaras tú, siempre.</strong> Finflow
            nunca crea una: si llega una alerta de una tarjeta que no declaraste, el
            movimiento queda sin asignar, y eso es a propósito — inventar una cuenta
            sería adivinar sobre la plata de alguien.
          </p>
          <p>
            Hay dos familias, y no se eligen: salen del tipo que escojas.{" "}
            <strong className="text-text">Lo que tienes</strong> —ahorros, corriente,
            efectivo, inversión— suma a tu patrimonio.{" "}
            <strong className="text-text">Lo que debes</strong> —tarjeta de crédito,
            préstamo, hipoteca— resta.
          </p>
          <p>
            En lo que debes, <strong className="text-text">el saldo es la deuda</strong>
            : en una tarjeta, $158.800 quiere decir que debes eso, no que lo tienes.
            Gastar sube ese número y pagar lo baja.
          </p>
          <Note>
            El <strong className="text-text">saldo de hoy</strong> que pides al crearla
            es opcional. Sin él la cuenta arranca en cero, y su saldo es “lo que ha
            pasado desde que la declaré”, no lo que hay en el banco.
          </Note>
        </Section>

        <Section
          icon={CreditCard}
          glow="accent"
          title="El cupo de una tarjeta"
          lead="Dos números distintos que es fácil confundir."
          delay={140}
        >
          <p>
            <strong className="text-text">Lo gastado</strong> es lo que debes hoy.{" "}
            <strong className="text-text">El cupo</strong> es el total que te presta el
            banco. Con los dos, Finflow calcula cuánto te queda disponible y lo muestra
            en la barra de la tarjeta.
          </p>
          <p>
            Meter el cupo donde va lo gastado deja la tarjeta leyéndose como agotada el
            mismo día que la declaras. Si no lo sabes, déjalo vacío: el cupo se puede
            declarar después.
          </p>
        </Section>

        <Section
          icon={Link2}
          glow="violet"
          title="Cómo se juntan las dos"
          lead="Un movimiento encuentra su cuenta por tres datos, no por el nombre."
          delay={210}
        >
          <p>
            Cada alerta dice de qué banco viene, por qué medio se movió la plata y los
            últimos cuatro dígitos. Esos tres datos juntos son la llave.{" "}
            <strong className="text-text">
              Los últimos cuatro no bastan solos ni el banco solo
            </strong>
            : dos cuentas del mismo banco se fusionarían en un saldo equivocado.
          </p>
          <p>
            <strong className="text-text">Enlazar es retroactivo.</strong> En cuanto la
            cuenta existe con esa llave, adopta los movimientos que ya habían llegado y
            estaban esperando, y recalcula su saldo con ellos. No hay que reenviar
            ningún correo.
          </p>
          <Note>
            <strong className="text-text">
              Una cuenta real manda alertas de varias formas.
            </strong>{" "}
            Tu cuenta de ahorros avisa como <em>la cuenta</em> cuando transfieres, y
            como <em>tarjeta débito</em> cuando compras — y los últimos cuatro no son
            los mismos. Son dos llaves distintas: hay que enlazar las dos, o la mitad de
            sus movimientos espera para siempre. Se hace desde la cuenta, en “formas de
            llegar”.
          </Note>
          <p>
            Por eso lo que se elige ahí es{" "}
            <strong className="text-text">la palabra del banco</strong>, no el tipo de
            cuenta: una cuenta de ahorros se declara como ahorros, pero sus
            transferencias llegan nombradas como “la cuenta”.
          </p>
        </Section>

        <Section
          icon={Search}
          glow="cyan"
          title="Cuando algo no cuadra"
          lead="Los cuatro casos que se ven de verdad, y qué hacer en cada uno."
          delay={280}
        >
          <Case title="No llega nada">
            El problema está antes de las cuentas: o el reenvío no está activo, o el
            remitente de tu banco no está aprobado —y lo que llega de alguien sin
            aprobar se descarta sin leerse—.{" "}
            <Link to="/conectar" className="text-cyan underline underline-offset-4">
              Revísalo en la guía de conexión
            </Link>
            .
          </Case>
          <Case title="Llega, pero queda sin asignar">
            La alerta no coincide con ninguna llave. Declara la cuenta con el banco y
            los cuatro dígitos que ves en el correo, o enlaza esa forma de llegar a una
            cuenta que ya tengas: los movimientos que estaban esperando se acomodan
            solos.
          </Case>
          <Case title="Un movimiento tiene algo mal">
            Ábrelo y corrígelo: el monto, la fecha, el comercio o a qué cuenta
            pertenece. Si venía de una alerta, Finflow guarda aparte{" "}
            <strong className="text-text">lo que dijo el banco</strong>, para que
            siempre se pueda comparar tu corrección con el original.
          </Case>
          <Case title="El saldo no coincide con el del banco">
            Casi siempre falta el punto de partida: la cuenta arrancó en cero y solo
            cuenta lo que ha pasado desde entonces. También puede faltar una forma de
            llegar por enlazar, si la mitad de sus compras no aparece.
          </Case>
        </Section>

        <Section
          icon={ShieldCheck}
          glow="none"
          title="Lo que Finflow no hace"
          lead="Igual de importante, y más corto."
          delay={350}
        >
          <ul className="flex flex-col gap-2.5">
            <Never>
              No entra a tu banco ni a tu correo. Solo lee lo que tú le reenvías, y solo
              de quien apruebes.
            </Never>
            <Never>
              No crea cuentas por su cuenta, ni adivina a cuál pertenece un movimiento
              que no coincide.
            </Never>
            <Never>
              No suma monedas distintas: no hay tasa de cambio en ninguna parte, así que
              verás una cifra por moneda.
            </Never>
            <Never>
              No borra cuentas. Una cuenta cerrada conserva su historia: un crédito
              pagado que cierra en cero es justo lo que hay que poder ver.
            </Never>
          </ul>
        </Section>

        <Card
          glow="accent"
          lift={false}
          className="flex flex-col gap-4 sm:flex-row sm:items-center"
        >
          <div className="min-w-0 flex-1">
            <h2 className="font-medium">¿Listo para declarar una?</h2>
            <p className="mt-1 text-muted text-sm">
              Son tres pasos y lo único obligatorio es el tipo y el nombre.
            </p>
          </div>
          {/* Styled as the primary action rather than wrapping a <button>:
              a control inside a link is two controls to assistive tech. */}
          <Link
            to="/cuentas/nueva"
            className="inline-flex shrink-0 items-center justify-center gap-2 rounded-xl bg-accent px-4 py-3 font-semibold text-accent-ink text-sm transition-all duration-150 hover:brightness-108"
          >
            <Plus className="size-4" />
            Crear una cuenta
          </Link>
        </Card>
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
