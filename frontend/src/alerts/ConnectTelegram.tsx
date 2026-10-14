import { useQuery } from "@tanstack/react-query";
import { Loader2, Send } from "lucide-react";
import { useState } from "react";
import { channelState } from "@/alerts/channels";
import { alertChannelsQuery, useCreateAlertChannel } from "@/api/queries";
import { Button } from "@/components/ui/Button";

/**
 * Connecting Telegram: one tap, then waiting for «Empezar».
 *
 * Shared by Perfil and the alerts guide, so the guide can be done rather
 * than read. The link opens the bot with a single-use token in `?start=`;
 * whoever taps it proves the chat is theirs, so nobody types an id or a
 * code. The token is returned once and lives only here, in React state —
 * `localStorage` would leave a live credential on the device, and the API
 * never returns it again. Lost, another is asked for, which retires it.
 *
 * Confirmation happens inside Telegram, where this page sees nothing, so the
 * channels query polls until the channel turns up verified; whoever renders
 * this stops rendering it at that point.
 */
export function ConnectTelegram() {
  const { data } = useQuery(alertChannelsQuery);
  const create = useCreateAlertChannel();
  const [link, setLink] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const pending = channelState(data?.channels ?? []) === "pending";

  async function onLink() {
    setLink(null);
    setError(null);
    try {
      const created = await create.mutateAsync();
      setLink(created.link_url);
      window.open(created.link_url, "_blank", "noopener,noreferrer");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Algo salió mal");
    }
  }

  return (
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
            {link || pending ? "Conectar otra vez" : "Conectar Telegram"}
          </>
        )}
      </Button>

      {link ? (
        <div className="rise rounded-xl border border-line bg-ink p-3.5">
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
            . Sirve una sola vez y vence a los 15 minutos.
          </p>
        </div>
      ) : pending ? (
        // A link from an earlier visit: its token is gone from this page,
        // so the honest offer is a fresh one, which retires the old.
        <p className="text-faint text-xs">
          Hay un enlace sin usar de antes. Pulsa «Conectar otra vez» para uno nuevo.
        </p>
      ) : null}

      {error ? (
        <p role="alert" className="text-outgoing text-sm">
          {error}
        </p>
      ) : null}
    </div>
  );
}
