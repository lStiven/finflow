import { useEffect, useState } from "react";

/**
 * Whether the viewer asked for less motion.
 *
 * The CSS reset covers transitions and keyframes, but an animation driven in
 * JavaScript — a figure counting up — has to ask for itself.
 */
export function useReducedMotion(): boolean {
  const [reduced, setReduced] = useState(() =>
    typeof window === "undefined"
      ? true
      : window.matchMedia("(prefers-reduced-motion: reduce)").matches,
  );

  useEffect(() => {
    const query = window.matchMedia("(prefers-reduced-motion: reduce)");
    const update = () => setReduced(query.matches);
    update();
    query.addEventListener("change", update);
    return () => query.removeEventListener("change", update);
  }, []);

  return reduced;
}
