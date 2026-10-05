import { describe, expect, it } from "vitest";
import { gmailFromFilter } from "@/lib/forwarding";

describe("gmailFromFilter", () => {
  it("joins every approved sender into one From criterion", () => {
    expect(
      gmailFromFilter(
        ["an.notificacionesbancolombia.com", "lulobank.com"],
        ["alertas@banco.com"],
      ),
    ).toBe("@an.notificacionesbancolombia.com OR @lulobank.com OR alertas@banco.com");
  });

  it("marks a domain with @ so Gmail matches any address on it", () => {
    expect(gmailFromFilter(["bancolombia.com.co"], [])).toBe("@bancolombia.com.co");
  });

  it("is empty when nobody is approved, so the screen shows no filter", () => {
    expect(gmailFromFilter([], [])).toBe("");
  });
});
