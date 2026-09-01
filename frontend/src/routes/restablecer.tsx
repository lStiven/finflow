import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { CheckCircle2, Loader2, Lock } from "lucide-react";
import { type SubmitEvent, useState } from "react";
import { api, unwrap } from "@/api/client";
import { identityErrorMessage } from "@/auth/errors";
import { LENGTH_MESSAGE, newPasswordIssue } from "@/auth/password";
import { NeonBackdrop } from "@/components/NeonBackdrop";
import { Button } from "@/components/ui/Button";
import { Field } from "@/components/ui/Field";

type Search = { token?: string };

export const Route = createFileRoute("/restablecer")({
  // The token arrives in the query string because that is where the emailed
  // link puts it. It is read, used once and never stored.
  validateSearch: (search: Record<string, unknown>): Search => ({
    token: typeof search.token === "string" ? search.token : undefined,
  }),
  component: ResetScreen,
});

/**
 * The page behind the emailed link.
 *
 * Deliberately not guarded by a session: whoever opens this cannot log in, and
 * the token in the link is the whole credential. Setting the password here
 * ends every session the account had, so it finishes by sending people to the
 * login form rather than signing them in.
 */
function ResetScreen() {
  const { token } = Route.useSearch();
  const navigate = useNavigate();

  const [password, setPassword] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [done, setDone] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function onSubmit(event: SubmitEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!token) return;

    // Checked before sending, and the link is why. The API refuses a short
    // password without spending it, but it cannot check a second box at all —
    // and a typo in a password nobody can read back is what would lock
    // somebody out of the account they are in the middle of recovering.
    const issue = newPasswordIssue(password);
    if (issue) {
      setError(issue);
      return;
    }
    if (confirmation !== password) {
      setError("Las dos contraseñas no coinciden.");
      return;
    }

    setError(null);
    setBusy(true);
    try {
      await unwrap(
        api.POST("/identity/password/reset", {
          body: { token, new_password: password },
        }),
      );
      setDone(true);
    } catch (cause) {
      setError(identityErrorMessage(cause));
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <NeonBackdrop />

      <main className="relative mx-auto flex min-h-dvh w-full max-w-md items-center px-5 py-12">
        <section className="w-full">
          <div className="surface surface-static glow-cyan rounded-card border border-line bg-surface/80 p-6 backdrop-blur-xl sm:p-8">
            {!token ? (
              <div className="rise flex flex-col gap-4">
                <h1 className="font-semibold text-2xl tracking-tight">
                  Ese enlace no sirve
                </h1>
                <p className="text-muted text-sm">
                  Ábrelo tal como llegó al correo, completo. Si ya lo usaste o pasaron
                  más de 30 minutos, pide uno nuevo.
                </p>
                <Button onClick={() => navigate({ to: "/recuperar" })} full>
                  Pedir otro enlace
                </Button>
              </div>
            ) : done ? (
              <div className="rise flex flex-col gap-4">
                <CheckCircle2 className="size-8 text-cyan" />
                <h1 className="font-semibold text-2xl tracking-tight">
                  Listo, ya la cambiaste
                </h1>
                <p className="text-muted text-sm">
                  Entra con tu contraseña nueva. Las sesiones que estaban abiertas se
                  cerraron: si alguien más tenía la anterior, ya no le sirve.
                </p>
                <Button onClick={() => navigate({ to: "/login" })} full>
                  Iniciar sesión
                </Button>
              </div>
            ) : (
              <>
                <header className="rise">
                  <h1 className="font-semibold text-2xl tracking-tight">
                    Elige una contraseña nueva
                  </h1>
                  <p className="mt-1.5 text-muted text-sm">
                    Este enlace sirve una sola vez.
                  </p>
                </header>

                <form onSubmit={onSubmit} className="mt-6 flex flex-col gap-5">
                  <Field
                    label="Contraseña nueva"
                    icon={Lock}
                    type="password"
                    autoComplete="new-password"
                    required
                    hint={LENGTH_MESSAGE}
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                  />

                  <Field
                    label="Repítela"
                    icon={Lock}
                    type="password"
                    autoComplete="new-password"
                    required
                    hint="Para que un dedazo no te deje fuera."
                    value={confirmation}
                    onChange={(e) => setConfirmation(e.target.value)}
                  />

                  {error ? (
                    <p role="alert" className="rise text-outgoing text-sm">
                      {error}
                    </p>
                  ) : null}

                  <Button type="submit" full disabled={busy} className="py-3.5">
                    {busy ? (
                      <>
                        <Loader2 className="size-4 animate-spin" />
                        Guardando…
                      </>
                    ) : (
                      "Guardar contraseña"
                    )}
                  </Button>
                </form>
              </>
            )}
          </div>

          <p className="mt-5 text-center text-faint text-xs">
            <Link to="/login" className="hover:text-text">
              Volver a iniciar sesión
            </Link>
          </p>
        </section>
      </main>
    </>
  );
}
