import { describe, expect, it } from "vitest";
import {
  pausedNotice,
  pauseEnd,
  pauseLines,
  REVOKED_NOTICE,
  TEMPLATE_BANKS,
} from "@/alerts/modelPause";

// 2026-11-01 09:05 in Bogotá.
const UNTIL = 1_793_541_900;

describe("the end of a pause", () => {
  it("reads as a person would say it, in Bogotá", () => {
    expect(pauseEnd(UNTIL)).toBe("1 de noviembre a las 9:05 a. m.");
  });
});

describe("the notice", () => {
  it("says until when, and that the templates keep reading", () => {
    const notice = pausedNotice(UNTIL);

    expect(notice).toContain("hasta el 1 de noviembre a las 9:05 a. m.");
    expect(notice).toContain(TEMPLATE_BANKS);
  });

  it("says what comes next after each strike", () => {
    expect(pauseLines({ strike: 1, paused_until: UNTIL }).lines[1]).toContain("3 días");
    expect(pauseLines({ strike: 2, paused_until: UNTIL }).lines[1]).toContain(
      "último aviso",
    );
  });

  it("has no end once the reading is revoked", () => {
    const revoked = pauseLines({ strike: 3, paused_until: null });

    expect(revoked.title).toBe("Lectura desactivada");
    expect(revoked.lines).toEqual([REVOKED_NOTICE]);
  });
});
