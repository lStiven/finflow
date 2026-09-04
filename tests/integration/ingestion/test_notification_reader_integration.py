"""Reading one user's notifications off `by_user_received_at`, against DynamoDB.

The index is what keeps the cost of this list independent of everybody else's
mail *and* of the reader's own history — sorted by arrival, so a page is read
as a page rather than sliced out of everything the account ever received. It
is also what makes the scoping structural rather than a filter somebody has to
remember to apply. None of that is true unless the table is actually
provisioned with it, which is why this test provisions the real thing.
"""

from mypy_boto3_dynamodb.client import DynamoDBClient
import pytest

from personal_finance.contexts.ingestion.application.ports import (
    NotificationPageRequest,
)
from personal_finance.contexts.ingestion.domain.entities import BankNotification
from personal_finance.contexts.ingestion.domain.value_objects import (
    EmailAddress,
    EmailMessageId,
    NotificationDeferredReason,
    NotificationIgnoredReason,
    ProcessingStatus,
)
from personal_finance.contexts.ingestion.infrastructure.persistence.dynamodb import (
    NOTIFICATIONS_BY_USER_INDEX,
    RECEIVED_AT_ATTRIBUTE,
    SUMMARY_ATTRIBUTES,
    USER_ID_ATTRIBUTE,
    DynamoDBBankNotificationRepository,
    DynamoDBNotificationReader,
)
from personal_finance.shared.domain.value_objects import PosixTime, UserId
from personal_finance.shared.infrastructure.aws.provisioning import (
    SecondaryIndex,
    provision_table,
)


TABLE_NAME = "bank_notifications"
RETENTION_DAYS = 90
USER = UserId.from_string("11111111-1111-1111-1111-111111111111")
OTHER_USER = UserId.from_string("22222222-2222-2222-2222-222222222222")


@pytest.fixture
def table(dynamodb_client: DynamoDBClient) -> DynamoDBClient:
    provision_table(
        dynamodb_client,
        table_name=TABLE_NAME,
        secondary_indexes=(
            SecondaryIndex(
                name=NOTIFICATIONS_BY_USER_INDEX,
                partition_key=USER_ID_ATTRIBUTE,
                sort_key=RECEIVED_AT_ATTRIBUTE,
                projected_attributes=SUMMARY_ATTRIBUTES,
            ),
        ),
    )

    return dynamodb_client


@pytest.fixture
def repository(table: DynamoDBClient) -> DynamoDBBankNotificationRepository:
    return DynamoDBBankNotificationRepository(
        client=table,
        table_name=TABLE_NAME,
        retention_days=RETENTION_DAYS,
    )


@pytest.fixture
def reader(table: DynamoDBClient) -> DynamoDBNotificationReader:
    return DynamoDBNotificationReader(client=table, table_name=TABLE_NAME)


def _notification(
    *,
    user_id: UserId = USER,
    message_id: str = "<1@bank.com>",
    received_at: int = 1_787_000_000,
) -> BankNotification:
    return BankNotification.receive(
        user_id=user_id,
        message_id=EmailMessageId(message_id),
        sender=EmailAddress("alerts@bank.com"),
        subject="Purchase notification",
        raw_content="Bancolombia: Compraste $45.000 en EXITO",
        received_at=PosixTime.from_epoch_seconds(received_at),
    )


def test_a_stored_notification_comes_back_as_a_summary(
    repository: DynamoDBBankNotificationRepository,
    reader: DynamoDBNotificationReader,
) -> None:
    repository.add_if_new(_notification())

    summaries = reader.list_by_user(USER)

    assert len(summaries) == 1
    assert summaries[0].message_id == EmailMessageId("<1@bank.com>")
    assert summaries[0].sender == EmailAddress("alerts@bank.com")
    assert summaries[0].status is ProcessingStatus.RECEIVED
    assert summaries[0].received_at.as_epoch_seconds() == 1_787_000_000


def test_it_returns_nothing_that_belongs_to_another_user(
    repository: DynamoDBBankNotificationRepository,
    reader: DynamoDBNotificationReader,
) -> None:
    repository.add_if_new(_notification(message_id="<mine@bank.com>"))
    repository.add_if_new(
        _notification(user_id=OTHER_USER, message_id="<theirs@bank.com>"),
    )

    assert [summary.message_id.value for summary in reader.list_by_user(USER)] == [
        "<mine@bank.com>",
    ]


