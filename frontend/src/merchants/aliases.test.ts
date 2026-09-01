import { describe, expect, it } from "vitest";
import {
  aliasCountLabel,
  countGuesses,
  originCopy,
  sortLabel,
  statusLabel,
  timesSeenLabel,
} from "@/merchants/aliases";

describe("originCopy", () => {
  /*
   * The whole reason origin is on screen: `suggested` is the only one that is
   * a guess, so it is the only one the review screen asks about. Marking a
   * `derived` alias as doubtful would send people to check matches that
   * cannot be wrong about the name they matched.
   */
  it("marks only the suggested origin as a guess", () => {
    expect(originCopy("suggested").guess).toBe(true);
    expect(originCopy("seed").guess).toBe(false);
    expect(originCopy("derived").guess).toBe(false);
    expect(originCopy("manual").guess).toBe(false);
  });

  it("describes an origin this build has never heard of without calling it a guess", () => {
    const copy = originCopy("imported");
    expect(copy.label).toBe("Otra");
    expect(copy.guess).toBe(false);
  });
});

describe("countGuesses", () => {
  it("counts the spellings the user is actually being asked to look at", () => {
    expect(
      countGuesses([
        { origin: "seed" },
        { origin: "suggested" },
        { origin: "derived" },
        { origin: "suggested" },
      ]),
    ).toBe(2);
  });

  it("is zero when nothing was guessed", () => {
    expect(countGuesses([{ origin: "seed" }, { origin: "manual" }])).toBe(0);
  });
});

describe("statusLabel", () => {
  /*
   * "Automatic" describes how the merchant got here, not what it means to the
   * reader — and what it means is that nobody has looked at it yet.
   */
  it("says what the status means rather than how it got there", () => {
    expect(statusLabel("automatic")).toBe("Sin revisar");
    expect(statusLabel("confirmed")).toBe("Revisado");
  });

  it("passes an unknown status through rather than blanking it", () => {
    expect(statusLabel("archived")).toBe("archived");
  });
});

describe("sortLabel", () => {
  it("names each sort by what it puts first", () => {
    expect(sortLabel("times_seen", "Times seen")).toBe("Más frecuentes");
  });

  it("falls back to the catalogue's label for a sort added on the server", () => {
    expect(sortLabel("first_seen", "First seen")).toBe("First seen");
  });
});

describe("counts", () => {
  it("agrees with itself in the singular", () => {
    expect(aliasCountLabel(1)).toBe("1 forma de escribirse");
    expect(timesSeenLabel(1)).toBe("visto 1 vez");
  });

  it("agrees with itself in the plural", () => {
    expect(aliasCountLabel(3)).toBe("3 formas de escribirse");
    expect(timesSeenLabel(3)).toBe("visto 3 veces");
  });
});
