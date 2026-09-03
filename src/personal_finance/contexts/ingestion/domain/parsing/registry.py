from __future__ import annotations

from collections.abc import Mapping
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
    "bancolombia.com.co": BANCOLOMBIA,
    # Lulo sends from `notificaciones@lulobank.com`. Its message ids come from
    # Amazon SES, which is where the mail is *sent* from and is shared with
    # everybody else on SES — the From domain is the part that identifies the
    # bank, and it is the one DKIM signs.
    "lulobank.com": LULO_BANK,
}


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