def test_a_user_with_no_mail_gets_an_empty_list(
    reader: DynamoDBNotificationReader,
) -> None:
    assert reader.list_by_user(USER) == []


def test_an_ignored_notification_is_still_listed(
    repository: DynamoDBBankNotificationRepository,
    reader: DynamoDBNotificationReader,
) -> None:
    """An unapproved sender is exactly what somebody needs to see: the email
    arrived, it was refused, and approving that sender is the fix.
    """
    notification = _notification()
    notification.ignore(reason=NotificationIgnoredReason.UNAUTHORIZED_SENDER)
    repository.add_if_new(notification)

    summaries = reader.list_by_user(USER)

    assert summaries[0].status is ProcessingStatus.IGNORED
    assert summaries[0].sender == EmailAddress("alerts@bank.com")


def test_a_deferred_notification_keeps_its_reason(
    repository: DynamoDBBankNotificationRepository,
    reader: DynamoDBNotificationReader,
) -> None:
    notification = _notification()
    repository.add_if_new(notification)
    notification.mark_as_queued()
    notification.start_processing()
    notification.defer_to_fallback(
        reason=NotificationDeferredReason.NO_FALLBACK_CONFIGURED,
    )
    repository.save(notification)

    summary = reader.list_by_user(USER)[0]

    assert summary.status is ProcessingStatus.PENDING_FALLBACK
    assert summary.deferred_reason is (
        NotificationDeferredReason.NO_FALLBACK_CONFIGURED
    )


def test_the_index_does_not_carry_the_email_body(
    repository: DynamoDBBankNotificationRepository,
    table: DynamoDBClient,
) -> None:
    """The projection is the enforcement: an index over `ALL` would keep a
    second copy of every untrusted message for a list that never shows one.
    """
    repository.add_if_new(_notification())

    items = table.query(
        TableName=TABLE_NAME,
        IndexName=NOTIFICATIONS_BY_USER_INDEX,
        KeyConditionExpression=f"{USER_ID_ATTRIBUTE} = :user_id",
        ExpressionAttributeValues={":user_id": {"S": str(USER.value)}},
    )["Items"]

    assert "raw_content" not in items[0]


def test_a_page_comes_back_newest_first(
    repository: DynamoDBBankNotificationRepository,
    reader: DynamoDBNotificationReader,
) -> None:
    """The order is the index's, not a sort in memory — which is what lets the
    query stop at the end of the page.
    """
    for offset in range(4):
        repository.add_if_new(
            _notification(
                message_id=f"<{offset}@bank.com>",
                received_at=1_787_000_000 + offset,
            ),
        )

    page = reader.page_by_user(NotificationPageRequest(user_id=USER, limit=2))

    assert [summary.message_id.value for summary in page.notifications] == [
        "<3@bank.com>",
        "<2@bank.com>",
    ]
    assert page.has_more is True


def test_a_page_reads_only_its_own_window(
    repository: DynamoDBBankNotificationRepository,
    reader: DynamoDBNotificationReader,
    table: DynamoDBClient,
) -> None:
    """The gap this closes: the list used to read the account's whole history
    on every refresh and throw away everything past the window.
    """
    for offset in range(30):
        repository.add_if_new(
            _notification(
                message_id=f"<{offset}@bank.com>",
                received_at=1_787_000_000 + offset,
            ),
        )

    scanned: list[int] = []
    queried = table.query

    def counting_query(**kwargs: object) -> object:
        answer = queried(**kwargs)  # type: ignore[arg-type]
        scanned.append(answer["ScannedCount"])

        return answer

    table.query = counting_query  # type: ignore[assignment]
    page = reader.page_by_user(NotificationPageRequest(user_id=USER, limit=5))

    assert len(page.notifications) == 5
    # The five shown plus the one that answers "is there another page".
    assert sum(scanned) == 6


def test_a_later_page_does_not_repeat_the_first(
    repository: DynamoDBBankNotificationRepository,
    reader: DynamoDBNotificationReader,
) -> None:
    for offset in range(5):
        repository.add_if_new(
            _notification(
                message_id=f"<{offset}@bank.com>",
                received_at=1_787_000_000 + offset,
            ),
        )

    first = reader.page_by_user(NotificationPageRequest(user_id=USER, limit=2))
    second = reader.page_by_user(
        NotificationPageRequest(user_id=USER, limit=2, offset=2),
    )
    last = reader.page_by_user(
        NotificationPageRequest(user_id=USER, limit=2, offset=4),
    )
    seen = [
        summary.message_id.value
        for page in (first, second, last)
        for summary in page.notifications
    ]

    assert len(seen) == len(set(seen)) == 5
    assert last.has_more is False


