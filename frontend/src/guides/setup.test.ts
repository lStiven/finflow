import { describe, expect, it } from "vitest";
import { progressOf, setupTasks } from "@/guides/setup";

const NOTHING = { connected: false, accounts: 0, budgets: 0, alerts: false };

describe("what is left before Finflow works on its own", () => {
  it("starts with everything pending, the bank first", () => {
    const tasks = setupTasks(NOTHING);

    expect(tasks.map((task) => task.id)).toEqual([
      "connect",
      "accounts",
      "budget",
      "alerts",
    ]);
    expect(tasks.every((task) => !task.done)).toBe(true);
    expect(progressOf(tasks)).toEqual({ done: 0, total: 4 });
  });

  it("marks done only what the server says exists", () => {
    const tasks = setupTasks({ ...NOTHING, accounts: 2, budgets: 1 });

    expect(tasks.filter((task) => task.done).map((task) => task.id)).toEqual([
      "accounts",
      "budget",
    ]);
    expect(progressOf(tasks)).toEqual({ done: 2, total: 4 });
  });

  it("says which ones the app works without", () => {
    expect(
      setupTasks(NOTHING)
        .filter((task) => task.optional)
        .map((task) => task.id),
    ).toEqual(["budget", "alerts"]);
  });
});
