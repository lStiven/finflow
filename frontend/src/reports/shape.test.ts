/**
 * The shaping rules, against payloads shaped like the ones the API really
 * sends. Most of these are about the three different nulls this surface has —
 * "could not place", "folded remainder", and "not compared" — none of which
 * mean the same thing and any two of which are easy to conflate.
 */

import { describe, expect, it } from "vitest";
import type { Summary, Trend } from "@/api/queries";
import { bandPalette, REMAINDER_FILL } from "@/components/charts/palette";
import {
  bandsOf,
  bucketsOf,
  cashflowSeries,
  describePrevious,
  drilldown,
  perDay,
  rankedRows,
  type View,
  weekdaySeries,
} from "@/reports/shape";

const AUGUST_START = 1_785_560_400;
const SEPTEMBER_START = 1_788_238_800;

const VIEW: View = {
  range: { from: AUGUST_START, to: SEPTEMBER_START },
  currency: "COP",
  interval: "week",
};

function totals(outgoing: string, incoming = "0") {
  return [
    {
      currency: "COP",
      incoming,
      outgoing,
      net: String(Number(incoming) - Number(outgoing)),
      movements: 1,
    },
  ];
}

function summary(over: Partial<Summary> = {}): Summary {
  return {
    group_by: "category",
    timezone: "America/Bogota",
    order: "amount",
    totals: totals("100"),
    groups: [],
    others: null,
    folded: 0,
    previous_totals: null,
    previous_starts_at: null,
    previous_ends_at: null,
    ...over,
  } as Summary;
}

function band(key: string | null, label: string, amounts: string[]) {
  return {
    key,
    label,
    movements: amounts.length,
    totals: totals(amounts[0] ?? "0"),
    points: amounts.map((amount, index) => ({
      bucket: `b${index}`,
      totals: amount === "0" ? [] : totals(amount),
    })),
  };
}

function trend(over: Partial<Trend> = {}): Trend {
  return {
    interval: "week",
    dimension: "category",
    timezone: "America/Bogota",
    starts_at: AUGUST_START,
    ends_at: SEPTEMBER_START,
    buckets: [
      {
        key: "b0",
        starts_at: AUGUST_START,
        ends_at: AUGUST_START + 604_800,
        partial: false,
      },
      {
        key: "b1",
        starts_at: AUGUST_START + 604_800,
        ends_at: SEPTEMBER_START,
        partial: true,
      },
    ],
    series: [],
    others: null,
    folded: 0,
    totals: [],
    ...over,
  } as Trend;
}

describe("bandsOf", () => {
  it("keeps unplaced spending apart from the folded remainder", () => {
    /*
     * Both arrive with a null key and they are not the same thing: one is
     * spending no merchant owns yet, the other is the tail the API folded.
     * Labelling the first "Otros" renames somebody's real spending into a
     * remainder that is already on the chart.
     */
    const hues = bandPalette(["groceries", null]);
    const bands = bandsOf(
      trend({
        series: [
          band("groceries", "groceries", ["100", "50"]),
          band(null, "Unattributed", ["30", "0"]),
        ],
        others: band(null, "Others", ["10", "5"]),
        folded: 3,
      }),
      hues,
    );

    expect(bands.map((entry) => entry.label)).toEqual([
      "groceries",
      "Sin comercio",
      "Otros (3)",
    ]);
    // The unplaced band is a real bucket and carries a real hue; only the
    // remainder wears the gray that means "no identity".
    expect(bands[1]?.tone).not.toBe(REMAINDER_FILL);
    expect(bands[2]?.tone).toBe(REMAINDER_FILL);
  });

  it("gives a category the same colour the ranked list gave it", () => {
    const hues = bandPalette(["transport", "groceries"]);
    const bands = bandsOf(
      trend({ series: [band("groceries", "groceries", ["1"])] }),
      hues,
    );

    expect(bands[0]?.tone).toBe(hues.get("groceries"));
  });

  it("holds one point per bucket, including the empty ones", () => {
    const bands = bandsOf(
      trend({ series: [band("groceries", "groceries", ["100", "0"])] }),
      bandPalette(["groceries"]),
    );

    // Dense: the chart zips points against buckets by index and never has to
    // notice a missing period.
    expect(bands[0]?.points).toHaveLength(2);
    expect(bands[0]?.points[1]).toEqual({ value: 0, amount: "0" });
  });
});

describe("cashflowSeries", () => {
  it("reads one undivided band as income against expense", () => {
    const series = cashflowSeries(
      trend({
        dimension: "none",
        series: [
          {
            key: null,
            label: "Total",
            movements: 2,
            totals: totals("80", "300"),
            points: [
              { bucket: "b0", totals: totals("80", "300") },
              { bucket: "b1", totals: [] },
            ],
          },
        ],
      }),
    );

    expect(series.map((s) => s.label)).toEqual(["Ingresos", "Gastos"]);
    expect(series[0]?.points[0]?.amount).toBe("300");
    expect(series[1]?.points[0]?.amount).toBe("80");
  });

  it("still draws a baseline when nothing moved at all", () => {
    const series = cashflowSeries(trend({ dimension: "none", series: [] }));

    expect(series[0]?.points).toHaveLength(2);
    expect(series[0]?.points.every((point) => point.value === 0)).toBe(true);
  });
});

