"""The provider-push path end to end, against real (moto) AWS.

A message sits in the mailbox until the provider says something changed; only
then do we read, and only the senders the user approved.
"""

from mypy_boto3_dynamodb.client import DynamoDBClient
from mypy_boto3_sqs.client import SQSClient
import pytest

from personal_finance.contexts.ingestion.application.handlers import (
    ReceiveBankNotificationUseCase,
)
from personal_finance.contexts.ingestion.application.mailbox import (
    InboundEmail,
    MailboxConnection,
    MailboxConnectionStatus,
    MailboxEvent,
    MailboxProvider,
)
from personal_finance.contexts.ingestion.application.sync_handlers import (
    HandleMailboxEventUseCase,
    SyncMailboxUseCase,
)
from personal_finance.contexts.ingestion.domain.entities import UserInbox
from personal_finance.contexts.ingestion.domain.policies import AuthorizedSenderPolicy
from personal_finance.contexts.ingestion.domain.value_objects import (
    EmailAddress,
    EmailMessageId,
)
from personal_finance.contexts.ingestion.infrastructure.mailbox.simulated import (
    MAILBOX_PARTITION_KEY,
    MAILBOX_SORT_KEY,
    SimulatedMailboxReader,
    SimulatedMailboxStore,
)
from personal_finance.contexts.ingestion.infrastructure.messaging.sqs import (
    SQSQueuePublisher,
)
from personal_finance.contexts.ingestion.infrastructure.persistence.dynamodb import (
    DynamoDBBankNotificationRepository,
)
from personal_finance.contexts.ingestion.infrastructure.persistence.mailbox_connection_dynamodb import (  # noqa: E501
    CONNECTION_BY_USER_INDEX,
    CONNECTION_PARTITION_KEY,
    USER_ID_ATTRIBUTE,
    DynamoDBMailboxConnectionRepository,
)
from personal_finance.contexts.ingestion.infrastructure.persistence.user_inbox_dynamodb import (  # noqa: E501
    INBOX_PARTITION_KEY,
    DynamoDBUserInboxRepository,
)
from personal_finance.shared.domain.value_objects import PosixTime, UserId
from personal_finance.shared.infrastructure.aws.provisioning import (
    SecondaryIndex,
    provision_queue,
    provision_table,
)
from personal_finance.shared.infrastructure.observability.logging_event_publisher import (  # noqa: E501
    LoggingEventPublisher,
)


NOTIFICATIONS_TABLE = "bank_notifications"
INBOXES_TABLE = "user_inboxes"
CONNECTIONS_TABLE = "mailbox_connections"
MAILBOX_TABLE = "simulated_mailbox"
QUEUE_NAME = "parse-notifications"

USER_ID = UserId.from_string("11111111-1111-1111-1111-111111111111")
OTHER_USER_ID = UserId.from_string("22222222-2222-2222-2222-222222222222")
MAILBOX = "ana@gmail.test"
BANK = "alertas@bancolombia.com.co"
BODY = (
    "Bancolombia: Compraste $45.000 en EXITO CALI con tu T.Cred *1234, "
    "el 20/08/2026 a las 10:15"
)


@pytest.fixture
def tables(dynamodb_client: DynamoDBClient) -> None:
    provision_table(dynamodb_client, table_name=NOTIFICATIONS_TABLE)
    provision_table(
        dynamodb_client,
        table_name=INBOXES_TABLE,
        partition_key=INBOX_PARTITION_KEY,
        enable_ttl=False,
    )
    provision_table(
        dynamodb_client,
        table_name=CONNECTIONS_TABLE,
        partition_key=CONNECTION_PARTITION_KEY,
        enable_ttl=False,
        secondary_indexes=(
            SecondaryIndex(
                name=CONNECTION_BY_USER_INDEX,
                partition_key=USER_ID_ATTRIBUTE,
            ),
        ),
    )
    provision_table(
        dynamodb_client,
        table_name=MAILBOX_TABLE,
        partition_key=MAILBOX_PARTITION_KEY,
        sort_key=MAILBOX_SORT_KEY,
        enable_ttl=False,
    )


@pytest.fixture
def store(dynamodb_client: DynamoDBClient, tables: None) -> SimulatedMailboxStore:
    del tables

    return SimulatedMailboxStore(client=dynamodb_client, table_name=MAILBOX_TABLE)


@pytest.fixture
def connections(
    dynamodb_client: DynamoDBClient,
    tables: None,
) -> DynamoDBMailboxConnectionRepository:
    del tables

    return DynamoDBMailboxConnectionRepository(
        client=dynamodb_client,
        table_name=CONNECTIONS_TABLE,
    )


