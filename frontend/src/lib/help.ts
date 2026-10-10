/**
 * Which screens' «Cómo funciona» somebody has already read and closed.
 *
 * In the browser, per account, like the connect guide's acknowledgements and
 * for the same reason: "I read this" is a claim nothing on the server can
 * check, so it does not belong next to facts the server proves. The cost is
 * accepted — another browser shows each explanation once more, and it never
 * replays a real step.
 */

/** Per account: two people on one laptop must not inherit each other's reads. */
function keyFor(userId: string): string {
  return `finflow.help.${userId}`;
}

/** What was stored, read defensively: anything odd degrades to "seen nothing". */
export function parseSeen(raw: string | null): string[] {
  if (!raw) return [];
  try {
    const parsed: unknown = JSON.parse(raw);
    return Array.isArray(parsed)
      ? parsed.filter((entry): entry is string => typeof entry === "string")
      : [];
  } catch {
    return [];
  }
}

/** The list with one more screen in it, never twice. */
export function withSeen(seen: readonly string[], id: string): string[] {
  return seen.includes(id) ? [...seen] : [...seen, id];
}

export function hasSeenHelp(userId: string, id: string): boolean {
  try {
    return parseSeen(window.localStorage.getItem(keyFor(userId))).includes(id);
  } catch {
    // A private window throws instead of answering. Showing the explanation
    // once more is the harmless way to be wrong.
    return false;
  }
}

export function markHelpSeen(userId: string, id: string): void {
  try {
    const seen = parseSeen(window.localStorage.getItem(keyFor(userId)));
    window.localStorage.setItem(keyFor(userId), JSON.stringify(withSeen(seen, id)));
  } catch {
    // Not remembered: it opens again next visit, and closes the same way.
  }
}
