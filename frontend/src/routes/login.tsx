import { createFileRoute, Link, redirect } from "@tanstack/react-router";
import {
  AtSign,
  Clock3,
  KeyRound,
  Loader2,
  Lock,
  Mail,
  Sparkles,
  User,
  Wallet,
  X,
} from "lucide-react";
import type { ComponentType, ReactNode } from "react";
import { type SubmitEvent, useState } from "react";
import { useAuth } from "@/auth/AuthContext";
import { identityErrorMessage } from "@/auth/errors";
import { codeLooksComplete, normalizeCode } from "@/auth/password";
import { BetaMark } from "@/components/BetaMark";
import { Logo } from "@/components/Logo";
import { NeonBackdrop } from "@/components/NeonBackdrop";
import { Button } from "@/components/ui/Button";
import { Field } from "@/components/ui/Field";
import { cn } from "@/lib/cn";

export const Route = createFileRoute("/login")({
  beforeLoad: ({ context }) => {
    if (context.session) throw redirect({ to: "/" });
  },
  component: LoginScreen,
});

type Mode = "login" | "register";

/**
 * The two halves of the door read as two places, not one form with a
 * different button.
 *
 * Signing in is a return: it says as much, asks for two things and gets out
 * of the way. Signing up is an arrival, so it carries the hue that means
 * "secondary" everywhere else in the app, asks for a name, and spends the
 * panel beside it explaining what happens after the account exists — which is
 * the part of Finflow nobody guesses.
 */
const COPY: Record<
  Mode,
  {
    title: string;
    lead: string;
    submit: string;
    busy: string;
    /** Which hue the card and its accents borrow. */
    glow: "accent" | "cyan";
    panelTitle: string;
    points: {
      icon: ComponentType<{ className?: string }>;
      title: string;
      body: string;
    }[];
  }
> = {
  login: {
    title: "Hola de nuevo",
    lead: "Entra y mira en qué va tu mes.",
    submit: "Entrar",
    busy: "Entrando…",
    glow: "accent",
    panelTitle: "Tu dinero fluye. Finflow lo entiende.",
    points: [
      {
        icon: Mail,
        title: "Llega un correo del banco",
        body: "Se reenvía solo a tu dirección de Finflow.",
      },
      {
        icon: Sparkles,
        title: "Se lee y se ordena",
        body: "Monto, comercio y cuenta, sin que toques nada.",
      },
      {
        icon: Wallet,
        title: "Tus saldos, al día",
        body: "Lo que entra, lo que sale y lo que queda.",
      },
    ],
  },
  register: {
    title: "Crea tu cuenta",
    lead: "Un correo y una contraseña. El resto lo hace Finflow.",
    submit: "Crear cuenta",
    busy: "Creando…",
    glow: "cyan",
    panelTitle: "Tres pasos y queda andando.",
    points: [
      {
        icon: User,
        title: "1 · Crea tu cuenta",
        body: "Aquí mismo, en unos segundos.",
      },
      {
        icon: Mail,
        title: "2 · Reenvía el correo de tu banco",
        body: "Te asignamos una dirección propia al terminar.",
      },
      {
        icon: Wallet,
        title: "3 · Listo",
        body: "Cada alerta se vuelve un movimiento en tus cuentas.",
      },
    ],
  },
};

