"""The authenticated surface, against in-memory use cases.

The assertion worth keeping is the negative one: the link is returned by the
create call and by nothing else. A field added to `ChannelResponse` later
could quietly undo that, and this is what would notice.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from decimal import Decimal
import uuid

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from personal_finance.contexts.alerts.application.commands import (
    CreateChannelCommand,
    DeleteChannelCommand,
    UpdateChannelPreferenceCommand,
)
from personal_finance.contexts.alerts.application.handlers import (
    CreateChannelUseCase,
    DeleteChannelUseCase,
    IssuedLink,
    UpdateChannelPreferenceUseCase,
)
from personal_finance.contexts.alerts.application.queries import (
    ChannelView,
    ListChannelsUseCase,
)
from personal_finance.contexts.alerts.domain.entities import AlertChannel
from personal_finance.contexts.alerts.domain.exceptions import (
    ChannelNotFoundError,
    TooManyChannelsError,
)
from personal_finance.contexts.alerts.domain.value_objects import (
    AlertPreference,
    AlertType,
    ChannelKind,
    ChatId,
    SecretHash,
)
from personal_finance.contexts.alerts.presentation.http.router import (
    get_create_channel_use_case,
    get_delete_channel_use_case,
    get_list_channels_use_case,
    get_update_preference_use_case,
    router,
)
from personal_finance.contexts.identity.presentation.http.router import (
    get_current_user_id,
)
from personal_finance.shared.domain.value_objects import (
    Currency,
    Money,
    PosixTime,
    UserId,
)
from personal_finance.shared.infrastructure.config.settings import reset_settings


USER = UserId.new()
NOW = PosixTime.from_epoch_seconds(1_700_000_000)
TOKEN = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
BOT = "finflow_test_bot"
LINK_TTL_MINUTES = 15


@pytest.fixture(autouse=True)
def pinned_settings(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """The bot name the link is built from, chosen here rather than inherited.

    Without this the deep link depends on whichever `.env` the machine
    running the suite happens to have, so the test passes or fails on the
    developer's own bot rather than on the code.
    """
    monkeypatch.setenv("ALERTS_TELEGRAM_BOT_USERNAME", BOT)
    monkeypatch.setenv("ALERTS_LINK_TTL_MINUTES", str(LINK_TTL_MINUTES))
    reset_settings()

    yield

    reset_settings()


def _channel(*, verified: bool = False) -> AlertChannel:
    channel = AlertChannel.pending(
        user_id=USER,
        kind=ChannelKind.TELEGRAM,
        link_hash=SecretHash("sha256:abc"),
        now=NOW,
    )

    if verified:
        channel.verify(chat_id=ChatId("123456789"), label="Ana", now=NOW)
        channel.pull_events()

    return channel


class StubCreate(CreateChannelUseCase):
    def __init__(self, *, raises: Exception | None = None) -> None:
        self._raises = raises

    def execute(self, command: CreateChannelCommand) -> IssuedLink:
        del command

        if self._raises is not None:
            raise self._raises

        return IssuedLink(channel=_channel(), token=TOKEN)


class StubList(ListChannelsUseCase):
    def __init__(self, *channels: AlertChannel) -> None:
        self._channels = channels

    def execute(self, user_id: UserId) -> Sequence[ChannelView]:
        del user_id

        return [ChannelView.of(channel) for channel in self._channels]


class StubUpdate(UpdateChannelPreferenceUseCase):
    def __init__(self, *, raises: Exception | None = None) -> None:
        self._raises = raises
        self.commands: list[UpdateChannelPreferenceCommand] = []

    def execute(self, command: UpdateChannelPreferenceCommand) -> AlertChannel:
        self.commands.append(command)

        if self._raises is not None:
            raise self._raises

        channel = _channel(verified=True)
        channel.update_preference(
            AlertPreference(
                alert_type=command.alert_type,
                enabled=command.enabled,
                minimum_amount=command.minimum_amount,
            ),
        )

        return channel


class StubDelete(DeleteChannelUseCase):
    def __init__(self, *, raises: Exception | None = None) -> None:
        self._raises = raises
        self.commands: list[DeleteChannelCommand] = []

    def execute(self, command: DeleteChannelCommand) -> None:
        self.commands.append(command)

        if self._raises is not None:
            raise self._raises


def _client(
    *,
    create: CreateChannelUseCase | None = None,
    listing: ListChannelsUseCase | None = None,
    update: UpdateChannelPreferenceUseCase | None = None,
    delete: DeleteChannelUseCase | None = None,
) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user_id] = lambda: USER
    app.dependency_overrides[get_create_channel_use_case] = lambda: (
        create or StubCreate()
    )
    app.dependency_overrides[get_list_channels_use_case] = lambda: listing or StubList()
    app.dependency_overrides[get_update_preference_use_case] = lambda: (
        update or StubUpdate()
    )
    app.dependency_overrides[get_delete_channel_use_case] = lambda: (
        delete or StubDelete()
    )

    return TestClient(app)


# ----------------------------------------------------------------------
# Creating
# ----------------------------------------------------------------------


def test_creating_a_channel_returns_the_deep_link_once() -> None:
    response = _client().post("/alerts/channels", json={})

    assert response.status_code == 201
    body = response.json()
    assert body["link_url"] == f"https://t.me/{BOT}?start={TOKEN}"
    assert body["expires_in_minutes"] == LINK_TTL_MINUTES
    assert body["channel"]["status"] == "pending"
    assert body["channel"]["chat_hint"] is None


def test_too_many_channels_is_a_conflict() -> None:
    client = _client(create=StubCreate(raises=TooManyChannelsError("enough")))

    assert client.post("/alerts/channels", json={}).status_code == 409


# ----------------------------------------------------------------------
# Listing — and what it must never carry
# ----------------------------------------------------------------------


def test_listing_never_hands_the_link_back() -> None:
    """A one-time credential that can be fetched again is a permanent one."""
    client = _client(listing=StubList(_channel(), _channel(verified=True)))

    body = client.get("/alerts/channels").json()

    serialized = str(body)
    assert TOKEN not in serialized
    assert "link_url" not in serialized
    assert "t.me" not in serialized


def test_listing_shows_only_the_tail_of_the_destination() -> None:
    client = _client(listing=StubList(_channel(verified=True)))

    [channel] = client.get("/alerts/channels").json()["channels"]

    assert channel["chat_hint"] == "…6789"
    assert "123456789" not in str(channel)


def test_listing_carries_a_switch_for_every_alert_type() -> None:
    client = _client(listing=StubList(_channel(verified=True)))

    [channel] = client.get("/alerts/channels").json()["channels"]

    assert {entry["alert_type"] for entry in channel["preferences"]} == {
        alert_type.value for alert_type in AlertType
    }


# ----------------------------------------------------------------------
# Preferences
# ----------------------------------------------------------------------


def test_a_floor_round_trips_as_a_string() -> None:
    update = StubUpdate()
    client = _client(update=update)

    body = client.patch(
        f"/alerts/channels/{uuid.uuid4()}",
        json={
            "alert_type": "movement",
            "enabled": True,
            "minimum_amount": "20000.50",
            "minimum_currency": "COP",
        },
    ).json()

    [command] = update.commands
    assert command.minimum_amount == Money(
        amount=Decimal("20000.50"),
        currency=Currency.COP,
    )
    [preference] = [
        entry for entry in body["preferences"] if entry["alert_type"] == "movement"
    ]
    assert preference["minimum_amount"] == "20000.50"


def test_clearing_the_floor_sends_no_amount() -> None:
    update = StubUpdate()
    client = _client(update=update)

    client.patch(
        f"/alerts/channels/{uuid.uuid4()}",
        json={"alert_type": "movement", "enabled": True, "minimum_amount": None},
    )

    [command] = update.commands
    assert command.minimum_amount is None


@pytest.mark.parametrize("amount", ["-5", "1E+1000000", "NaN", "abc"])
def test_an_amount_that_is_not_one_is_refused_at_the_boundary(amount: str) -> None:
    response = _client().patch(
        f"/alerts/channels/{uuid.uuid4()}",
        json={"alert_type": "movement", "enabled": True, "minimum_amount": amount},
    )

    assert response.status_code == 422


def test_a_channel_that_is_not_yours_is_not_found() -> None:
    client = _client(update=StubUpdate(raises=ChannelNotFoundError("nope")))

    response = client.patch(
        f"/alerts/channels/{uuid.uuid4()}",
        json={"alert_type": "movement", "enabled": False},
    )

    assert response.status_code == 404


def test_a_malformed_id_reads_as_no_such_channel() -> None:
    """The same answer a stranger's id gets, so ids cannot be enumerated."""
    response = _client().patch(
        "/alerts/channels/not-a-uuid",
        json={"alert_type": "movement", "enabled": False},
    )

    assert response.status_code == 404


# ----------------------------------------------------------------------
# Deleting
# ----------------------------------------------------------------------


def test_deleting_answers_no_content() -> None:
    delete = StubDelete()
    channel_id = uuid.uuid4()

    response = _client(delete=delete).delete(f"/alerts/channels/{channel_id}")

    assert response.status_code == 204
    [command] = delete.commands
    assert command.channel_id.value == channel_id


def test_deleting_something_that_is_not_yours_is_not_found() -> None:
    client = _client(delete=StubDelete(raises=ChannelNotFoundError("nope")))

    assert client.delete(f"/alerts/channels/{uuid.uuid4()}").status_code == 404
