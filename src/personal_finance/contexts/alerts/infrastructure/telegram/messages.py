"""What an alert actually says, in Spanish, as plain text.

**No parse mode, ever.** Part of every message is what a bank wrote about a
purchase — sometimes by way of a language model — and in HTML or Markdown a
link smuggled into that string would render as a clickable link inside a
message its reader trusts. Plain text makes the escaping problem not exist
rather than solving it, which is the only way to solve it reliably.

The wording lives here rather than in the application layer because it is a
property of the transport: a message to Telegram and a message to anything
else would say the same facts differently. The use case passes facts.
"""

from __future__ import annotations

from decimal import Decimal
import re
from zoneinfo import ZoneInfo

from personal_finance.contexts.alerts.application.messages import (
    MovementAlert,
    MovementDirection,
)
from personal_finance.shared.domain.value_objects import Currency, Money, PosixTime


# How much of a bank's counterparty text is worth showing. They run long and
# repeat the merchant three times; the first line of it is the recognisable
# part.
MAX_COUNTERPARTY_LENGTH = 64

# The bank name is short by nature; anything longer is not a bank name.
MAX_BANK_LENGTH = 32

# What a bank's name is made of. Deliberately excludes `:` and `/`, which is
# what stops an injected URL from surviving into a message its reader trusts.
_BANK_CHARACTER = re.compile(r"[^\W_]|[ .\-&']", re.UNICODE)

_DECIMALS = {Currency.COP: 0, Currency.USD: 2}
_SYMBOL = {Currency.COP: "$", Currency.USD: "US$"}


def format_money(amount: Money) -> str:
    """A figure somebody reads on a phone, in their own currency's shape.

    Pesos carry no cents in practice and showing `,00` on every line is
    noise; dollars without cents would be wrong. Thousands are grouped with
    a dot and the decimal is a comma, which is how it is written here.
    """
    places = _DECIMALS[amount.currency]
    quantized = amount.amount.quantize(Decimal(1).scaleb(-places))
    # Python groups with "," and points with "."; here it is the other way
    # round, so the two swap through a placeholder rather than clobbering
    # each other.
    grouped = (
        f"{quantized:,.{places}f}".replace(",", "\x00")
        .replace(".", ",")
        .replace("\x00", ".")
    )

    return f"{_SYMBOL[amount.currency]}{grouped}"


def clean_line(text: str, *, limit: int) -> str:
    """Untrusted text, made safe to put on a line of our own message.

    Collapses all whitespace — a newline inside it would otherwise forge a
    line that looks like ours — and trims what is left to something that fits.

    Every untrusted string that reaches a message goes through this, and that
    is the whole rule. The counterparty is the obvious one, but `bank` is
    just as untrusted: for the two banks with a template it is a constant,
    and for everything else it is whatever a language model read out of an
    email. A message from the bot its owner has been taught to trust is a
    better place to put a link than the email ever was.
    """
    collapsed = " ".join(text.split())

    if len(collapsed) <= limit:
        return collapsed

    return f"{collapsed[: limit - 1].rstrip()}…"


def clean_counterparty(counterparty: str) -> str:
    return clean_line(counterparty, limit=MAX_COUNTERPARTY_LENGTH) or "Sin descripción"


def clean_bank(bank: str) -> str:
    """The bank's name, reduced to what a bank's name can contain.

    A whitelist rather than a length cap, because a cap is not enough: a URL
    is short, and Telegram turns one into a tappable link in plain text
    whatever the parse mode. Dropping everything outside this set removes the
    `:` and the `/` a URL needs, so the worst an injected string can do here
    is read oddly.

    Letters, digits, spaces and the handful of marks real names use —
    Bancolombia, Lulo Bank, Banco de Bogotá, Scotiabank Colpatria, BBVA.
    """
    kept = "".join(character for character in bank if _BANK_CHARACTER.match(character))

    return clean_line(kept, limit=MAX_BANK_LENGTH) or "Tu banco"


def format_moment(moment: PosixTime, *, timezone: str) -> str:
    local = moment.to_datetime().astimezone(ZoneInfo(timezone))

    return local.strftime("%d/%m %I:%M %p").lower()


def compose_movement_alert(
    alert: MovementAlert,
    *,
    timezone: str,
) -> str:
    """One movement, as four short lines at most.

    Says what the payload says and nothing else. There is no month-to-date
    total here and no canonical merchant name: both belong to other contexts,
    and reading them would make an alert depend on two more things being up.
    The counterparty is the bank's own text — `COMPRA EN *PAYU*COL`, not
    `PayU` — which is what the owner would have seen in the bank's own alert.
    """
    heading = "Gasto" if alert.direction is MovementDirection.OUTGOING else "Ingreso"
    moment = format_moment(alert.occurred_at, timezone=timezone)
    lines = [
        f"{heading} {format_money(alert.amount)}",
        clean_counterparty(alert.counterparty),
        f"{clean_bank(alert.bank)} · {moment}",
    ]

    if alert.unassigned:
        # Worth saying: the movement is real and counted nowhere, which is
        # the one thing the owner can act on from here.
        lines.append("Sin cuenta asignada")

    return "\n".join(lines)


def compose_link_confirmation() -> str:
    return (
        "Listo. Te aviso por aquí cada vez que se mueva plata.\n"
        "Puedes apagarlo o ponerle un monto mínimo desde tu perfil."
    )


def compose_chat_already_linked() -> str:
    """Why a tap did nothing.

    Specific on purpose. Only the owner of a private chat reads what is
    written to it, so naming the reason costs nothing and saves them guessing
    at what went wrong.
    """
    return (
        "Este Telegram ya está conectado a otra cuenta de Finflow.\n"
        "Desconéctalo allí primero, o usa otra cuenta de Telegram."
    )
