import { describe, expect, it } from "vitest";
import {
  approvedBanks,
  bankDisplayName,
  bankOfSender,
  bankState,
  customSenders,
  filterDrift,
  isApproved,
  type KnownBank,
  parseSender,
  type Senders,
  senderErrorMessage,
  withBank,
  withoutBank,
  withoutSender,
  withSender,
} from "@/onboarding/banks";

/** What `/ingestion/catalog` answers in `known_banks`, as a fixture. */
const BANCOLOMBIA: KnownBank = {
  id: "bancolombia",
  name: "Bancolombia",
  domains: [
    "an.notificacionesbancolombia.com",
    "notificacionesbancolombia.com",
    "ayn.notificacionesbancolombia.com",
    "bancolombia.com.co",
  ],
};
const LULO: KnownBank = {
  id: "lulo bank",
  name: "Lulo Bank",
  domains: ["lulobank.com"],
};
const BANKS = [BANCOLOMBIA, LULO];
const NONE: Senders = { domains: [], addresses: [] };

describe("a known bank's state", () => {
  it("is on only when every domain it sends from is approved", () => {
    const all = withBank(NONE, BANCOLOMBIA);

    expect(bankState(BANCOLOMBIA, all)).toBe("on");
    expect(bankState(LULO, all)).toBe("off");
  });

  /*
   * Approving the alert domains alone accepts card purchases and drops every
   * transfer in silence. Calling that "approved" is the lie this avoids.
   */
  it("is partial when only some of its domains are approved", () => {
    const some: Senders = {
      domains: ["an.notificacionesbancolombia.com"],
      addresses: [],
    };

    expect(bankState(BANCOLOMBIA, some)).toBe("partial");
  });

  it("adds a bank without touching what else is approved", () => {
    const before: Senders = {
      domains: ["lulobank.com", "an.notificacionesbancolombia.com"],
      addresses: ["alertas@banco.com"],
    };
    const after = withBank(before, BANCOLOMBIA);

    expect(after.addresses).toEqual(["alertas@banco.com"]);
    expect(after.domains).toContain("lulobank.com");
    // No duplicate for the domain that was already there.
    expect(
      after.domains.filter((domain) => domain === "an.notificacionesbancolombia.com"),
    ).toHaveLength(1);
    expect(bankState(BANCOLOMBIA, after)).toBe("on");
  });

  it("removes every domain of a bank and nothing else", () => {
    const before = withBank(withBank(NONE, BANCOLOMBIA), LULO);
    const after = withoutBank(before, BANCOLOMBIA);

    expect(after.domains).toEqual(["lulobank.com"]);
  });
});

describe("approvals no known bank accounts for", () => {
  it("lists other domains and every address, domains first", () => {
    const senders: Senders = {
      domains: ["lulobank.com", "davivienda.com"],
      addresses: ["alertas@banco.com"],
    };

    expect(customSenders(senders, BANKS)).toEqual([
      { type: "domain", value: "davivienda.com" },
      { type: "address", value: "alertas@banco.com" },
    ]);
  });

  it("adds and removes one of them", () => {
    const added = withSender(NONE, { type: "address", value: "alertas@banco.com" });

    expect(added.addresses).toEqual(["alertas@banco.com"]);
    expect(
      withoutSender(added, { type: "address", value: "alertas@banco.com" }),
    ).toEqual(NONE);
  });

  it("names every approved bank, flagging the half-approved one", () => {
    const senders: Senders = {
      domains: ["an.notificacionesbancolombia.com", "lulobank.com"],
      addresses: ["alertas@banco.com"],
    };

    expect(approvedBanks(senders, BANKS)).toEqual([
      { name: "Bancolombia", partial: true },
      { name: "Lulo Bank", partial: false },
      { name: "alertas@banco.com", partial: false },
    ]);
  });
});

