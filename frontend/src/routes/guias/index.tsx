/**
 * Guías: a handful of topics, each opening onto what can be done about it.
 *
 * Two earlier shapes taught what this one keeps. A shelf of four readings was
 * calm, but answered only «quiero entender» — most people open help with a
 * task or a symptom. A flat list of every task and symptom answered those, and
 * was a wall of nineteen rows. So: the topics, as cards, the way the app
 * itself is divided; one opens at a time, onto its tasks first, its symptoms
 * after, and the reading and the animated walk-through at its foot. The setup
 * checklist sits above them and opens on its own only while something the app
 * needs is still missing.
 */

import { useQuery } from "@tanstack/react-query";
import { createFileRoute, Link, redirect } from "@tanstack/react-router";
import {
  ArrowRight,
  Banknote,
  Bell,
  BookOpen,
  Check,
  ChevronDown,
  Download,
  Landmark,
  Link2,
  ListChecks,
  Mail,
  Percent,
  Play,
  Receipt,
  Scale,
  Send,
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
  useState,
} from "react";
import { channelState } from "@/alerts/channels";
import { accountsQuery, alertChannelsQuery, budgetsQuery } from "@/api/queries";
import { AppShell } from "@/components/AppShell";
import { PageHeader } from "@/components/PageHeader";
import { Card, type Glow } from "@/components/ui/Card";
import {
  progressOf,
  type SetupTask,
  type SetupTaskId,
  setupTasks,
} from "@/guides/setup";
import { type StoryId, storyOf } from "@/guides/stories";
import { cn } from "@/lib/cn";
import { useOnboarding } from "@/onboarding/useOnboarding";

export const Route = createFileRoute("/guias/")({
  beforeLoad: ({ context }) => {
    if (!context.session) throw redirect({ to: "/login" });
  },
  component: GuidesScreen,
});

type Icon = ComponentType<{ className?: string }>;
type Topic = "setup" | "accounts" | "spending" | "credit" | "alerts";

