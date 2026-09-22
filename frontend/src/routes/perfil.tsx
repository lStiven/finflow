import { useQuery, useSuspenseQuery } from "@tanstack/react-query";
import { createFileRoute, Link, redirect } from "@tanstack/react-router";
import {
  AtSign,
  Bell,
  Check,
  ChevronRight,
  Clock3,
  KeyRound,
  Loader2,
  LogOut,
  Mail,
  Send,
  ShieldCheck,
  User,
} from "lucide-react";
import { type SubmitEvent, useState } from "react";
import {
  channelState,
  formatMinimumAmount,
  linkedChannel,
  movementPreference,
  parseMinimumAmount,
} from "@/alerts/channels";
import { ApiError } from "@/api/client";
import type { AlertChannel } from "@/api/queries";
import {
  alertChannelsQuery,
  profileQuery,
  useCreateAlertChannel,
  useDeleteAlertChannel,
  useUpdateAlertPreference,
  useUpdateProfile,
} from "@/api/queries";
import { useAuth } from "@/auth/AuthContext";
import { identityErrorMessage } from "@/auth/errors";
import { LENGTH_MESSAGE, passwordChangeIssue } from "@/auth/password";
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
          Cómo te llamamos, con qué correo entras y con qué contraseña.
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

        <TelegramCard />
        <PasswordCard />
      </div>
    </AppShell>
  );
}

/**
 * Avisos por Telegram: vincular el canal, y decidir qué avisar.
 *
 * Vincular es un toque, no un formulario. El enlace abre el bot con un token
 * de un solo uso en `?start=`, y quien lo toca prueba con eso que el chat es
 * suyo — no hace falta que nadie averigüe su chat_id ni teclee un código.
 *
 * Ese token se devuelve una sola vez y vive únicamente aquí, en estado de
 * React: guardarlo en `localStorage` dejaría una credencial viva en el
 * dispositivo, y `GET /alerts/channels` no lo devuelve nunca. Si se pierde,
 * se pide otro.
 *
 * La confirmación ocurre dentro de Telegram, donde esta pantalla no ve nada,
 * así que pregunta cada pocos segundos hasta que el canal aparece vinculado —
 * y deja de preguntar en cuanto lo está.
 */