describe("matching a sender the way the intake does", () => {
  const senders: Senders = {
    domains: ["lulobank.com"],
    addresses: ["alertas@banco.com"],
  };

  it("accepts an exact address or anything on an approved domain", () => {
    expect(isApproved("Alertas@Banco.com", senders)).toBe(true);
    expect(isApproved("notificaciones@lulobank.com", senders)).toBe(true);
  });

  it("does not treat a subdomain as its parent", () => {
    // The intake compares the domain exactly, so this screen must too.
    expect(isApproved("x@mail.lulobank.com", senders)).toBe(false);
  });

  it("knows which bank an address belongs to", () => {
    expect(
      bankOfSender("alertasynotificaciones@an.notificacionesbancolombia.com", BANKS)
        ?.id,
    ).toBe("bancolombia");
    expect(bankOfSender("alertas@banco.com", BANKS)).toBeUndefined();
  });

  it("spells a movement's bank the way the bank does", () => {
    expect(bankDisplayName("lulo bank", BANKS)).toBe("Lulo Bank");
    expect(bankDisplayName("bancolombia", BANKS)).toBe("Bancolombia");
    expect(bankDisplayName("Davivienda", BANKS)).toBe("Davivienda");
  });

  it("knows no bank at all until the catalogue says so", () => {
    expect(bankOfSender("notificaciones@lulobank.com", [])).toBeUndefined();
    expect(bankDisplayName("lulo bank", [])).toBe("lulo bank");
  });
});

describe("reading what somebody pasted", () => {
  it("takes a plain address as an address", () => {
    expect(parseSender("alertas@banco.com.co", NONE)).toEqual({
      ok: true,
      sender: { type: "address", value: "alertas@banco.com.co" },
    });
  });

  it("takes the address out of Gmail's «Name <address>»", () => {
    expect(parseSender('"Mi Banco" <Alertas@MiBanco.com.co>', NONE)).toEqual({
      ok: true,
      sender: { type: "address", value: "alertas@mibanco.com.co" },
    });
  });

  it("reads a leading @ as a domain, never as an address", () => {
    // Sent as an address, `@banco.com` would be stored and never match.
    expect(parseSender("@banco.com", NONE)).toEqual({
      ok: true,
      sender: { type: "domain", value: "banco.com" },
    });
  });

  it("reads a bare domain as a domain", () => {
    expect(parseSender("  Davivienda.com. ", NONE)).toEqual({
      ok: true,
      sender: { type: "domain", value: "davivienda.com" },
    });
  });

  it("drops a mailto: prefix", () => {
    expect(parseSender("mailto:alertas@banco.com", NONE)).toEqual({
      ok: true,
      sender: { type: "address", value: "alertas@banco.com" },
    });
  });

  it("refuses what is not a sender", () => {
    for (const raw of [
      "banco",
      "https://banco.com/alertas",
      "alertas@",
      "a b@banco.com",
    ]) {
      expect(parseSender(raw, NONE)).toEqual({ ok: false, error: { code: "invalid" } });
    }
    expect(parseSender("   ", NONE)).toEqual({ ok: false, error: { code: "empty" } });
  });

  /*
   * Approving gmail.com would accept mail from anybody with a Gmail account.
   * An exact address there is somebody in particular, and stays allowed.
   */
  it("refuses a webmail domain but not an address on it", () => {
    expect(parseSender("gmail.com", NONE)).toEqual({
      ok: false,
      error: { code: "personal-domain" },
    });
    expect(parseSender("@Hotmail.com", NONE)).toEqual({
      ok: false,
      error: { code: "personal-domain" },
    });
    expect(parseSender("yo@gmail.com", NONE).ok).toBe(true);
  });

  it("says when it is already there, or already covered by its domain", () => {
    const senders: Senders = { domains: ["banco.com"], addresses: ["x@otro.com"] };

    expect(parseSender("banco.com", senders)).toEqual({
      ok: false,
      error: { code: "duplicate" },
    });
    expect(parseSender("X@otro.com", senders)).toEqual({
      ok: false,
      error: { code: "duplicate" },
    });
    expect(parseSender("alertas@banco.com", senders)).toEqual({
      ok: false,
      error: { code: "covered", domain: "banco.com" },
    });
  });

  it("has words for every refusal", () => {
    expect(senderErrorMessage({ code: "covered", domain: "banco.com" })).toContain(
      "@banco.com",
    );
    expect(senderErrorMessage({ code: "personal-domain" })).toMatch(/personal/);
  });
});

describe("the Gmail filter", () => {
  it("says what was added and removed since the filter was made", () => {
    expect(
      filterDrift(
        ["@lulobank.com", "alertas@banco.com"],
        ["@lulobank.com", "@davivienda.com"],
      ),
    ).toEqual({ added: ["@davivienda.com"], removed: ["alertas@banco.com"] });
  });

  it("claims nothing when this browser never saw the filter made", () => {
    expect(filterDrift(null, ["@lulobank.com"])).toBeNull();
  });
});