function GuidesScreen() {
  const setup = useSetup();
  // Untouched, the checklist is open exactly while the app still cannot work
  // on its own; after the first click the person decides.
  const [chosen, setChosen] = useState<Topic | null | undefined>(undefined);
  const open = chosen === undefined ? (setup?.needed ? "setup" : null) : chosen;
  const toggle = (topic: Topic) => setChosen(open === topic ? null : topic);

  return (
    <AppShell>
      <div className="flex flex-col gap-8">
        <PageHeader
          title="Guías"
          lead="Elige un tema: ahí está lo que puedes hacer y qué revisar si algo no cuadra."
          actions={
            <Link
              to="/guias/flujos"
              className="inline-flex min-h-11 items-center gap-2 rounded-xl border border-accent/30 bg-accent/10 px-3.5 text-accent text-sm transition-colors hover:bg-accent/15 sm:min-h-9"
            >
              <Play className="size-3.5" aria-hidden />
              Ver recorridos animados
            </Link>
          }
        />

        <div className="grid gap-4 has-data-open:items-start sm:grid-cols-2">
          <TopicCard
            id="setup"
            className="sm:col-span-2"
            icon={ListChecks}
            glow="accent"
            title="Primeros pasos"
            blurb="Lo que falta para que Finflow trabaje solo."
            badge={setup ? <SetupBadge setup={setup} /> : undefined}
            progress={setup?.progress}
            open={open === "setup"}
            onToggle={() => toggle("setup")}
            story="movimiento"
          >
            {setup ? (
              <Checklist tasks={setup.tasks} doneCount={setup.connectStep} />
            ) : null}
            <Row
              to={<Link to="/conectar" />}
              icon={Mail}
              title="No me llegan los movimientos"
              line="El estado de tu conexión dice qué llegó y qué se descartó."
            />
          </TopicCard>

          <TopicCard
            id="accounts"
            icon={Wallet}
            glow="cyan"
            title="Cuentas y movimientos"
            blurb="Registrar a mano, enlazar tarjetas y qué hacer cuando un saldo no cuadra."
            open={open === "accounts"}
            onToggle={() => toggle("accounts")}
            story="traslado"
            footer={
              <FootLink to={<Link to="/guias/cuentas-y-movimientos" />}>
                <BookOpen className="size-3.5" aria-hidden />
                Leer la guía
              </FootLink>
            }
          >
            <Row
              to={<Link to="/transacciones/nueva" />}
              icon={Banknote}
              title="Registrar un gasto en efectivo"
              line="O cualquier movimiento que tu banco no avise."
            />
            <Row
              to={<Link to="/cuentas" />}
              icon={Link2}
              title="Enlazar una tarjeta a su cuenta"
              line="Para que sus movimientos caigan solos ahí."
            />
            <Row
              to={<Link to="/transacciones" />}
              icon={Download}
              title="Exportar tus movimientos"
              line="A CSV o Excel, con «Exportar» en Transacciones."
            />
            <Row
              to={<Link to="/transacciones" search={{ unassigned: true }} />}
              icon={Unlink}
              title="Un movimiento quedó sin asignar"
              line="Asígnalo, o enlaza su tarjeta para los siguientes."
            />
            <Row
              to={<Link to="/guias/cuentas-y-movimientos" />}
              icon={Scale}
              title="Un saldo no cuadra con mi banco"
              line="De dónde sale cada saldo y cómo corregirlo."
            />
          </TopicCard>

          <TopicCard
            id="spending"
            icon={Receipt}
            glow="violet"
            title="Facturas y presupuestos"
            blurb="Lo que se cobra solo, cuánto quieres gastar y en qué categoría cae cada cosa."
            open={open === "spending"}
            onToggle={() => toggle("spending")}
            story="factura"
          >
            <Row
              to={<Link to="/facturas" />}
              icon={Receipt}
              title="Declarar algo que se cobra solo"
              line="El arriendo, el gimnasio, el streaming."
            />
            <Row
              to={<Link to="/presupuestos" />}
              icon={Target}
              title="Ponerle tope a lo que gastas"
              line="Todo el mes, o una categoría."
            />
            <Row
              to={<Link to="/comercios" search={{ review: true }} />}
              icon={Tag}
              title="Corregir la categoría de un comercio"
              line="Empieza por los que Finflow dedujo solo."
            />
          </TopicCard>

          <TopicCard
            id="credit"
            icon={Percent}
            glow="cyan"
            title="Créditos e inversiones"
            blurb="Por qué pagar 2 millones no baja la deuda 2 millones, y qué datos lo calculan bien."
            open={open === "credit"}
            onToggle={() => toggle("credit")}
            story="cuota"
            footer={
              <FootLink to={<Link to="/guias/prestamos-e-inversiones" />}>
                <BookOpen className="size-3.5" aria-hidden />
                Leer la guía
              </FootLink>
            }
          >
            <Row
              to={<Link to="/cuentas" />}
              icon={Landmark}
              title="Registrar las condiciones de un crédito"
              line="La tasa, el día de corte y los seguros, desde su cuenta."
            />
            <Row
              to={<Link to="/guias/prestamos-e-inversiones" />}
              icon={Percent}
              title="Mi crédito baja menos de lo que pago"
              line="Por qué, y qué datos lo calculan bien."
            />
          </TopicCard>

          <TopicCard
            id="alerts"
            icon={Bell}
            glow="violet"
            title="Avisos en tu teléfono"
            blurb="Conectar Telegram con un toque, qué te va a llegar y qué nunca."
            open={open === "alerts"}
            onToggle={() => toggle("alerts")}
            footer={
              <FootLink to={<Link to="/guias/avisos" />}>
                <BookOpen className="size-3.5" aria-hidden />
                Leer la guía
              </FootLink>
            }
          >
            <Row
              to={<Link to="/perfil" />}
              icon={Send}
              title="Conectar o ajustar Telegram"
              line="Desde Perfil, con un toque."
            />
            <Row
              to={<Link to="/guias/avisos" />}
              icon={Bell}
              title="No me llegan avisos al teléfono"
              line="Qué revisar, y cómo bajarle el volumen sin apagarlo."
            />
          </TopicCard>
        </div>

        <p className="flex items-start gap-2.5 text-faint text-xs">
          <BookOpen className="mt-0.5 size-3.5 shrink-0" aria-hidden />
          Todo lo que declares se puede corregir después.
        </p>
      </div>
    </AppShell>
  );
}

/* ----------------------------------------------------------------- setup */

