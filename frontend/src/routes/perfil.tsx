import { useSuspenseQuery } from "@tanstack/react-query";
import { createFileRoute, Link, redirect } from "@tanstack/react-router";
import {
  AtSign,
  Check,
  ChevronRight,
  Clock3,
  Loader2,
  LogOut,
  Mail,
  User,
} from "lucide-react";
import { type SubmitEvent, useState } from "react";
import { profileQuery, useUpdateProfile } from "@/api/queries";
import { useAuth } from "@/auth/AuthContext";
import { AppShell } from "@/components/AppShell";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { Field } from "@/components/ui/Field";
import { formatDateTime } from "@/lib/dates";

export const Route = createFileRoute("/perfil")({
  beforeLoad: ({ context }) => {
    if (!context.session) throw redirect({ to: "/login" });
  },
  loader: ({ context }) => context.queryClient.ensureQueryData(profileQuery),
  component: ProfileScreen,
});

function ProfileScreen() {
  const { data: profile } = useSuspenseQuery(profileQuery);
  const { session, logout } = useAuth();
  const update = useUpdateProfile();

  const [name, setName] = useState(profile.name ?? "");
  const [saved, setSaved] = useState(false);

  // Whitespace-only is what the backend refuses, and comparing the trimmed
  // value is also what keeps "save" from firing on a name nobody changed.
  const trimmed = name.trim();
  const changed = trimmed !== (profile.name ?? "");
  const canSave = trimmed.length > 0 && changed && !update.isPending;

  async function onSubmit(event: SubmitEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!canSave) return;
    setSaved(false);
    try {
      const next = await update.mutateAsync({ name: trimmed });
      setName(next.name ?? "");
      setSaved(true);
    } catch {
      // `update.error` carries it; the form reports it below.
    }
  }

  return (
    <AppShell>
      <header className="mb-8">
        <h1 className="font-semibold text-2xl tracking-tight">Tu cuenta</h1>
        <p className="mt-1.5 text-muted text-sm">
          Cómo te llamamos, y con qué correo entras.
        </p>
      </header>

      <div className="grid gap-5 lg:grid-cols-2">
        <Card glow="accent" lift={false} className="flex flex-col gap-6">
          <div className="flex items-center gap-4">
            <span
              aria-hidden
              className="grid size-14 shrink-0 place-items-center rounded-2xl bg-accent/15 font-semibold text-accent text-xl ring-1 ring-accent/30"
            >
              {initialOf(profile.name, profile.email)}
            </span>
            <div className="min-w-0">
              <p className="truncate font-medium">{profile.name ?? "Sin nombre"}</p>
              <p className="truncate text-muted text-sm">{profile.email}</p>
            </div>
          </div>

          <form onSubmit={onSubmit} className="flex flex-col gap-5">
            <Field
              label="Nombre"
              icon={User}
              autoComplete="name"
              maxLength={80}
              placeholder="Como quieres que te llamemos"
              value={name}
              onChange={(e) => {
                setName(e.target.value);
                setSaved(false);
              }}
            />

            {update.error ? (
              <p role="alert" className="text-outgoing text-sm">
                {update.error.message}
              </p>
            ) : null}

            <div className="flex items-center gap-3">
              <Button type="submit" disabled={!canSave}>
                {update.isPending ? (
                  <>
                    <Loader2 className="size-4 animate-spin" />
                    Guardando…
                  </>
                ) : (
                  "Guardar"
                )}
              </Button>

              {saved && !changed ? (
                <span
                  role="status"
                  className="rise flex items-center gap-1.5 text-incoming text-sm"
                >
                  <Check className="size-4" />
                  Guardado
                </span>
              ) : null}
            </div>
          </form>
        </Card>

        <Card glow="cyan" lift={false} className="flex flex-col gap-5">
          <Detail
            icon={AtSign}
            label="Correo"
            value={profile.email}
            note="Es con lo que entras, y no se puede cambiar por ahora."
          />

          {session ? (
            <Detail
              icon={Clock3}
              label="Esta sesión vence"
              value={formatDateTime(session.expiresAt)}
              note="Al vencer te avisamos y te devolvemos al inicio de sesión."
            />
          ) : null}

          {/*
           * The address itself lives on one screen only, so there is never a
           * second copy to disagree with it — but this is where people come
           * looking for it, so the way there is one click.
           */}
          <Link
            to="/conectar"
            className="flex items-center gap-3 rounded-xl border border-line bg-ink p-3.5 transition-colors hover:bg-surface-raised"
          >
            <span
              aria-hidden
              className="grid size-9 shrink-0 place-items-center rounded-xl border border-line bg-surface"
            >
              <Mail className="size-4 text-cyan" />
            </span>
            <span className="min-w-0 flex-1">
              <span className="block font-medium text-sm">Tu dirección de reenvío</span>
              <span className="block text-faint text-xs">
                Verla, copiarla y repasar cómo funciona
              </span>
            </span>
            <ChevronRight className="size-4 shrink-0 text-faint" />
          </Link>

          <div className="mt-auto border-line border-t pt-5">
            <Button variant="ghost" onClick={logout}>
              <LogOut className="size-4" />
              Cerrar sesión
            </Button>
          </div>
        </Card>
      </div>
    </AppShell>
  );
}

function Detail({
  icon: Icon,
  label,
  value,
  note,
}: {
  icon: typeof AtSign;
  label: string;
  value: string;
  note: string;
}) {
  return (
    <div className="flex items-start gap-3">
      <span
        aria-hidden
        className="mt-0.5 grid size-9 shrink-0 place-items-center rounded-xl border border-line bg-ink"
      >
        <Icon className="size-4 text-cyan" />
      </span>
      <div className="min-w-0">
        <p className="text-muted text-xs uppercase tracking-wider">{label}</p>
        <p className="mt-1 truncate">{value}</p>
        <p className="mt-1 text-faint text-xs">{note}</p>
      </div>
    </div>
  );
}

/** The avatar stands in for a picture there is no way to upload. */
function initialOf(name: string | null | undefined, email: string): string {
  const source = name?.trim() || email;
  return source.slice(0, 1).toUpperCase();
}