@pytest.fixture
def inboxes(
    dynamodb_client: DynamoDBClient,
    tables: None,
) -> DynamoDBUserInboxRepository:
    del tables

    return DynamoDBUserInboxRepository(
        client=dynamodb_client,
        table_name=INBOXES_TABLE,
    )


@pytest.fixture
def handle_event(
    dynamodb_client: DynamoDBClient,
    sqs_client: SQSClient,
    connections: DynamoDBMailboxConnectionRepository,
    inboxes: DynamoDBUserInboxRepository,
) -> HandleMailboxEventUseCase:
    queue_url, _ = provision_queue(sqs_client, queue_name=QUEUE_NAME)

    return HandleMailboxEventUseCase(
        connection_repository=connections,
        sync_use_case=SyncMailboxUseCase(
            readers={
                MailboxProvider.SIMULATED: SimulatedMailboxReader(
                    client=dynamodb_client,
                    table_name=MAILBOX_TABLE,
                ),
            },
            inbox_repository=inboxes,
            connection_repository=connections,
            receive_use_case=ReceiveBankNotificationUseCase(
                repository=DynamoDBBankNotificationRepository(
                    client=dynamodb_client,
                    table_name=NOTIFICATIONS_TABLE,
                    retention_days=90,
                ),
                inbox_repository=inboxes,
                queue_publisher=SQSQueuePublisher(
                    client=sqs_client,
                    queue_url=queue_url,
                ),
                event_publisher=LoggingEventPublisher(),
            ),
        ),
    )


def _authorize(
    inboxes: DynamoDBUserInboxRepository,
    *,
    domains: frozenset[str] = frozenset({"bancolombia.com.co"}),
) -> None:
    inboxes.save(
        UserInbox(
            user_id=USER_ID,
            address=EmailAddress(MAILBOX),
            sender_policy=AuthorizedSenderPolicy(allowed_domains=domains),
        ),
    )


def _connect(
    connections: DynamoDBMailboxConnectionRepository,
    *,
    status: MailboxConnectionStatus = MailboxConnectionStatus.ACTIVE,
) -> None:
    connections.save(
        MailboxConnection(
            user_id=USER_ID,
            address=EmailAddress(MAILBOX),
            provider=MailboxProvider.SIMULATED,
            status=status,
        ),
    )


def _deliver(
    store: SimulatedMailboxStore,
    *,
    sender: str = BANK,
    message_id: str = "<m-1@simulated.test>",
    body: str = BODY,
) -> None:
    store.deliver(
        InboundEmail(
            message_id=EmailMessageId(message_id),
            recipient=EmailAddress(MAILBOX),
            sender=EmailAddress(sender),
            subject="Notificación",
            raw_content=body,
            received_at=PosixTime.now(),
        ),
    )


def _ring() -> MailboxEvent:
    return MailboxEvent(
        provider=MailboxProvider.SIMULATED,
        address=EmailAddress(MAILBOX),
    )


def test_a_notification_pulls_in_the_waiting_mail(
    handle_event: HandleMailboxEventUseCase,
    store: SimulatedMailboxStore,
    connections: DynamoDBMailboxConnectionRepository,
    inboxes: DynamoDBUserInboxRepository,
) -> None:
    _authorize(inboxes)
    _connect(connections)
    _deliver(store)

    result = handle_event.execute(_ring())

    assert result.fetched == 1
    assert result.accepted == 1


def test_nothing_is_read_before_the_provider_says_so(
    store: SimulatedMailboxStore,
    connections: DynamoDBMailboxConnectionRepository,
    inboxes: DynamoDBUserInboxRepository,
    dynamodb_client: DynamoDBClient,
) -> None:
    _authorize(inboxes)
    _connect(connections)
    _deliver(store)

    # The mail exists in the mailbox, but no notification arrived, so nothing
    # was ingested.
    stored = dynamodb_client.scan(TableName=NOTIFICATIONS_TABLE).get("Items", [])
    assert stored == []


def test_a_mailbox_we_hold_no_connection_for_is_never_read(
    handle_event: HandleMailboxEventUseCase,
    store: SimulatedMailboxStore,
    inboxes: DynamoDBUserInboxRepository,
) -> None:
    _authorize(inboxes)
    _deliver(store)

    # No connection was saved: it is not ours to read, whatever the provider
    # claims.
    result = handle_event.execute(_ring())

    assert result.fetched == 0