function LoginScreen() {
  const {
    login,
    register,
    requestVerification,
    confirmVerification,
    sessionEnd,
    dismissSessionEnd,
  } = useAuth();

  const [mode, setMode] = useState<Mode>("login");
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  // Registering is two submits, not one: the first asks for a code and the
  // second sends it back. `awaitingCode` is which of the two this form is on.
  const [awaitingCode, setAwaitingCode] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const isRegister = mode === "register";
  const copy = COPY[mode];

  function switchTo(next: Mode) {
    if (next === mode) return;
    setMode(next);
    setError(null);
    setNotice(null);
    setAwaitingCode(false);
    setCode("");
  }

  /** Back to the first step, with the address still editable. */
  function editEmail() {
    setAwaitingCode(false);
    setCode("");
    setError(null);
    setNotice(null);
  }

  async function sendCode() {
    setError(null);
    setNotice(null);
    setBusy(true);
    try {
      // Nothing is created yet — this only sends mail. The account is made by
      // the second submit, with the ticket that code buys.
      await requestVerification(email);
      setAwaitingCode(true);
      setCode("");
      setNotice(`Te enviamos un código a ${email}.`);
    } catch (cause) {
      setError(identityErrorMessage(cause));
    } finally {
      setBusy(false);
    }
  }

  async function onSubmit(event: SubmitEvent<HTMLFormElement>) {
    event.preventDefault();

    if (isRegister && !awaitingCode) {
      await sendCode();
      return;
    }

    setError(null);
    setNotice(null);
    setBusy(true);
    try {
      if (isRegister) {
        const ticket = await confirmVerification(email, normalizeCode(code));
        await register(email, password, ticket, name);
      } else {
        await login(email, password);
      }
      // No navigation here on purpose. The new session invalidates the router
      // (see `RoutedApp`), this route's own `beforeLoad` then re-runs and
      // redirects to `/`. Navigating as well would race that redirect.
      // `busy` deliberately stays true: the form is on its way out.
    } catch (cause) {
      setError(identityErrorMessage(cause));
      setBusy(false);
    }
  }

  return (
    <>
      <NeonBackdrop />

      {/*
       * `items-start`, not `items-center`: registering asks for one field
       * more than signing in, and a card centred in the viewport grows half
       * of that upwards — the top edge slides out from under the cursor on
       * the very control that was just clicked. Anchored, the extra field
       * appears below the ones already on screen, which is where somebody
       * filling a form is looking.
       *
       * The offset scales with the viewport so the anchoring does not read as
       * glued to the top on a tall monitor, and stays a thumb's width down on
       * a phone.
       */}
      <main className="relative mx-auto grid min-h-dvh w-full max-w-6xl items-start gap-12 px-5 py-[clamp(2.5rem,9vh,7rem)] lg:grid-cols-[1.05fr_minmax(24rem,26rem)] lg:gap-16 lg:px-10">
        <Panel mode={mode} />

        <section className="w-full">
          <div
            className={cn(
              "surface surface-static rounded-card border border-line bg-surface/80 p-6 backdrop-blur-xl sm:p-8",
              copy.glow === "cyan" ? "glow-cyan" : "glow-accent",
            )}
          >
            {sessionEnd === "expired" ? (
              <ExpiredNotice onDismiss={dismissSessionEnd} />
            ) : null}

            <ModeSwitch mode={mode} onChange={switchTo} disabled={busy} />

            {/*
             * Keyed on the mode so switching replays the entrance rather than
             * swapping the words in place — the two are different errands and
             * should not look like one screen relabelling itself.
             */}
            <div key={mode}>
              <header className="rise mt-6">
                <h1 className="font-semibold text-2xl tracking-tight">{copy.title}</h1>
                <p className="mt-1.5 text-muted text-sm">{copy.lead}</p>
              </header>

              <form onSubmit={onSubmit} className="mt-6 flex flex-col gap-5">
                {isRegister ? (
                  <Stagger step={0}>
                    <Field
                      label="Nombre"
                      icon={User}
                      autoComplete="name"
                      maxLength={80}
                      placeholder="Como quieres que te llamemos"
                      hint="Opcional. Lo puedes cambiar después."
                      value={name}
                      onChange={(e) => setName(e.target.value)}
                    />
                  </Stagger>
                ) : null}

                <Stagger step={isRegister ? 1 : 0}>
                  <Field
                    label="Correo"
                    icon={AtSign}
                    type="email"
                    autoComplete="email"
                    inputMode="email"
                    placeholder="tu@correo.com"
                    required
                    value={email}
                    onChange={(e) => setEmail(e.target.value)}
                  />
                </Stagger>

                <Stagger step={isRegister ? 2 : 1}>
                  <Field
                    label="Contraseña"
                    icon={Lock}
                    type="password"
                    autoComplete={isRegister ? "new-password" : "current-password"}
                    required
                    // The backend asks for length and nothing else — no
                    // composition rules, which are known to push people
                    // toward predictable patterns. The message here says the
                    // same thing it says.
                    minLength={8}
                    hint={
                      isRegister
                        ? "Mínimo 8 caracteres. Una frase larga sirve."
                        : undefined
                    }
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                  />
                </Stagger>

                {awaitingCode ? (
                  <Stagger step={3}>
                    <div className="flex flex-col gap-3">
                      <Field
                        label="Código"
                        icon={KeyRound}
                        // `inputMode` and `one-time-code` together are what
                        // make a phone offer the code straight from the
                        // notification instead of a keyboard.
                        inputMode="numeric"
                        autoComplete="one-time-code"
                        // Room for the spacing a mail client adds; the value
                        // is normalized before it is sent.
                        maxLength={12}
                        placeholder="000000"
                        required
                        className="text-center font-mono text-xl tracking-[0.4em]"
                        hint={`Seis dígitos, enviados a ${email}. Vencen en 15 minutos.`}
                        value={code}
                        onChange={(e) => setCode(e.target.value)}
                      />

                      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs">
                        <button
                          type="button"
                          onClick={sendCode}
                          disabled={busy}
                          className="text-muted transition-colors hover:text-text disabled:opacity-50"
                        >
                          Reenviar código
                        </button>
                        <button
                          type="button"
                          onClick={editEmail}
                          disabled={busy}
                          className="text-muted transition-colors hover:text-text disabled:opacity-50"
                        >
                          Usar otro correo
                        </button>
                      </div>
                    </div>
                  </Stagger>
                ) : null}

                {notice && !error ? (
                  <p role="status" className="rise text-cyan text-sm">
                    {notice}
                  </p>
                ) : null}

                {error ? (
                  <p role="alert" className="rise text-outgoing text-sm">
                    {error}
                  </p>
                ) : null}

                <Stagger step={isRegister ? 4 : 2}>
                  <Button
                    type="submit"
                    // A guess of the wrong length would still spend one of the
                    // five attempts the challenge allows, so the form does not
                    // let one through.
                    full
                    disabled={busy || (awaitingCode && !codeLooksComplete(code))}
                    className={cn(
                      "py-3.5",
                      copy.glow === "cyan" && "bg-cyan text-ink hover:brightness-110",
                    )}
                  >
                    {busy ? (
                      <>
                        <Loader2 className="size-4 animate-spin" />
                        {isRegister && !awaitingCode ? "Enviando código…" : copy.busy}
                      </>
                    ) : isRegister && !awaitingCode ? (
                      "Enviar código"
                    ) : (
                      copy.submit
                    )}
                  </Button>
                </Stagger>
              </form>
            </div>
          </div>

          {isRegister ? null : (
            <p className="mt-5 text-center text-xs">
              <Link to="/recuperar" className="text-muted hover:text-text">
                ¿Olvidaste tu contraseña?
              </Link>
            </p>
          )}

          <p className="mt-5 text-center text-faint text-xs">
            Al continuar aceptas que Finflow lea los correos que le reenvíes, y solo
            esos.
          </p>

          <MobileSteps mode={mode} />
        </section>
      </main>

      <BetaMark className="fixed right-5 bottom-4 z-10 sm:right-7 sm:bottom-5" />
    </>
  );
}