function TelegramCard() {
  const { data } = useQuery(alertChannelsQuery);
  const create = useCreateAlertChannel();
  const update = useUpdateAlertPreference();
  const remove = useDeleteAlertChannel();

  const [link, setLink] = useState<string | null>(null);
  const [minimum, setMinimum] = useState<string | null>(null);
  const [minimumError, setMinimumError] = useState<string | null>(null);
  // Every write here reports its own failure. Believing an unlink that did
  // not happen is the bad one: the person walks away thinking the alerts
  // stopped, and they keep arriving.
  const [error, setError] = useState<string | null>(null);

  const channels = data?.channels ?? [];
  const state = channelState(channels);
  const linked = linkedChannel(channels);
  const preference = movementPreference(linked);

  // El enlace deja de tener sentido en cuanto el canal queda vinculado.
  if (state === "linked" && link !== null) setLink(null);

  const minimumText = minimum ?? formatMinimumAmount(preference);

  async function guard(work: () => Promise<unknown>) {
    setError(null);
    try {
      await work();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Algo salió mal");
    }
  }

  async function onLink() {
    setLink(null);
    await guard(async () => {
      const created = await create.mutateAsync();
      setLink(created.link_url);
      window.open(created.link_url, "_blank", "noopener,noreferrer");
    });
  }

  async function onToggle(enabled: boolean) {
    if (!linked) return;
    await guard(() =>
      update.mutateAsync({
        channelId: linked.channel_id,
        body: {
          alert_type: "movement",
          enabled,
          minimum_amount: preference?.minimum_amount ?? null,
          minimum_currency: preference?.minimum_currency ?? "COP",
        },
      }),
    );
  }

  async function onSaveMinimum() {
    if (!linked) return;
    const parsed = parseMinimumAmount(minimumText);
    if (parsed === undefined) {
      setMinimumError("Escribe una cifra, por ejemplo 20.000.");
      return;
    }
    setMinimumError(null);
    await guard(async () => {
      await update.mutateAsync({
        channelId: linked.channel_id,
        body: {
          alert_type: "movement",
          enabled: preference?.enabled ?? true,
          minimum_amount: parsed,
          minimum_currency: "COP",
        },
      });
      setMinimum(null);
    });
  }

  async function onUnlink() {
    if (!linked) return;
    await guard(() => remove.mutateAsync(linked.channel_id));
  }

  return (
    <Card glow="violet" lift={false} className="flex flex-col gap-5 lg:col-span-2">
      <div className="flex items-start gap-3">
        <span
          aria-hidden
          className="mt-0.5 grid size-9 shrink-0 place-items-center rounded-xl border border-line bg-ink"
        >
          <Send className="size-4 text-violet" />
        </span>
        <div className="min-w-0">
          <h2 className="font-medium">Avisos por Telegram</h2>
          <p className="mt-1 text-faint text-xs">
            Cada movimiento te llega al teléfono segundos después, sin abrir la app.{" "}
            <Link
              to="/guias/avisos"
              className="text-violet underline underline-offset-2"
            >
              Cómo funciona
            </Link>
          </p>
        </div>
        {linked ? (
          <span className="ml-auto flex shrink-0 items-center gap-1.5 text-incoming text-xs">
            <ShieldCheck className="size-4" />
            Vinculado
          </span>
        ) : null}
      </div>

      {state === "linked" && linked ? (
        <div className="flex flex-col gap-4">
          <Detail
            icon={Send}
            label="Chat"
            value={describeChat(linked)}
            note="De la dirección solo guardamos el final; no hace falta más."
          />

          <label className="flex items-center justify-between gap-3 rounded-xl border border-line bg-ink p-3.5">
            <span className="min-w-0">
              <span className="block font-medium text-sm">Movimientos</span>
              <span className="block text-faint text-xs">
                Cada gasto y cada ingreso que entre.
              </span>
            </span>
            <input
              type="checkbox"
              className="size-5 shrink-0 accent-violet"
              checked={preference?.enabled ?? true}
              disabled={update.isPending}
              onChange={(e) => void onToggle(e.target.checked)}
            />
          </label>

          <div className="flex flex-col gap-2">
            <Field
              label="No avisar por debajo de"
              icon={Bell}
              inputMode="numeric"
              placeholder="20.000"
              hint="Déjalo vacío para que te avise de todo. Un café de $3.000 gasta la atención que necesita un cargo de $400.000."
              value={minimumText}
              onChange={(e) => {
                setMinimum(e.target.value);
                setMinimumError(null);
              }}
            />
            {minimumError ? (
              <p role="alert" className="text-outgoing text-sm">
                {minimumError}
              </p>
            ) : null}
            <div>
              <Button
                variant="ghost"
                disabled={update.isPending || minimum === null}
                onClick={() => void onSaveMinimum()}
              >
                {update.isPending ? (
                  <>
                    <Loader2 className="size-4 animate-spin" />
                    Guardando…
                  </>
                ) : (
                  "Guardar mínimo"
                )}
              </Button>
            </div>
          </div>

          {error ? (
            <p role="alert" className="text-outgoing text-sm">
              {error}
            </p>
          ) : null}

          <div className="border-line border-t pt-4">
            <Button
              variant="ghost"
              disabled={remove.isPending}
              onClick={() => void onUnlink()}
            >
              <LogOut className="size-4" />
              Desvincular
            </Button>
          </div>
        </div>
      ) : (
        <div className="flex flex-col gap-3">
          <Button disabled={create.isPending} onClick={() => void onLink()}>
            {create.isPending ? (
              <>
                <Loader2 className="size-4 animate-spin" />
                Preparando…
              </>
            ) : (
              <>
                <Send className="size-4" />
                Conectar Telegram
              </>
            )}
          </Button>

          {link ? (
            <div className="rounded-xl border border-line bg-ink p-3.5">
              <p className="flex items-center gap-2 text-sm">
                <Loader2 className="size-4 animate-spin text-violet" />
                Esperando a que pulses Empezar en Telegram…
              </p>
              <p className="mt-2 text-faint text-xs">
                Si no se abrió solo,{" "}
                <a
                  href={link}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-violet underline underline-offset-2"
                >
                  abre el bot aquí
                </a>
                . El enlace sirve una sola vez y vence en unos minutos; si se pasa,
                vuelve a pulsar Conectar.
              </p>
            </div>
          ) : null}

          {error ? (
            <p role="alert" className="text-outgoing text-sm">
              {error}
            </p>
          ) : null}
        </div>
      )}
    </Card>
  );
}

/**
 * Changing the password from inside the app.
 *
 * The current password is asked for even though this page is already behind a
 * session: a laptop left open on somebody's desk must not be enough to take
 * the account over. The API enforces that; the field is here because a form
 * that did not ask would look like it had been forgotten.
 *
 * What the copy has to get right is the consequence. The change ends every
 * session opened with the old password — on this device too, which is why the
 * API hands back a replacement token and `changePassword` adopts it. Said
 * plainly, that is a feature: it is how somebody who suspects their password
 * has leaked gets the other person out.
 */
