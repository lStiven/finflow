import { Check, Monitor, Send } from "lucide-react";
import { useState } from "react";
import { Button } from "@/components/ui/Button";
import { useMediaQuery } from "@/lib/useMediaQuery";

/**
 * Said only on a touch screen, at the two steps that happen in Gmail.
 *
 * Google lets forwarding be set up from a computer and not from the Gmail
 * app, so on a phone the useful thing is not a page of instructions but a
 * way to carry this exact step to the computer: the share sheet where there
 * is one, the clipboard otherwise. The link keeps the step it was sent from.
 */
export function DesktopHint() {
  const touch = useMediaQuery("(pointer: coarse)");
  const [sent, setSent] = useState<"shared" | "copied" | null>(null);

  if (!touch) return null;

  const canShare = typeof navigator !== "undefined" && "share" in navigator;

  async function send() {
    const url = window.location.href;
    if (canShare) {
      try {
        await navigator.share({ title: "Conectar mi banco con Finflow", url });
        setSent("shared");
        return;
      } catch (error) {
        // Closing the share sheet is an answer, not a failure.
        if (error instanceof DOMException && error.name === "AbortError") return;
      }
    }
    try {
      await navigator.clipboard.writeText(url);
      setSent("copied");
    } catch {
      setSent(null);
    }
  }

  return (
    <div className="flex items-start gap-3 rounded-2xl border border-cyan/25 bg-cyan/6 p-4">
      <Monitor className="mt-0.5 size-5 shrink-0 text-cyan" aria-hidden />
      <div className="min-w-0 flex-1">
        <p className="font-medium text-sm">Este paso se hace en un computador</p>
        <p className="mt-1 text-muted text-sm leading-relaxed">
          La app de Gmail no tiene esta opción. Abre esta página en tu computador y
          sigue desde ahí.
        </p>
        <Button variant="ghost" onClick={send} className="mt-3 px-3 py-2">
          {sent ? (
            <Check className="pop size-4 text-incoming" aria-hidden />
          ) : (
            <Send className="size-4" aria-hidden />
          )}
          {sent === "copied"
            ? "Enlace copiado"
            : sent === "shared"
              ? "Enlace enviado"
              : canShare
                ? "Enviarme el enlace"
                : "Copiar el enlace"}
        </Button>
      </div>
    </div>
  );
}
