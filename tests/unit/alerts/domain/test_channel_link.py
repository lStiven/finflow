from datetime import timedelta
import json

import pytest

from personal_finance.contexts.alerts.domain.linking import ChannelLink
from personal_finance.contexts.alerts.domain.value_objects import (
    ChannelId,
    ChatId,
    SecretHash,
)
from personal_finance.shared.domain.value_objects import PosixTime, UserId


NOW = PosixTime.from_epoch_seconds(1_700_000_000)


def _later(minutes: int) -> PosixTime:
    return PosixTime.from_datetime(NOW.to_datetime() + timedelta(minutes=minutes))


def _link(expires_in_minutes: int = 15) -> ChannelLink:
    return ChannelLink(
        token_hash=SecretHash("sha256:abc"),
        channel_id=ChannelId.new(),
        user_id=UserId.new(),
        expires_at=_later(expires_in_minutes),
    )


def test_a_link_expires_at_its_deadline_not_after_it() -> None:
    link = _link()

    assert not link.is_expired(_later(14))
    assert link.is_expired(_later(15))
    assert link.is_expired(_later(16))


def test_a_link_carries_the_owner_it_was_issued_for() -> None:
    """What stops a link from ever landing on another account."""
    user = UserId.new()
    channel = ChannelId.new()

    link = ChannelLink(
        token_hash=SecretHash("sha256:abc"),
        channel_id=channel,
        user_id=user,
        expires_at=_later(15),
    )

    assert link.user_id == user
    assert link.channel_id == channel


# ----------------------------------------------------------------------
# What these refuse to print
# ----------------------------------------------------------------------


def test_a_token_hash_redacts_itself() -> None:
    assert SecretHash("sha256:abc").to_dict() == "<redacted>"
    assert "abc" not in SecretHash("sha256:abc").to_json()


def test_a_chat_id_is_only_ever_shown_by_its_tail() -> None:
    chat = ChatId("123456789")

    assert chat.masked == "…6789"
    assert chat.to_dict() == "…6789"
    assert "12345" not in json.dumps(chat.to_dict())


def test_neither_may_be_empty() -> None:
    with pytest.raises(ValueError, match="Secret hash"):
        SecretHash("  ")

    with pytest.raises(ValueError, match="Chat id"):
        ChatId("")
