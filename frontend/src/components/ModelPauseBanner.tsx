import { useQuery } from "@tanstack/react-query";
import { PauseCircle } from "lucide-react";
import { pausedNotice, REVOKED_NOTICE } from "@/alerts/modelPause";
import { modelAccessQuery } from "@/api/queries";

/**
 * The notice that the model stopped reading this account's unrecognised mail.
 *
 * On every signed-in screen while it lasts, drawn from the record the parse
 * worker enforces rather than from the alert that announced it — so it is
 * right even for somebody who never saw that alert. Not a link: there is
 * nothing on screen that lifts it, and a banner that leads nowhere teaches
 * people to tap it anyway.
 */
export function ModelPauseBanner() {
  const { data } = useQuery(modelAccessQuery);

  if (!data || data.access === "open") return null;

  const text =
    data.access === "paused" && data.paused_until !== null
      ? pausedNotice(data.paused_until)
      : REVOKED_NOTICE;

  return (
    <div
      role="status"
      className="surface rise mb-6 flex items-start gap-4 rounded-card border border-warn/30 bg-gradient-to-r from-warn/10 to-transparent p-4"
    >
      <span
        aria-hidden
        className="grid size-10 shrink-0 place-items-center rounded-xl bg-warn/15 text-warn ring-1 ring-warn/30"
      >
        <PauseCircle className="size-4" />
      </span>
      <span className="min-w-0 flex-1">
        <span className="block font-medium text-sm">
          {data.access === "paused" ? "Lectura pausada" : "Lectura desactivada"}
        </span>
        <span className="mt-0.5 block text-muted text-sm leading-relaxed">{text}</span>
      </span>
    </div>
  );
}