type Setup = {
  tasks: SetupTask[];
  progress: { done: number; total: number };
  /** Something the app cannot work without is still pending. */
  needed: boolean;
  /** Which of the connection's own steps comes next. */
  connectStep: string;
};

/**
 * The checklist, answered by the server.
 *
 * Plain queries: an enrichment of a help screen, so a slow or failed read
 * leaves the badge off rather than the screen blank. Nothing is answered
 * until the four reads are in, so «0 de 4» never flashes before «3 de 4».
 */
function useSetup(): Setup | null {
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

  return {
    tasks,
    progress: progressOf(tasks),
    needed: tasks.some((task) => !task.done && !task.optional),
    connectStep: `Paso ${Math.min(state.doneCount + 1, state.total)} de ${state.total}.`,
  };
}

function SetupBadge({ setup }: { setup: Setup }) {
  const { done, total } = setup.progress;

  if (done === total) {
    return (
      <span className="rounded-full border border-incoming/30 bg-incoming/10 px-2.5 py-1 text-[0.625rem] text-incoming uppercase tracking-wider">
        Completo
      </span>
    );
  }

  return (
    <span
      className={cn(
        "rounded-full border px-2.5 py-1 text-[0.625rem] uppercase tracking-wider tabular-nums",
        setup.needed
          ? "border-accent/30 bg-accent/10 text-accent"
          : "border-line bg-surface-raised text-muted",
      )}
    >
      {done} de {total} listos
    </span>
  );
}

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

