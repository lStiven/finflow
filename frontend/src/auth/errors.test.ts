import { describe, expect, it } from "vitest";
import { ApiError } from "@/api/errors";
import { identityErrorMessage, retryMessage } from "@/auth/errors";

describe("identityErrorMessage", () => {
  it("tells an expired code apart from a wrong one", () => {
    // Different next move: one is "pide otro", the other is "revisa los
    // dígitos". The API's own detail does not distinguish them.
    expect(identityErrorMessage(new ApiError(410, null))).toContain("Pide uno nuevo");
  });

  it("turns a spent ticket into the step that has to be repeated", () => {
    expect(identityErrorMessage(new ApiError(403, null))).toContain("código nuevo");
  });

  it("says how long to wait when the API said", () => {
    expect(identityErrorMessage(new ApiError(429, null, 45))).toBe(
      "Espera 45 segundos y vuelve a intentarlo.",
    );
  });

  it("says the mail could not be sent, without blaming the user", () => {
    expect(identityErrorMessage(new ApiError(502, null))).toContain("correo");
  });

  it("falls back to whatever the API said for anything else", () => {
    // A 400 from `confirm` is the deliberately uninformative one, and its own
    // detail is the right thing to show.
    const wrongCode = new ApiError(400, { detail: "That code is not valid" });
    expect(identityErrorMessage(wrongCode)).toBe("That code is not valid");
  });

  it("survives something that is not an error at all", () => {
    expect(identityErrorMessage("nope")).toBe("Algo salió mal");
  });
});

describe("retryMessage", () => {
  it("counts in seconds while that reads naturally", () => {
    expect(retryMessage(30)).toContain("30 segundos");
  });

  it("switches to minutes once seconds stop being useful", () => {
    // The hourly cap answers with the rest of the window, which is minutes.
    expect(retryMessage(2400)).toContain("40 minutos");
  });

  it("rounds up, so the advice is never one attempt early", () => {
    expect(retryMessage(91)).toContain("2 minutos");
  });

  it("says something useful when the API sent no header", () => {
    expect(retryMessage(undefined)).toBe("Demasiados intentos. Espera un momento.");
  });
});
