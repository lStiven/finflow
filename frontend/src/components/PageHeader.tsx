import { CircleHelp, X } from "lucide-react";
import {
  type ComponentType,
  type CSSProperties,
  type ReactNode,
  useEffect,
  useId,
  useRef,
  useState,
} from "react";
import { useAuth } from "@/auth/AuthContext";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { hasSeenHelp, markHelpSeen } from "@/lib/help";

export type HelpPoint = {
  icon: ComponentType<{ className?: string }>;
  title: string;
  body: ReactNode;
};

export type PageHelp = {
  /** Stable per screen: it is what «ya lo leí» is remembered under. */
  id: string;
  points: readonly HelpPoint[];
  /**
   * Opens on its own the first time somebody lands here. Off where something
   * else already greets a first visit — the welcome dialog, a first-run card.
   */
  openFirstTime?: boolean;
};

/**
 * A screen's title, one line about it, and its main action.
 *
 * What used to be a paragraph under the title lives in «Cómo funciona»: open
 * on the first visit, the way the connect guide teaches while it is used,
 * and a tap away after that. Closing it is remembered per account, so the
 * same explanation is never pushed twice — and never lost either.
 */
export function PageHeader({
  title,
  lead,
  actions,
  help,
}: {
  title: string;
  lead?: ReactNode;
  actions?: ReactNode;
  help?: PageHelp;
}) {
  const { session } = useAuth();
  const userId = session?.userId ?? "";
  const panelId = useId();
  const toggle = useRef<HTMLButtonElement>(null);
  const [open, setOpen] = useState(
    () =>
      help !== undefined &&
      (help.openFirstTime ?? true) &&
      userId !== "" &&
      !hasSeenHelp(userId, help.id),
  );
  // Only an explicit open takes the focus; one that opened by itself on
  // arrival must not pull a screen reader away from the page's title.
  const [asked, setAsked] = useState(false);

  function close() {
    setOpen(false);
    if (help && userId) markHelpSeen(userId, help.id);
    if (asked) toggle.current?.focus();
  }

  return (
    <header className="flex flex-col gap-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-x-2">
            <h1 className="font-semibold text-2xl tracking-tight">{title}</h1>
            {help ? (
              <button
                ref={toggle}
                type="button"
                aria-expanded={open}
                aria-controls={panelId}
                onClick={() => {
                  if (open) return close();
                  setAsked(true);
                  setOpen(true);
                }}
                className="-my-2 inline-flex min-h-11 items-center gap-1.5 rounded-full px-2.5 text-faint text-xs transition-colors duration-150 hover:text-cyan"
              >
                <CircleHelp className="size-4" aria-hidden />
                Cómo funciona
              </button>
            ) : null}
          </div>
          {lead ? <p className="mt-1 text-muted text-sm">{lead}</p> : null}
        </div>
        {actions ? <div className="flex items-center gap-2">{actions}</div> : null}
      </div>

      {help && open ? (
        <HelpPanel id={panelId} points={help.points} focus={asked} onClose={close} />
      ) : null}
    </header>
  );
}

function HelpPanel({
  id,
  points,
  focus,
  onClose,
}: {
  id: string;
  points: readonly HelpPoint[];
  focus: boolean;
  onClose: () => void;
}) {
  const heading = useRef<HTMLHeadingElement>(null);

  useEffect(() => {
    if (focus) heading.current?.focus();
  }, [focus]);

  return (
    <Card
      id={id}
      glow="cyan"
      lift={false}
      className="rise relative flex flex-col gap-5 overflow-hidden"
    >
      <span
        aria-hidden
        className="absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-cyan/60 to-transparent"
      />

      <div className="flex items-start justify-between gap-3">
        <h2
          ref={heading}
          tabIndex={-1}
          className="font-medium text-sm focus:outline-none"
        >
          Cómo funciona
        </h2>
        <button
          type="button"
          onClick={onClose}
          aria-label="Cerrar la ayuda"
          className="-m-2 grid size-11 shrink-0 place-items-center rounded-xl text-faint transition-colors hover:bg-surface-raised hover:text-text"
        >
          <X className="size-4" aria-hidden />
        </button>
      </div>

      <ul className="grid grid-cols-1 gap-5 sm:grid-cols-2">
        {points.map(({ icon: Icon, title: point, body }, index) => (
          <li
            key={point}
            className="rise flex items-start gap-3"
            style={{ animationDelay: `${60 + index * 70}ms` } as CSSProperties}
          >
            <span
              aria-hidden
              className="grid size-9 shrink-0 place-items-center rounded-xl border border-line bg-ink"
            >
              <Icon className="size-4 text-cyan" />
            </span>
            <span className="min-w-0">
              <span className="block font-medium text-sm">{point}</span>
              <span className="mt-0.5 block text-muted text-sm leading-relaxed">
                {body}
              </span>
            </span>
          </li>
        ))}
      </ul>

      <Button variant="ghost" className="self-start py-2 text-xs" onClick={onClose}>
        Entendido
      </Button>
    </Card>
  );
}
