import { describe, expect, it } from "vitest";
import { parseSeen, withSeen } from "@/lib/help";

describe("the screens whose explanation was read", () => {
  it("starts empty", () => {
    expect(parseSeen(null)).toEqual([]);
    expect(parseSeen("")).toEqual([]);
  });

  it("reads back what was stored", () => {
    expect(parseSeen('["facturas","presupuestos"]')).toEqual([
      "facturas",
      "presupuestos",
    ]);
  });

  /* Written by an older build, or edited by hand: never a crash. */
  it("degrades to nothing seen when the entry is not a list of names", () => {
    expect(parseSeen("{not json")).toEqual([]);
    expect(parseSeen('{"facturas":true}')).toEqual([]);
    expect(parseSeen('["facturas",3,null]')).toEqual(["facturas"]);
  });

  it("adds a screen once", () => {
    expect(withSeen([], "cuentas")).toEqual(["cuentas"]);
    expect(withSeen(["cuentas"], "cuentas")).toEqual(["cuentas"]);
  });
});
