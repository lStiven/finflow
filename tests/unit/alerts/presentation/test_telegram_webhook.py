"""The public surface, and the four things that protect it.

The secret header is the only authentication a Lambda Function URL can have,
so its comparison and the fact that it runs *before* anything is parsed are
both load-bearing. The rest is about answering 200 to everything already
decided: Telegram retries any non-2xx and eventually disables the webhook, so
a "no" to a body we do not care about would take linking down for everybody.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from personal_finance.contexts.alerts.application.commands import (
    RedeemChannelLinkCommand,
)
from personal_finance.contexts.alerts.application.handlers import (
    RedeemChannelLinkUseCase,
)
from personal_finance.contexts.alerts.application.ports import (
    DestinationRefusedError,
    TransportUnavailableError,
)
from personal_finance.contexts.alerts.domain.entities import AlertChannel
from personal_finance.contexts.alerts.domain.exceptions import (
    ChannelAlreadyVerifiedError,
    ChatAlreadyLinkedError,
    InvalidLinkTokenError,
)
from personal_finance.contexts.alerts.domain.value_objects import (
    ChannelKind,
    ChatId,
    SecretHash,
)
from personal_finance.contexts.alerts.presentation.http.telegram import (
    SECRET_HEADER,
    get_redeem_use_case,
    router,
)
from personal_finance.shared.domain.value_objects import PosixTime, UserId
from personal_finance.shared.infrastructure.config.settings import reset_settings
from personal_finance.shared.infrastructure.throttling import build_guard
from personal_finance.shared.presentation.throttling import Guard


SECRET = "a-webhook-secret-only-telegram-knows"
NOW = PosixTime.from_epoch_seconds(1_700_000_000)
WEBHOOK = "/alerts/telegram/webhook"


class StubRedeem(RedeemChannelLinkUseCase):
    def __init__(self, *, raises: Exception | None = None) -> None:
        self._raises = raises
        self.commands: list[RedeemChannelLinkCommand] = []

    def execute(self, command: RedeemChannelLinkCommand) -> AlertChannel:
        self.commands.append(command)

        if self._raises is not None:
            raise self._raises

        channel = AlertChannel.pending(
            user_id=UserId.new(),
            kind=ChannelKind.TELEGRAM,
            link_hash=SecretHash("sha256:abc"),
            now=NOW,
        )
        channel.verify(chat_id=ChatId(command.chat_id.value), label=None, now=NOW)

        return channel


@pytest.fixture(autouse=True)
def configured_secret(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """A known webhook secret, and a settings cache that forgets it after.

    The settings are cached for the process, so clearing on the way out
    matters as much as on the way in: without it every later test in the run
    would see this secret.
    """
    monkeypatch.setenv("ALERTS_TELEGRAM_WEBHOOK_SECRET", SECRET)
    reset_settings()

    yield

    reset_settings()


class InMemoryAttemptCounter:
    """El contador del limitador, en un diccionario.

    Sin esto estos casos dependerían de si el emulador está arriba: con él
    caído el guardia se abre y todo pasa; con él en pie, los secretos
    equivocados de una prueba se suman a los de la siguiente y la vigésimo
    primera se lleva un 429 que nadie escribió. Una prueba que cambia de
    resultado según qué haya corriendo en la máquina no está probando nada.
    """

    def __init__(self) -> None:
        self.hits: dict[str, int] = {}

    def spent(self, bucket: str) -> int:
        return self.hits.get(bucket, 0)

    def record(self, *, bucket: str, expires_at: PosixTime) -> int:
        del expires_at
        self.hits[bucket] = self.hits.get(bucket, 0) + 1

        return self.hits[bucket]

    def clear(self, bucket: str) -> None:
        self.hits.pop(bucket, None)


def _client(use_case: RedeemChannelLinkUseCase | None = None) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_redeem_use_case] = lambda: use_case or StubRedeem()
    # Un contador por cliente, no por petición: la dependencia se resuelve
    # en cada llamada, así que construirlo dentro del lambda daría un
    # contador nuevo cada vez — y un límite que nunca cuenta dos.
    guard = Guard(InMemoryAttemptCounter(), trust_proxy=False)
    app.dependency_overrides[build_guard] = lambda: guard

    return TestClient(app)


def _update(
    *,
    text: str = "/start abc123",
    chat_type: str = "private",
    chat_id: int = 123456789,
) -> dict[str, Any]:
    return {
        "update_id": 1,
        "message": {
            "message_id": 7,
            "chat": {"id": chat_id, "type": chat_type},
            "from": {"id": 42, "first_name": "Ana"},
            "text": text,
        },
    }


def _post(
    client: TestClient,
    payload: dict[str, Any],
    *,
    secret: str | None = SECRET,
) -> Any:  # noqa: ANN401
    headers = {SECRET_HEADER: secret} if secret is not None else {}

    return client.post(WEBHOOK, json=payload, headers=headers)


# ----------------------------------------------------------------------
# The secret
# ----------------------------------------------------------------------


def test_the_right_secret_gets_through() -> None:
    use_case = StubRedeem()

    response = _post(_client(use_case), _update())

    assert response.status_code == 200
    assert response.json() == {"ok": True}
    [command] = use_case.commands
    assert command.token == "abc123"
    assert command.chat_id == ChatId("123456789")


@pytest.mark.parametrize("secret", [None, "", "wrong", SECRET + "x", SECRET[:-1]])
def test_a_wrong_or_missing_secret_is_forbidden(secret: str | None) -> None:
    use_case = StubRedeem()

    response = _post(_client(use_case), _update(), secret=secret)

    assert response.status_code == 403
    # And nothing was looked up: the check runs before the body is read, so
    # an anonymous flood costs one comparison rather than a parse and a read.
    assert use_case.commands == []


def test_an_unset_secret_lets_nobody_in_rather_than_everybody(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`compare_digest("", "")` is True, so an unset secret would not weaken
    the door — it would remove it."""
    monkeypatch.setenv("ALERTS_TELEGRAM_WEBHOOK_SECRET", "")
    reset_settings()
    use_case = StubRedeem()

    for secret in (None, "", "anything"):
        response = _post(_client(use_case), _update(), secret=secret)
        assert response.status_code == 403

    assert use_case.commands == []


