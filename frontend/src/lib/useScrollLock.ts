import { useEffect } from "react";

/**
 * Holds the page still while something is open over it.
 *
 * A `fixed inset-0` overlay covers the screen but does nothing to the
 * document underneath: on a phone the backdrop takes the tap and the page
 * still slides under the finger, which reads as the sheet having come loose
 * from the screen it opened on.
 *
 * `overflow: hidden` on its own is not enough — Safari on iOS goes on
 * scrolling the body by touch regardless — so the body is taken out of flow
 * at the offset it was already at, and put back, offset included, on the way
 * out. Restoring it is the whole reason this is a hook rather than two lines
 * at the top of each overlay.
 *
 * Counted, because two overlays can be up at once: `WelcomeDialog` renders
 * from the shell, over whatever else is open. Without the count the first one
 * to close would hand the page back while the second was still covering it.
 */

let depth = 0;
let release: (() => void) | null = null;

function lock(): () => void {
  const { body, documentElement: root } = document;
  const offset = window.scrollY;
  // What the scrollbar takes up, and is about to stop taking up. Unpaid for,
  // the whole page shifts sideways the moment an overlay opens on a desktop.
  const gap = window.innerWidth - root.clientWidth;
  // Saved whole rather than property by property: nothing else writes inline
  // styles on the body, so the string is both the simpler restore and the
  // complete one.
  const before = body.style.cssText;

  body.style.position = "fixed";
  body.style.top = `${-offset}px`;
  body.style.width = "100%";
  body.style.overflow = "hidden";
  if (gap > 0) body.style.paddingRight = `${gap}px`;

  return () => {
    body.style.cssText = before;
    window.scrollTo(0, offset);
  };
}

/**
 * @param enabled Off is a real state: the two onboarding dialogs decide
 * whether they exist after their hooks have run, so they pass the condition
 * in rather than calling this behind an early return.
 */
export function useScrollLock(enabled = true): void {
  useEffect(() => {
    if (!enabled) return;

    depth += 1;
    if (depth === 1) release = lock();

    return () => {
      depth -= 1;
      if (depth === 0) {
        release?.();
        release = null;
      }
    };
  }, [enabled]);
}
