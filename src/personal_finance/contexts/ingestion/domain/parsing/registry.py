from __future__ import annotations

from collections.abc import Mapping
import dataclasses
from typing import Protocol

from personal_finance.contexts.ingestion.domain.parsing.bancolombia import (
    BANK_NAME as BANCOLOMBIA,
    BancolombiaParser,
)
from personal_finance.contexts.ingestion.domain.parsing.forwarded_email import (
    forwarded_sender,
)
from personal_finance.contexts.ingestion.domain.parsing.lulobank import (
    BANK_NAME as LULO_BANK,
    LuloBankParser,
)
from personal_finance.contexts.ingestion.domain.transactions import ExtractedMovement
from personal_finance.contexts.ingestion.domain.value_objects import EmailAddress
from personal_finance.shared.domain.value_objects import ValueObject


class DeterministicParser(Protocol):
    """Reads a bank's own alert templates. Pure: no network, no I/O, no LLM."""

    bank: str

    def parse(self, text: str) -> ExtractedMovement | None:
        """Return what the text describes, or None when no template matches —
        which is the caller's signal to fall back to the LLM.

        A template may answer with an `ExtractedTransfer` instead of a
        transaction: some alerts state money moving between two of the
        owner's own instruments, and that is two movements rather than one.
        """
        ...


# Keyed by the sender's domain rather than the full address: banks rotate the
# local part of their alert addresses far more often than the domain.
BANK_DOMAINS: Mapping[str, str] = {
    "an.notificacionesbancolombia.com": BANCOLOMBIA,
    "notificacionesbancolombia.com": BANCOLOMBIA,
    # Another alert subdomain, seen in the wild on 2026-09-12: the bank runs
    # more than one, same templates behind each. Listed one by one rather than
    # matched by a suffix — anything looser also accepts
    # `notificacionesbancolombia.com.evil.co`. Only the domains actually
    # observed belong here.
    "ayn.notificacionesbancolombia.com": BANCOLOMBIA,
    "bancolombia.com.co": BANCOLOMBIA,
    # Lulo sends from `notificaciones@lulobank.com`. Its message ids come from
    # Amazon SES, which is where the mail is *sent* from and is shared with
    # everybody else on SES — the From domain is the part that identifies the
    # bank, and it is the one DKIM signs.
    "lulobank.com": LULO_BANK,
}

# How each bank spells itself on a screen. A parser's `BANK_NAME` is lowercase
# because it is an identifier written into every movement, not a label.
BANK_DISPLAY_NAMES: Mapping[str, str] = {
    BANCOLOMBIA: "Bancolombia",
    LULO_BANK: "Lulo Bank",
}


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class KnownBank(ValueObject):
    """A bank with a deterministic parser, as a connect-your-bank screen shows it.

    Knowing a bank approves nothing: each user still approves its senders.
    """

    # The parser's `BANK_NAME`, which is also what lands in a movement's `bank`.
    id: str
    name: str
    domains: tuple[str, ...]


def known_banks(
    bank_domains: Mapping[str, str] = BANK_DOMAINS,
    display_names: Mapping[str, str] = BANK_DISPLAY_NAMES,
) -> tuple[KnownBank, ...]:
    """Every bank in `bank_domains`, in the order it first appears there.

    Derived rather than listed a second time, so a parser registered with its
    domains reaches the screen without touching anything else. All of a bank's
    domains travel together: approving only some of Bancolombia's accepts card
    purchases and silently drops every transfer.
    """
    domains_by_bank: dict[str, list[str]] = {}

    for domain, bank in bank_domains.items():
        domains_by_bank.setdefault(bank, []).append(domain)

    return tuple(
        KnownBank(
            id=bank,
            name=display_names.get(bank, bank.title()),
            domains=tuple(domains),
        )
        for bank, domains in domains_by_bank.items()
    )


class ParserRegistry:
    """Selects the deterministic parser that knows a given sender's templates."""

    def __init__(
        self,
        *,
        parsers: Mapping[str, DeterministicParser] | None = None,
        bank_domains: Mapping[str, str] | None = None,
    ) -> None:
        self._parsers = parsers if parsers is not None else default_parsers()
        self._bank_domains = bank_domains if bank_domains is not None else BANK_DOMAINS

    def for_sender(self, sender: EmailAddress) -> DeterministicParser | None:
        bank = self._bank_domains.get(sender.domain)

        return self._parsers.get(bank) if bank else None

    def for_forwarded_message(self, raw: str) -> DeterministicParser | None:
        """The parser for the bank a forwarded email came from, if any.

        The second question, asked only when `for_sender` has already answered
        None: the intake model is that people forward their bank's mail, and a
        forward made by hand arrives from the person, not the bank. The bank
        is then named only in the header block the mail client wrote into the
        body, which is what this reads.

        Takes the raw body, not the text a template reads: normalising strips
        the very header this depends on. See `forwarded_sender`.

        Returns None for anything that is not a forward, or that was forwarded
        from an address no parser knows — the caller is then exactly where it
        was, on its way to the fallback.
        """
        original = forwarded_sender(raw)

        return self.for_sender(original) if original is not None else None


def default_parsers() -> Mapping[str, DeterministicParser]:
    return {BANCOLOMBIA: BancolombiaParser(), LULO_BANK: LuloBankParser()}