/**
 * The three steps again, under the form, on every width the panel is hidden at.
 *
 * `Panel` stays hidden *above* the fields for the reason its own comment
 * gives. But registering is the one mode where the middle step — reenvía el
 * correo de tu banco — is the part of Finflow nobody guesses, and living
 * inside `hidden lg:block` meant it had never once been seen on a phone,
 * which is the browser this is delivered to. So it rides underneath instead:
 * the form keeps the fold and the explanation is a scroll away rather than
 * absent.
 *
 * Only when registering. The login panel is a pitch, and somebody coming back
 * to sign in did not arrive to read one.
 *
 * Renders `COPY.register.points`, the same array the panel does — one set of
 * words, so the two can never drift into saying different things.
 */
function MobileSteps({ mode }: { mode: Mode }) {
  if (mode !== "register") return null;

  const copy = COPY.register;

  return (
    <section className="mt-9 lg:hidden">
      <h2 className="font-medium text-sm">{copy.panelTitle}</h2>

      {/* No list marker: each title already carries its own number. */}
      <ul className="mt-4 flex flex-col gap-4">
        {copy.points.map(({ icon: Icon, title, body }) => (
          <li key={title} className="flex items-start gap-3">
            <span
              aria-hidden
              className="mt-0.5 grid size-8 shrink-0 place-items-center rounded-xl border border-line bg-surface"
            >
              <Icon className="size-3.5 text-cyan" />
            </span>
            <span className="min-w-0">
              <span className="block font-medium text-sm">{title}</span>
              <span className="mt-0.5 block text-muted text-sm leading-relaxed">
                {body}
              </span>
            </span>
          </li>
        ))}
      </ul>
    </section>
  );
}

