"""The opt-in backfill path end to end, against real (moto) AWS.

Unlike the ordinary sync, a backfill is allowed to read mail that arrived
before the mailbox was ever connected — but only within an explicit date
range, and only from approved senders. It exists for someone who signs up
partway through the month and would otherwise have a first month of history
missing whatever came before they connected.
"""

from mypy_boto3_dynamodb.client import DynamoDBClient
from mypy_boto3_sqs.client import SQSClient
import pytest

from personal_finance.contexts.ingestion.application.backfill_handlers import (
    BackfillMailboxUseCase,
    BackfillUserMailboxesUseCase,
)
from personal_finance.contexts.ingestion.application.handlers import (
    ReceiveBankNotificationUseCase,
)
from personal_finance.contexts.ingestion.application.mailbox import (
    InboundEmail,
    MailboxConnection,
    MailboxProvider,
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
MAILBOX = "ana@gmail.test"
BANK = "alertas@bancolombia.com.co"
BODY = "Bancolombia: Compraste $45.000 en EXITO CALI"


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
def backfill_user(
    dynamodb_client: DynamoDBClient,
    sqs_client: SQSClient,
    connections: DynamoDBMailboxConnectionRepository,
    inboxes: DynamoDBUserInboxRepository,
) -> BackfillUserMailboxesUseCase:
    queue_url, _ = provision_queue(sqs_client, queue_name=QUEUE_NAME)

    return BackfillUserMailboxesUseCase(
        connection_repository=connections,
        backfill_use_case=BackfillMailboxUseCase(
            readers={
                MailboxProvider.SIMULATED: SimulatedMailboxReader(
                    client=dynamodb_client,
                    table_name=MAILBOX_TABLE,
                ),
            },
            inbox_repository=inboxes,
            receive_use_case=ReceiveBankNotificationUseCase(
                repository=DynamoDBBankNotificationRepository(
                    client=dynamodb_client,
                    table_name=NOTIFICATIONS_TABLE,
                    retention_days=90,
                ),
                inbox_repository=inboxes,
                queue_publisher=SQSQueuePublisher(
                    client=sqs_client, queue_url=queue_url
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


def _connect(connections: DynamoDBMailboxConnectionRepository) -> None:
    # No cursor, no subscription: exactly the state right after an OAuth
    # callback finishes but before any push notification has ever arrived.
    connections.save(
        MailboxConnection(
            user_id=USER_ID,
            address=EmailAddress(MAILBOX),
            provider=MailboxProvider.SIMULATED,
        ),
    )


def _deliver(
    store: SimulatedMailboxStore,
    *,
    sender: str = BANK,
    message_id: str = "<m-1@simulated.test>",
    received_at: PosixTime | None = None,
) -> None:
    store.deliver(
        InboundEmail(
            message_id=EmailMessageId(message_id),
            recipient=EmailAddress(MAILBOX),
            sender=EmailAddress(sender),
            subject="Notificación",
            raw_content=BODY,
            received_at=received_at or PosixTime.now(),
        ),
    )


def test_backfill_finds_mail_that_arrived_before_the_mailbox_was_ever_connected(
    backfill_user: BackfillUserMailboxesUseCase,
    store: SimulatedMailboxStore,
    connections: DynamoDBMailboxConnectionRepository,
    inboxes: DynamoDBUserInboxRepository,
) -> None:
    _authorize(inboxes)
    _deliver(store)
    # Connected only after the mail already existed — the ordinary push-based
    # sync would never see it, because it only ever reads forward.
    _connect(connections)

    result = backfill_user.execute(USER_ID)

    assert result.mailboxes == 1
    assert result.accepted == 1


def test_backfill_only_takes_approved_senders(
    backfill_user: BackfillUserMailboxesUseCase,
    store: SimulatedMailboxStore,
    connections: DynamoDBMailboxConnectionRepository,
    inboxes: DynamoDBUserInboxRepository,
) -> None:
    _authorize(inboxes)
    _deliver(store, sender="abuela@gmail.com", message_id="<personal@simulated.test>")
    _deliver(store, sender=BANK, message_id="<bank@simulated.test>")
    _connect(connections)

    result = backfill_user.execute(USER_ID)

    assert result.accepted == 1


def test_backfill_ignores_mail_from_before_the_current_month(
    backfill_user: BackfillUserMailboxesUseCase,
    store: SimulatedMailboxStore,
    connections: DynamoDBMailboxConnectionRepository,
    inboxes: DynamoDBUserInboxRepository,
) -> None:
    _authorize(inboxes)
    last_month = PosixTime.from_epoch_seconds(
        PosixTime.now().as_epoch_seconds() - 40 * 24 * 3_600,
    )
    _deliver(store, received_at=last_month)
    _connect(connections)

    result = backfill_user.execute(USER_ID)

    assert result.accepted == 0


def test_backfill_does_not_move_the_incremental_cursor(
    backfill_user: BackfillUserMailboxesUseCase,
    store: SimulatedMailboxStore,
    connections: DynamoDBMailboxConnectionRepository,
    inboxes: DynamoDBUserInboxRepository,
) -> None:
    _authorize(inboxes)
    _deliver(store)
    _connect(connections)

    backfill_user.execute(USER_ID)

    stored = connections.find(
        provider=MailboxProvider.SIMULATED,
        address=EmailAddress(MAILBOX),
    )
    assert stored is not None
    assert stored.cursor is None


def test_running_backfill_twice_only_ingests_once(
    backfill_user: BackfillUserMailboxesUseCase,
    store: SimulatedMailboxStore,
    connections: DynamoDBMailboxConnectionRepository,
    inboxes: DynamoDBUserInboxRepository,
) -> None:
    _authorize(inboxes)
    _deliver(store)
    _connect(connections)

    backfill_user.execute(USER_ID)
    second = backfill_user.execute(USER_ID)

    assert second.accepted == 0
    assert second.duplicates == 1
