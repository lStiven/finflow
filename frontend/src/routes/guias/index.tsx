/**
 * Guías: what is left to set up, what somebody came to do, what went wrong.
 *
 * It used to be a shelf of four readings, which is the right answer to «quiero
 * entender» and the wrong one to everything else people open help for. Most
 * come with a task or a symptom, so those come first and each one is a link to
 * the place it is done — not a paragraph about it. The readings stay, at the
 * bottom, for when the question is «¿por qué?».
 */

import { useQuery } from "@tanstack/react-query";
import { createFileRoute, Link, redirect } from "@tanstack/react-router";
import {
  ArrowRight,
  Banknote,
  Bell,
  BookOpen,
  Check,
  Download,
  Landmark,
  Link2,
  Mail,
  Percent,
  Play,
  Receipt,
  Scale,
  Tag,
  Target,
  Unlink,
  Wallet,
} from "lucide-react";
import {
  type ComponentType,
  cloneElement,
  isValidElement,
  type ReactElement,
  type ReactNode,
} from "react";
import { channelState } from "@/alerts/channels";
import { accountsQuery, alertChannelsQuery, budgetsQuery } from "@/api/queries";
import { AppShell } from "@/components/AppShell";
import { PageHeader } from "@/components/PageHeader";
import { Card } from "@/components/ui/Card";
import { progressOf, type SetupTaskId, setupTasks } from "@/guides/setup";
import { cn } from "@/lib/cn";
import { useOnboarding } from "@/onboarding/useOnboarding";

export const Route = createFileRoute("/guias/")({
  beforeLoad: ({ context }) => {
    if (!context.session) throw redirect({ to: "/login" });
  },
  component: GuidesScreen,
});

type Icon = ComponentType<{ className?: string }>;

function GuidesScreen() {
  return (
    <AppShell>
      <div className="flex flex-col gap-8">
        <PageHeader
          title="Guías"
          lead="Lo que te falta, lo que quieres hacer y lo que no cuadra."
        />
        <Setup />
        <Section title="¿Qué quieres hacer?">
          <TaskRow
            to={<Link to="/transacciones/nueva" />}
            icon={Banknote}
            title="Registrar un gasto en efectivo"
            line="O cualquier movimiento que tu banco no avise."
          />
          <TaskRow
            to={<Link to="/facturas" />}
            icon={Receipt}
            title="Declarar algo que se cobra solo"
            line="El arriendo, el gimnasio, el streaming."
          />
          <TaskRow
            to={<Link to="/presupuestos" />}
            icon={Target}
            title="Ponerle tope a lo que gastas"
            line="Todo el mes, o una categoría."
          />
          <TaskRow
            to={<Link to="/cuentas" />}
            icon={Link2}
            title="Enlazar una tarjeta a su cuenta"
            line="Para que sus movimientos caigan solos ahí."
          />
          <TaskRow
            to={<Link to="/comercios" search={{ review: true }} />}
            icon={Tag}
            title="Corregir la categoría de un comercio"
            line="Empieza por los que Finflow dedujo solo."
          />
          <TaskRow
            to={<Link to="/transacciones" />}
            icon={Download}
            title="Exportar tus movimientos"
            line="A CSV o Excel, con «Exportar» en Transacciones."
          />
        </Section>

        <Section title="Resolver un problema">
          <TaskRow
            to={<Link to="/conectar" />}
            icon={Mail}
            title="No me llegan los movimientos"
            line="El estado de tu conexión dice qué llegó y qué se descartó."
          />
          <TaskRow
            to={<Link to="/transacciones" search={{ unassigned: true }} />}
            icon={Unlink}
            title="Un movimiento quedó sin asignar"
            line="Asígnalo, o enlaza su tarjeta para los siguientes."
          />
          <TaskRow
            to={<Link to="/guias/cuentas-y-movimientos" />}
            icon={Scale}
            title="Un saldo no cuadra con mi banco"
            line="De dónde sale cada saldo y cómo corregirlo."
          />
          <TaskRow
            to={<Link to="/guias/prestamos-e-inversiones" />}
            icon={Percent}
            title="Mi crédito baja menos de lo que pago"
            line="Por qué, y qué datos lo calculan bien."
          />
          <TaskRow
            to={<Link to="/guias/avisos" />}
            icon={Bell}
            title="No me llegan avisos al teléfono"
            line="Cómo conectar Telegram y qué te llega."
          />
        </Section>

        <Section title="Para entender">
          <TaskRow
            to={<Link to="/guias/flujos" />}
            icon={Play}
            title="Verlo en movimiento"
            line="Cómo llega un movimiento, un traslado, una cuota y una factura."
            verb="Ver"
          />
          <TaskRow
            to={<Link to="/guias/cuentas-y-movimientos" />}
            icon={Wallet}
            title="Cuentas y movimientos"
            line="Qué es cada uno, cómo se juntan y qué es «sin asignar»."
            verb="Leer"
          />
          <TaskRow
            to={<Link to="/guias/prestamos-e-inversiones" />}
            icon={Percent}
            title="Créditos e inversiones"
            line="Intereses, seguros, rendimientos y cortes."
            verb="Leer"
          />
          <TaskRow
            to={<Link to="/guias/avisos" />}
            icon={Bell}
            title="Avisos en tu teléfono"
            line="Qué te va a llegar, qué nunca, y cómo bajarle el volumen."
            verb="Leer"
          />
        </Section>

        <p className="flex items-start gap-2.5 text-faint text-xs">
          <BookOpen className="mt-0.5 size-3.5 shrink-0" aria-hidden />
          Todo lo que declares se puede corregir después.
        </p>
      </div>
    </AppShell>
  );
}

