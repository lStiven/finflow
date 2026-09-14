"""The conditional writes, against a real DynamoDB.

Spend-once, one-chat-one-account and verify-only-from-pending are not
properties of the use cases: they are properties of the condition expressions
underneath them. An in-memory double can be written to agree with whatever
the use case does, which is exactly why these run against the real thing.
"""

from datetime import timedelta
from decimal import Decimal
import uuid

from mypy_boto3_dynamodb.client import DynamoDBClient
import pytest

from personal_finance.contexts.alerts.domain.entities import AlertChannel
from personal_finance.contexts.alerts.domain.exceptions import (
    ChannelAlreadyVerifiedError,
    ChatAlreadyLinkedError,
)
from personal_finance.contexts.alerts.domain.linking import ChannelLink
from personal_finance.contexts.alerts.domain.value_objects import (
    AlertPreference,
    AlertType,
    ChannelId,
    ChannelKind,
    ChannelStatus,
    ChatId,
    SecretHash,
)
from personal_finance.contexts.alerts.infrastructure.persistence.dynamodb import (
    PARTITION_KEY,
    SORT_KEY,
    DynamoDBAlertChannelRepository,
    DynamoDBChannelLinkRepository,
    DynamoDBDeliveryLog,
)
from personal_finance.shared.domain.value_objects import (
    Currency,
    Money,
    PosixTime,
    UserId,
)
from personal_finance.shared.infrastructure.aws.provisioning import provision_table


TABLE_NAME = "alert_channels"
NOW = PosixTime.from_epoch_seconds(1_700_000_000)
USER = UserId.new()
OTHER_USER = UserId.new()
CHAT = ChatId("123456789")
TOKEN_HASH = SecretHash("sha256:token")


def _later(minutes: int) -> PosixTime:
    return PosixTime.from_datetime(NOW.to_datetime() + timedelta(minutes=minutes))


@pytest.fixture
def table(dynamodb_client: DynamoDBClient) -> DynamoDBClient:
    provision_table(
        dynamodb_client,
        table_name=TABLE_NAME,
        partition_key=PARTITION_KEY,
        sort_key=SORT_KEY,
        sort_key_type="S",
        enable_ttl=True,
    )

    return dynamodb_client


@pytest.fixture
def channels(table: DynamoDBClient) -> DynamoDBAlertChannelRepository:
    return DynamoDBAlertChannelRepository(client=table, table_name=TABLE_NAME)


@pytest.fixture
def links(table: DynamoDBClient) -> DynamoDBChannelLinkRepository:
    return DynamoDBChannelLinkRepository(client=table, table_name=TABLE_NAME)


@pytest.fixture
def deliveries(table: DynamoDBClient) -> DynamoDBDeliveryLog:
    return DynamoDBDeliveryLog(client=table, table_name=TABLE_NAME)


def _pending(user: UserId = USER) -> AlertChannel:
    return AlertChannel.pending(
        user_id=user,
        kind=ChannelKind.TELEGRAM,
        link_hash=TOKEN_HASH,
        now=NOW,
    )


def _link(channel: AlertChannel, *, token_hash: SecretHash = TOKEN_HASH) -> ChannelLink:
    return ChannelLink(
        token_hash=token_hash,
        channel_id=channel.id,
        user_id=channel.user_id,
        expires_at=_later(15),
    )


# ----------------------------------------------------------------------
# Round tripping
# ----------------------------------------------------------------------


def test_a_channel_round_trips_with_its_preferences(
    channels: DynamoDBAlertChannelRepository,
) -> None:
    channel = _pending()
    channel.update_preference(
        AlertPreference(
            alert_type=AlertType.MOVEMENT,
            enabled=True,
            minimum_amount=Money(amount=Decimal("20000.50"), currency=Currency.COP),
        ),
    )
    channels.save(channel)

    stored = channels.find(user_id=USER, channel_id=channel.id)

    assert stored is not None
    assert stored.status is ChannelStatus.PENDING
    assert stored.pending_link_hash == TOKEN_HASH
    minimum = stored.preferences.for_type(AlertType.MOVEMENT).minimum_amount
    assert minimum is not None
    # The floor survives as a Decimal, to the cent.
    assert minimum.amount == Decimal("20000.50")


