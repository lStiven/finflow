import dataclasses
import json

from mypy_boto3_dynamodb.client import DynamoDBClient
from mypy_boto3_sqs.client import SQSClient
import pytest

from personal_finance.contexts.ingestion.application.commands import (
    ReceiveBankNotificationCommand,
)
from personal_finance.contexts.ingestion.application.handlers import (
    ReceiveBankNotificationUseCase,
    ReceiveOutcome,
)
from personal_finance.contexts.ingestion.domain.entities import UserInbox
from personal_finance.contexts.ingestion.domain.policies import AuthorizedSenderPolicy
from personal_finance.contexts.ingestion.domain.value_objects import (
    EmailAddress,
    EmailMessageId,
    ProcessingStatus,
)
from personal_finance.contexts.ingestion.infrastructure.messaging.sqs import (
    SQSQueuePublisher,
)
from personal_finance.contexts.ingestion.infrastructure.persistence.dynamodb import (
    DynamoDBBankNotificationRepository,
)
from personal_finance.shared.domain.value_objects import PosixTime, UserId
from personal_finance.shared.infrastructure.aws.provisioning import (
    provision_queue,
    provision_table,
)
from personal_finance.shared.infrastructure.observability.logging_event_publisher import (  # noqa: E501
    LoggingEventPublisher,
)


TABLE_NAME = "bank_notifications"
QUEUE_NAME = "parse-notifications"

INBOX_ADDRESS = "u-7f3a9c@inbound.finflow.test"
USER_ID = UserId.from_string("11111111-1111-1111-1111-111111111111")


class InMemoryUserInboxRepository:
    def __init__(self, *inboxes: UserInbox) -> None:
        self.inboxes = {inbox.address: inbox for inbox in inboxes}

    def find_by_address(self, address: EmailAddress) -> UserInbox | None:
        return self.inboxes.get(address)

    def find_by_user(self, user_id: UserId) -> list[UserInbox]:
        return [inbox for inbox in self.inboxes.values() if inbox.user_id == user_id]

    def save(self, inbox: UserInbox) -> None:
        self.inboxes[inbox.address] = inbox

    def mark_forwarding_confirmed(
        self,
        *,
        address: EmailAddress,
        confirmed_at: PosixTime,
    ) -> bool:
        return self._mark(address, forwarding_confirmed_at=confirmed_at)

    def mark_first_accepted(
        self,
        *,
        address: EmailAddress,
        accepted_at: PosixTime,
    ) -> bool:
        return self._mark(address, first_accepted_at=accepted_at)

    def _mark(self, address: EmailAddress, **milestone: PosixTime) -> bool:
        inbox = self.inboxes.get(address)

        if inbox is None:
            return False

        self.inboxes[address] = dataclasses.replace(inbox, **milestone)

        return True


def _inbox(
    *,
    address: str = INBOX_ADDRESS,
    domains: frozenset[str] = frozenset({"bank.com"}),
) -> UserInbox:
    return UserInbox(
        user_id=USER_ID,
        address=EmailAddress(address),
        sender_policy=AuthorizedSenderPolicy(allowed_domains=domains),
    )


@pytest.fixture
def queue_url(sqs_client: SQSClient) -> str:
    url, _ = provision_queue(sqs_client, queue_name=QUEUE_NAME)

    return url


@pytest.fixture
def use_case(
    dynamodb_client: DynamoDBClient,
    sqs_client: SQSClient,
    queue_url: str,
) -> ReceiveBankNotificationUseCase:
    provision_table(dynamodb_client, table_name=TABLE_NAME)

    return ReceiveBankNotificationUseCase(
        repository=DynamoDBBankNotificationRepository(
            client=dynamodb_client,
            table_name=TABLE_NAME,
            retention_days=90,
        ),
        inbox_repository=InMemoryUserInboxRepository(_inbox()),
        queue_publisher=SQSQueuePublisher(client=sqs_client, queue_url=queue_url),
        event_publisher=LoggingEventPublisher(),
    )


def _command(*, sender: str = "alerts@bank.com") -> ReceiveBankNotificationCommand:
    return ReceiveBankNotificationCommand(
        recipient=EmailAddress(INBOX_ADDRESS),
        message_id=EmailMessageId("message-1"),
        sender=EmailAddress(sender),
        subject="Purchase notification",
        raw_content="Purchase for COP 50,000",
        received_at=PosixTime.now(),
    )


def _drain(sqs_client: SQSClient, queue_url: str) -> list[str]:
    received = sqs_client.receive_message(QueueUrl=queue_url, MaxNumberOfMessages=10)

    return [
        body
        for message in received.get("Messages", [])
        if (body := message.get("Body")) is not None
    ]


def test_notification_is_stored_and_queued(
    use_case: ReceiveBankNotificationUseCase,
    sqs_client: SQSClient,
    queue_url: str,
) -> None:
    result = use_case.execute(_command())

    assert result.status is ProcessingStatus.QUEUED
    bodies = _drain(sqs_client, queue_url)
    assert len(bodies) == 1
    assert result.notification_id is not None
    assert json.loads(bodies[0])["notification_id"] == str(
        result.notification_id.value,
    )


def test_redelivery_is_queued_exactly_once(
    use_case: ReceiveBankNotificationUseCase,
    sqs_client: SQSClient,
    queue_url: str,
) -> None:
    first = use_case.execute(_command())
    second = use_case.execute(_command())
    third = use_case.execute(_command())

    assert first.outcome is ReceiveOutcome.ACCEPTED
    assert (second.outcome, third.outcome) == (
        ReceiveOutcome.DUPLICATE,
        ReceiveOutcome.DUPLICATE,
    )
    assert second.notification_id == first.notification_id
    # The whole point of the conditional write: at-least-once redelivery of the
    # same email produces exactly one unit of downstream work.
    assert len(_drain(sqs_client, queue_url)) == 1


def test_unauthorized_sender_reaches_storage_but_not_the_queue(
    use_case: ReceiveBankNotificationUseCase,
    sqs_client: SQSClient,
    queue_url: str,
) -> None:
    result = use_case.execute(_command(sender="phisher@evil.com"))

    assert result.status is ProcessingStatus.IGNORED
    assert _drain(sqs_client, queue_url) == []
