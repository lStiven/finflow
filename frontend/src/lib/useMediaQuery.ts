import { useEffect, useState } from "react";

/**
 * Whether a media query matches, kept current as it changes.
 *
 * For the decisions CSS cannot make on its own — what a button does, not how
 * it looks — such as offering to send a link to a computer only to somebody
 * holding a phone.
 */
export function useMediaQuery(query: string): boolean {
  const [matches, setMatches] = useState(() =>
    typeof window === "undefined" ? false : window.matchMedia(query).matches,
  );

  useEffect(() => {
    const list = window.matchMedia(query);
    const update = () => setMatches(list.matches);
    update();
    list.addEventListener("change", update);
    return () => list.removeEventListener("change", update);
  }, [query]);

  return matches;
}