def test_a_channel_stored_before_a_type_existed_reads_back_fine(
    channels: DynamoDBAlertChannelRepository,
    table: DynamoDBClient,
) -> None:
    """What makes E2 and E4 need no migration.

    The row is written with no preferences at all — which is what every row
    written today looks like to a deploy that has learned a new alert type.
    """
    channel = _pending()
    channels.save(channel)
    table.update_item(
        TableName=TABLE_NAME,
        Key={
            PARTITION_KEY: {"S": f"USER#{USER.value}"},
            SORT_KEY: {"S": f"CHANNEL#{channel.id.value}"},
        },
        UpdateExpression="REMOVE preferences",
    )

    stored = channels.find(user_id=USER, channel_id=channel.id)

    assert stored is not None
    fallback = stored.preferences.for_type(AlertType.MOVEMENT)
    assert fallback.enabled is AlertType.MOVEMENT.enabled_by_default


def test_a_preference_this_deploy_does_not_know_is_ignored_not_fatal(
    channels: DynamoDBAlertChannelRepository,
    table: DynamoDBClient,
) -> None:
    channel = _pending()
    channels.save(channel)
    table.update_item(
        TableName=TABLE_NAME,
        Key={
            PARTITION_KEY: {"S": f"USER#{USER.value}"},
            SORT_KEY: {"S": f"CHANNEL#{channel.id.value}"},
        },
        UpdateExpression="SET preferences = :preferences",
        ExpressionAttributeValues={
            ":preferences": {
                "L": [
                    {
                        "M": {
                            "alert_type": {"S": "something_from_the_future"},
                            "enabled": {"BOOL": False},
                        },
                    },
                ],
            },
        },
    )

    stored = channels.find(user_id=USER, channel_id=channel.id)

    assert stored is not None
    assert stored.preferences.entries == ()


# ----------------------------------------------------------------------
# Per-user isolation, which is the shape of the key
# ----------------------------------------------------------------------


def test_one_account_cannot_read_another_accounts_channel(
    channels: DynamoDBAlertChannelRepository,
) -> None:
    channel = _pending(USER)
    channels.save(channel)

    assert channels.find(user_id=OTHER_USER, channel_id=channel.id) is None
    assert channels.list_by_user(OTHER_USER) == []


def test_one_account_cannot_delete_another_accounts_channel(
    channels: DynamoDBAlertChannelRepository,
) -> None:
    channel = _pending(USER)
    channels.save(channel)

    assert channels.delete(user_id=OTHER_USER, channel_id=channel.id) is None
    assert channels.find(user_id=USER, channel_id=channel.id) is not None


# ----------------------------------------------------------------------
# Binding: the transaction
# ----------------------------------------------------------------------


def test_binding_writes_the_channel_and_reserves_the_chat_together(
    channels: DynamoDBAlertChannelRepository,
) -> None:
    channel = _pending()
    channels.save(channel)
    channel.verify(chat_id=CHAT, label="Ana", now=NOW)

    channels.save_verified(channel)

    stored = channels.find(user_id=USER, channel_id=channel.id)
    assert stored is not None
    assert stored.status is ChannelStatus.VERIFIED
    assert stored.chat_id == CHAT
    assert stored.pending_link_hash is None