describe("rankedRows", () => {
  const options = {
    view: VIEW,
    hues: bandPalette(["groceries", null]),
    label: (group: { key: string | null; label: string }) =>
      group.key === null ? "Sin comercio" : group.label,
    link: (group: { key: string | null }) =>
      group.key === null ? undefined : { category: group.key },
  };

  it("puts the folded remainder last, unlinked and gray", () => {
    const others = {
      key: null,
      label: "Others",
      movements: 4,
      totals: totals("40"),
      previous_totals: null,
    };
    const rows = rankedRows(
      summary({
        groups: [
          {
            key: "groceries",
            label: "groceries",
            movements: 3,
            totals: totals("300"),
            previous_totals: null,
          },
        ],
        others,
        folded: 4,
      }),
      options,
    );

    expect(rows.map((row) => row.label)).toEqual(["groceries", "Otros (4)"]);
    // A remainder cannot be reopened as the movements behind it, so it must
    // not look as though it can.
    expect(rows[1]?.to).toBeUndefined();
    expect(rows[1]?.tone).toBe(REMAINDER_FILL);
    expect(rows[0]?.to?.search.category).toBe("groceries");
  });

  it("scales every bar against the largest row", () => {
    const rows = rankedRows(
      summary({
        groups: [
          {
            key: "a",
            label: "a",
            movements: 1,
            totals: totals("200"),
            previous_totals: null,
          },
          {
            key: "b",
            label: "b",
            movements: 1,
            totals: totals("50"),
            previous_totals: null,
          },
        ],
      }),
      options,
    );

    expect(rows[0]?.fill).toBe(1);
    expect(rows[1]?.fill).toBe(0.25);
  });

  it("tells 'not compared' apart from 'compared and nothing there'", () => {
    const rows = rankedRows(
      summary({
        groups: [
          {
            key: "a",
            label: "a",
            movements: 1,
            totals: totals("10"),
            previous_totals: null,
          },
          {
            key: "b",
            label: "b",
            movements: 1,
            totals: totals("10"),
            previous_totals: [],
          },
        ],
      }),
      options,
    );

    // Absent means no comparison was asked for — a badge would be invented.
    expect(rows[0]?.previous).toBeUndefined();
    // Empty means the window was run and held nothing: a real zero.
    expect(rows[1]?.previous).toBe("0");
  });

  it("drops a bucket that spent nothing in the chosen currency", () => {
    const rows = rankedRows(
      summary({
        groups: [
          {
            key: "a",
            label: "a",
            movements: 1,
            totals: totals("0"),
            previous_totals: null,
          },
          {
            key: "usd",
            label: "usd",
            movements: 1,
            totals: [
              {
                currency: "USD",
                incoming: "0",
                outgoing: "9",
                net: "-9",
                movements: 1,
              },
            ],
            previous_totals: null,
          },
        ],
      }),
      options,
    );

    expect(rows).toEqual([]);
  });
});

describe("weekdaySeries", () => {
  it("fills in the days nothing happened on", () => {
    // The API only returns weekdays that saw movement, and here the gap is
    // the answer: "you never spend on Sundays".
    const series = weekdaySeries(
      summary({
        group_by: "weekday",
        groups: [
          {
            key: "1",
            label: "Monday",
            movements: 2,
            totals: totals("500"),
            previous_totals: null,
          },
        ],
      }),
      "COP",
    );

    expect(series[0]?.points).toHaveLength(7);
    expect(series[0]?.points[0]?.amount).toBe("500");
    expect(series[0]?.points[6]?.amount).toBe("0");
  });
});

describe("bucketsOf", () => {
  it("names a week by the date it opens, not by its ISO key", () => {
    const buckets = bucketsOf(trend(), "week");

    expect(buckets[0]?.full).toMatch(/^Semana del /);
    expect(buckets[1]?.partial).toBe(true);
  });
});

describe("drilldown", () => {
  it("lands on the last day the window covers, not the one after it", () => {
    // The window's upper bound is exclusive; the list's is an inclusive day.
    const search = drilldown(VIEW);

    expect(search.from).toBe("2026-08-01");
    expect(search.to).toBe("2026-08-31");
    expect(search.transfers).toBe("exclude");
  });
});

describe("perDay", () => {
  it("spreads the total over the days the window covers", () => {
    expect(perDay("3100", { from: 0, to: 31 * 86_400 })).toBe("100.00");
  });

  it("never divides by zero on an empty window", () => {
    expect(perDay("50", { from: 0, to: 0 })).toBe("50.00");
  });
});

describe("describePrevious", () => {
  it("falls back to plain words when nothing was compared", () => {
    expect(describePrevious(summary())).toBe("el periodo anterior");
  });

  it("names the window when there was one", () => {
    const text = describePrevious(
      summary({ previous_starts_at: 1_782_882_000, previous_ends_at: AUGUST_START }),
    );

    expect(text).toContain("jul");
    expect(text).not.toContain("ago");
  });
});
