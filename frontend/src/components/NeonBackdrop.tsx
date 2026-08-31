/**
 * The moving ground behind every screen.
 *
 * One plasma, two strengths. On the door — login and register — it is the
 * point: four wide pools of pink, violet, cyan and magenta drifting and
 * turning across each other, visible enough to give the screen a personality
 * while somebody is waiting on a form rather than reading a figure. Past the
 * door the same ground survives at a tenth of that and a good deal slower,
 * masked away from the middle of the viewport so it only ever lights the
 * empty margins. What it should read as there is the room the numbers sit in,
 * not something happening on screen.
 *
 * Deliberately no particles, no sweeps, no pulsing: every layer only travels,
 * turns and breathes, on cycles long enough (25s to 37s at the door, over a
 * minute in the app, and each one walking its path out and back) that the
 * loop never announces itself.
 *
 * Purely decorative and marked as such: nothing here is focusable, nothing
 * carries meaning, and every layer animates on transform alone so it never
 * triggers layout. `prefers-reduced-motion` is handled globally in
 * `index.css` — with no fill mode on any of these animations, each layer
 * simply rests where its own class puts it, which is a composed still rather
 * than a frozen half-frame.
 *
 * Sizes and positions live in the stylesheet beside each pool's hue rather
 * than as utilities here: they are one decision per pool, and the gradients
 * cannot be expressed as utilities anyway.
 */

import { cn } from "@/lib/cn";

type Variant = "door" | "ambient";

export function NeonBackdrop({ variant = "door" }: { variant?: Variant }) {
  const door = variant === "door";

  return (
    <div
      aria-hidden
      className={cn(
        "plasma pointer-events-none fixed inset-0 -z-10 overflow-hidden",
        door ? "plasma-door" : "plasma-ambient",
      )}
    >
      <div className="plasma-blob plasma-pink" />
      <div className="plasma-blob plasma-violet" />
      <div className="plasma-blob plasma-cyan" />
      <div className="plasma-blob plasma-magenta" />

      {/*
       * The door only. Behind a signed-in screen the shade would darken the
       * very margins the ambient mask exists to light, and grain under a
       * table of figures is one more texture to read past.
       */}
      {door ? (
        <>
          <div className="plasma-shade absolute inset-0" />
          <div className="plasma-grain absolute inset-0" />
        </>
      ) : null}
    </div>
  );
}
