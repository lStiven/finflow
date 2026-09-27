"""Finding the other side of a payment its alert could not name.

A payment to a card at another bank arrives as one ordinary movement: the
alert names the account it left and the institution it went to, never the
card. Only the owner knows that institution holds their card, so declaring
the transfer is theirs to do — what this module does is make the offer
precise, so the right choice is the one already on screen.

Two questions, both pure:

* **Which of the owner's accounts does the alert name?** "Pagaste $X a BANCO
  COMERCIAL AV VILLAS" names the account declared at AV Villas. Matched on
  whole words of the bank's name, never on a substring: "NU" is a bank, and
  "NUEVA EPS" is not it.
* **Is the other side already here?** When both banks emailed — money sent
  from Bancolombia to Lulo, and Lulo announcing it arrived — writing a second
  side would count the money twice. The movement that could be it is offered
  instead, and pairing the two moves no balance at all.

Neither answer is acted on here. A proposal the owner has not confirmed
changes nothing.
"""

from __future__ import annotations

import datetime as dt
import re
import unicodedata

from personal_finance.contexts.financial.domain.entities import Transaction


# How far apart the two sides of one transfer can land. Transfers between
# Colombian banks settle the same day or the next working one, so a Friday
# payment can surface on a Monday; past that, an equal amount going the other
# way is a coincidence, not the same money.
COUNTERPART_WINDOW = dt.timedelta(days=4)

# Words that say nothing about *which* bank. A name made only of these — a
# bank declared as "Banco" — would match every institution an alert names.
_GENERIC_WORDS = frozenset(
    {"banco", "bank", "de", "del", "la", "el", "s", "a", "sa", "comercial", "colombia"},
)
_NOT_A_WORD = re.compile(r"[^a-z0-9]+")


def names_institution(counterparty: str, bank: str | None) -> bool:
    """Whether a movement's counterparty is the bank named `bank`.

    The bank's name, with its spaces removed, has to equal a run of whole
    consecutive words in the counterparty: "AV Villas" and "avvillas" both
    match "BANCO COMERCIAL AV VILLAS", "Lulo Bank" matches "LULO BANK S A",
    and "Nu" matches "NU COLOMBIA" but not "NUEVA EPS".

    Generic words are dropped from both sides first, so "Banco AV Villas" —
    the way an owner is as likely to type it — still matches the alert's
    "BANCO COMERCIAL AV VILLAS".
    """
    if bank is None:
        return False

    wanted = _distinctive(bank)

    if not wanted:
        return False

    joined = "".join(wanted)
    words = _distinctive(counterparty)

    for start in range(len(words)):
        run = ""

        for word in words[start:]:
            run += word

            if run == joined:
                return True

            if len(run) >= len(joined):
                break

    return False


def could_be_other_side(movement: Transaction, candidate: Transaction) -> bool:
    """Whether `candidate` could be the other side of `movement`.

    Everything `Transaction.pair_with` would insist on — opposite directions,
    the same amount to the cent, two different accounts, both free to be
    declared — plus the one thing it deliberately does not: that they landed
    close together. The owner may pair two movements a month apart; this is
    only what is worth proposing.
    """
    if candidate.id == movement.id:
        return False

    if candidate.declaration_refusal is not None:
        return False

    if candidate.direction is movement.direction:
        return False

    if candidate.amount != movement.amount:
        return False

    if movement.account_id is not None and candidate.account_id == movement.account_id:
        return False

    apart = abs(
        candidate.occurred_at.as_epoch_seconds()
        - movement.occurred_at.as_epoch_seconds(),
    )

    return apart <= COUNTERPART_WINDOW.total_seconds()


def _distinctive(text: str) -> list[str]:
    """The words of `text` that could tell one bank from another."""
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    plain = "".join(char for char in decomposed if not unicodedata.combining(char))

    return [
        word for word in _NOT_A_WORD.split(plain) if word and word not in _GENERIC_WORDS
    ]
