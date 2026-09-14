from datetime import timedelta
from decimal import Decimal

import pytest

from personal_finance.contexts.alerts.domain.entities import (
    MAX_LABEL_LENGTH,
    AlertChannel,
)
from personal_finance.contexts.alerts.domain.events import (
    AlertChannelRevoked,
    AlertChannelVerified,
)
from personal_finance.contexts.alerts.domain.exceptions import (
    ChannelAlreadyVerifiedError,
)
from personal_finance.contexts.alerts.domain.value_objects import (
    AlertPreference,
    AlertType,
    ChannelId,
    ChannelKind,
    ChannelStatus,
    ChatId,
    SecretHash,
)
from personal_finance.shared.domain.value_objects import (
    Currency,
    Money,
    PosixTime,
    UserId,
)


NOW = PosixTime.from_epoch_seconds(1_700_000_000)
USER = UserId.new()
CHAT = ChatId("123456789")
LINK_HASH = SecretHash("sha256:abc")


def _later(seconds: int) -> PosixTime:
    return PosixTime.from_datetime(NOW.to_datetime() + timedelta(seconds=seconds))


def _pending() -> AlertChannel:
    return AlertChannel.pending(
        user_id=USER,
        kind=ChannelKind.TELEGRAM,
        link_hash=LINK_HASH,
        now=NOW,
    )


def _verified(label: str | None = "Ana") -> AlertChannel:
    channel = _pending()
    channel.verify(chat_id=CHAT, label=label, now=NOW)
    channel.pull_events()

    return channel


def _cop(amount: str) -> Money:
    return Money(amount=Decimal(amount), currency=Currency.COP)


# ----------------------------------------------------------------------
# Linking
# ----------------------------------------------------------------------


def test_a_new_channel_has_nowhere_to_send_yet() -> None:
    channel = _pending()

    assert channel.status is ChannelStatus.PENDING
    assert channel.chat_id is None
    assert not channel.is_verified
    assert channel.pending_link_hash == LINK_HASH


def test_binding_spends_the_link_so_nothing_can_bind_it_again() -> None:
    channel = _pending()

    channel.verify(chat_id=CHAT, label="Ana", now=NOW)

    assert channel.pending_link_hash is None


def test_verifying_binds_the_chat_and_records_it() -> None:
    channel = _pending()

    channel.verify(chat_id=CHAT, label="Ana", now=_later(30))

    assert channel.is_verified
    assert channel.chat_id == CHAT
    assert channel.verified_at == _later(30)

    [event] = channel.pull_events()
    assert isinstance(event, AlertChannelVerified)
    assert event.channel_id == channel.id
    assert event.user_id == USER


def test_verifying_twice_with_the_same_chat_changes_nothing() -> None:
    """Telegram redelivers a webhook as a matter of course."""
    channel = _verified()

    channel.verify(chat_id=CHAT, label="Ana", now=_later(60))

    assert channel.verified_at == NOW
    assert channel.pull_events() == []


def test_verifying_with_a_different_chat_is_refused() -> None:
    """Rebinding would move somebody's purchases to another destination."""
    channel = _verified()

    with pytest.raises(ChannelAlreadyVerifiedError):
        channel.verify(chat_id=ChatId("987654321"), label="Otro", now=_later(60))

    assert channel.chat_id == CHAT


def test_a_verified_channel_cannot_be_built_without_a_destination() -> None:
    with pytest.raises(ValueError, match="somewhere to send"):
        AlertChannel(
            id=ChannelId.new(),
            user_id=USER,
            kind=ChannelKind.TELEGRAM,
            status=ChannelStatus.VERIFIED,
            created_at=NOW,
        )


def test_revoking_records_the_fact() -> None:
    channel = _verified()

    channel.revoke()

    [event] = channel.pull_events()
    assert isinstance(event, AlertChannelRevoked)
    assert event.channel_id == channel.id


# ----------------------------------------------------------------------
# The label, which a stranger chose
# ----------------------------------------------------------------------


def test_a_label_is_trimmed_to_something_that_fits_on_one_line() -> None:
    channel = _verified(label="A" * 200)

    assert channel.label is not None
    assert len(channel.label) == MAX_LABEL_LENGTH


def test_control_characters_and_the_rtl_override_are_stripped() -> None:
    """The override redraws everything after it, which is how one name is
    made to display as another. It arrives from a stranger's profile."""
    channel = _verified(label="An\u202ea\x00")

    assert channel.label == "Ana"


def test_a_label_that_is_only_whitespace_is_no_label() -> None:
    channel = _verified(label="   ")

    assert channel.label is None


# ----------------------------------------------------------------------
# The delivery decision
# ----------------------------------------------------------------------


def test_a_pending_channel_is_never_delivered_to() -> None:
    channel = _pending()

    assert not channel.should_deliver(
        alert_type=AlertType.MOVEMENT,
        amount=_cop("84300"),
    )


def test_a_verified_channel_with_no_stated_preference_is_delivered_to() -> None:
    channel = _verified()

    assert channel.should_deliver(
        alert_type=AlertType.MOVEMENT,
        amount=_cop("84300"),
    )


def test_a_switched_off_type_is_not_delivered_to() -> None:
    channel = _verified()

    channel.update_preference(
        AlertPreference(alert_type=AlertType.MOVEMENT, enabled=False),
    )

    assert not channel.should_deliver(
        alert_type=AlertType.MOVEMENT,
        amount=_cop("84300"),
    )


def test_an_amount_below_the_floor_is_not_delivered_to() -> None:
    channel = _verified()

    channel.update_preference(
        AlertPreference(
            alert_type=AlertType.MOVEMENT,
            enabled=True,
            minimum_amount=_cop("100000"),
        ),
    )

    assert not channel.should_deliver(
        alert_type=AlertType.MOVEMENT,
        amount=_cop("84300"),
    )
    assert channel.should_deliver(
        alert_type=AlertType.MOVEMENT,
        amount=_cop("100000"),
    )
