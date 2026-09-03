import { createFileRoute, Link, redirect } from "@tanstack/react-router";
import {
  ArrowLeft,
  Ban,
  CalendarClock,
  Coins,
  Percent,
  Plus,
  ShieldCheck,
  Sparkles,
  TrendingUp,
  TriangleAlert,
} from "lucide-react";
import type { ComponentType, ReactNode } from "react";
import { AppShell } from "@/components/AppShell";
import { Card } from "@/components/ui/Card";

export const Route = createFileRoute("/guias/prestamos-e-inversiones")({
  beforeLoad: ({ context }) => {
    if (!context.session) throw redirect({ to: "/login" });
  },
  component: FinancingGuide,
});

/**
 * Why a credit needs data no bank alert carries, and what each field is.
 *
 * Written for the moment somebody is looking at the form and wondering what
 * "efectivo anual" has to do with the number on their contract. Every claim
 * matches a rule the backend enforces: a guide that promises what the API
 * refuses is worse than no guide.
 *
 * The order is the order of the questions people actually arrive with — why
 * the debt did not go down by what I paid, what you need from me, what each
 * field means, and what happens once I fill it in.
 */
function FinancingGuide() {
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
              Créditos e inversiones
            </h1>
            <p className="mt-1.5 text-muted text-sm leading-relaxed">
              Un préstamo no se comporta como una cuenta de ahorros: la deuda crece sola
              mientras la pagas. Esta guía explica por qué, qué datos hay que darle a
              Finflow y para qué sirve cada uno.
            </p>
            <p className="mt-3 flex items-start gap-2.5 rounded-xl border border-line bg-ink p-3.5 text-muted text-sm leading-relaxed">
              <Sparkles aria-hidden className="mt-0.5 size-4 shrink-0 text-cyan" />
              <span className="min-w-0">
                <strong className="text-text">
                  Un crédito no entra en tu patrimonio ni en tus gastos.
                </strong>{" "}
                Es un seguimiento aparte, y a propósito: ya sabes lo que debes, y la
                cuota se registra como gasto cuando sale de tu cuenta. Lo que hace esta
                herramienta es mostrarte cómo va la deuda por dentro.
              </span>
            </p>
          </div>
        </header>

        <Section
          icon={TriangleAlert}
          glow="accent"
          title="Pagar 2 millones no baja la deuda 2 millones"
          lead="El caso que hace falta entender antes que cualquier otra cosa."
          delay={0}
        >
          <p>
            Supón que debes <strong className="text-text">60 000 000</strong> a una tasa
            del 19,56 % efectivo anual y pagas una cuota de{" "}
            <strong className="text-text">2 000 000</strong>. Al mes siguiente no debes
            58 000 000. Debes <strong className="text-text">58 920 623</strong>.
          </p>
          <Ledger />
          <p>
            Los 920 623 de diferencia no aparecen en ninguna alerta del banco: la alerta
            dice que pagaste, nunca de qué se compuso el pago. Si Finflow solo
            registrara la cuota, tu deuda se vería más baja de lo que es —{" "}
            <strong className="text-text">todos los meses, y cada vez por más</strong>.
          </p>
          <Note>
            Por eso estas cuentas piden datos extra. No es papeleo: es lo único con lo
            que se puede saber cuánto debes de verdad.
          </Note>
        </Section>

        <Section
          icon={Percent}
          glow="cyan"
          title="Qué se te pide, y por qué"
          lead="Todo está en tu contrato o en el extracto del mes."
          delay={70}
        >
          <FieldNote name="Tasa de interés">
            El porcentaje del contrato, escrito tal cual: si dice 19,56, escribe{" "}
            <code className="text-text">19.56</code>. Al lado eliges cómo está
            expresada, y eso <strong className="text-text">no es un detalle</strong>:
            19,56 % <em>efectivo anual</em> equivale a 1,4999 % mensual, mientras que
            19,56 % <em>nominal anual</em> es 1,63 % mensual. Confundirlas cambia los
            intereses de todo el crédito.
          </FieldNote>

          <FieldNote name="Fecha de desembolso">
            Cuándo te entregaron la plata. Marca el arranque del primer periodo, que
            casi siempre es más corto que un mes y por eso cobra menos interés.
          </FieldNote>

          <FieldNote name="Plazo en meses">
            A cuántos meses lo tomaste. Si no escribes la cuota, es de aquí de donde
            Finflow la calcula.
          </FieldNote>

          <FieldNote name="Fecha de corte">
            El día del mes en que cierra el extracto. Ese día se cobran los intereses
            del periodo y los seguros. Es la fecha que decide cuánto de tu pago llega al
            capital: lo que pagues antes del corte baja el saldo sobre el que te cobran.
          </FieldNote>

          <FieldNote name="Día de pago">
            Cuándo vence la cuota, que suele ser unos días después del corte. Si es el
            mismo día, déjalo vacío.
          </FieldNote>

          <FieldNote name="Monto desembolsado">
            Opcional. Lo que te prestaron al principio. Solo hace falta si algún cobro
            se calcula sobre esa cifra.
          </FieldNote>

          <FieldNote name="Cuota mensual">
            Opcional. La que te cobran hoy. Si la dejas vacía, Finflow calcula la cuota
            fija que liquida el saldo en el plazo que queda.
          </FieldNote>

          <FieldNote name="La cuota ya incluye los seguros">
            Casi siempre sí: el número del extracto suele traerlos dentro. Marcarlo al
            revés cambia cuánto de tu cuota se va a capital, exactamente por el valor de
            los seguros, todos los meses.
          </FieldNote>
        </Section>

        <Section
          icon={ShieldCheck}
          glow="violet"
          title="Los seguros, que es donde se esconde el costo"
          lead="Un crédito al 19 % rara vez cuesta 19 %."
          delay={140}
        >
          <p>
            En una hipoteca colombiana casi siempre hay dos, y se calculan sobre cosas
            distintas:
          </p>

          <Case title="Seguro de vida deudores">
            Un porcentaje del <strong className="text-text">saldo de la deuda</strong>{" "}
            —del orden de 0,0345 % al mes—. Baja a medida que pagas, porque hay menos
            deuda que asegurar.
          </Case>

          <Case title="Seguro de incendio y terremoto">
            Un porcentaje del{" "}
            <strong className="text-text">valor asegurado de la vivienda</strong>, no de
            la deuda. Por eso pide ese avalúo aparte: no baja aunque pagues medio
            crédito, y es el cobro que más sorprende cuando falta.
          </Case>

          <Case title="Cuotas de manejo y administración">
            Un valor fijo cada mes. Se registran igual, eligiendo “un valor fijo”.
          </Case>

          <p>
            De cada cobro se pregunta además si{" "}
            <strong className="text-text">lo suman a la deuda</strong>. Normalmente sí:
            el banco lo carga al crédito y tu cuota lo cubre. Si en cambio te lo debitan
            de otra cuenta, apágalo — ese débito ya llega como su propio movimiento y
            sumarlo aquí te lo cobraría dos veces.
          </p>
          <Note>
            Cada cobro va con el nombre que aparece en tu extracto, y{" "}
            <strong className="text-text">dos cobros no pueden llamarse igual</strong>:
            cada uno se registra una vez al mes bajo su nombre, así que el segundo nunca
            se cobraría.
          </Note>
        </Section>

        <Section
          icon={CalendarClock}
          glow="cyan"
          title="¿Desde cuándo se empieza a cobrar?"
          lead="La pregunta que más fácil se responde mal."
          delay={210}
        >
          <p>
            Por defecto, <strong className="text-text">desde hoy</strong>. Es lo
            correcto en el caso normal: si llevas tres años pagando la hipoteca y
            registraste el saldo que te muestra el banco, ese número{" "}
            <strong className="text-text">ya trae</strong> esos tres años de intereses.
            Volvérselos a cobrar duplicaría la deuda.
          </p>
          <p>
            Solo enciende “reconstruir desde el desembolso” si registraste como saldo el{" "}
            <strong className="text-text">monto original</strong> del crédito y quieres
            que Finflow arme la historia mes a mes desde entonces.
          </p>
        </Section>

        <Section
          icon={Coins}
          glow="none"
          title="Qué pasa después"
          lead="Los intereses se vuelven movimientos, como todo lo demás."
          delay={280}
        >
          <p>
            Cada vez que cierra un corte, Finflow registra{" "}
            <strong className="text-text">un movimiento por los intereses</strong> y{" "}
            <strong className="text-text">uno por cada seguro</strong>, con su nombre.
            No es un número que aparece de la nada: es una fila que puedes abrir,
            entender y, si hace falta, corregir.
          </p>
          <p>
            Eso también los pone donde corresponde en tus reportes. Los intereses{" "}
            <strong className="text-text">sí son un gasto</strong> —es lo que el crédito
            te cuesta— mientras que la cuota en sí no lo es: es un traslado entre dos
            bolsillos tuyos.
          </p>
          <Note>
            <strong className="text-text">
              Registra el pago de la cuota como traslado
            </strong>
            , no como gasto. Desde <em>Transacciones → Nueva → Traslado</em>, eligiendo
            el crédito como destino. Así baja la deuda sin contarse como gasto del mes.
          </Note>
          <p>
            El botón <strong className="text-text">Actualizar</strong> se puede pulsar
            cuantas veces quieras: cada cobro se identifica por su cuenta y su periodo,
            así que un mes ya cobrado no se cobra dos veces.
          </p>
        </Section>

        <Section
          icon={TrendingUp}
          glow="cyan"
          title="Inversiones"
          lead="Dos casos, y solo uno se puede calcular."
          delay={350}
        >
          <Case title="Con tasa pactada — un CDT, una cuenta remunerada">
            Se registra igual que un crédito, pero al revés: cada corte abona el
            rendimiento y descuenta lo que retengan. La{" "}
            <strong className="text-text">retención en la fuente</strong> se registra
            como un cobro “% sobre el rendimiento” —el 4 % de lo que ganó, no del
            saldo—. Si es un CDT, la fecha de vencimiento importa: después de ese día
            deja de rendir.
          </Case>

          <Case title="Sin tasa — acciones, un fondo cuyo valor se mueve">
            No hay fórmula que valga. Deja la tasa vacía y usa{" "}
            <strong className="text-text">“¿Cuánto vale hoy?”</strong> cuando quieras:
            la diferencia contra lo registrado queda como un movimiento de valoración.
            Así se distingue lo que <em>aportaste</em> de lo que <em>ganaste</em>, que
            es justo lo que se pierde si solo corriges el saldo.
          </Case>
        </Section>

        <Section
          icon={Ban}
          glow="none"
          title="Lo que Finflow no calcula"
          lead="Más corto, e igual de importante."
          delay={420}
        >
          <ul className="flex flex-col gap-2.5">
            <Never>
              <strong className="text-text">Tarjetas de crédito.</strong> Sus intereses
              dependen de cuánto del extracto quedó sin pagar, y eso no llega en ninguna
              alerta. Si te los cobran, regístralos a mano.
            </Never>
            <Never>
              <strong className="text-text">Créditos en UVR.</strong> El saldo se indexa
              a la inflación, y ese índice no está en la app.
            </Never>
            <Never>
              <strong className="text-text">Intereses de mora.</strong> Si te atrasas,
              el banco cobra una tasa distinta que aquí no se modela.
            </Never>
            <Never>
              La tabla que ves es una <strong className="text-text">proyección</strong>:
              supone que pagas puntual y no abonas de más. Cualquier pago real la vuelve
              a calcular desde el saldo nuevo.
            </Never>
            <Never>
              Nada de esto entra en tu <strong className="text-text">patrimonio</strong>
              , en lo que <strong className="text-text">debes</strong> ni en tus{" "}
              <strong className="text-text">reportes</strong>. Es deliberado: Finflow
              está para mostrarte en qué se te va la plata, y un crédito es un
              compromiso que ya tenías.
            </Never>
          </ul>
        </Section>

        <Card
          glow="accent"
          lift={false}
          className="flex flex-col gap-4 sm:flex-row sm:items-center"
        >
          <div className="min-w-0 flex-1">
            <h2 className="font-medium">¿Tienes un crédito por registrar?</h2>
            <p className="mt-1 text-muted text-sm">
              Créalo como cuenta —préstamo o hipoteca— y desde su tarjeta entras a
              registrar las condiciones.
            </p>
          </div>
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

/**
 * The example, laid out as the four lines it actually is.
 *
 * A table rather than a paragraph because the point is arithmetic somebody
 * should be able to check against their own statement, and a sentence hides
 * exactly the step that surprises people.
 */
function Ledger() {
  const rows: { label: string; amount: string; tone?: "up" | "down" }[] = [
    { label: "Debías", amount: "60 000 000" },
    { label: "Intereses del mes (1,4999 %)", amount: "+ 899 923", tone: "up" },
    { label: "Seguro de vida deudores", amount: "+ 20 700", tone: "up" },
    { label: "Tu cuota", amount: "− 2 000 000", tone: "down" },
  ];

  return (
    <div className="rounded-xl border border-line bg-ink p-4">
      <dl className="flex flex-col gap-2 text-sm">
        {rows.map((row) => (
          <div key={row.label} className="flex items-baseline justify-between gap-4">
            <dt className="min-w-0 text-muted">{row.label}</dt>
            <dd
              className={
                row.tone === "up"
                  ? "tabular shrink-0 text-outgoing"
                  : row.tone === "down"
                    ? "tabular shrink-0 text-incoming"
                    : "tabular shrink-0"
              }
            >
              {row.amount}
            </dd>
          </div>
        ))}
        <div className="mt-1 flex items-baseline justify-between gap-4 border-line border-t pt-3">
          <dt className="font-medium">Ahora debes</dt>
          <dd className="tabular shrink-0 font-medium">58 920 623</dd>
        </div>
      </dl>
    </div>
  );
}

function FieldNote({ name, children }: { name: string; children: ReactNode }) {
  return (
    <div className="border-line/70 border-l-2 pl-4">
      <p className="font-medium text-sm text-text">{name}</p>
      <p className="mt-1">{children}</p>
    </div>
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
