import { describe, expect, it } from "vitest";
import {
  codeLooksComplete,
  newPasswordIssue,
  normalizeCode,
  passwordChangeIssue,
} from "@/auth/password";

describe("newPasswordIssue", () => {
  it("accepts a long enough password", () => {
    expect(newPasswordIssue("una frase larga")).toBeUndefined();
  });

  it("refuses one under eight characters", () => {
    expect(newPasswordIssue("corta")).toBeDefined();
  });

  it("counts spaces, because the backend does", () => {
    // Trimming here would accept exactly what the API then refuses, and the
    // person would be told their own passphrase is too short after sending it.
    expect(newPasswordIssue("  abcd  ")).toBeUndefined();
  });

  it("has no composition rules", () => {
    // Deliberately: they push people toward predictable patterns, and the
    // backend's policy says so in as many words.
    expect(newPasswordIssue("aaaaaaaa")).toBeUndefined();
  });
});

describe("passwordChangeIssue", () => {
  const valid = {
    current: "la de siempre",
    next: "una distinta y larga",
    confirmation: "una distinta y larga",
  };

  it("accepts a well-formed change", () => {
    expect(passwordChangeIssue(valid)).toBeUndefined();
  });

  it("asks for the current password first", () => {
    expect(passwordChangeIssue({ ...valid, current: "" })).toBe(
      "Escribe tu contraseña actual.",
    );
  });

  it("refuses a new password that is too short", () => {
    expect(
      passwordChangeIssue({ ...valid, next: "corta", confirmation: "corta" }),
    ).toBe(newPasswordIssue("corta"));
  });

  it("complains about length before sameness", () => {
    // "No puede ser la misma" on a five-character attempt answers a question
    // nobody asked.
    expect(
      passwordChangeIssue({ current: "corta", next: "corta", confirmation: "corta" }),
    ).toBe(newPasswordIssue("corta"));
  });

  it("refuses repeating the current password", () => {
    // The backend refuses it too, but the round trip would have ended every
    // session for a change that changes nothing.
    expect(
      passwordChangeIssue({
        current: "la de siempre y larga",
        next: "la de siempre y larga",
        confirmation: "la de siempre y larga",
      }),
    ).toBe("La contraseña nueva es igual a la actual.");
  });

  it("refuses a confirmation that does not match", () => {
    expect(passwordChangeIssue({ ...valid, confirmation: "otra cosa" })).toBe(
      "Las dos contraseñas no coinciden.",
    );
  });

  it("checks the confirmation last, once the password itself is usable", () => {
    expect(
      passwordChangeIssue({ current: "la de siempre", next: "x", confirmation: "y" }),
    ).toBe(newPasswordIssue("x"));
  });
});

describe("normalizeCode", () => {
  it.each(["123456", "123 456", " 123456 ", "123-456", "12 34 56"])(
    "reads %s as the same six digits",
    (typed) => {
      expect(normalizeCode(typed)).toBe("123456");
    },
  );
});

describe("codeLooksComplete", () => {
  it("accepts six digits however they were pasted", () => {
    expect(codeLooksComplete(" 123 456 ")).toBe(true);
  });

  it.each(["12345", "1234567", "12345a", ""])("refuses %s", (typed) => {
    expect(codeLooksComplete(typed)).toBe(false);
  });

  it("never claims a code is correct, only that it is worth sending", () => {
    // Only the backend knows; five wrong guesses spend the challenge.
    expect(codeLooksComplete("000000")).toBe(true);
  });
});
