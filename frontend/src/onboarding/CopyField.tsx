import { Check, Copy } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/Button";
import { cn } from "@/lib/cn";

type CopyState = "idle" | "copied" | "failed";

/**
 * Copies text and says so, for a moment.
 *
 * The clipboard can be refused outright — an insecure origin, a permission
 * prompt denied — and that is reported rather than swallowed, so the screen
 * can fall back to selecting the text for a manual copy.
 */
export function useCopy(resetAfterMs = 2200) {
  const [state, setState] = useState<CopyState>("idle");
  const timer = useRef<number | undefined>(undefined);

  useEffect(() => () => window.clearTimeout(timer.current), []);

  const copy = useCallback(
    async (value: string): Promise<boolean> => {
      window.clearTimeout(timer.current);
      try {
        await navigator.clipboard.writeText(value);
        setState("copied");
        timer.current = window.setTimeout(() => setState("idle"), resetAfterMs);
        return true;
      } catch {
        setState("failed");
        return false;
      }
    },
    [resetAfterMs],
  );

  return { state, copy };
}

function selectContents(element: HTMLElement | null) {
  if (!element) return;
  const range = document.createRange();
  range.selectNodeContents(element);
  const selection = window.getSelection();
  selection?.removeAllRanges();
  selection?.addRange(range);
}

/**
 * Something to paste into Gmail, and the button that copies it.
 *
 * The text stays visible and selectable whatever happens: it is what gets
 * compared against what Gmail shows, and a copy button that hid it would
 * leave nothing to check against.
 */
export function CopyField({
  label,
  value,
  copyLabel = "Copiar",
  copiedLabel = "¡Copiado!",
  announce,
  emphasis = "ghost",
  onCopied,
  className,
}: {
  label: string;
  value: string;
  copyLabel?: string;
  copiedLabel?: string;
  /** What a screen reader hears once it is copied. */
  announce: string;
  emphasis?: "primary" | "ghost";
  onCopied?: () => void;
  className?: string;
}) {
  const { state, copy } = useCopy();
  const text = useRef<HTMLElement>(null);

  async function onCopy() {
    if (await copy(value)) {
      onCopied?.();
    } else {
      // Selected, so a long-press or Ctrl+C finishes the job by hand.
      selectContents(text.current);
    }
  }

  const copied = state === "copied";

  return (
    <div className={className}>
      <p className="mb-2 text-faint text-xs uppercase tracking-wider">{label}</p>
      <div className="flex flex-col gap-2 sm:flex-row sm:items-stretch">
        <code
          ref={text}
          className="min-w-0 flex-1 select-all break-all rounded-xl border border-line bg-ink px-4 py-3 font-mono text-sm leading-relaxed"
        >
          {value}
        </code>
        <Button
          variant={copied ? "ghost" : emphasis}
          onClick={onCopy}
          className={cn(
            "shrink-0 sm:min-w-36",
            copied && "border-incoming/40 bg-incoming/10 text-incoming",
          )}
        >
          {copied ? (
            <Check key="copied" className="pop size-4" aria-hidden />
          ) : (
            <Copy key="copy" className="size-4" aria-hidden />
          )}
          {copied ? copiedLabel : copyLabel}
        </Button>
      </div>
      {state === "failed" ? (
        <p role="alert" className="mt-2 text-warn text-xs">
          Tu navegador no dejó copiar. Ya quedó seleccionado: cópialo a mano.
        </p>
      ) : null}
      <span role="status" className="sr-only">
        {copied ? announce : ""}
      </span>
    </div>
  );
}
