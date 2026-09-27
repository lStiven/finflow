import { describe, expect, it } from "vitest";
import type { Account, Transaction, TransferLeg } from "@/api/queries";
import { describeDeletion } from "@/lib/deletion";

const SAVINGS: Account = {
  id: "acc-savings",
  name: "Ahorros",
  kind: "savings",
  category: "asset",
  informational: false,
  currency: "COP",
  balance: "1000000",
  opening_balance: "1000000",
  credit_limit: null,
  available: null,
  movements_applied: 3,
  opened_at: 1_787_500_000,
  closed_at: null,
  bank: "bancolombia",
  instruments: [],
};

const CARD: Account = {
  ...SAVINGS,
  id: "acc-card",
  name: "Tarjeta",
  kind: "credit_card",
  category: "liability",
  balance: "800000",
  credit_limit: "5000000",
  available: "4200000",
};

/**
 * `Intl` separates the symbol from the digits with a no-break space (U+00A0),
 * not an ordinary one. That is its business, not a rule of this app, so the
 * assertions below normalise it and stay readable — the same helper
 * `money.test.ts` uses.
 */
function plain(text: string): string {
  return text.replace(/[\u00A0\u202F]/g, " ");
}

function movement(overrides: Partial<Transaction> = {}): Transaction {
  return {
    id: "mov-1",
    direction: "outgoing",
    amount: "2000",
    currency: "COP",
    occurred_at: 1_787_500_000,
    counterparty: "RESTAURANTE EL LAGO",
    bank: "bancolombia",
    origin: "bank_alert",
    status: "assigned",
    account_id: SAVINGS.id,
    note: null,
    stated: null,
    merchant: null,
    transfer: null,
    ...overrides,
  };
}

/** One side of a transfer whose other side is a row here too. */
function pairedLeg(overrides: Partial<TransferLeg> = {}): TransferLeg {
  return {
    id: "transfer-1",
    role: "source",
    external: false,
    counterpart_movement_id: "mov-2",
    counterpart_instrument_kind: "credit_card",
    counterpart_last_four: "7653",
    basis: "stated",
    ...overrides,
  };
}

/** A card paid from another bank, a wallet or cash: only this side exists. */
function loneLeg(overrides: Partial<TransferLeg> = {}): TransferLeg {
  return {
    id: "transfer-2",
    role: "destination",
    external: true,
    counterpart_movement_id: null,
    counterpart_instrument_kind: null,
    counterpart_last_four: null,
    basis: "stated",
    ...overrides,
  };
}

describe("an ordinary movement on an asset", () => {
  it("says the money comes back, naming the account", () => {
    const { balance } = describeDeletion(movement(), SAVINGS);

    expect(plain(balance)).toBe("Vuelven $ 2.000 a Ahorros.");
  });

  it("says an erased income is taken back off", () => {
    const { balance } = describeDeletion(
      movement({ direction: "incoming", amount: "4500000" }),
      SAVINGS,
    );

    expect(plain(balance)).toBe("Se le restan $ 4.500.000 a Ahorros.");
  });

  it("says it stops counting as spending", () => {
    expect(describeDeletion(movement(), SAVINGS).totals).toBe(
      "Deja de contar como gasto en tus totales.",
    );
  });

  it("says it stops counting as income, on one that came in", () => {
    expect(describeDeletion(movement({ direction: "incoming" }), SAVINGS).totals).toBe(
      "Deja de contar como ingreso en tus totales.",
    );
  });

  it("takes one row and nothing else", () => {
    const consequence = describeDeletion(movement(), SAVINGS);

    expect(consequence.rows).toBe(1);
    expect(consequence.transfer).toBeNull();
  });
});

/*
 * The pair of sentences that reading `direction` alone gets backwards.
 * Spending on a card raises what it owes, so erasing that spending lowers the
 * debt — and erasing a payment to the card puts the debt back.
 */
