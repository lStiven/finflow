import { useEffect } from "react";

/**
 * Closes whatever is open over the page when Escape is pressed.
 *
 * The other half of `useScrollLock`, and kept beside it for the same reason:
 * everything that covers the screen has to behave the same way, and the way
 * to make sure of that is for there to be one place where it is decided. The
 * phone's "Más" sheet had this inline and the two onboarding dialogs did not,
 * so the app answered Escape on one overlay out of three.
 *
 * Escape is not a nicety on a modal. A dialog that traps the pointer and
 * ignores the one key everybody presses to get out of a dialog reads as
 * frozen — and somebody navigating by keyboard has no backdrop to click.
 *
 * **Only the topmost overlay is dismissed**, which is the whole reason this
 * is a stack rather than a listener per overlay. `AppShell` mounts the two
 * onboarding dialogs beside the "Más" sheet, and the onboarding state is
 * polled, so a celebration can appear while the sheet is open: with a
 * listener each, one Escape would close the sheet *and* acknowledge a dialog
 * that is only ever shown once — burned without anybody having read it.
 * `stopPropagation` cannot fix that, because listeners on the same target
 * and phase all run regardless; a stack can, and `useScrollLock` next door
 * already counts its depth for the same kind of reason.
 */

const stack: Array<() => void> = [];

function onKey(event: KeyboardEvent): void {
  if (event.key !== "Escape") return;

  stack.at(-1)?.();
}

/**
 * @param enabled Off is a real state, like `useScrollLock`: the dialogs
 * decide whether they exist after their hooks have run, so the condition is
 * an argument rather than a hook that sometimes runs.
 */
export function useDismissOnEscape(onDismiss: () => void, enabled = true): void {
  useEffect(() => {
    if (!enabled) return;

    // `capture` so it fires before anything inside the overlay can swallow
    // the key: a field that stops `keydown` from bubbling would make Escape
    // work everywhere except while typing, which is exactly when it is most
    // wanted.
    if (stack.length === 0) {
      window.addEventListener("keydown", onKey, { capture: true });
    }

    stack.push(onDismiss);

    return () => {
      const at = stack.lastIndexOf(onDismiss);
      if (at !== -1) stack.splice(at, 1);

      if (stack.length === 0) {
        window.removeEventListener("keydown", onKey, { capture: true });
      }
    };
  }, [enabled, onDismiss]);
}
