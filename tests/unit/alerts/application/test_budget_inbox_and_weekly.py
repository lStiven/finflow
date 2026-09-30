"""The budget line under a purchase, the in-app inbox, and Monday's summary.

The budget line is an enrichment: an answer that fails must never cost the
alert. The inbox is for everybody, channel or not. And Monday's job must be
safe to run twice — nobody gets the same summary twice — and one person's bad
data must not stop anybody else's.
"""

from __future__ import annotations

from collections.abc import Sequence
import datetime as dt
from decimal import Decimal
import uuid

import pytest

from personal_finance.contexts.alerts.application.commands import (
    DeliverMovementAlertCommand,
)
from personal_finance.contexts.alerts.application.handlers import (
    DeliverMovementAlertUseCase,
    SendWeeklySummariesUseCase,
    WeekNotOverError,
    weekly_created_at,
    weekly_entry_id,
)
from personal_finance.contexts.alerts.application.inbox import (
    InboxEntry,
    InboxKind,
    ListInboxUseCase,
)
from personal_finance.contexts.alerts.application.messages import (
    BudgetStanding,
    BudgetState,
    MovementAlert,
    MovementDirection,
    MovementOrigin,
    WeeklySummary,
)
from personal_finance.contexts.alerts.application.ports import (
    DestinationRefusedError,
    TransportUnavailableError,
)
from personal_finance.contexts.alerts.domain.entities import AlertChannel
from personal_finance.contexts.alerts.domain.value_objects import (
    AlertPreference,
    AlertType,
    ChannelId,
    ChannelKind,
    ChatId,
    SecretHash,
)
from personal_finance.shared.domain.value_objects import (
    Currency,
    Money,
    PosixTime,
    UserId,
)


USER = UserId.new()
OTHER = UserId.new()
NOW = PosixTime.from_epoch_seconds(1_790_000_000)
RECORDED = PosixTime.from_epoch_seconds(1_789_999_000)
MONDAY = dt.date(2026, 9, 21)
# The Monday after: the week read is over.
AFTER = dt.date(2026, 9, 28)

SALIDAS = BudgetStanding(
    name="Salidas",
    currency=Currency.COP,
    limit=Decimal("600000"),
    spent=Decimal("480000"),
    remaining=Decimal("120000"),
    state=BudgetState.WARNING,
)


# ------------------------------------------------------------------- fakes


class Channels:
    def __init__(self, *channels: AlertChannel) -> None:
        self._channels = list(channels)

    def find(self, *, user_id: UserId, channel_id: ChannelId) -> AlertChannel | None:
        return next(
            (
                channel
                for channel in self._channels
                if channel.user_id == user_id and channel.id == channel_id
            ),
            None,
        )

    def list_by_user(self, user_id: UserId) -> Sequence[AlertChannel]:
        return [channel for channel in self._channels if channel.user_id == user_id]

    def save(self, channel: AlertChannel) -> None:
        raise NotImplementedError

    def save_verified(self, channel: AlertChannel) -> None:
        raise NotImplementedError

    def delete(self, *, user_id: UserId, channel_id: ChannelId) -> ChatId | None:
        raise NotImplementedError


class Deliveries:
    def __init__(self) -> None:
        self.marks: set[tuple[UserId, uuid.UUID, ChannelId]] = set()

    def was_delivered(
        self,
        *,
        user_id: UserId,
        event_id: uuid.UUID,
        channel_id: ChannelId,
    ) -> bool:
        return (user_id, event_id, channel_id) in self.marks

    def record_delivery(
        self,
        *,
        user_id: UserId,
        event_id: uuid.UUID,
        channel_id: ChannelId,
        now: PosixTime,
    ) -> None:
        self.marks.add((user_id, event_id, channel_id))


