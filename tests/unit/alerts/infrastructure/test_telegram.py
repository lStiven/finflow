"""The transport adapter, and the two things it must never get wrong.

The bot token lives in the request URL, so every failure path is checked for
whether it could carry that URL into a message or a log. And an update from a
group chat must be refused, because linking one would broadcast a person's
purchases to a room.
"""

from decimal import Decimal

import httpx
from pydantic import SecretStr
import pytest

from personal_finance.contexts.alerts.application.messages import (
    MovementAlert,
    MovementDirection,
    MovementOrigin,
)
from personal_finance.contexts.alerts.application.ports import (
    DestinationRefusedError,
    TransportUnavailableError,
)
from personal_finance.contexts.alerts.domain.value_objects import ChatId
from personal_finance.contexts.alerts.infrastructure.telegram.client import (
    TelegramMessageSender,
)
from personal_finance.contexts.alerts.infrastructure.telegram.deep_link import (
    build_deep_link,
)
from personal_finance.contexts.alerts.infrastructure.telegram.messages import (
    clean_counterparty,
    compose_chat_already_linked,
    compose_movement_alert,
    format_money,
)
from personal_finance.contexts.alerts.infrastructure.telegram.updates import (
    TelegramUpdate,
    extract_start_command,
)
from personal_finance.shared.domain.value_objects import Currency, Money, PosixTime


BOT_TOKEN = "1234567890:AAHsecrettokenthatmustnotleak"
CHAT = ChatId("123456789")
TIMEZONE = "America/Bogota"


def _alert(
    *,
    direction: MovementDirection = MovementDirection.OUTGOING,
    counterparty: str = "COMPRA EN *PAYU*COL",
    bank: str = "Bancolombia",
    unassigned: bool = False,
) -> MovementAlert:
    return MovementAlert(
        amount=Money(amount=Decimal("84300"), currency=Currency.COP),
        direction=direction,
        counterparty=counterparty,
        bank=bank,
        occurred_at=PosixTime.from_epoch_seconds(1_757_800_000),
        origin=MovementOrigin.BANK_ALERT,
        unassigned=unassigned,
    )


def _sender(handler: object) -> TelegramMessageSender:
    transport = httpx.MockTransport(handler)  # type: ignore[arg-type]

    return TelegramMessageSender(
        bot_token=SecretStr(BOT_TOKEN),
        display_timezone=TIMEZONE,
        client=httpx.Client(
            transport=transport,
            base_url="https://api.telegram.org",
        ),
    )


def _responding(status_code: int) -> object:
    def handler(request: httpx.Request) -> httpx.Response:
        del request

        return httpx.Response(status_code, json={"ok": status_code < 400})

    return handler


# ----------------------------------------------------------------------
# Sending
# ----------------------------------------------------------------------


def test_a_message_goes_out_as_plain_text_with_no_parse_mode() -> None:
    """Part of the text is what a bank wrote, sometimes via a model.

    In HTML mode an `<a href>` inside that string would render as a link
    inside a message its reader trusts.
    """
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)

        return httpx.Response(200, json={"ok": True})

    _sender(handler).send_movement_alert(chat_id=CHAT, alert=_alert())

    [request] = seen
    body = request.read().decode()
    assert "parse_mode" not in body
    assert "84.300" in body


def test_the_token_travels_in_the_url_and_only_there() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)

        return httpx.Response(200, json={"ok": True})

    _sender(handler).send_movement_alert(chat_id=CHAT, alert=_alert())

    [request] = seen
    assert BOT_TOKEN in str(request.url)
    assert BOT_TOKEN not in request.read().decode()


@pytest.mark.parametrize("status_code", [408, 429, 500, 502, 503])
def test_a_transport_wobble_asks_to_be_retried(status_code: int) -> None:
    with pytest.raises(TransportUnavailableError) as raised:
        _sender(_responding(status_code)).send_movement_alert(
            chat_id=CHAT,
            alert=_alert(),
        )

    assert BOT_TOKEN not in str(raised.value)


@pytest.mark.parametrize("status_code", [400, 403, 404])
def test_a_blocked_chat_is_refused_for_good(status_code: int) -> None:
    """A person who blocked the bot said no. Retrying until a dead-letter
    queue takes it would be treating that as an outage."""
    with pytest.raises(DestinationRefusedError) as raised:
        _sender(_responding(status_code)).send_movement_alert(
            chat_id=CHAT,
            alert=_alert(),
        )

    assert BOT_TOKEN not in str(raised.value)