function PasswordCard() {
  const { changePassword } = useAuth();

  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [changed, setChanged] = useState(false);
  const [busy, setBusy] = useState(false);

  const issue = passwordChangeIssue({ current, next, confirmation });
  // Only after a first attempt: telling somebody their password is too short
  // while they are still typing the second character is not help.
  const showIssue = error !== null && issue !== undefined;

  async function onSubmit(event: SubmitEvent<HTMLFormElement>) {
    event.preventDefault();
    setChanged(false);

    if (issue) {
      setError(issue);
      return;
    }

    setError(null);
    setBusy(true);
    try {
      await changePassword(current, next);
      setCurrent("");
      setNext("");
      setConfirmation("");
      setChanged(true);
    } catch (cause) {
      // A wrong current password is a 403, not a 401: the session is fine, and
      // the client must not read it as one that ended. Saying which of the two
      // it was is safe here — whoever is asking already holds the account.
      //
      // Everything else goes through the same function the login, the
      // recovery and the reset screens use. That is what makes a 429 here say
      // *how long* rather than "espera un momento": the wait is in a header,
      // and `identityErrorMessage` is the only place that reads it.
      setError(
        cause instanceof ApiError && cause.status === 403
          ? "La contraseña actual no es correcta."
          : identityErrorMessage(cause),
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card glow="accent" lift={false} className="flex flex-col gap-6 lg:col-span-2">
      <div className="flex items-start gap-3">
        <span
          aria-hidden
          className="mt-0.5 grid size-9 shrink-0 place-items-center rounded-xl border border-line bg-ink"
        >
          <KeyRound className="size-4 text-accent" />
        </span>
        <div>
          <h2 className="font-medium">Cambiar contraseña</h2>
          <p className="mt-1 text-faint text-xs">
            Al cambiarla se cierran todas las sesiones abiertas, incluidas las de otros
            dispositivos. Aquí seguirás dentro.
          </p>
        </div>
      </div>

      <form
        onSubmit={onSubmit}
        className="grid gap-5 sm:grid-cols-2 lg:grid-cols-3"
        // Tells password managers this is a change, not a sign-in, so they
        // offer to update the stored entry instead of filling the old one.
        autoComplete="off"
      >
        <Field
          label="Contraseña actual"
          icon={KeyRound}
          type="password"
          autoComplete="current-password"
          value={current}
          onChange={(e) => {
            setCurrent(e.target.value);
            setChanged(false);
          }}
        />
        <Field
          label="Contraseña nueva"
          icon={KeyRound}
          type="password"
          autoComplete="new-password"
          hint={LENGTH_MESSAGE}
          value={next}
          onChange={(e) => {
            setNext(e.target.value);
            setChanged(false);
          }}
        />
        <Field
          label="Repítela"
          icon={KeyRound}
          type="password"
          autoComplete="new-password"
          hint="Para que un dedazo no te deje fuera."
          value={confirmation}
          onChange={(e) => {
            setConfirmation(e.target.value);
            setChanged(false);
          }}
        />

        {error ? (
          <p
            role="alert"
            className="rise text-outgoing text-sm sm:col-span-2 lg:col-span-3"
          >
            {showIssue ? issue : error}
          </p>
        ) : null}

        <div className="flex items-center gap-3 sm:col-span-2 lg:col-span-3">
          <Button type="submit" disabled={busy}>
            {busy ? (
              <>
                <Loader2 className="size-4 animate-spin" />
                Cambiando…
              </>
            ) : (
              "Cambiar contraseña"
            )}
          </Button>

          {changed ? (
            <span
              role="status"
              className="rise flex items-center gap-1.5 text-incoming text-sm"
            >
              <ShieldCheck className="size-4" />
              Contraseña cambiada. Las demás sesiones se cerraron.
            </span>
          ) : null}
        </div>
      </form>
    </Card>
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

/**
 * Cómo se nombra el destino en pantalla.
 *
 * El nombre que la persona tiene en Telegram, y detrás el final de la
 * dirección — que es lo que de verdad identifica el chat cuando hay dos
 * cuentas con el mismo nombre, y lo único que esta app llegó a guardar.
 */
function describeChat(channel: AlertChannel): string {
  if (!channel.label) return channel.chat_hint ?? "Telegram";
  return channel.chat_hint ? `${channel.label} · ${channel.chat_hint}` : channel.label;
}

/** The avatar stands in for a picture there is no way to upload. */
function initialOf(name: string | null | undefined, email: string): string {
  const source = name?.trim() || email;
  return source.slice(0, 1).toUpperCase();
}