function Checklist({ tasks, doneCount }: { tasks: SetupTask[]; doneCount: string }) {
  const pendingLine: Record<SetupTaskId, string> = {
    connect: doneCount,
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

  return tasks.map((task) => {
    const copy = SETUP_COPY[task.id];
    return task.done ? (
      <div key={task.id} className="flex min-h-14 items-center gap-3 px-4 py-3">
        <span
          aria-hidden
          className="grid size-9 shrink-0 place-items-center rounded-xl bg-incoming/15 text-incoming"
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
      <Row
        key={task.id}
        to={links[task.id]}
        icon={copy.icon}
        title={copy.title}
        line={pendingLine[task.id]}
      />
    );
  });
}

/* ------------------------------------------------------------------ parts */

const ICON_TONES: Record<Glow, string> = {
  accent: "bg-accent/12 text-accent ring-accent/25",
  cyan: "bg-cyan/12 text-cyan ring-cyan/25",
  violet: "bg-violet/12 text-violet ring-violet/25",
  green: "bg-incoming/12 text-incoming ring-incoming/25",
  none: "bg-surface-raised text-muted ring-line",
};

/**
 * One topic: closed it is the calm card the screen used to be, open it lists
 * what can be done about it. The whole head is the button, so the card is
 * still one tap — it just opens instead of leaving.
 */
function TopicCard({
  id,
  icon: Icon,
  glow,
  title,
  blurb,
  badge,
  progress,
  open,
  onToggle,
  footer,
  story,
  className,
  children,
}: {
  id: Topic;
  icon: Icon;
  glow: Glow;
  title: string;
  blurb: string;
  badge?: ReactNode;
  progress?: { done: number; total: number };
  open: boolean;
  onToggle: () => void;
  footer?: ReactNode;
  /** The animated walk-through this topic has, if any: marked on the card, first inside it. */
  story?: StoryId;
  className?: string;
  children: ReactNode;
}) {
  const panel = `guia-${id}`;

  return (
    <Card
      glow={glow}
      lift={false}
      className={cn("flex flex-col p-0", className)}
      data-open={open ? "" : undefined}
    >
      <h2 className="flex flex-1 flex-col">
        <button
          type="button"
          onClick={onToggle}
          aria-expanded={open}
          aria-controls={panel}
          className="group flex w-full flex-1 flex-col gap-4 rounded-card p-5 text-left"
        >
          <span className="flex items-start justify-between gap-3">
            <span
              aria-hidden
              className={cn(
                "grid size-11 place-items-center rounded-xl ring-1",
                ICON_TONES[glow],
              )}
            >
              <Icon className="size-5" />
            </span>
            <span className="flex flex-wrap items-center justify-end gap-1.5">
              {story ? <AnimatedChip /> : null}
              {badge}
            </span>
          </span>

          <span className="block">
            <span className="block font-medium">{title}</span>
            <span className="mt-1.5 block text-muted text-sm leading-relaxed">
              {blurb}
            </span>
          </span>

          {progress ? (
            <span
              role="progressbar"
              aria-label="Primeros pasos"
              aria-valuemin={0}
              aria-valuemax={progress.total}
              aria-valuenow={progress.done}
              className="block h-1 overflow-hidden rounded-full bg-surface-raised"
            >
              <span
                className="block h-full rounded-full bg-gradient-to-r from-accent to-cyan transition-[width] duration-500 ease-out"
                style={{ width: `${(progress.done / progress.total) * 100}%` }}
              />
            </span>
          ) : null}

          <span className="mt-auto flex items-center gap-1.5 text-cyan text-sm">
            {open ? "Cerrar" : "Ver qué hay"}
            <ChevronDown
              aria-hidden
              className={cn(
                "size-3.5 transition-transform duration-200",
                open && "rotate-180",
              )}
            />
          </span>
        </button>
      </h2>

      {open ? (
        <div id={panel} className="rise border-line/60 border-t">
          <div className="flex flex-col [&>*+*]:border-line/60 [&>*+*]:border-t">
            {story ? <StoryRow id={story} /> : null}
            {children}
          </div>
          {footer ? (
            <div className="flex flex-wrap gap-x-5 border-line/60 border-t px-4 py-1">
              {footer}
            </div>
          ) : null}
        </div>
      ) : null}
    </Card>
  );
}

/**
 * Said on the closed card, so which topics can be *watched* is visible
 * before anything is opened — they are the easiest way in, and a link at the
 * foot of an open card is the last thing anybody reads.
 */
function AnimatedChip() {
  return (
    <span className="inline-flex items-center gap-1 rounded-full border border-accent/30 bg-accent/10 px-2.5 py-1 text-[0.625rem] text-accent uppercase tracking-wider">
      <Play className="size-2.5" aria-hidden />
      Paso a paso
    </span>
  );
}

/** The topic's walk-through, first in the open card and louder than the rows. */
function StoryRow({ id }: { id: StoryId }) {
  const story = storyOf(id);

  return (
    <Link
      to="/guias/flujos"
      search={{ ver: id }}
      className="group flex min-h-14 items-center gap-3 bg-accent/5 px-4 py-3 transition-colors duration-150 hover:bg-accent/10"
    >
      <span
        aria-hidden
        className="grid size-9 shrink-0 place-items-center rounded-xl bg-accent/15 text-accent ring-1 ring-accent/30 transition-transform duration-200 group-hover:scale-110"
      >
        <Play className="size-4" />
      </span>
      <span className="min-w-0 flex-1">
        <span className="block text-sm">{story.title}</span>
        <span className="block text-faint text-xs">
          Recorrido animado · {story.slides.length} pasos, a tu ritmo.
        </span>
      </span>
      <ArrowRight
        aria-hidden
        className="size-3.5 shrink-0 text-accent transition-transform duration-200 group-hover:translate-x-0.5"
      />
    </Link>
  );
}

/**
 * One thing to do, as a whole-row link to where it is done.
 *
 * `to` is a `<Link>` with its target and nothing else, so the router checks
 * every destination at build time; the row lends it its look and content.
 */
function Row({
  to,
  icon: Icon,
  title,
  line,
}: {
  to: ReactElement;
  icon: Icon;
  title: string;
  line: string;
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
      <ArrowRight
        aria-hidden
        className="size-3.5 shrink-0 text-cyan transition-transform duration-200 group-hover:translate-x-0.5"
      />
    </>
  );

  return isValidElement<{ className?: string; children?: ReactNode }>(to)
    ? cloneElement(to, {
        className:
          "group flex min-h-14 items-center gap-3 px-4 py-3 transition-colors duration-150 hover:bg-surface-raised/70",
        children: content,
      })
    : null;
}

/** The topic's reading or walk-through, below its rows and quieter than them. */
function FootLink({ to, children }: { to: ReactElement; children: ReactNode }) {
  return isValidElement<{ className?: string; children?: ReactNode }>(to)
    ? cloneElement(to, {
        className:
          "inline-flex min-h-11 items-center gap-1.5 text-muted text-xs transition-colors hover:text-text",
        children,
      })
    : null;
}
