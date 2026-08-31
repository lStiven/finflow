/**
 * The moving ground behind the login and register screens.
 *
 * Deliberately the one place in the app that does this. Everything past the
 * door follows the 80/20 rule and stays still; the door is where somebody is
 * waiting on a form rather than reading a figure, so it is the one screen a
 * little motion costs nothing.
 *
 * Purely decorative and marked as such: nothing here is focusable, nothing
 * carries meaning, and every layer animates on transform or opacity alone so
 * it never triggers layout. `prefers-reduced-motion` is handled globally in
 * `index.css` — with no fill mode on any of these animations, each layer
 * simply rests where its own class puts it, which is a composed still rather
 * than a frozen half-frame.
 */
export function NeonBackdrop() {
  return (
    <div
      aria-hidden
      className="pointer-events-none fixed inset-0 -z-10 overflow-hidden bg-ink"
    >
      <div className="neon-grid absolute inset-0" />

      <div className="orb orb-a orb-magenta -left-40 -top-52 absolute size-[42rem]" />
      <div className="orb orb-b orb-violet -right-52 absolute top-[15%] size-[38rem]" />
      <div className="orb orb-c orb-cyan -bottom-56 absolute left-[20%] size-[34rem]" />

      <div className="neon-beam -inset-y-40 absolute left-1/3 w-48" />
      <div className="neon-vignette absolute inset-0" />
    </div>
  );
}