def test_a_network_failure_never_carries_the_token_into_its_message() -> None:
    """`str(httpx_error)` holds the request URL, and the URL holds the token."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("timed out", request=request)

    with pytest.raises(TransportUnavailableError) as raised:
        _sender(handler).send_movement_alert(chat_id=CHAT, alert=_alert())

    assert BOT_TOKEN not in str(raised.value)
    assert raised.value.__cause__ is None


# ----------------------------------------------------------------------
# Wording
# ----------------------------------------------------------------------


def test_pesos_carry_no_cents_and_dollars_do() -> None:
    assert format_money(Money(amount=Decimal("84300"), currency=Currency.COP)) == (
        "$84.300"
    )
    assert format_money(Money(amount=Decimal("12.5"), currency=Currency.USD)) == (
        "US$12,50"
    )


def test_a_counterparty_cannot_forge_a_line_of_our_own_message() -> None:
    assert clean_counterparty("EXITO\nGasto $1") == "EXITO Gasto $1"


def test_the_bank_cannot_forge_a_line_either() -> None:
    """`bank` is a constant for the two banks with a template and whatever a
    language model read out of an email for everything else. A message from
    the bot its owner trusts is a better place to put a link than the email."""
    text = compose_movement_alert(
        _alert(bank="Bancolombia\n\nBloqueo de seguridad: entra a http://malo.co"),
        timezone=TIMEZONE,
    )

    # No forged line, and nothing a URL needs survives.
    assert text.count("\n") == 2
    assert "http" not in text
    assert "://" not in text
    assert "malo.co" not in text


def test_a_real_bank_name_survives_intact() -> None:
    for name in ("Bancolombia", "Lulo Bank", "Banco de Bogotá", "BBVA"):
        text = compose_movement_alert(_alert(bank=name), timezone=TIMEZONE)
        assert name in text


def test_every_untrusted_string_in_a_message_is_trimmed() -> None:
    text = compose_movement_alert(
        _alert(counterparty="C" * 300, bank="B" * 300),
        timezone=TIMEZONE,
    )

    for line in text.splitlines():
        assert len(line) <= 80


def test_a_very_long_counterparty_is_trimmed() -> None:
    assert len(clean_counterparty("A" * 300)) <= 64


def test_an_empty_counterparty_still_reads_as_something() -> None:
    assert clean_counterparty("   ") == "Sin descripción"


def test_incoming_money_is_announced_as_income() -> None:
    text = compose_movement_alert(
        _alert(direction=MovementDirection.INCOMING),
        timezone=TIMEZONE,
    )

    assert text.startswith("Ingreso ")


def test_a_movement_with_no_account_says_so() -> None:
    text = compose_movement_alert(_alert(unassigned=True), timezone=TIMEZONE)

    assert "Sin cuenta asignada" in text


# ----------------------------------------------------------------------
# The deep link
# ----------------------------------------------------------------------


def test_a_deep_link_carries_the_token_in_the_start_payload() -> None:
    link = build_deep_link(bot_username="@finflow_bot", token="abc-DEF_123")

    assert link == "https://t.me/finflow_bot?start=abc-DEF_123"


def test_a_token_that_would_need_escaping_is_refused() -> None:
    with pytest.raises(ValueError, match="deep-link payload"):
        build_deep_link(bot_username="finflow_bot", token="has spaces")


def test_a_username_that_is_not_one_is_refused() -> None:
    with pytest.raises(ValueError, match="bot username"):
        build_deep_link(bot_username="no", token="abc")


# ----------------------------------------------------------------------
# What the webhook believes
# ----------------------------------------------------------------------


def _update(
    *,
    text: str = "/start abc123",
    chat_type: str = "private",
    first_name: str | None = "Ana",
) -> TelegramUpdate:
    return TelegramUpdate.model_validate(
        {
            "update_id": 1,
            "message": {
                "message_id": 7,
                "chat": {"id": 123456789, "type": chat_type},
                "from": {"id": 42, "first_name": first_name},
                "text": text,
            },
        },
    )


def test_a_private_start_is_a_linking_attempt() -> None:
    command = extract_start_command(_update())

    assert command is not None
    assert command.chat_id == "123456789"
    assert command.token == "abc123"
    assert command.label == "Ana"


def test_a_group_chat_is_refused() -> None:
    """Otherwise one person's purchases go to everyone in the room."""
    for chat_type in ("group", "supergroup", "channel"):
        assert extract_start_command(_update(chat_type=chat_type)) is None


def test_a_start_addressed_to_the_bot_by_name_still_parses() -> None:
    command = extract_start_command(_update(text="/start@finflow_bot abc123"))

    assert command is not None
    assert command.token == "abc123"


@pytest.mark.parametrize(
    "text",
    ["hola", "/start", "/start ", "/start abc 123", "/startabc", "/help abc"],
)
def test_anything_that_is_not_a_start_with_a_payload_is_ignored(text: str) -> None:
    assert extract_start_command(_update(text=text)) is None


def test_a_payload_outside_the_deep_link_alphabet_is_refused_before_hashing() -> None:
    assert extract_start_command(_update(text="/start ../../etc/passwd")) is None


def test_an_update_that_is_not_a_message_is_ignored() -> None:
    update = TelegramUpdate.model_validate({"update_id": 1, "edited_message": {}})

    assert extract_start_command(update) is None


def test_unknown_fields_do_not_make_an_update_unreadable() -> None:
    update = TelegramUpdate.model_validate(
        {
            "update_id": 1,
            "something_telegram_added_later": {"nested": True},
            "message": {
                "chat": {"id": 1, "type": "private", "new_field": 1},
                "text": "/start abc123",
            },
        },
    )

    assert extract_start_command(update) is not None


def test_a_message_with_no_name_still_links() -> None:
    command = extract_start_command(_update(first_name=None))

    assert command is not None
    assert command.label is None


def test_the_refusal_says_what_to_do_about_it() -> None:
    text = compose_chat_already_linked()

    assert "otra cuenta" in text