/* ----------------------------------------------------------------- setup */

const SETUP_COPY: Record<SetupTaskId, { title: string; icon: Icon; doneLine: string }> =
  {
    connect: {
      title: "Conectar tu banco",
      icon: Mail,
      doneLine: "Tus movimientos llegan solos.",
    },
    accounts: {
      title: "Declarar tus cuentas",
      icon: Landmark,
      doneLine: "Tus saldos y tu patrimonio salen de ellas.",
    },
    budget: {
      title: "Ponerle tope a tu mes",
      icon: Target,
      doneLine: "Tienes al menos un presupuesto.",
    },
    alerts: {
      title: "Avisos en tu teléfono",
      icon: Bell,
      doneLine: "Telegram te avisa de cada movimiento.",
    },
  };

/**
 * The checklist, answered by the server.
 *
 * Plain queries: an enrichment of a help screen, so a slow or failed read
 * leaves the item pending rather than the screen blank. Nothing is shown
 * until the four answers are in, so «0 de 4» never flashes before «3 de 4».
 */
function Setup() {
  const { state } = useOnboarding();
  const accounts = useQuery(accountsQuery("all"));
  const budgets = useQuery(budgetsQuery());
  const channels = useQuery(alertChannelsQuery);

  if (!state || !accounts.data || !budgets.data || !channels.data) return null;

  const tasks = setupTasks({
    connected: state.complete,
    accounts: accounts.data.accounts.length,
    budgets: budgets.data.budgets.length,
    alerts: channelState(channels.data.channels) === "linked",
  });
  const progress = progressOf(tasks);

  const pendingLine: Record<SetupTaskId, string> = {
    connect: `Paso ${Math.min(state.doneCount + 1, state.total)} de ${state.total}.`,
    accounts: "Sin ninguna, todo queda «sin asignar».",
    budget: "Opcional. El más fácil es uno sobre todo el mes.",
    alerts: "Opcional. Se conecta con un toque desde Perfil.",
  };

  const links: Record<SetupTaskId, ReactElement> = {
    connect: <Link to="/conectar" />,
    accounts: <Link to="/cuentas/nueva" />,
    budget: <Link to="/presupuestos" />,
    alerts: <Link to="/perfil" />,
  };

  return (
    <section aria-labelledby="setup-title" className="flex flex-col gap-3">
      <div className="flex items-baseline justify-between gap-3">
        <h2 id="setup-title" className="font-medium text-sm">
          Para que Finflow trabaje solo
        </h2>
        <span className="text-faint text-xs tabular-nums">
          {progress.done} de {progress.total} listos
        </span>
      </div>
      <div
        role="progressbar"
        aria-label="Configuración"
        aria-valuemin={0}
        aria-valuemax={progress.total}
        aria-valuenow={progress.done}
        className="h-1 overflow-hidden rounded-full bg-surface-raised"
      >
        <div
          className="h-full rounded-full bg-gradient-to-r from-accent to-cyan transition-[width] duration-500 ease-out"
          style={{ width: `${(progress.done / progress.total) * 100}%` }}
        />
      </div>

      <Card lift={false} className="flex flex-col p-0">
        {tasks.map((task, index) => {
          const copy = SETUP_COPY[task.id];
          return task.done ? (
            <div
              key={task.id}
              className={cn(
                "flex min-h-14 items-center gap-3 px-4 py-3",
                index > 0 && "border-line/60 border-t",
              )}
            >
              <span
                aria-hidden
                className="pop grid size-9 shrink-0 place-items-center rounded-xl bg-incoming/15 text-incoming"
              >
                <Check className="size-4" />
              </span>
              <span className="min-w-0 flex-1">
                <span className="block text-muted text-sm line-through decoration-faint">
                  {copy.title}
                </span>
                <span className="block text-faint text-xs">{copy.doneLine}</span>
              </span>
              <span className="shrink-0 rounded-full bg-incoming/10 px-2 py-0.5 text-[0.625rem] text-incoming uppercase tracking-wider">
                Comprobado
              </span>
            </div>
          ) : (
            <TaskRow
              key={task.id}
              to={links[task.id]}
              icon={copy.icon}
              title={copy.title}
              line={pendingLine[task.id]}
              bare
              divided={index > 0}
            />
          );
        })}
      </Card>
    </section>
  );
}

