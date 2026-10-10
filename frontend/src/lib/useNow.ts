import { useEffect, useState } from "react";
import { nowInSeconds } from "@/lib/dates";

/**
 * The current time in epoch seconds, re-read every `intervalMs`.
 *
 * For a "hace 3 minutos" that has to keep counting while nothing else on the
 * screen changes — a wait, most of all, where a frozen figure reads as a
 * frozen page.
 */
export function useNow(intervalMs: number): number {
  const [now, setNow] = useState(nowInSeconds);

  useEffect(() => {
    const id = window.setInterval(() => setNow(nowInSeconds()), intervalMs);
    return () => window.clearInterval(id);
  }, [intervalMs]);

  return now;
}