def test_a_status_filter_is_applied_by_dynamodb(
    repository: DynamoDBBankNotificationRepository,
    reader: DynamoDBNotificationReader,
) -> None:
    ignored = _notification(message_id="<ignored@bank.com>")
    ignored.ignore(reason=NotificationIgnoredReason.UNAUTHORIZED_SENDER)
    repository.add_if_new(ignored)
    repository.add_if_new(
        _notification(message_id="<received@bank.com>", received_at=1_787_000_100),
    )

    page = reader.page_by_user(
        NotificationPageRequest(
            user_id=USER,
            limit=10,
            status=ProcessingStatus.IGNORED,
        ),
    )

    assert [summary.message_id.value for summary in page.notifications] == [
        "<ignored@bank.com>",
    ]
    assert page.has_more is False


def test_a_page_never_reaches_another_users_mail(
    repository: DynamoDBBankNotificationRepository,
    reader: DynamoDBNotificationReader,
) -> None:
    repository.add_if_new(_notification(message_id="<mine@bank.com>"))
    repository.add_if_new(
        _notification(
            user_id=OTHER_USER,
            message_id="<theirs@bank.com>",
            received_at=1_787_000_100,
        ),
    )

    page = reader.page_by_user(NotificationPageRequest(user_id=USER, limit=10))

    assert [summary.message_id.value for summary in page.notifications] == [
        "<mine@bank.com>",
    ]


def test_the_counts_cover_every_status_the_user_has(
    repository: DynamoDBBankNotificationRepository,
    reader: DynamoDBNotificationReader,
) -> None:
    ignored = _notification(message_id="<ignored@bank.com>")
    ignored.ignore(reason=NotificationIgnoredReason.UNAUTHORIZED_SENDER)
    repository.add_if_new(ignored)
    repository.add_if_new(
        _notification(message_id="<received@bank.com>", received_at=1_787_000_100),
    )
    repository.add_if_new(
        _notification(user_id=OTHER_USER, message_id="<theirs@bank.com>"),
    )

    assert reader.count_by_status(USER) == {
        ProcessingStatus.IGNORED: 1,
        ProcessingStatus.RECEIVED: 1,
    }


def test_the_counts_of_a_user_with_no_mail_are_empty(
    reader: DynamoDBNotificationReader,
) -> None:
    assert reader.count_by_status(USER) == {}


def test_a_nearly_full_page_does_not_finish_one_round_trip_per_row(
    repository: DynamoDBBankNotificationRepository,
    reader: DynamoDBNotificationReader,
    table: DynamoDBClient,
) -> None:
    """A filter is applied after the read, so `Limit` has to stay the whole
    window on every round.

    Asking only for the rows still missing looks thriftier and is the
    opposite: as matches accumulate the window shrinks towards one, so a page
    that fills up and then keeps looking costs a blocking call per row scanned
    — on a list that polls. Here twenty matches are followed by a hundred rows
    that never match.
    """
    for offset in range(100):
        repository.add_if_new(
            _notification(
                message_id=f"<{offset}@bank.com>",
                received_at=1_787_000_000 + offset,
            ),
        )

    for offset in range(20):
        ignored = _notification(
            message_id=f"<ignored-{offset}@bank.com>",
            received_at=1_787_000_100 + offset,
        )
        ignored.ignore(reason=NotificationIgnoredReason.UNAUTHORIZED_SENDER)
        repository.add_if_new(ignored)

    rounds = 0
    queried = table.query

    def counting_query(**kwargs: object) -> object:
        nonlocal rounds
        rounds += 1

        return queried(**kwargs)  # type: ignore[arg-type]

    table.query = counting_query  # type: ignore[assignment]
    page = reader.page_by_user(
        NotificationPageRequest(
            user_id=USER,
            limit=20,
            status=ProcessingStatus.IGNORED,
        ),
    )

    assert len(page.notifications) == 20
    assert page.has_more is False
    # 120 rows, 21 evaluated per round.
    assert rounds <= 6
