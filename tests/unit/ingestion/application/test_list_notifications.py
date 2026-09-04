"""Listing the mail that arrived for one user.

The screen this answers is "did what I forwarded get here, and what happened
to it" — so the states that produced nothing (ignored, pending fallback) are
as much a part of the answer as the ones that produced a transaction.
"""

from collections import Counter
from collections.abc import Mapping, Sequence

from personal_finance.contexts.ingestion.application.ports import (
    NotificationPage,
    NotificationPageRequest,
    NotificationSummary,
)
from personal_finance.contexts.ingestion.application.queries import (
    ListNotificationsUseCase,
    NotificationQuery,
)
from personal_finance.contexts.ingestion.domain.value_objects import (
    EmailAddress,
    EmailMessageId,
    NotificationDeferredReason,
    NotificationId,
    ProcessingStatus,
)
from personal_finance.shared.domain.value_objects import PosixTime, UserId


USER = UserId.from_string("11111111-1111-1111-1111-111111111111")
OTHER_USER = UserId.from_string("22222222-2222-2222-2222-222222222222")


class InMemoryReader:
    """Answers with what belongs to the user asked for, and nothing else.

    Scoping lives in the real adapter's index query; a fake that ignored it
    would let a test pass while the endpoint leaked somebody else's mail.

    Ordering and windowing live there too, so this stands in for them: it
    counts what each answer had to look at, which is what the paging tests
    below are actually about.
    """

    def __init__(self, *summaries: NotificationSummary) -> None:
        self.summaries = summaries
        self.owners: dict[NotificationId, UserId] = {}
        self.counted: list[UserId] = []
        self.rows_read = 0

    def owned_by(
        self,
        user_id: UserId,
        *summaries: NotificationSummary,
    ) -> "InMemoryReader":
        for summary in summaries:
            self.owners[summary.id] = user_id

        self.summaries = (*self.summaries, *summaries)

        return self

    def page_by_user(self, request: NotificationPageRequest) -> NotificationPage:
        found = [
            summary
            for summary in self._newest_first(request.user_id)
            if request.status is None or summary.status is request.status
        ]
        wanted = request.offset + request.limit
        self.rows_read += len(found[: wanted + 1])

        return NotificationPage(
            notifications=found[request.offset : wanted],
            has_more=len(found) > wanted,
        )

    def count_by_status(self, user_id: UserId) -> Mapping[ProcessingStatus, int]:
        self.counted.append(user_id)
        self.rows_read += len(self._owned(user_id))

        return Counter(summary.status for summary in self._owned(user_id))

    def list_by_user(self, user_id: UserId) -> Sequence[NotificationSummary]:
        return self._owned(user_id)

    def _owned(self, user_id: UserId) -> list[NotificationSummary]:
        return [
            summary
            for summary in self.summaries
            if self.owners.get(summary.id) == user_id
        ]

    def _newest_first(self, user_id: UserId) -> list[NotificationSummary]:
        return sorted(
            self._owned(user_id),
            key=lambda summary: summary.received_at.as_epoch_seconds(),
            reverse=True,
        )


def _summary(
    *,
    message_id: str,
    received_at: int,
    status: ProcessingStatus = ProcessingStatus.PROCESSED,
    deferred_reason: NotificationDeferredReason | None = None,
    sender: str = "alertas@bank.com",
) -> NotificationSummary:
    return NotificationSummary(
        id=NotificationId.for_message(
            user_id=USER,
            message_id=EmailMessageId(message_id),
        ),
        message_id=EmailMessageId(message_id),
        sender=EmailAddress(sender),
        subject="Notificación",
        status=status,
        deferred_reason=deferred_reason,
        received_at=PosixTime.from_epoch_seconds(received_at),
    )


def _use_case(reader: InMemoryReader) -> ListNotificationsUseCase:
    return ListNotificationsUseCase(reader=reader)


def test_the_newest_notification_comes_first() -> None:
    oldest = _summary(message_id="<1@bank.com>", received_at=1_000)
    newest = _summary(message_id="<2@bank.com>", received_at=2_000)
    reader = InMemoryReader().owned_by(USER, oldest, newest)

    page = _use_case(reader).execute(NotificationQuery(user_id=USER))

    assert [summary.message_id for summary in page.notifications] == [
        newest.message_id,
        oldest.message_id,
    ]


def test_only_the_asking_users_mail_is_listed() -> None:
    mine = _summary(message_id="<mine@bank.com>", received_at=1_000)
    theirs = _summary(message_id="<theirs@bank.com>", received_at=2_000)
    reader = InMemoryReader().owned_by(USER, mine).owned_by(OTHER_USER, theirs)

    page = _use_case(reader).execute(NotificationQuery(user_id=USER))

    assert [summary.message_id for summary in page.notifications] == [mine.message_id]
    assert page.has_more is False