describe("a movement on a credit card", () => {
  it("says an erased purchase lowers the debt", () => {
    const { balance } = describeDeletion(
      movement({ account_id: CARD.id, amount: "150000" }),
      CARD,
    );

    expect(plain(balance)).toBe("La deuda de Tarjeta baja $ 150.000.");
  });

  it("says an erased payment puts the debt back, and why", () => {
    const { balance } = describeDeletion(
      movement({ account_id: CARD.id, direction: "incoming", amount: "500000" }),
      CARD,
    );

    expect(plain(balance)).toContain("La deuda de Tarjeta vuelve a subir $ 500.000");
    expect(balance).toContain("este pago deja de existir");
  });

  it("never tells a card holder the money comes back to them", () => {
    const { balance } = describeDeletion(movement({ account_id: CARD.id }), CARD);

    expect(balance).not.toContain("Vuelven");
  });
});

describe("a movement on no account", () => {
  /*
   * The ordinary state for somebody watching only what comes in and goes out.
   * The movement is real and worth erasing; there is simply no balance to give
   * anything back to, and saying one moves would be a lie.
   */
  it("says plainly that no balance moves", () => {
    const { balance } = describeDeletion(
      movement({ account_id: null, status: "unassigned" }),
      undefined,
    );

    expect(balance).toBe(
      "No está en ninguna cuenta, así que no se mueve ningún saldo.",
    );
  });

  it("still stops counting in the totals", () => {
    const { totals } = describeDeletion(
      movement({ account_id: null, status: "unassigned" }),
      undefined,
    );

    expect(totals).toBe("Deja de contar como gasto en tus totales.");
  });
});

describe("an account the screen does not have", () => {
  /*
   * The movement names an account, so a balance genuinely moves — but without
   * the account there is no category, and a direction read without one is the
   * card sentence backwards. It states the figure and stops there.
   */
  it("states the figure without claiming which way it goes", () => {
    const { balance } = describeDeletion(movement(), undefined);

    expect(plain(balance)).toBe("Se ajusta el saldo de su cuenta en $ 2.000.");
    expect(balance).not.toContain("Vuelven");
    expect(balance).not.toContain("deuda");
  });
});

describe("a transfer whose other half is here", () => {
  it("takes two rows, not one", () => {
    const consequence = describeDeletion(movement({ transfer: pairedLeg() }), SAVINGS);

    expect(consequence.rows).toBe(2);
  });

  it("says the other half goes too, and why it cannot stay", () => {
    const { transfer } = describeDeletion(movement({ transfer: pairedLeg() }), SAVINGS);

    expect(transfer).toContain("Se borra también la otra mitad");
    expect(transfer).toContain("apuntando a algo que ya no existe");
  });

  it("promises both halves on the button, so nobody confirms one and loses two", () => {
    expect(describeDeletion(movement({ transfer: pairedLeg() }), SAVINGS).confirm).toBe(
      "Sí, eliminar las dos mitades",
    );
  });

  it("leaves the month's totals alone, because it never counted", () => {
    const { totals } = describeDeletion(movement({ transfer: pairedLeg() }), SAVINGS);

    expect(totals).toContain("no cuenta como gasto ni como ingreso");
    expect(totals).toContain("no cambian");
  });

  it("still says what this side gives back", () => {
    const { balance } = describeDeletion(
      movement({ transfer: pairedLeg(), amount: "500000" }),
      SAVINGS,
    );

    expect(plain(balance)).toBe("Vuelven $ 500.000 a Ahorros.");
  });
});

describe("a transfer paid from outside Finflow", () => {
  it("takes one row, because there is no second one", () => {
    const consequence = describeDeletion(
      movement({ transfer: loneLeg(), account_id: CARD.id }),
      CARD,
    );

    expect(consequence.rows).toBe(1);
    expect(consequence.confirm).toBe("Sí, eliminarlo");
  });

  it("says why only this side goes", () => {
    const { transfer } = describeDeletion(
      movement({ transfer: loneLeg(), account_id: CARD.id }),
      CARD,
    );

    expect(transfer).toBe(
      "La otra mitad no está en Finflow, así que solo se borra este lado.",
    );
  });

  it("puts the card's debt back, which is what erasing a payment means", () => {
    const { balance } = describeDeletion(
      movement({
        transfer: loneLeg(),
        account_id: CARD.id,
        direction: "incoming",
        amount: "500000",
      }),
      CARD,
    );

    expect(plain(balance)).toContain("La deuda de Tarjeta vuelve a subir $ 500.000");
  });

  it("never promises a second row it cannot delete", () => {
    const { transfer } = describeDeletion(
      movement({ transfer: loneLeg(), account_id: CARD.id }),
      CARD,
    );

    expect(transfer).not.toContain("las dos");
  });
});