class Sender:
    def __init__(self) -> None:
        self.alerts: list[MovementAlert] = []
        self.summaries: list[tuple[ChatId, WeeklySummary]] = []
        self.refuse: set[ChatId] = set()
        self.down: set[ChatId] = set()

    def send_movement_alert(self, *, chat_id: ChatId, alert: MovementAlert) -> None:
        self.alerts.append(alert)

    def send_weekly_summary(self, *, chat_id: ChatId, summary: WeeklySummary) -> None:
        if chat_id in self.down:
            raise TransportUnavailableError("down")
        if chat_id in self.refuse:
            raise DestinationRefusedError("blocked")
        self.summaries.append((chat_id, summary))

    def send_link_confirmation(self, *, chat_id: ChatId) -> None:
        raise NotImplementedError

    def send_chat_already_linked(self, *, chat_id: ChatId) -> None:
        raise NotImplementedError


class Inbox:
    """Keyed like the real one, so a second copy of a fact is a no-op."""

    def __init__(self) -> None:
        self.entries: dict[tuple[UserId, int, uuid.UUID], InboxEntry] = {}
        self.writes = 0

    def record(self, entry: InboxEntry) -> None:
        self.writes += 1
        key = (entry.user_id, entry.created_at.as_epoch_seconds(), entry.entry_id)
        self.entries.setdefault(key, entry)

    def recent(self, *, user_id: UserId, limit: int) -> Sequence[InboxEntry]:
        mine = [
            entry for (owner, _, _), entry in self.entries.items() if owner == user_id
        ]
        mine.sort(key=lambda entry: entry.created_at.as_epoch_seconds(), reverse=True)
        return mine[:limit]


class Recipients:
    def __init__(self, *users: UserId) -> None:
        self.users = list(users)

    def remember(self, *, user_id: UserId, now: PosixTime) -> None:
        if user_id not in self.users:
            self.users.append(user_id)

    def everyone(self) -> Sequence[UserId]:
        return list(self.users)


class Budgets:
    def __init__(self, *standings: BudgetStanding, fails: bool = False) -> None:
        self._standings = standings
        self._fails = fails
        self.asked: list[str] = []

    def covering(
        self,
        *,
        user_id: UserId,
        movement_id: str,
    ) -> Sequence[BudgetStanding]:
        self.asked.append(movement_id)
        if self._fails:
            raise RuntimeError("the table had a bad second")
        return list(self._standings)


class Weeks:
    def __init__(
        self, answers: dict[UserId, list[WeeklySummary]] | None = None
    ) -> None:
        self.answers = answers or {}
        self.broken: set[UserId] = set()

    def week(self, *, user_id: UserId, week_of: dt.date) -> Sequence[WeeklySummary]:
        if user_id in self.broken:
            raise ValueError("odd data")
        return self.answers.get(user_id, [])


def _channel(
    user_id: UserId = USER,
    chat: str = "1001",
    *,
    weekly: bool = True,
) -> AlertChannel:
    channel = AlertChannel.pending(
        user_id=user_id,
        kind=ChannelKind.TELEGRAM,
        link_hash=SecretHash(value=f"hash-{chat}"),
        now=NOW,
    )
    channel.verify(chat_id=ChatId(value=chat), label=None, now=NOW)
    if not weekly:
        channel.update_preference(
            AlertPreference(alert_type=AlertType.WEEKLY_SUMMARY, enabled=False),
        )
    return channel


def _alert(
    *,
    direction: MovementDirection = MovementDirection.OUTGOING,
    origin: MovementOrigin = MovementOrigin.BANK_ALERT,
    movement_id: str | None = "abc123",
) -> MovementAlert:
    return MovementAlert(
        amount=Money(amount=Decimal("84300"), currency=Currency.COP),
        direction=direction,
        counterparty="COMPRA EN EXITO",
        bank="Bancolombia",
        occurred_at=NOW,
        origin=origin,
        unassigned=False,
        movement_id=movement_id,
    )


def _command(
    alert: MovementAlert | None = None,
    *,
    user_id: UserId = USER,
    event_id: uuid.UUID | None = None,
) -> DeliverMovementAlertCommand:
    return DeliverMovementAlertCommand(
        user_id=user_id,
        event_id=event_id or uuid.uuid4(),
        alert=alert or _alert(),
        recorded_at=RECORDED,
    )


def _deliverer(
    *,
    channels: Channels | None = None,
    sender: Sender | None = None,
    budgets: Budgets | None = None,
    inbox: Inbox | None = None,
    recipients: Recipients | None = None,
) -> DeliverMovementAlertUseCase:
    return DeliverMovementAlertUseCase(
        channels=channels or Channels(),
        deliveries=Deliveries(),
        sender=sender or Sender(),
        budgets=budgets,
        inbox=inbox,
        recipients=recipients,
    )