def test_a_chat_held_by_one_account_is_refused_to_another(
    channels: DynamoDBAlertChannelRepository,
) -> None:
    mine = _pending(USER)
    channels.save(mine)
    mine.verify(chat_id=CHAT, label="Ana", now=NOW)
    channels.save_verified(mine)

    theirs = _pending(OTHER_USER)
    channels.save(theirs)
    theirs.verify(chat_id=CHAT, label="Otro", now=NOW)

    with pytest.raises(ChatAlreadyLinkedError):
        channels.save_verified(theirs)

    # And nothing of theirs was written by the half-transaction.
    stored = channels.find(user_id=OTHER_USER, channel_id=theirs.id)
    assert stored is not None
    assert stored.status is ChannelStatus.PENDING


def test_one_account_cannot_point_two_channels_at_one_chat(
    channels: DynamoDBAlertChannelRepository,
) -> None:
    """Otherwise every movement arrives twice — and worse, deleting either
    channel would free a chat the other still delivers to, leaving it open
    for a different account to claim while the first keeps sending."""
    first = _pending(USER)
    channels.save(first)
    first.verify(chat_id=CHAT, label="Ana", now=NOW)
    channels.save_verified(first)

    second = _pending(USER)
    channels.save(second)
    second.verify(chat_id=CHAT, label="Ana", now=NOW)

    with pytest.raises(ChatAlreadyLinkedError):
        channels.save_verified(second)


def test_a_redelivered_binding_of_the_same_channel_is_accepted(
    channels: DynamoDBAlertChannelRepository,
) -> None:
    """Telegram redelivers a webhook as a matter of course."""
    channel = _pending(USER)
    channels.save(channel)
    channel.verify(chat_id=CHAT, label="Ana", now=NOW)
    channels.save_verified(channel)

    # The row is already VERIFIED, so the second write is refused by the
    # channel's own condition rather than by the chat reservation — which is
    # the point: the reservation must not be what stops it.
    with pytest.raises(ChannelAlreadyVerifiedError):
        channels.save_verified(channel)

    stored = channels.find(user_id=USER, channel_id=channel.id)
    assert stored is not None
    assert stored.chat_id == CHAT


def test_the_same_account_may_rebind_the_same_chat(
    channels: DynamoDBAlertChannelRepository,
) -> None:
    """Deleting a channel and linking again is an ordinary thing to do."""
    first = _pending(USER)
    channels.save(first)
    first.verify(chat_id=CHAT, label="Ana", now=NOW)
    channels.save_verified(first)
    channels.delete(user_id=USER, channel_id=first.id)

    second = _pending(USER)
    channels.save(second)
    second.verify(chat_id=CHAT, label="Ana", now=NOW)
    channels.save_verified(second)

    stored = channels.find(user_id=USER, channel_id=second.id)
    assert stored is not None
    assert stored.chat_id == CHAT


def test_a_channel_that_already_moved_on_cannot_be_verified_again(
    channels: DynamoDBAlertChannelRepository,
) -> None:
    """A replayed token, arriving after the first one already won."""
    channel = _pending()
    channels.save(channel)
    channel.verify(chat_id=CHAT, label="Ana", now=NOW)
    channels.save_verified(channel)

    replay = _pending()
    replay.id = channel.id
    replay.verify(chat_id=CHAT, label="Ana", now=NOW)

    with pytest.raises(ChannelAlreadyVerifiedError):
        channels.save_verified(replay)


def test_deleting_a_channel_frees_its_chat(
    channels: DynamoDBAlertChannelRepository,
) -> None:
    mine = _pending(USER)
    channels.save(mine)
    mine.verify(chat_id=CHAT, label="Ana", now=NOW)
    channels.save_verified(mine)

    assert channels.delete(user_id=USER, channel_id=mine.id) == CHAT

    theirs = _pending(OTHER_USER)
    channels.save(theirs)
    theirs.verify(chat_id=CHAT, label="Otro", now=NOW)
    channels.save_verified(theirs)

    stored = channels.find(user_id=OTHER_USER, channel_id=theirs.id)
    assert stored is not None
    assert stored.chat_id == CHAT


# ----------------------------------------------------------------------
# Links: spent once, and never after they expire
# ----------------------------------------------------------------------


