import { describe, expect, it, vi } from "vitest";
import { intervalFor, previousRange, resolveRange } from "@/lib/periods";

/** 2026-09-02 12:00 in Bogotá. */
const NOW_MS = 1_788_368_400_000;
const AUGUST_START = 1_785_560_400;
const SEPTEMBER_START = 1_788_238_800;
const JULY_START = 1_782_882_000;
const DAY = 86_400;

function at<T>(millis: number, run: () => T): T {
  vi.useFakeTimers();
  vi.setSystemTime(millis);
  try {
    return run();
  } finally {
    vi.useRealTimers();
  }
}

describe("resolveRange", () => {
  it("ends the running month at now, not at the end of the month", () => {
    /*
     * This is what makes `compare` honest. A window covering the whole of
     * September would be compared against the whole of August, and on the 2nd
     * that reports spending down by 90-odd percent every single time.
     */
    const range = at(NOW_MS, () => resolveRange("mes"));

    expect(range?.from).toBe(SEPTEMBER_START);
    expect(range?.to).toBe(NOW_MS / 1000);
    expect(range?.to).toBeLessThan(SEPTEMBER_START + 31 * DAY);
  });

  it("compares a running month against the same stretch of the one before", () => {
    const range = at(NOW_MS, () => resolveRange("mes"));
    if (!range) throw new Error("no range");
    const before = previousRange(range);

    // Ends exactly where September began, and covers the same elapsed length.
    expect(before.to).toBe(SEPTEMBER_START);
    expect(before.to - before.from).toBe(range.to - range.from);
  });

  it("gives the finished month whole ends, both of them real month bounds", () => {
    const range = at(NOW_MS, () => resolveRange("mes-pasado"));

    expect(range).toEqual({ from: AUGUST_START, to: SEPTEMBER_START });
  });

  it("counts a multi-month preset back to the start of a month", () => {
    const range = at(NOW_MS, () => resolveRange("3-meses"));

    // July, August, September — three months, opening on the 1st of July.
    expect(range?.from).toBe(JULY_START);
  });

  it("starts the year preset at the first of January", () => {
    const range = at(NOW_MS, () => resolveRange("ano"));

    expect(range?.from).toBe(1_767_243_600); // 2026-01-01 00:00 Bogotá
  });

  it("includes the last day a custom range names", () => {
    // "Hasta el 15" has to cover the 15th, so the exclusive bound is the 16th.
    const range = resolveRange("custom", { from: "2026-08-01", to: "2026-08-15" });

    expect(range?.from).toBe(AUGUST_START);
    expect(range?.to).toBe(AUGUST_START + 15 * DAY);
  });

  it("refuses a custom range that is missing a side or runs backwards", () => {
    expect(resolveRange("custom", { from: "2026-08-01" })).toBeNull();
    expect(resolveRange("custom", {})).toBeNull();
    expect(resolveRange("custom", { from: "2026-08-15", to: "2026-08-01" })).toBeNull();
  });
});

describe("intervalFor", () => {
  it("steps by day over a month, by week over a quarter, by month over a year", () => {
    expect(intervalFor({ from: 0, to: 20 * DAY })).toBe("day");
    expect(intervalFor({ from: 0, to: 90 * DAY })).toBe("week");
    expect(intervalFor({ from: 0, to: 365 * DAY })).toBe("month");
  });
});