def test_a_status_filter_narrows_the_list() -> None:
    ignored = _summary(
        message_id="<ignored@bank.com>",
        received_at=1_000,
        status=ProcessingStatus.IGNORED,
    )
    processed = _summary(message_id="<processed@bank.com>", received_at=2_000)
    reader = InMemoryReader().owned_by(USER, ignored, processed)

    page = _use_case(reader).execute(
        NotificationQuery(user_id=USER, status=ProcessingStatus.IGNORED),
    )

    assert [summary.message_id for summary in page.notifications] == [
        ignored.message_id,
    ]
    assert page.has_more is False


def test_the_counts_cover_everything_the_filter_hides() -> None:
    """The summary beside the list is what tells somebody that three emails
    were ignored — which they would never find by looking at a list filtered
    to something else.
    """
    reader = InMemoryReader().owned_by(
        USER,
        _summary(
            message_id="<ignored@bank.com>",
            received_at=1_000,
            status=ProcessingStatus.IGNORED,
        ),
        _summary(message_id="<processed@bank.com>", received_at=2_000),
        _summary(message_id="<also-processed@bank.com>", received_at=3_000),
    )

    page = _use_case(reader).execute(
        NotificationQuery(
            user_id=USER,
            status=ProcessingStatus.IGNORED,
            with_counts=True,
        ),
    )

    assert page.total == 1
    assert page.counts == {
        ProcessingStatus.IGNORED: 1,
        ProcessingStatus.PROCESSED: 2,
    }


def test_paging_walks_the_whole_list_without_repeating() -> None:
    reader = InMemoryReader().owned_by(
        USER,
        *(
            _summary(message_id=f"<{index}@bank.com>", received_at=1_000 + index)
            for index in range(5)
        ),
    )
    use_case = _use_case(reader)

    first = use_case.execute(NotificationQuery(user_id=USER, limit=2, offset=0))
    second = use_case.execute(NotificationQuery(user_id=USER, limit=2, offset=2))
    third = use_case.execute(NotificationQuery(user_id=USER, limit=2, offset=4))

    seen = [
        summary.message_id
        for page in (first, second, third)
        for summary in page.notifications
    ]

    assert len(seen) == len(set(seen)) == 5
    assert (first.has_more, second.has_more, third.has_more) == (True, True, False)


def test_an_offset_past_the_end_is_an_empty_page_not_an_error() -> None:
    reader = InMemoryReader().owned_by(
        USER,
        _summary(message_id="<1@bank.com>", received_at=1_000),
    )

    page = _use_case(reader).execute(NotificationQuery(user_id=USER, offset=50))

    assert page.notifications == []
    assert page.has_more is False


def test_a_user_with_no_mail_gets_an_empty_answer() -> None:
    page = _use_case(InMemoryReader()).execute(
        NotificationQuery(user_id=USER, with_counts=True),
    )

    assert page.notifications == []
    assert page.total == 0
    assert page.counts == {}


def test_a_deferred_notification_says_why_it_was_deferred() -> None:
    """`pending_fallback` alone reads as "still queued" when it is final."""
    reader = InMemoryReader().owned_by(
        USER,
        _summary(
            message_id="<deferred@bank.com>",
            received_at=1_000,
            status=ProcessingStatus.PENDING_FALLBACK,
            deferred_reason=NotificationDeferredReason.FALLBACK_FOUND_NOTHING,
        ),
    )

    page = _use_case(reader).execute(NotificationQuery(user_id=USER))

    assert page.notifications[0].deferred_reason is (
        NotificationDeferredReason.FALLBACK_FOUND_NOTHING
    )


def test_a_page_reads_the_page_and_not_the_whole_history() -> None:
    """The whole point of the sorted index.

    A list that polls every thirty seconds used to read everything the account
    ever received in order to show twenty rows, and the paging only ever
    shrank the answer. Here forty notifications must not cost forty reads.
    """
    reader = InMemoryReader().owned_by(
        USER,
        *(
            _summary(message_id=f"<{index}@bank.com>", received_at=1_000 + index)
            for index in range(40)
        ),
    )

    page = _use_case(reader).execute(NotificationQuery(user_id=USER, limit=5))

    assert len(page.notifications) == 5
    assert page.has_more is True
    # The five shown plus the one that answers "is there more".
    assert reader.rows_read == 6


def test_nothing_counts_the_whole_history_unless_it_is_asked_to() -> None:
    """Counting is the one answer that has to look at everything, so it is
    the one a caller opts into.
    """
    reader = InMemoryReader().owned_by(
        USER,
        _summary(message_id="<1@bank.com>", received_at=1_000),
    )

    page = _use_case(reader).execute(NotificationQuery(user_id=USER))

    assert reader.counted == []
    assert page.counts is None
    assert page.total is None