def test_a_link_is_spendable_exactly_once(
    channels: DynamoDBAlertChannelRepository,
    links: DynamoDBChannelLinkRepository,
) -> None:
    channel = _pending()
    channels.save(channel)
    links.issue(_link(channel))

    spent = links.spend(token_hash=TOKEN_HASH, now=NOW)

    assert spent is not None
    assert spent.user_id == USER
    assert spent.channel_id == channel.id
    assert links.spend(token_hash=TOKEN_HASH, now=NOW) is None


def test_an_expired_link_is_refused_even_before_the_sweep_removes_it(
    channels: DynamoDBAlertChannelRepository,
    links: DynamoDBChannelLinkRepository,
) -> None:
    """A table's time-to-live is eventual and routinely hours late."""
    channel = _pending()
    channels.save(channel)
    links.issue(_link(channel))

    assert links.spend(token_hash=TOKEN_HASH, now=_later(16)) is None


def test_an_unknown_link_is_refused(
    links: DynamoDBChannelLinkRepository,
) -> None:
    assert links.spend(token_hash=SecretHash("sha256:nope"), now=NOW) is None


def test_a_discarded_link_can_no_longer_be_spent(
    channels: DynamoDBAlertChannelRepository,
    links: DynamoDBChannelLinkRepository,
) -> None:
    channel = _pending()
    channels.save(channel)
    links.issue(_link(channel))

    links.discard(TOKEN_HASH)

    assert links.spend(token_hash=TOKEN_HASH, now=NOW) is None


def test_issuing_over_a_live_hash_is_refused(
    channels: DynamoDBAlertChannelRepository,
    links: DynamoDBChannelLinkRepository,
    table: DynamoDBClient,
) -> None:
    """A collision on 256 random bits does not happen; a bug that reuses a
    hash does, and it would hand one account's link to another."""
    channel = _pending()
    channels.save(channel)
    links.issue(_link(channel))

    with pytest.raises(table.exceptions.ConditionalCheckFailedException):
        links.issue(_link(_pending(OTHER_USER)))


# ----------------------------------------------------------------------
# Delivery markers
# ----------------------------------------------------------------------


def test_a_delivery_is_remembered_per_event_and_channel(
    deliveries: DynamoDBDeliveryLog,
) -> None:
    event_id = uuid.uuid4()
    one = ChannelId.new()
    another = ChannelId.new()

    deliveries.record_delivery(
        user_id=USER,
        event_id=event_id,
        channel_id=one,
        now=NOW,
    )

    assert deliveries.was_delivered(user_id=USER, event_id=event_id, channel_id=one)
    # The other channel has not been told, which is what lets a partial send
    # finish when the message is redelivered.
    assert not deliveries.was_delivered(
        user_id=USER,
        event_id=event_id,
        channel_id=another,
    )


def test_recording_the_same_delivery_twice_is_not_an_error(
    deliveries: DynamoDBDeliveryLog,
) -> None:
    event_id = uuid.uuid4()
    channel_id = ChannelId.new()

    deliveries.record_delivery(
        user_id=USER,
        event_id=event_id,
        channel_id=channel_id,
        now=NOW,
    )
    deliveries.record_delivery(
        user_id=USER,
        event_id=event_id,
        channel_id=channel_id,
        now=NOW,
    )

    assert deliveries.was_delivered(
        user_id=USER,
        event_id=event_id,
        channel_id=channel_id,
    )


def test_one_accounts_delivery_marker_is_not_anothers(
    deliveries: DynamoDBDeliveryLog,
) -> None:
    event_id = uuid.uuid4()
    channel_id = ChannelId.new()

    deliveries.record_delivery(
        user_id=USER,
        event_id=event_id,
        channel_id=channel_id,
        now=NOW,
    )

    assert not deliveries.was_delivered(
        user_id=OTHER_USER,
        event_id=event_id,
        channel_id=channel_id,
    )
