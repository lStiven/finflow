import { createFileRoute, Link, redirect } from "@tanstack/react-router";
import { AtSign, Loader2, MailCheck } from "lucide-react";
import { type SubmitEvent, useState } from "react";
import { api, unwrap } from "@/api/client";
import { identityErrorMessage } from "@/auth/errors";
import { NeonBackdrop } from "@/components/NeonBackdrop";
import { Button } from "@/components/ui/Button";
import { Field } from "@/components/ui/Field";

export const Route = createFileRoute("/recuperar")({
  beforeLoad: ({ context }) => {
    // Somebody already signed in does not need this: they can change their
    // password from `/perfil` without going through their inbox.
    if (context.session) throw redirect({ to: "/" });
  },
  component: RecoverScreen,
});

/**
 * "I cannot get in."
 *
 * The screen says the same thing whether or not the address has an account,
 * because the API does: it answers 202 either way and mails something either
 * way. Reporting "no existe esa cuenta" here would hand back exactly the
 * answer the API refuses to give.
 */
function RecoverScreen() {
  const [email, setEmail] = useState("");
  const [sent, setSent] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function onSubmit(event: SubmitEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    setBusy(true);
    try {
      await unwrap(api.POST("/identity/password/forgot", { body: { email } }));
      setSent(true);
    } catch (cause) {
      // A 429 here is the per-address cap, and its `Retry-After` is the only
      // thing the endpoint will say — it will not confirm whether the address
      // has an account.
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
          <div className="surface surface-static glow-accent rounded-card border border-line bg-surface/80 p-6 backdrop-blur-xl sm:p-8">
            {sent ? (
              <div className="rise flex flex-col gap-4">
                <MailCheck className="size-8 text-cyan" />
                <h1 className="font-semibold text-2xl tracking-tight">
                  Revisa tu correo
                </h1>
                <p className="text-muted text-sm">
                  Si hay una cuenta con <span className="text-text">{email}</span>, le
                  enviamos un enlace para elegir una contraseña nueva. Vence en 30
                  minutos y sirve una sola vez.
                </p>
                <p className="text-faint text-xs">
                  ¿No llegó? Revisa spam. Puedes pedir otro en un minuto.
                </p>
              </div>
            ) : (
              <>
                <header className="rise">
                  <h1 className="font-semibold text-2xl tracking-tight">
                    ¿Olvidaste tu contraseña?
                  </h1>
                  <p className="mt-1.5 text-muted text-sm">
                    Escribe tu correo y te enviamos un enlace para cambiarla.
                  </p>
                </header>

                <form onSubmit={onSubmit} className="mt-6 flex flex-col gap-5">
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

                  {error ? (
                    <p role="alert" className="rise text-outgoing text-sm">
                      {error}
                    </p>
                  ) : null}

                  <Button type="submit" full disabled={busy} className="py-3.5">
                    {busy ? (
                      <>
                        <Loader2 className="size-4 animate-spin" />
                        Enviando…
                      </>
                    ) : (
                      "Enviar enlace"
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