describe("what to do instead", () => {
  /*
   * Correcting is always there. Taking a movement off its account is not, and
   * pointing somebody at a control that is not on the edit form — or that the
   * API answers 409 — is worse than not mentioning it.
   */
  it("offers taking it off the account, on one that is on an account", () => {
    expect(describeDeletion(movement(), SAVINGS).instead).toContain(
      "sacarlo de la cuenta sin borrarlo",
    );
  });

  it("does not, on a movement that is on no account", () => {
    const { instead } = describeDeletion(
      movement({ account_id: null, status: "unassigned" }),
      undefined,
    );

    expect(instead).not.toContain("sacarlo de la cuenta");
    expect(instead).toContain("Corregir");
  });

  it("does not, on a leg paid from outside — the API refuses that with a 409", () => {
    const { instead } = describeDeletion(
      movement({ transfer: loneLeg(), account_id: CARD.id }),
      CARD,
    );

    expect(instead).not.toContain("sacarlo de la cuenta");
  });

  it("still offers it on a paired leg, which can be moved off its account", () => {
    expect(
      describeDeletion(movement({ transfer: pairedLeg() }), SAVINGS).instead,
    ).toContain("sacarlo de la cuenta sin borrarlo");
  });

  it("always says it cannot be undone", () => {
    expect(describeDeletion(movement(), SAVINGS).instead).toContain(
      "no se puede deshacer",
    );
  });
});

describe("every shape", () => {
  const shapes: Array<[string, Transaction, Account | undefined]> = [
    ["asset expense", movement(), SAVINGS],
    ["asset income", movement({ direction: "incoming" }), SAVINGS],
    ["card purchase", movement({ account_id: CARD.id }), CARD],
    ["unassigned", movement({ account_id: null, status: "unassigned" }), undefined],
    ["paired leg", movement({ transfer: pairedLeg() }), SAVINGS],
    ["lone leg", movement({ transfer: loneLeg(), account_id: CARD.id }), CARD],
  ];

  /*
   * The screen renders these straight into a confirmation, so a null or a
   * "undefined" leaking through would be read as a fact about somebody's
   * money. Only `transfer` is allowed to be absent, and only when there is
   * no transfer.
   */
  it.each(shapes)("says something true for %s", (_name, entry, account) => {
    const consequence = describeDeletion(entry, account);

    expect(consequence.balance).not.toContain("undefined");
    expect(consequence.balance.endsWith(".")).toBe(true);
    expect(consequence.totals.endsWith(".")).toBe(true);
    expect(consequence.confirm.length).toBeGreaterThan(0);
    expect(consequence.instead).toContain("no se puede deshacer");
    expect(consequence.transfer === null || consequence.transfer.length > 0).toBe(true);
  });

  it.each(shapes)("only promises a second row for a pair, on %s", (_n, entry, acc) => {
    const consequence = describeDeletion(entry, acc);
    const leg = entry.transfer ?? null;
    const isPair = leg !== null && leg.external === false;

    expect(consequence.rows).toBe(isPair ? 2 : 1);
  });
});

describe("a transfer declared after the fact", () => {
  it("points at undoing it rather than erasing the bank's own alert", () => {
    const consequence = describeDeletion(
      movement({
        transfer: pairedLeg({
          basis: "reclassified",
          counterpart_instrument_kind: null,
          counterpart_last_four: null,
        }),
      }),
      undefined,
    );

    expect(consequence.instead).toContain("«Deshacer traslado»");
  });
});