# ------------------------------------------------------------ budget line


def test_a_purchase_under_a_budget_carries_its_standing_to_telegram() -> None:
    sender = Sender()
    budgets = Budgets(SALIDAS)

    _deliverer(channels=Channels(_channel()), sender=sender, budgets=budgets).execute(
        _command(),
    )

    [sent] = sender.alerts
    assert sent.budgets == (SALIDAS,)
    assert budgets.asked == ["abc123"]


def test_a_purchase_under_no_budget_is_the_alert_it_always_was() -> None:
    sender = Sender()

    _deliverer(channels=Channels(_channel()), sender=sender, budgets=Budgets()).execute(
        _command(),
    )

    [sent] = sender.alerts
    assert sent.budgets == ()


def test_income_never_asks_about_budgets() -> None:
    budgets = Budgets(SALIDAS)

    _deliverer(channels=Channels(_channel()), budgets=budgets).execute(
        _command(_alert(direction=MovementDirection.INCOMING)),
    )

    assert budgets.asked == []


def test_an_alert_without_a_movement_id_never_asks_either() -> None:
    budgets = Budgets(SALIDAS)

    _deliverer(channels=Channels(_channel()), budgets=budgets).execute(
        _command(_alert(movement_id=None)),
    )

    assert budgets.asked == []


def test_a_budget_read_that_fails_still_sends_the_alert() -> None:
    sender = Sender()

    delivered = _deliverer(
        channels=Channels(_channel()),
        sender=sender,
        budgets=Budgets(SALIDAS, fails=True),
    ).execute(_command())

    assert delivered == 1
    [sent] = sender.alerts
    assert sent.budgets == ()


# ------------------------------------------------------------------ inbox


def test_the_inbox_keeps_the_alert_even_with_no_channel_at_all() -> None:
    inbox = Inbox()

    delivered = _deliverer(inbox=inbox, budgets=Budgets(SALIDAS)).execute(_command())

    assert delivered == 0
    [entry] = inbox.entries.values()
    assert entry.kind is InboxKind.MOVEMENT
    assert entry.movement is not None
    assert entry.movement.budgets == (SALIDAS,)
    assert entry.created_at == RECORDED


def test_a_redelivered_alert_is_one_inbox_entry() -> None:
    inbox = Inbox()
    event_id = uuid.uuid4()
    deliverer = _deliverer(inbox=inbox)

    deliverer.execute(_command(event_id=event_id))
    deliverer.execute(_command(event_id=event_id))

    assert len(inbox.entries) == 1


def test_an_accrual_is_neither_announced_nor_kept() -> None:
    inbox = Inbox()
    recipients = Recipients()

    _deliverer(inbox=inbox, recipients=recipients).execute(
        _command(_alert(origin=MovementOrigin.ACCRUAL)),
    )

    assert inbox.entries == {}
    assert recipients.users == []


def test_somebody_alerted_becomes_a_recipient_of_the_weekly_summary() -> None:
    recipients = Recipients()

    _deliverer(recipients=recipients).execute(_command())

    assert recipients.users == [USER]


def test_the_inbox_lists_only_its_owners_entries_newest_first() -> None:
    inbox = Inbox()
    deliverer = _deliverer(inbox=inbox)
    first = _command()
    deliverer.execute(first)
    deliverer.execute(
        DeliverMovementAlertCommand(
            user_id=USER,
            event_id=uuid.uuid4(),
            alert=_alert(),
            recorded_at=PosixTime.from_epoch_seconds(RECORDED.as_epoch_seconds() + 60),
        ),
    )
    deliverer.execute(_command(user_id=OTHER))

    listed = ListInboxUseCase(inbox=inbox).execute(user_id=USER, limit=10)

    assert len(listed) == 2
    assert (
        listed[0].created_at.as_epoch_seconds()
        > listed[1].created_at.as_epoch_seconds()
    )
    assert all(entry.user_id == USER for entry in listed)


