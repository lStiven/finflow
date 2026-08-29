import { describe, expect, it } from "vitest";
import { formatDate, formatDateTime, formatMonthKey } from "@/lib/dates";

describe("dates", () => {
  /**
   * The backend groups months in Bogota time. If this rendered in the
   * browser's own zone, a purchase made at 8pm on the 31st would sit in one
   * month in the list and another in the summary.
   */
  it("renders in Bogota, not in the runtime's timezone", () => {
    // 2026-09-01T02:00:00Z is still 2026-08-31 at 21:00 in Bogota.
    const lateOnTheLastOfTheMonth = Date.UTC(2026, 8, 1, 2, 0, 0) / 1000;
    expect(formatDate(lateOnTheLastOfTheMonth)).toContain("31");
    expect(formatDate(lateOnTheLastOfTheMonth)).toContain("ago");
  });

  it("formats a timestamp with the time of day", () => {
    const noonInBogota = Date.UTC(2026, 7, 20, 17, 0, 0) / 1000;
    expect(formatDateTime(noonInBogota)).toContain("12:00");
  });

  it("turns a summary bucket key into a readable month", () => {
    expect(formatMonthKey("2026-08")).toBe("agosto de 2026");
  });

  it("returns a malformed key untouched rather than inventing a date", () => {
    expect(formatMonthKey("nope")).toBe("nope");
  });
});
