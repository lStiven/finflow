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
    BudgetStanding,
    MovementAlert,
    MovementDirection,
    WeeklySummary,
)
from personal_finance.shared.domain.value_objects import Currency, Money, PosixTime


# How much of a bank's counterparty text is worth showing. They run long and
# repeat the merchant three times; the first line of it is the recognisable
# part.
MAX_COUNTERPARTY_LENGTH = 64

# The bank name is short by nature; anything longer is not a bank name.
MAX_BANK_LENGTH = 32

# A budget's name is its owner's own words, kept to one short line.
MAX_BUDGET_NAME_LENGTH = 40

# More budget lines than this under one purchase stops being a message.
MAX_BUDGET_LINES = 3

# The shipped categories reach Alerts under their stable value; a message in
# Spanish restates them, the same table the frontend keeps in
# `@/merchants/categories`. A category somebody wrote keeps their own name.
CATEGORY_COPY = {
    "uncategorized": "Sin categoría",
    "groceries": "Mercado",
    "restaurants": "Restaurantes",
    "transport": "Transporte",
    "fuel": "Combustible",
    "shopping": "Compras",
    "entertainment": "Entretenimiento",
    "subscriptions": "Suscripciones",
    "utilities": "Servicios",
    "health": "Salud",
    "education": "Educación",
    "travel": "Viajes",
    "fees": "Comisiones",
    "transfers": "Transferencias",
    "income": "Ingresos",
    "other": "Otros",
}

_EN_DASH = "\u2013"

_MONTHS = (
    "ene",
    "feb",
    "mar",
    "abr",
    "may",
    "jun",
    "jul",
    "ago",
    "sep",
    "oct",
    "nov",
    "dic",
)

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

    lines.extend(compose_budget_lines(alert.budgets))

    return "\n".join(lines)


def compose_budget_lines(budgets: tuple[BudgetStanding, ...]) -> list[str]:
    """What is left of each budget the purchase counts against.

    Only budgets that cover it — Financial decided which — so a purchase under
    no budget says nothing more than it always did. The distance to the cap is
    said plainly, and past it by how much: no alarm words, because the tone of
    this app is to inform and let its owner decide.
    """
    if not budgets:
        return []

    lines = [_budget_line(budget) for budget in budgets[:MAX_BUDGET_LINES]]
    extra = len(budgets) - MAX_BUDGET_LINES

    if extra > 0:
        lines.append(f"y {extra} {'presupuesto' if extra == 1 else 'presupuestos'} más")

    return ["", *lines]


def _budget_line(budget: BudgetStanding) -> str:
    name = clean_line(budget.name, limit=MAX_BUDGET_NAME_LENGTH) or "Presupuesto"
    limit = format_money(Money(amount=budget.limit, currency=budget.currency))

    if budget.remaining > 0:
        left = format_money(Money(amount=budget.remaining, currency=budget.currency))
        return f"{name}: te quedan {left} de {limit}"

    if budget.remaining == 0:
        return f"{name}: llegaste al tope de {limit}"

    over = format_money(Money(amount=-budget.remaining, currency=budget.currency))
    return f"{name}: vas {over} por encima del tope de {limit}"


def category_name(category: str, label: str) -> str:
    return CATEGORY_COPY.get(category) or clean_line(
        label, limit=MAX_BUDGET_NAME_LENGTH
    )


def format_week(summary: WeeklySummary) -> str:
    """`22-28 sep`, or `29 sep - 5 oct` across a month, with an en dash."""
    start, end = summary.week_start, summary.week_end

    if start.month == end.month:
        return f"{start.day}{_EN_DASH}{end.day} {_MONTHS[end.month - 1]}"

    return (
        f"{start.day} {_MONTHS[start.month - 1]} {_EN_DASH} "
        f"{end.day} {_MONTHS[end.month - 1]}"
    )


def compose_weekly_summary(summary: WeeklySummary) -> str:
    """Monday's look back, compared with its owner and nobody else.

    Never «bien» or «mal»: a number against their own normal, and the one
    category that moved the most. Somebody who spent more has a reason this
    message cannot know.
    """
    currency = summary.currency
    spent = format_money(Money(amount=summary.spent, currency=currency))
    lines = [f"Tu semana ({format_week(summary)})"]

    if summary.movements == 0:
        lines.append("No registraste gastos esta semana.")
    else:
        count = "gasto" if summary.movements == 1 else "gastos"
        lines.append(f"Gastaste {spent} en {summary.movements} {count}.")

    if summary.movements == 0 and summary.typical:
        typical = format_money(Money(amount=summary.typical, currency=currency))
        lines.append(f"Tu semana normal es de {typical}.")
    elif summary.typical is None:
        lines.append(
            "Es tu primera semana: desde la próxima te la comparo con las anteriores."
        )
    elif summary.typical == 0:
        lines.append("Las semanas anteriores no habías registrado gastos.")
    else:
        typical = format_money(Money(amount=summary.typical, currency=currency))
        change = (summary.spent - summary.typical) / summary.typical * 100
        rounded = int(change.to_integral_value())

        if rounded == 0:
            lines.append(f"Casi igual que tu semana normal ({typical}).")
        elif rounded < 0:
            lines.append(f"{-rounded} % menos que tu semana normal ({typical}).")
        else:
            lines.append(f"{rounded} % más que tu semana normal ({typical}).")

    if summary.rise is not None:
        rise = summary.rise
        spent_there = format_money(Money(amount=rise.spent, currency=currency))
        usual = format_money(Money(amount=rise.typical, currency=currency))
        lines.append(
            f"Lo que más subió: {category_name(rise.category, rise.label)}, "
            f"{spent_there} (normalmente {usual}).",
        )

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