def test_a_revoked_mailbox_is_never_read(
    handle_event: HandleMailboxEventUseCase,
    store: SimulatedMailboxStore,
    connections: DynamoDBMailboxConnectionRepository,
    inboxes: DynamoDBUserInboxRepository,
) -> None:
    _authorize(inboxes)
    _connect(connections, status=MailboxConnectionStatus.REVOKED)
    _deliver(store)

    result = handle_event.execute(_ring())

    assert result.fetched == 0


def test_nothing_is_read_when_the_user_approved_no_senders(
    handle_event: HandleMailboxEventUseCase,
    store: SimulatedMailboxStore,
    connections: DynamoDBMailboxConnectionRepository,
    inboxes: DynamoDBUserInboxRepository,
) -> None:
    _authorize(inboxes, domains=frozenset())
    _connect(connections)
    _deliver(store)

    # An unfiltered query would pull someone's whole private mailbox.
    result = handle_event.execute(_ring())

    assert result.fetched == 0


def test_only_approved_senders_are_taken_out_of_the_mailbox(
    handle_event: HandleMailboxEventUseCase,
    store: SimulatedMailboxStore,
    connections: DynamoDBMailboxConnectionRepository,
    inboxes: DynamoDBUserInboxRepository,
) -> None:
    _authorize(inboxes)
    _connect(connections)
    _deliver(store, sender="abuela@gmail.com", message_id="<personal@simulated.test>")
    _deliver(store, sender=BANK, message_id="<bank@simulated.test>")

    result = handle_event.execute(_ring())

    # The personal mail was never turned into a notification at all.
    assert result.fetched == 1
    assert result.accepted == 1


def test_a_second_notification_does_not_reread_old_mail(
    handle_event: HandleMailboxEventUseCase,
    store: SimulatedMailboxStore,
    connections: DynamoDBMailboxConnectionRepository,
    inboxes: DynamoDBUserInboxRepository,
) -> None:
    _authorize(inboxes)
    _connect(connections)
    _deliver(store)

    handle_event.execute(_ring())
    second = handle_event.execute(_ring())

    # The cursor moved, so the second doorbell finds nothing new.
    assert second.fetched == 0


def test_the_cursor_survives_and_only_new_mail_is_picked_up(
    handle_event: HandleMailboxEventUseCase,
    store: SimulatedMailboxStore,
    connections: DynamoDBMailboxConnectionRepository,
    inboxes: DynamoDBUserInboxRepository,
) -> None:
    _authorize(inboxes)
    _connect(connections)
    _deliver(store, message_id="<first@simulated.test>")
    handle_event.execute(_ring())

    _deliver(store, message_id="<second@simulated.test>")
    result = handle_event.execute(_ring())

    assert result.fetched == 1
    assert result.accepted == 1

    stored = connections.find(
        provider=MailboxProvider.SIMULATED,
        address=EmailAddress(MAILBOX),
    )
    assert stored is not None
    assert stored.cursor == "2"


def test_skipped_mail_still_advances_the_cursor(
    handle_event: HandleMailboxEventUseCase,
    store: SimulatedMailboxStore,
    connections: DynamoDBMailboxConnectionRepository,
    inboxes: DynamoDBUserInboxRepository,
) -> None:
    _authorize(inboxes)
    _connect(connections)
    _deliver(store, sender="abuela@gmail.com", message_id="<personal@simulated.test>")

    handle_event.execute(_ring())

    # Re-examining skipped mail on every future notification would grow
    # without bound.
    stored = connections.find(
        provider=MailboxProvider.SIMULATED,
        address=EmailAddress(MAILBOX),
    )
    assert stored is not None
    assert stored.cursor == "1"


def test_the_same_message_delivered_twice_is_ingested_once(
    handle_event: HandleMailboxEventUseCase,
    store: SimulatedMailboxStore,
    connections: DynamoDBMailboxConnectionRepository,
    inboxes: DynamoDBUserInboxRepository,
) -> None:
    _authorize(inboxes)
    _connect(connections)
    _deliver(store, message_id="<same@simulated.test>")
    handle_event.execute(_ring())

    # A provider replaying a message it already sent, at a new position.
    _deliver(store, message_id="<same@simulated.test>")
    result = handle_event.execute(_ring())

    assert result.fetched == 1
    assert result.accepted == 0
    assert result.duplicates == 1


def test_connections_are_scoped_to_their_owner(
    connections: DynamoDBMailboxConnectionRepository,
) -> None:
    _connect(connections)
    connections.save(
        MailboxConnection(
            user_id=OTHER_USER_ID,
            address=EmailAddress("theirs@gmail.test"),
            provider=MailboxProvider.SIMULATED,
        ),
    )

    mine = connections.find_by_user(USER_ID)

    assert [connection.address.value for connection in mine] == [MAILBOX]
