import { createFileRoute, redirect } from "@tanstack/react-router";
import { type SubmitEvent, useState } from "react";
import { useAuth } from "@/auth/AuthContext";
import { Button } from "@/components/ui/Button";
import { Field } from "@/components/ui/Field";

export const Route = createFileRoute("/login")({
  beforeLoad: ({ context }) => {
    if (context.session) throw redirect({ to: "/" });
  },
  component: LoginScreen,
});

function LoginScreen() {
  const { login, register } = useAuth();

  const [mode, setMode] = useState<"login" | "register">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const isRegister = mode === "register";

  async function onSubmit(event: SubmitEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    setBusy(true);
    try {
      await (isRegister ? register(email, password) : login(email, password));
      // No navigation here on purpose. The new session invalidates the router
      // (see `RoutedApp`), this route's own `beforeLoad` then re-runs and
      // redirects to `/`. Navigating as well would race that redirect.
      // `busy` deliberately stays true: the form is on its way out.
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Algo salió mal");
      setBusy(false);
    }
  }

  return (
    <main className="flex min-h-[80dvh] flex-col justify-center">
      <header className="mb-10">
        <h1 className="text-3xl font-semibold tracking-tight">Finflow</h1>
        <p className="mt-2 text-sm text-muted">
          Tus movimientos, sin escribir ninguno.
        </p>
      </header>

      <form onSubmit={onSubmit} className="flex flex-col gap-5">
        <Field
          label="Correo"
          type="email"
          autoComplete="email"
          inputMode="email"
          required
          value={email}
          onChange={(e) => setEmail(e.target.value)}
        />
        <Field
          label="Contraseña"
          type="password"
          autoComplete={isRegister ? "new-password" : "current-password"}
          required
          // The backend asks for length and nothing else — no composition
          // rules, which are known to push people toward predictable
          // patterns. The message here says the same thing it says.
          minLength={8}
          hint={isRegister ? "Mínimo 8 caracteres. Una frase larga sirve." : undefined}
          value={password}
          onChange={(e) => setPassword(e.target.value)}
        />

        {error ? (
          <p role="alert" className="text-sm text-outgoing">
            {error}
          </p>
        ) : null}

        <Button type="submit" full disabled={busy}>
          {busy ? "Un momento…" : isRegister ? "Crear cuenta" : "Entrar"}
        </Button>
      </form>

      <Button
        variant="quiet"
        className="mt-6"
        onClick={() => {
          setMode(isRegister ? "login" : "register");
          setError(null);
        }}
      >
        {isRegister ? "Ya tengo cuenta" : "Crear una cuenta"}
      </Button>
    </main>
  );
}
