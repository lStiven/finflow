import { describe, expect, it } from "vitest";
import { ApiError } from "@/api/errors";
import {
  type ExportOptions,
  exportErrorMessage,
  exportFileName,
  exportFilters,
  exportOptionsError,
  exportQuery,
  initialExportOptions,
  periodDays,
} from "@/lib/exporting";

/** 30 September 2026, 21:00 in Bogotá — already 1 October in UTC. */
const LATE_SEPTEMBER = new Date(Date.UTC(2026, 9, 1, 2, 0));

const BASE: ExportOptions = initialExportOptions({});

describe("initialExportOptions", () => {
  it("starts from what the list has selected", () => {
    expect(
      initialExportOptions({
        direction: "outgoing",
        account: "acc-1",
        category: "groceries",
        from: "2026-08-01",
        to: "2026-08-31",
        search: "exito",
      }),
    ).toMatchObject({
      period: "custom",
      from: "2026-08-01",
      to: "2026-08-31",
      direction: "outgoing",
      accountId: "acc-1",
      category: "groceries",
      search: "exito",
      transfers: "include",
    });
  });

  it("starts on this month when the list has no dates", () => {
    expect(BASE.period).toBe("this_month");
    expect(BASE.direction).toBe("all");
    expect(BASE.format).toBe("xlsx");
  });

  it("keeps a list that left transfers out, and never starts on only transfers", () => {
    expect(initialExportOptions({ transfers: "exclude" }).transfers).toBe("exclude");
    expect(initialExportOptions({ transfers: "only" }).transfers).toBe("include");
  });
});

describe("periodDays", () => {
  it("reads the month in Bogotá, not in UTC", () => {
    expect(periodDays("this_month", LATE_SEPTEMBER)).toEqual({
      from: "2026-09-01",
      to: "2026-09-30",
    });
  });

  it("knows last month, three months and the year", () => {
    expect(periodDays("last_month", LATE_SEPTEMBER)).toEqual({
      from: "2026-08-01",
      to: "2026-08-31",
    });
    expect(periodDays("last_3_months", LATE_SEPTEMBER)).toEqual({
      from: "2026-07-01",
      to: "2026-09-30",
    });
    expect(periodDays("this_year", LATE_SEPTEMBER)).toEqual({
      from: "2026-01-01",
      to: "2026-12-31",
    });
  });

  it("rolls last month back over January", () => {
    const january = new Date(Date.UTC(2027, 0, 15, 12));
    expect(periodDays("last_month", january)).toEqual({
      from: "2026-12-01",
      to: "2026-12-31",
    });
  });

  it("has no bounds for everything, and the form's for custom", () => {
    expect(periodDays("all", LATE_SEPTEMBER)).toEqual({});
    expect(
      periodDays("custom", LATE_SEPTEMBER, { from: "2026-05-02", to: "" }),
    ).toEqual({
      from: "2026-05-02",
      to: undefined,
    });
  });
});

describe("exportFilters", () => {
  it("turns inclusive days into the half-open instants the API reads", () => {
    const filters = exportFilters({ ...BASE, period: "last_month" }, LATE_SEPTEMBER);

    // 2026-08-01 00:00 and 2026-09-01 00:00 in Bogotá (UTC-5).
    expect(filters.from).toBe(Date.UTC(2026, 7, 1, 5) / 1000);
    expect(filters.to).toBe(Date.UTC(2026, 8, 1, 5) / 1000);
  });

  it("sends nothing for «all», and only what was chosen", () => {
    const filters = exportFilters(
      { ...BASE, period: "all", direction: "outgoing", transfers: "exclude" },
      LATE_SEPTEMBER,
    );

    expect(filters).toEqual({
      from: undefined,
      to: undefined,
      direction: "outgoing",
      account_id: undefined,
      category: undefined,
      transfers: "exclude",
      search: undefined,
      merchant_id: undefined,
      origin: undefined,
      unassigned: undefined,
    });
  });

  it("carries the list's other filters until they are dropped", () => {
    const withSearch = {
      ...BASE,
      period: "all" as const,
      search: "exito",
      origin: "manual" as const,
    };

    expect(exportFilters(withSearch).search).toBe("exito");
    expect(exportFilters(withSearch).origin).toBe("manual");
    expect(exportFilters({ ...withSearch, search: undefined }).search).toBeUndefined();
  });
});

describe("exportOptionsError", () => {
  it("accepts every preset", () => {
    expect(exportOptionsError(BASE)).toBeNull();
  });

  it("asks for a date when a custom range has none", () => {
    expect(
      exportOptionsError({ ...BASE, period: "custom", from: "", to: "" }),
    ).toContain("Elige");
  });

  it("refuses a range that ends before it starts", () => {
    expect(
      exportOptionsError({
        ...BASE,
        period: "custom",
        from: "2026-09-10",
        to: "2026-09-01",
      }),
    ).toContain("después");
  });

  it("accepts an open-ended custom range", () => {
    expect(
      exportOptionsError({ ...BASE, period: "custom", from: "2026-09-10", to: "" }),
    ).toBeNull();
  });
});

describe("exportQuery", () => {
  it("keeps every filter and drops what only a page needs", () => {
    expect(
      exportQuery({
        limit: 25,
        offset: 50,
        sort: "date",
        direction: "outgoing",
        category: "groceries",
        from: 1_787_500_000,
        to: 1_788_238_800,
        search: "exito",
      }),
    ).toEqual({
      direction: "outgoing",
      category: "groceries",
      from: 1_787_500_000,
      to: 1_788_238_800,
      search: "exito",
    });
  });
});

describe("exportFileName", () => {
  it("dates the file in Bogotá, not in UTC", () => {
    // 01:00 UTC on 1 September is still 31 August in Bogotá.
    const lateEvening = new Date(Date.UTC(2026, 8, 1, 1, 0));

    expect(exportFileName("xlsx", lateEvening)).toBe(
      "finflow-movimientos-2026-08-31.xlsx",
    );
    expect(exportFileName("csv", lateEvening)).toBe(
      "finflow-movimientos-2026-08-31.csv",
    );
  });
});

describe("exportErrorMessage", () => {
  it("asks for a shorter range only when the ceiling refused", () => {
    const ceiling = new ApiError(422, {
      detail: {
        code: "export_too_large",
        message: "12000 movements match",
        matched: 12_000,
        limit: 10_000,
      },
    });

    expect(exportErrorMessage(ceiling)).toContain("Acota las fechas");
    expect(exportErrorMessage(ceiling)).toContain("12.000");
  });

  it("never blames the dates for a filter the API refused", () => {
    const unknownCategory = new ApiError(422, { detail: "Unknown category: 'x'" });

    expect(exportErrorMessage(unknownCategory)).not.toContain("fechas");
    expect(exportErrorMessage(unknownCategory)).toBe(
      "Los filtros ya no son válidos. Recarga la página e inténtalo de nuevo.",
    );
  });

  it("keeps the offline message, which says what to do", () => {
    expect(exportErrorMessage(new ApiError(0, null))).toContain("conexión");
  });

  it("never shows an unknown error raw", () => {
    expect(exportErrorMessage(new Error("boom"))).toBe(
      "No pudimos generar el archivo. Inténtalo de nuevo.",
    );
  });
});
