import { describe, expect, it } from "vitest";
import {
  currentMonthKey,
  formatDate,
  formatDateTime,
  formatMonthKey,
  fromLocalInput,
  monthDayRange,
  monthRange,
  previousMonthKey,
  toLocalInput,
} from "@/lib/dates";

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

describe("month buckets", () => {
  it("names the month it is in Bogota, not in UTC", () => {
    // 2026-09-01T02:00:00Z is still 2026-08-31 at 21:00 in Bogota.
    const lateOnTheLastOfTheMonth = new Date(Date.UTC(2026, 8, 1, 2, 0, 0));
    expect(currentMonthKey(lateOnTheLastOfTheMonth)).toBe("2026-08");
  });

  it("rolls the year over at January", () => {
    expect(previousMonthKey("2026-01")).toBe("2025-12");
    expect(previousMonthKey("2026-08")).toBe("2026-07");
  });

  /**
   * The range has to start at midnight *in Bogota*, which is 05:00 UTC. Off
   * by those five hours, the first morning of every month lands in the
   * previous bucket and the donut disagrees with the list beside it.
   */
  it("spans the month in the display timezone", () => {
    const range = monthRange("2026-08");
    expect(range).not.toBeNull();
    expect(range?.from).toBe(Date.UTC(2026, 7, 1, 5, 0, 0) / 1000);
    expect(range?.to).toBe(Date.UTC(2026, 8, 1, 5, 0, 0) / 1000);
  });

  it("rolls into the next year for December", () => {
    expect(monthRange("2026-12")?.to).toBe(Date.UTC(2027, 0, 1, 5, 0, 0) / 1000);
  });

  it("refuses a malformed key rather than inventing a range", () => {
    expect(monthRange("nope")).toBeNull();
    expect(monthRange("2026-13")).toBeNull();
  });
});

describe("datetime-local round trip", () => {
  /**
   * The input carries no zone. Rendering in UTC and reading back as Bogota —
   * or the reverse — moves every movement by five hours each time the edit
   * form is opened and saved untouched.
   */
  it("renders an instant as the Bogota wall clock", () => {
    // 2026-08-20T17:00:00Z is 12:00 in Bogota.
    expect(toLocalInput(Date.UTC(2026, 7, 20, 17, 0, 0) / 1000)).toBe(
      "2026-08-20T12:00",
    );
  });

  it("reads a Bogota wall clock back to the same instant", () => {
    expect(fromLocalInput("2026-08-20T12:00")).toBe(
      Date.UTC(2026, 7, 20, 17, 0, 0) / 1000,
    );
  });

  it("survives a round trip untouched", () => {
    const instant = Date.UTC(2026, 0, 31, 3, 45, 0) / 1000;
    expect(fromLocalInput(toLocalInput(instant))).toBe(instant);
  });

  it("refuses a malformed value rather than guessing an instant", () => {
    expect(fromLocalInput("")).toBeNull();
    expect(fromLocalInput("20/08/2026")).toBeNull();
  });
});

describe("monthDayRange", () => {
  it("spans the whole month, both ends inclusive", () => {
    expect(monthDayRange("2026-08")).toEqual({
      from: "2026-08-01",
      to: "2026-08-31",
    });
  });

  it("knows how long February is on a leap year", () => {
    expect(monthDayRange("2028-02")?.to).toBe("2028-02-29");
    expect(monthDayRange("2026-02")?.to).toBe("2026-02-28");
  });

  it("refuses a malformed key rather than inventing a month", () => {
    expect(monthDayRange("2026-13")).toBeNull();
    expect(monthDayRange("nope")).toBeNull();
  });
});