/* ------------------------------------------------------------------ parts */

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="flex flex-col gap-3">
      <h2 className="font-medium text-sm">{title}</h2>
      <Card
        lift={false}
        className="flex flex-col p-0 [&>*+*]:border-line/60 [&>*+*]:border-t"
      >
        {children}
      </Card>
    </section>
  );
}

/**
 * One thing to do, as a whole-row link to where it is done.
 *
 * `to` is a `<Link>` with its target and nothing else, so the router checks
 * every destination at build time; the row lends it its look and content.
 */
function TaskRow({
  to,
  icon: Icon,
  title,
  line,
  verb = "Ir",
  bare = false,
  divided = false,
}: {
  to: ReactElement;
  icon: Icon;
  title: string;
  line: string;
  verb?: string;
  /** Inside a card that draws its own dividers. */
  bare?: boolean;
  divided?: boolean;
}) {
  const content = (
    <>
      <span
        aria-hidden
        className="grid size-9 shrink-0 place-items-center rounded-xl border border-line bg-ink text-cyan transition-transform duration-200 group-hover:scale-110"
      >
        <Icon className="size-4" />
      </span>
      <span className="min-w-0 flex-1">
        <span className="block text-sm">{title}</span>
        <span className="block text-faint text-xs">{line}</span>
      </span>
      <span className="flex shrink-0 items-center gap-1 text-cyan text-xs">
        {verb}
        <ArrowRight
          aria-hidden
          className="size-3.5 transition-transform duration-200 group-hover:translate-x-0.5"
        />
      </span>
    </>
  );

  return isValidElement<{ className?: string; children?: ReactNode }>(to)
    ? cloneElement(to, {
        className: cn(
          "group flex min-h-14 items-center gap-3 px-4 py-3 transition-colors duration-150 hover:bg-surface-raised/70",
          bare && divided && "border-line/60 border-t",
        ),
        children: content,
      })
    : null;
}