def test_the_page_size_is_clamped() -> None:
    inbox = Inbox()
    deliverer = _deliverer(inbox=inbox)
    for offset in range(60):
        deliverer.execute(
            DeliverMovementAlertCommand(
                user_id=USER,
                event_id=uuid.uuid4(),
                alert=_alert(),
                recorded_at=PosixTime.from_epoch_seconds(
                    RECORDED.as_epoch_seconds() + offset
                ),
            ),
        )

    assert len(ListInboxUseCase(inbox=inbox).execute(user_id=USER, limit=500)) == 50
    assert len(ListInboxUseCase(inbox=inbox).execute(user_id=USER, limit=0)) == 1


def test_an_entry_must_carry_the_facts_its_kind_names() -> None:
    with pytest.raises(ValueError, match="needs its facts"):
        InboxEntry(
            user_id=USER,
            entry_id=uuid.uuid4(),
            created_at=NOW,
            kind=InboxKind.WEEKLY_SUMMARY,
            movement=_alert(),
        )


# ------------------------------------------------------------------ weekly


def _week(
    currency: Currency = Currency.COP,
    spent: str = "820000",
) -> WeeklySummary:
    return WeeklySummary(
        week_start=MONDAY,
        week_end=MONDAY + dt.timedelta(days=6),
        currency=currency,
        spent=Decimal(spent),
        movements=12,
        typical=Decimal("930000"),
        rise=None,
    )


def _weekly(
    *,
    recipients: Recipients,
    weeks: Weeks,
    channels: Channels,
    sender: Sender,
    inbox: Inbox,
    deliveries: Deliveries | None = None,
) -> SendWeeklySummariesUseCase:
    return SendWeeklySummariesUseCase(
        recipients=recipients,
        spending=weeks,
        channels=channels,
        deliveries=deliveries or Deliveries(),
        sender=sender,
        inbox=inbox,
    )


def test_monday_tells_every_verified_channel_and_keeps_it_in_the_inbox() -> None:
    sender = Sender()
    inbox = Inbox()

    run = _weekly(
        recipients=Recipients(USER),
        weeks=Weeks({USER: [_week()]}),
        channels=Channels(_channel()),
        sender=sender,
        inbox=inbox,
    ).execute(week_of=MONDAY, today=AFTER)

    assert (run.users, run.summaries, run.delivered, run.failed) == (1, 1, 1, 0)
    assert [summary.spent for _, summary in sender.summaries] == [Decimal("820000")]
    [entry] = inbox.entries.values()
    assert entry.kind is InboxKind.WEEKLY_SUMMARY
    assert entry.created_at == weekly_created_at(_week())


def test_running_monday_twice_sends_nothing_twice() -> None:
    sender = Sender()
    inbox = Inbox()
    deliveries = Deliveries()
    job = _weekly(
        recipients=Recipients(USER),
        weeks=Weeks({USER: [_week()]}),
        channels=Channels(_channel()),
        sender=sender,
        inbox=inbox,
        deliveries=deliveries,
    )

    job.execute(week_of=MONDAY, today=AFTER)
    second = job.execute(week_of=MONDAY, today=AFTER)

    assert len(sender.summaries) == 1
    assert len(inbox.entries) == 1
    assert second.delivered == 0


def test_a_channel_that_turned_the_summary_off_is_not_told() -> None:
    sender = Sender()

    _weekly(
        recipients=Recipients(USER),
        weeks=Weeks({USER: [_week()]}),
        channels=Channels(_channel(weekly=False)),
        sender=sender,
        inbox=Inbox(),
    ).execute(week_of=MONDAY, today=AFTER)

    assert sender.summaries == []


def test_nothing_to_say_sends_nothing_and_keeps_nothing() -> None:
    sender = Sender()
    inbox = Inbox()

    run = _weekly(
        recipients=Recipients(USER),
        weeks=Weeks(),
        channels=Channels(_channel()),
        sender=sender,
        inbox=inbox,
    ).execute(week_of=MONDAY, today=AFTER)

    assert run.summaries == 0
    assert sender.summaries == []
    assert inbox.entries == {}