def test_a_body_is_not_even_parsed_without_the_secret() -> None:
    use_case = StubRedeem()
    client = _client(use_case)

    response = client.post(
        WEBHOOK,
        content=b"{ this is not json at all",
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 403
    assert use_case.commands == []


# ----------------------------------------------------------------------
# Everything decided answers 200
# ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "error",
    [
        InvalidLinkTokenError("unknown, spent or expired"),
        ChatAlreadyLinkedError("that chat belongs to another account"),
        ChannelAlreadyVerifiedError("already linked"),
        DestinationRefusedError("blocked"),
        TransportUnavailableError("telegram is down"),
    ],
)
def test_every_refusal_still_answers_ok(error: Exception) -> None:
    """A non-2xx puts Telegram into backoff and eventually kills the webhook.

    It also keeps the endpoint uninformative: unknown, spent and expired are
    indistinguishable from outside, so nothing here says whether a link
    exists.
    """
    response = _post(_client(StubRedeem(raises=error)), _update())

    assert response.status_code == 200
    assert response.json() == {"ok": True}


def test_a_group_chat_is_refused_without_ever_reaching_the_use_case() -> None:
    """Otherwise one person's purchases go to everyone in the room."""
    use_case = StubRedeem()

    response = _post(_client(use_case), _update(chat_type="supergroup"))

    assert response.status_code == 200
    assert use_case.commands == []


@pytest.mark.parametrize(
    "text",
    ["hola", "/start", "/start ../../etc/passwd", "/help abc123"],
)
def test_anything_that_is_not_a_link_is_shrugged_off(text: str) -> None:
    use_case = StubRedeem()

    response = _post(_client(use_case), _update(text=text))

    assert response.status_code == 200
    assert use_case.commands == []


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"update_id": 1},
        {"update_id": 1, "edited_message": {"text": "/start abc123"}},
        {"update_id": 1, "message": {"chat": {"id": "not-a-number"}}},
        {"update_id": 1, "message": {"chat": {"id": 10**30, "type": "private"}}},
    ],
)
def test_a_shape_this_cannot_read_is_shrugged_off(payload: dict[str, Any]) -> None:
    """A body Telegram changed must not become a 422 and a retry storm."""
    use_case = StubRedeem()

    response = _post(_client(use_case), payload)

    assert response.status_code == 200
    assert use_case.commands == []


def test_the_owner_is_never_read_from_what_the_caller_says() -> None:
    """The token decides whose channel binds. `from.id` is not identity."""
    use_case = StubRedeem()

    _post(_client(use_case), _update())

    [command] = use_case.commands
    assert not hasattr(command, "user_id")


def test_adivinar_el_secreto_se_corta_y_telegram_no_lo_nota() -> None:
    """Los intentos con secreto equivocado se cuentan por dirección; los que
    traen el secreto bueno no cuestan nada.

    Las dos mitades importan. Sin la primera, la única puerta que este
    despliegue tiene contra internet —`AuthType: NONE` no puede fijar rangos
    de direcciones— se puede tantear sin límite. Sin la segunda, una tarde
    movida de actualizaciones legítimas se frenaría sola, y Telegram
    responde a eso desactivando el webhook.
    """
    client = _client()
    body = _update(text="/start cualquiera")

    refused = [
        client.post(
            "/alerts/telegram/webhook",
            json=body,
            headers={SECRET_HEADER: "no es el secreto"},
        ).status_code
        for _ in range(22)
    ]

    assert refused[:20] == [403] * 20
    assert refused[20:] == [429, 429]

    # Y el que sí trae el secreto sigue entrando, con la puerta agotada.
    accepted = client.post(
        "/alerts/telegram/webhook",
        json=body,
        headers={SECRET_HEADER: SECRET},
    )

    assert accepted.status_code == 200