/**
 * The half that is not a form. Hidden on phones, where it would push the
 * fields below the fold to say something nobody came here to read —
 * `MobileSteps` is what carries the registering half of it there instead.
 */
function Panel({ mode }: { mode: Mode }) {
  const copy = COPY[mode];

  return (
    <section className="hidden lg:block">
      <div className="flex items-center gap-3">
        <Logo className="pulse-ring size-12" />
        <span className="wordmark-lit font-semibold text-3xl tracking-tight">
          Finflow
        </span>
      </div>

      <h2
        key={mode}
        className="rise mt-8 max-w-md font-semibold text-4xl leading-tight tracking-tight"
      >
        {copy.panelTitle}
      </h2>

      <ul key={`${mode}-points`} className="mt-10 flex max-w-md flex-col gap-6">
        {copy.points.map(({ icon: Icon, title, body }, index) => (
          <li
            key={title}
            className="rise flex items-start gap-4"
            style={{ animationDelay: `${80 + index * 90}ms` }}
          >
            <span
              aria-hidden
              className="mt-0.5 grid size-9 shrink-0 place-items-center rounded-xl border border-line bg-surface"
            >
              <Icon className="size-4 text-cyan" />
            </span>
            <span>
              <span className="block font-medium text-sm">{title}</span>
              <span className="mt-0.5 block text-muted text-sm">{body}</span>
            </span>
          </li>
        ))}
      </ul>
    </section>
  );
}

/**
 * Two destinations, one control. The lit pill slides between them, so the
 * switch itself shows which errand you are on rather than only saying it.
 */
function ModeSwitch({
  mode,
  onChange,
  disabled,
}: {
  mode: Mode;
  onChange: (mode: Mode) => void;
  disabled: boolean;
}) {
  return (
    <div className="relative grid grid-cols-2 rounded-xl border border-line bg-ink p-1">
      <span
        aria-hidden
        className={cn(
          "absolute inset-y-1 left-1 w-[calc(50%-0.25rem)] rounded-lg transition-transform duration-300 ease-out",
          mode === "login"
            ? "bg-accent/20 ring-1 ring-accent/40"
            : "translate-x-full bg-cyan/15 ring-1 ring-cyan/40",
        )}
      />
      {(["login", "register"] as const).map((option) => (
        <button
          key={option}
          type="button"
          disabled={disabled}
          onClick={() => onChange(option)}
          aria-pressed={mode === option}
          className={cn(
            "relative z-10 rounded-lg py-2 text-sm transition-colors duration-200",
            "disabled:cursor-not-allowed disabled:opacity-60",
            mode === option ? "font-medium text-text" : "text-muted hover:text-text",
          )}
        >
          {option === "login" ? "Entrar" : "Crear cuenta"}
        </button>
      ))}
    </div>
  );
}

/**
 * Says what happened before showing the form again.
 *
 * A session that ran out and a session that was never there look identical
 * once the token is gone, and being dropped at a login form with no
 * explanation reads as the app having lost your work.
 */
function ExpiredNotice({ onDismiss }: { onDismiss: () => void }) {
  return (
    <div
      role="status"
      className="rise mb-6 flex items-start gap-3 rounded-xl border border-warn/30 bg-warn/10 p-3.5"
    >
      <Clock3 className="mt-0.5 size-4 shrink-0 text-warn" />
      <div className="min-w-0 flex-1">
        <p className="font-medium text-sm text-warn">Tu sesión expiró</p>
        <p className="mt-0.5 text-muted text-sm">
          Por seguridad cerramos la sesión pasado un tiempo. Entra otra vez para seguir.
        </p>
      </div>
      <button
        type="button"
        onClick={onDismiss}
        aria-label="Cerrar aviso"
        className="rounded-lg p-1 text-faint transition-colors hover:text-text"
      >
        <X className="size-4" />
      </button>
    </div>
  );
}

/** One field's entrance, offset from the one above it. */
function Stagger({ step, children }: { step: number; children: ReactNode }) {
  return (
    <div className="rise" style={{ animationDelay: `${60 + step * 70}ms` }}>
      {children}
    </div>
  );
}