def test_only_the_busiest_currency_is_sent_but_every_one_is_kept() -> None:
    sender = Sender()
    inbox = Inbox()

    _weekly(
        recipients=Recipients(USER),
        weeks=Weeks({USER: [_week(), _week(Currency.USD, "120")]}),
        channels=Channels(_channel()),
        sender=sender,
        inbox=inbox,
    ).execute(week_of=MONDAY, today=AFTER)

    assert [summary.currency for _, summary in sender.summaries] == [Currency.COP]
    assert len(inbox.entries) == 2


def test_one_persons_bad_data_does_not_stop_anybody_else() -> None:
    sender = Sender()
    weeks = Weeks({USER: [_week()], OTHER: [_week()]})
    weeks.broken.add(USER)

    run = _weekly(
        recipients=Recipients(USER, OTHER),
        weeks=weeks,
        channels=Channels(_channel(), _channel(OTHER, "2002")),
        sender=sender,
        inbox=Inbox(),
    ).execute(week_of=MONDAY, today=AFTER)

    assert run.failed == 1
    assert [chat.value for chat, _ in sender.summaries] == ["2002"]


def test_a_blocked_chat_is_skipped_without_failing_the_run() -> None:
    sender = Sender()
    sender.refuse.add(ChatId(value="1001"))

    run = _weekly(
        recipients=Recipients(USER),
        weeks=Weeks({USER: [_week()]}),
        channels=Channels(_channel()),
        sender=sender,
        inbox=Inbox(),
    ).execute(week_of=MONDAY, today=AFTER)

    assert (run.delivered, run.failed) == (0, 0)


def test_a_transport_that_is_down_fails_the_run_after_telling_everyone_else() -> None:
    sender = Sender()
    sender.down.add(ChatId(value="1001"))
    deliveries = Deliveries()
    job = _weekly(
        recipients=Recipients(USER, OTHER),
        weeks=Weeks({USER: [_week()], OTHER: [_week()]}),
        channels=Channels(_channel(), _channel(OTHER, "2002")),
        sender=sender,
        inbox=Inbox(),
        deliveries=deliveries,
    )

    with pytest.raises(TransportUnavailableError):
        job.execute(week_of=MONDAY, today=AFTER)

    # The other one was told; the retry tells only the one that was missed.
    assert [chat.value for chat, _ in sender.summaries] == ["2002"]
    sender.down.clear()
    job.execute(week_of=MONDAY, today=AFTER)
    assert sorted(chat.value for chat, _ in sender.summaries) == ["1001", "2002"]


def test_a_weeks_id_is_the_same_on_every_run_and_differs_per_user_and_currency() -> (
    None
):
    assert weekly_entry_id(USER, _week()) == weekly_entry_id(USER, _week())
    assert weekly_entry_id(USER, _week()) != weekly_entry_id(OTHER, _week())
    assert weekly_entry_id(USER, _week()) != weekly_entry_id(
        USER,
        _week(Currency.USD),
    )


def test_the_summary_is_dated_monday_eight_in_the_morning_in_bogota() -> None:
    moment = weekly_created_at(_week()).to_datetime()

    assert moment == dt.datetime(2026, 9, 28, 13, tzinfo=dt.UTC)


@pytest.mark.parametrize("today", [MONDAY, dt.date(2026, 9, 24), dt.date(2026, 9, 27)])
def test_a_week_that_has_not_ended_is_refused(today: dt.date) -> None:
    # A week's id is fixed: half a week sent now would stand in for the
    # whole one on Monday, and the real figures would never go out.
    sender = Sender()
    inbox = Inbox()

    with pytest.raises(WeekNotOverError):
        _weekly(
            recipients=Recipients(USER),
            weeks=Weeks({USER: [_week()]}),
            channels=Channels(_channel()),
            sender=sender,
            inbox=inbox,
        ).execute(week_of=MONDAY, today=today)

    assert sender.summaries == []
    assert inbox.entries == {}


def test_the_monday_after_is_the_first_day_a_week_can_be_summarised() -> None:
    run = _weekly(
        recipients=Recipients(USER),
        weeks=Weeks({USER: [_week()]}),
        channels=Channels(_channel()),
        sender=Sender(),
        inbox=Inbox(),
    ).execute(week_of=dt.date(2026, 9, 27), today=AFTER)

    assert run.summaries == 1
