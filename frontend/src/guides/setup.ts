/**
 * What is left before Finflow works on its own, for the guides screen.
 *
 * Every item is answered by the server — the connection's own steps, the
 * accounts, the budgets, the alert channels — never by «I read this», which
 * is the connect guide's rule: nothing is marked done without evidence. The
 * screen says «Comprobado» for exactly that reason.
 */

export type SetupTaskId = "connect" | "accounts" | "budget" | "alerts";

export type SetupTask = {
  id: SetupTaskId;
  done: boolean;
  /** Worth doing, not needed for the app to work. */
  optional: boolean;
};

export type SetupEvidence = {
  /** The connection works on its own (`/ingestion/setup` `ready`). */
  connected: boolean;
  accounts: number;
  budgets: number;
  /** A Telegram chat is linked and verified. */
  alerts: boolean;
};

export function setupTasks(evidence: SetupEvidence): SetupTask[] {
  return [
    { id: "connect", done: evidence.connected, optional: false },
    { id: "accounts", done: evidence.accounts > 0, optional: false },
    { id: "budget", done: evidence.budgets > 0, optional: true },
    { id: "alerts", done: evidence.alerts, optional: true },
  ];
}

/** How many of the ones that matter are done, for «2 de 4». */
export function progressOf(tasks: readonly SetupTask[]): {
  done: number;
  total: number;
} {
  return { done: tasks.filter((task) => task.done).length, total: tasks.length };
}
