import { describe, expect, it } from "vitest";
import type { Account, Transaction } from "@/api/queries";
import { refusalMessage, undoConsequence, writtenSideEffect } from "@/lib/declaring";

const payment = {
  id: "abc",
  direction: "outgoing",
  amount: "3625733.00",
  currency: "COP",
  occurred_at: 1_767_111_420,
  counterparty: "BANCO COMERCIAL AV VILLAS",
  bank: "bancolombia",
  origin: "bank_alert",
  status: "assigned",
  account_id: "savings",
  note: null,
  stated: null,
  merchant: null,
  transfer: null,
} as Transaction;

const account = (category: "asset" | "liability", name: string) =>
  ({ id: name, name, category }) as Account;

/** Spaces as `Intl` writes them are not the ones typed in a test. */
const plain = (text: string) => text.replace(/\s/g, " ");

describe("writtenSideEffect", () => {
  it("says a card's debt falls when an outgoing payment is written onto it", () => {
    expect(
      plain(writtenSideEffect(payment, account("liability", "Tarjeta AV Villas"))),
    ).toMatch(/^La deuda de Tarjeta AV Villas baja \$ ?3\.625\.733/);
  });

  it("says a savings account rises when the money arrived there", () => {
    expect(plain(writtenSideEffect(payment, account("asset", "Lulo")))).toMatch(
      /^Lulo sube/,
    );
  });

  it("reads an incoming movement's other side as money leaving", () => {
    const arrival = { ...payment, direction: "incoming" } as Transaction;

    expect(writtenSideEffect(arrival, account("asset", "Ahorros"))).toMatch(
      /^A Ahorros se le restan/,
    );
  });
});

describe("refusalMessage", () => {
  it("has its own words for every code the API sends", () => {
    for (const code of [
      "already_transfer",
      "self_written",
      "unplaceable",
      "linked_to_bill",
    ]) {
      expect(refusalMessage(code)).not.toBe(refusalMessage("unknown"));
    }
  });
});

describe("undoConsequence", () => {
  it("promises the written side's balance back when one was written", () => {
    const declared = {
      ...payment,
      transfer: { basis: "reclassified", external: false },
    } as Transaction;

    expect(undoConsequence(declared, "Tarjeta AV Villas")).toContain(
      "se borra el lado que Finflow escribió en Tarjeta AV Villas",
    );
  });

  it("promises no balance change for a lone side", () => {
    const declared = {
      ...payment,
      transfer: { basis: "reclassified", external: true },
    } as Transaction;

    expect(undoConsequence(declared, null)).toBe(
      "Este movimiento vuelve a contar como gasto. Ningún saldo cambia.",
    );
  });
});

describe("undoConsequence while the other side is unknown", () => {
  it("does not promise that no balance moves", () => {
    const declared = {
      ...payment,
      transfer: { basis: "reclassified", external: false },
    } as Transaction;

    const said = undoConsequence(declared, undefined);

    expect(said).not.toContain("Ningún saldo cambia");
    expect(said).toContain("se borra");
  });
});
