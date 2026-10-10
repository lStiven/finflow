import { ArrowLeft, ArrowRight, CircleHelp } from "lucide-react";
import { type ReactNode, useCallback, useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/Button";
import { cn } from "@/lib/cn";

export type TutorialSlide = {
  /** One action, said as an instruction. */
  title: string;
  caption?: ReactNode;
  illustration: ReactNode;
  /** The one thing that tends to go wrong here, kept out of the way. */
  help?: { question: string; answer: ReactNode };
};

/** How far a finger has to travel sideways before it counts as a swipe. */
const SWIPE_PX = 48;

/**
 * One idea at a time, each with its picture.
 *
 * Born as the connect guide's Gmail walkthrough — one action per slide, the
 * spot on Gmail's screen lit — and shared since with the guides that show a
 * flow in motion: how a movement arrives, what a transfer does. Each slide's
 * illustration remounts as it arrives, so its animation plays when it is
 * reached rather than all at once. Arrows, dots, a swipe or the arrow keys
 * move between them; none of it locks, and nothing advances on its own:
 * somebody reading is never overtaken.
 */
export function Tutorial({
  label,
  slides,
}: {
  /** What the whole sequence is, for a screen reader. */
  label: string;
  slides: TutorialSlide[];
}) {
  const [view, setView] = useState<{ index: number; direction: "forward" | "back" }>({
    index: 0,
    direction: "forward",
  });
  const region = useRef<HTMLElement>(null);
  const last = slides.length - 1;
  const { index, direction } = view;

  /** To a slide, by position (`to`) or by how far from this one (`by`). */
  const move = useCallback(
    (target: { to: number } | { by: number }) =>
      setView((current) => {
        const wanted = "to" in target ? target.to : current.index + target.by;
        const next = Math.min(Math.max(wanted, 0), last);
        return next === current.index
          ? current
          : { index: next, direction: next > current.index ? "forward" : "back" };
      }),
    [last],
  );
  const go = (to: number) => move({ to });

  // Native listeners rather than JSX handlers: the region is not a control,
  // and these only add a shortcut to the buttons that already exist.
  useEffect(() => {
    const element = region.current;
    if (!element) return;

    let startX: number | null = null;
    let startY = 0;

    function onKey(event: KeyboardEvent) {
      if (event.target instanceof HTMLInputElement) return;
      if (event.key === "ArrowRight") move({ by: 1 });
      if (event.key === "ArrowLeft") move({ by: -1 });
    }
    function onDown(event: PointerEvent) {
      if (event.pointerType === "mouse") return;
      startX = event.clientX;
      startY = event.clientY;
    }
    function onUp(event: PointerEvent) {
      if (startX === null) return;
      const dx = event.clientX - startX;
      const dy = event.clientY - startY;
      startX = null;
      // Mostly sideways, or it was somebody scrolling the page.
      if (Math.abs(dx) < SWIPE_PX || Math.abs(dx) < Math.abs(dy) * 1.5) return;
      move({ by: dx < 0 ? 1 : -1 });
    }

    element.addEventListener("keydown", onKey);
    element.addEventListener("pointerdown", onDown);
    element.addEventListener("pointerup", onUp);
    return () => {
      element.removeEventListener("keydown", onKey);
      element.removeEventListener("pointerdown", onDown);
      element.removeEventListener("pointerup", onUp);
    };
  }, [move]);

  const slide = slides[index];
  if (!slide) return null;

  return (
    <section
      ref={region}
      aria-roledescription="carrusel"
      aria-label={label}
      className="overflow-hidden rounded-2xl border border-line bg-surface"
    >
      <div className="touch-pan-y">
        <div
          key={index}
          className={direction === "back" ? "step-in-back" : "step-in-forward"}
        >
          <div className="bg-gradient-to-b from-ink to-surface px-4 pt-5 pb-7 sm:px-8 sm:pt-7 sm:pb-9">
            <div className="mx-auto max-w-md">{slide.illustration}</div>
          </div>
          <div className="px-4 pb-5 sm:px-6">
            <p className="text-faint text-xs tabular">
              {index + 1} de {slides.length}
            </p>
            <h3 className="mt-1 text-pretty font-medium text-base">{slide.title}</h3>
            {slide.caption ? (
              <div className="mt-1.5 text-pretty text-muted text-sm leading-relaxed">
                {slide.caption}
              </div>
            ) : null}
            {slide.help ? (
              <details className="group mt-3">
                <summary className="inline-flex min-h-9 cursor-pointer list-none items-center gap-1.5 rounded-lg text-cyan text-sm [&::-webkit-details-marker]:hidden">
                  <CircleHelp className="size-4" aria-hidden />
                  {slide.help.question}
                </summary>
                <div className="rise mt-1 rounded-xl border border-line bg-ink p-3 text-muted text-sm leading-relaxed">
                  {slide.help.answer}
                </div>
              </details>
            ) : null}
          </div>
        </div>
      </div>

      <div className="flex items-center justify-between gap-2 border-line border-t px-2 py-2 sm:px-3">
        <Button
          variant="quiet"
          onClick={() => go(index - 1)}
          disabled={index === 0}
          className="px-3"
        >
          <ArrowLeft className="size-4" aria-hidden />
          <span className="max-sm:sr-only">Anterior</span>
        </Button>

        <div className="flex items-center">
          {slides.map((each, at) => (
            <button
              key={each.title}
              type="button"
              onClick={() => go(at)}
              aria-label={`Ir al paso ${at + 1}: ${each.title}`}
              aria-current={at === index ? "step" : undefined}
              className="grid size-7 place-items-center rounded-full"
            >
              <span
                aria-hidden
                className={cn(
                  "block h-1.5 rounded-full transition-all duration-300",
                  at === index
                    ? "w-5 bg-accent"
                    : at < index
                      ? "w-1.5 bg-muted"
                      : "w-1.5 bg-line",
                )}
              />
            </button>
          ))}
        </div>

        <Button
          variant={index === last ? "quiet" : "ghost"}
          onClick={() => go(index + 1)}
          disabled={index === last}
          className="px-3"
        >
          <span className="max-sm:sr-only">Siguiente</span>
          <ArrowRight className="size-4" aria-hidden />
        </Button>
      </div>

      <p className="sr-only" aria-live="polite">
        {`Paso ${index + 1} de ${slides.length}: ${slide.title}`}
      </p>
    </section>
  );
}
