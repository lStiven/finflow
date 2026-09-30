"""The inbox, the recipients and the budget line, against a real DynamoDB.

Three things only the real tables can answer: that a redelivered alert lands
on the same inbox row, that the newest entries come back first from the key
alone, and that the budget line Financial computes — across its ledger, its
budgets and Merchant's categories — is the one that reaches the message.
"""

from __future__ import annotations

from collections.abc import Sequence
import datetime as dt
from decimal import Decimal
import uuid

from mypy_boto3_dynamodb.client import DynamoDBClient
import pytest

from personal_finance.contexts.alerts.application.commands import (
    DeliverMovementAlertCommand,
)
from personal_finance.contexts.alerts.application.handlers import (
    DeliverMovementAlertUseCase,
    SendWeeklySummariesUseCase,
)
from personal_finance.contexts.alerts.application.inbox import InboxEntry, InboxKind
from personal_finance.contexts.alerts.application.messages import (
    BudgetState,
    MovementAlert,
    MovementDirection,
    MovementOrigin,
    WeeklySummary,
)
from personal_finance.contexts.alerts.domain.value_objects import ChatId
from personal_finance.contexts.alerts.infrastructure.financial.adapters import (
    FinancialBudgetStandings,
    FinancialWeeklySpending,
)
from personal_finance.contexts.alerts.infrastructure.persistence.dynamodb import (
    PARTITION_KEY,
    SORT_KEY,
    TTL_ATTRIBUTE,
    DynamoDBAlertChannelRepository,
    DynamoDBDeliveryLog,
)
from personal_finance.contexts.alerts.infrastructure.persistence.inbox import (
    INBOX_PREFIX,
    DynamoDBInbox,
    DynamoDBRecipients,
)
from personal_finance.contexts.financial.application.budget_standing import (
    ReadMovementBudgetsUseCase,
)
from personal_finance.contexts.financial.application.budgets import ReadBudgetsUseCase
from personal_finance.contexts.financial.application.queries import (
    SummarizeSpendingUseCase,
)
from personal_finance.contexts.financial.application.weekly_spending import (
    ReadWeeklySpendingUseCase,
)
from personal_finance.contexts.financial.domain.budgets import Budget, BudgetScope
from personal_finance.contexts.financial.domain.entities import Transaction
from personal_finance.contexts.financial.domain.value_objects import (
    MovementDirection as FinancialDirection,
)
from personal_finance.contexts.financial.infrastructure.merchant.merchant_directory import (  # noqa: E501
    MerchantContextDirectory,
)
from personal_finance.contexts.financial.infrastructure.persistence.dynamodb import (
    PARTITION_KEY as FINANCIAL_PARTITION_KEY,
    SORT_KEY as FINANCIAL_SORT_KEY,
    DynamoDBAccountRepository,
    DynamoDBBudgetRepository,
    DynamoDBTransactionLedger,
)
from personal_finance.contexts.merchant.application.categories import CategoryCatalog
from personal_finance.contexts.merchant.application.commands import (
    ClassifyCounterpartyCommand,
)
from personal_finance.contexts.merchant.application.handlers import (
    ClassifyCounterpartyUseCase,
)
from personal_finance.contexts.merchant.application.queries import (
    AttributeCounterpartiesUseCase,
)
from personal_finance.contexts.merchant.domain.value_objects import CategoryKey
from personal_finance.contexts.merchant.infrastructure.persistence.dynamodb import (
    PARTITION_KEY as MERCHANT_PARTITION_KEY,
    SORT_KEY as MERCHANT_SORT_KEY,
    DynamoDBCategoryRepository,
    DynamoDBMerchantRepository,
)
from personal_finance.shared.domain.events import Event
from personal_finance.shared.domain.value_objects import (
    Currency,
    Money,
    PosixTime,
    UserId,
)
from personal_finance.shared.infrastructure.aws.provisioning import provision_table


ALERTS_TABLE = "alert_channels"
FINANCIAL_TABLE = "financial"
MERCHANT_TABLE = "merchants"
TIMEZONE = "America/Bogota"

USER = UserId.new()
OTHER = UserId.new()


class NullPublisher:
    def publish(self, events: Sequence[Event]) -> None:
        del events


class RecordingSender:
    def __init__(self) -> None:
        self.alerts: list[MovementAlert] = []
        self.summaries: list[WeeklySummary] = []

    def send_movement_alert(self, *, chat_id: ChatId, alert: MovementAlert) -> None:
        self.alerts.append(alert)

    def send_weekly_summary(self, *, chat_id: ChatId, summary: WeeklySummary) -> None:
        self.summaries.append(summary)

    def send_link_confirmation(self, *, chat_id: ChatId) -> None:
        raise NotImplementedError

    def send_chat_already_linked(self, *, chat_id: ChatId) -> None:
        raise NotImplementedError


@pytest.fixture
def tables(dynamodb_client: DynamoDBClient) -> DynamoDBClient:
    provision_table(
        dynamodb_client,
        table_name=ALERTS_TABLE,
        partition_key=PARTITION_KEY,
        sort_key=SORT_KEY,
        sort_key_type="S",
        enable_ttl=True,
    )
    provision_table(
        dynamodb_client,
        table_name=FINANCIAL_TABLE,
        partition_key=FINANCIAL_PARTITION_KEY,
        sort_key=FINANCIAL_SORT_KEY,
        sort_key_type="S",
        enable_ttl=False,
    )
    provision_table(
        dynamodb_client,
        table_name=MERCHANT_TABLE,
        partition_key=MERCHANT_PARTITION_KEY,
        sort_key=MERCHANT_SORT_KEY,
        sort_key_type="S",
    )

    return dynamodb_client


def _alert(movement_id: str | None = None) -> MovementAlert:
    return MovementAlert(
        amount=Money(amount=Decimal("84300"), currency=Currency.COP),
        direction=MovementDirection.OUTGOING,
        counterparty="COMPRA EN EXITO",
        bank="Bancolombia",
        occurred_at=PosixTime.now(),
        origin=MovementOrigin.BANK_ALERT,
        unassigned=False,
        movement_id=movement_id,
    )


def _entry(user: UserId, at: int, event_id: uuid.UUID | None = None) -> InboxEntry:
    return InboxEntry(
        user_id=user,
        entry_id=event_id or uuid.uuid4(),
        created_at=PosixTime.from_epoch_seconds(at),
        kind=InboxKind.MOVEMENT,
        movement=_alert(),
    )


# ------------------------------------------------------------------ inbox


def test_the_newest_entries_come_back_first_and_only_the_owners(
    tables: DynamoDBClient,
) -> None:
    inbox = DynamoDBInbox(client=tables, table_name=ALERTS_TABLE)
    now = PosixTime.now().as_epoch_seconds()
    for offset in (30, 10, 20):
        inbox.record(_entry(USER, now - offset))
    inbox.record(_entry(OTHER, now))

    listed = inbox.recent(user_id=USER, limit=2)

    assert [entry.created_at.as_epoch_seconds() for entry in listed] == [
        now - 10,
        now - 20,
    ]


def test_the_same_fact_written_twice_is_one_row(tables: DynamoDBClient) -> None:
    inbox = DynamoDBInbox(client=tables, table_name=ALERTS_TABLE)
    at = PosixTime.now().as_epoch_seconds()
    event_id = uuid.uuid4()

    inbox.record(_entry(USER, at, event_id))
    inbox.record(_entry(USER, at, event_id))

    rows = tables.query(
        TableName=ALERTS_TABLE,
        KeyConditionExpression=(
            f"{PARTITION_KEY} = :user AND begins_with({SORT_KEY}, :p)"
        ),
        ExpressionAttributeValues={
            ":user": {"S": f"USER#{USER.value}"},
            ":p": {"S": INBOX_PREFIX},
        },
    )["Items"]
    assert len(rows) == 1
    assert TTL_ATTRIBUTE in rows[0]


def test_an_entry_past_its_time_is_not_shown_before_the_sweep(
    tables: DynamoDBClient,
) -> None:
    inbox = DynamoDBInbox(client=tables, table_name=ALERTS_TABLE)
    inbox.record(_entry(USER, PosixTime.now().as_epoch_seconds() - 40 * 86_400))

    assert inbox.recent(user_id=USER, limit=10) == []


def test_every_fact_survives_the_round_trip(tables: DynamoDBClient) -> None:
    inbox = DynamoDBInbox(client=tables, table_name=ALERTS_TABLE)
    summary = WeeklySummary(
        week_start=dt.date(2026, 9, 21),
        week_end=dt.date(2026, 9, 27),
        currency=Currency.COP,
        spent=Decimal("820000.50"),
        movements=12,
        typical=None,
        rise=None,
    )
    inbox.record(
        InboxEntry(
            user_id=USER,
            entry_id=uuid.uuid4(),
            created_at=PosixTime.now(),
            kind=InboxKind.WEEKLY_SUMMARY,
            summary=summary,
        ),
    )

    [entry] = inbox.recent(user_id=USER, limit=5)

    assert entry.summary == summary


def test_recipients_are_remembered_once_each(tables: DynamoDBClient) -> None:
    recipients = DynamoDBRecipients(client=tables, table_name=ALERTS_TABLE)

    recipients.remember(user_id=USER, now=PosixTime.now())
    recipients.remember(user_id=USER, now=PosixTime.now())
    recipients.remember(user_id=OTHER, now=PosixTime.now())

    assert {user.value for user in recipients.everyone()} == {USER.value, OTHER.value}


# ---------------------------------------------------------- budget line


def _financial(
    tables: DynamoDBClient,
) -> tuple[
    DynamoDBTransactionLedger,
    DynamoDBBudgetRepository,
    MerchantContextDirectory,
    SummarizeSpendingUseCase,
    ClassifyCounterpartyUseCase,
]:
    ledger = DynamoDBTransactionLedger(client=tables, table_name=FINANCIAL_TABLE)
    budgets = DynamoDBBudgetRepository(client=tables, table_name=FINANCIAL_TABLE)
    merchants = DynamoDBMerchantRepository(client=tables, table_name=MERCHANT_TABLE)
    catalog = CategoryCatalog(
        repository=DynamoDBCategoryRepository(client=tables, table_name=MERCHANT_TABLE),
    )
    classify = ClassifyCounterpartyUseCase(
        repository=merchants,
        event_publisher=NullPublisher(),
        categories=catalog,
    )
    directory = MerchantContextDirectory(
        use_case=AttributeCounterpartiesUseCase(repository=merchants),
        catalog=catalog,
        classify=classify,
    )
    spending = SummarizeSpendingUseCase(
        ledger=ledger,
        accounts=DynamoDBAccountRepository(client=tables, table_name=FINANCIAL_TABLE),
        merchants=directory,
    )

    return ledger, budgets, directory, spending, classify


def test_a_purchase_reaches_telegram_and_the_inbox_with_its_budgets_standing(
    tables: DynamoDBClient,
) -> None:
    ledger, budgets, directory, spending, classify = _financial(tables)
    now = PosixTime.now()
    classify.execute(
        ClassifyCounterpartyCommand(
            user_id=USER,
            counterparty="COMPRA EN EXITO",
            category=CategoryKey(value="groceries"),
            occurred_at=now,
        ),
    )
    budgets.save(
        Budget.declare(
            user_id=USER,
            name="Mercado",
            limit=Money(amount=Decimal("600000"), currency=Currency.COP),
            scope=BudgetScope.of(categories=frozenset({"groceries"})),
        ),
    )
    budgets.save(
        Budget.declare(
            user_id=USER,
            name="Salidas",
            limit=Money(amount=Decimal("300000"), currency=Currency.COP),
            scope=BudgetScope.of(categories=frozenset({"restaurants"})),
        ),
    )
    movement = Transaction.enter_manually(
        user_id=USER,
        direction=FinancialDirection.OUTGOING,
        amount=Money(amount=Decimal("84300"), currency=Currency.COP),
        occurred_at=now,
        counterparty="COMPRA EN EXITO",
    )
    ledger.record(transaction=movement, balance_delta=None)

    sender = RecordingSender()
    inbox = DynamoDBInbox(client=tables, table_name=ALERTS_TABLE)
    DeliverMovementAlertUseCase(
        channels=DynamoDBAlertChannelRepository(client=tables, table_name=ALERTS_TABLE),
        deliveries=DynamoDBDeliveryLog(client=tables, table_name=ALERTS_TABLE),
        sender=sender,
        budgets=FinancialBudgetStandings(
            use_case=ReadMovementBudgetsUseCase(
                ledger=ledger,
                budgets=ReadBudgetsUseCase(
                    budgets=budgets,
                    spending=spending,
                    merchants=directory,
                ),
                merchants=directory,
            ),
            timezone=TIMEZONE,
        ),
        inbox=inbox,
        recipients=DynamoDBRecipients(client=tables, table_name=ALERTS_TABLE),
    ).execute(
        DeliverMovementAlertCommand(
            user_id=USER,
            event_id=uuid.uuid4(),
            alert=_alert(movement.id.value),
            recorded_at=now,
        ),
    )

    [entry] = inbox.recent(user_id=USER, limit=5)
    assert entry.movement is not None
    [mercado] = entry.movement.budgets
    # Only the budget over groceries: restaurants does not cover this.
    assert mercado.name == "Mercado"
    assert mercado.remaining == Decimal("515700")
    assert mercado.state is BudgetState.OK
    # Nobody linked Telegram, and the inbox has it anyway.
    assert sender.alerts == []


def test_monday_reads_the_real_ledger_and_writes_the_inbox(
    tables: DynamoDBClient,
) -> None:
    ledger, _, directory, spending, _ = _financial(tables)
    zone_noon = dt.time(17)
    last_monday = dt.date(2026, 9, 21)
    for day, amount in (
        (last_monday - dt.timedelta(days=5), "100000"),
        (last_monday, "40000"),
    ):
        ledger.record(
            transaction=Transaction.enter_manually(
                user_id=USER,
                direction=FinancialDirection.OUTGOING,
                amount=Money(amount=Decimal(amount), currency=Currency.COP),
                occurred_at=PosixTime.from_datetime(
                    dt.datetime.combine(day, zone_noon, tzinfo=dt.UTC),
                ),
                counterparty="COMPRA EN EXITO",
            ),
            balance_delta=None,
        )
    recipients = DynamoDBRecipients(client=tables, table_name=ALERTS_TABLE)
    recipients.remember(user_id=USER, now=PosixTime.now())
    inbox = DynamoDBInbox(client=tables, table_name=ALERTS_TABLE)

    run = SendWeeklySummariesUseCase(
        recipients=recipients,
        spending=FinancialWeeklySpending(
            use_case=ReadWeeklySpendingUseCase(
                ledger=ledger,
                spending=spending,
                categories=directory,
            ),
            timezone=TIMEZONE,
        ),
        channels=DynamoDBAlertChannelRepository(client=tables, table_name=ALERTS_TABLE),
        deliveries=DynamoDBDeliveryLog(client=tables, table_name=ALERTS_TABLE),
        sender=RecordingSender(),
        inbox=inbox,
    ).execute(week_of=last_monday, today=last_monday + dt.timedelta(days=8))

    assert run.summaries == 1
    stored = [
        entry.summary
        for entry in inbox.recent(user_id=USER, limit=5)
        if entry.summary is not None
    ]
    [summary] = stored
    assert summary.spent == Decimal("40000")
    assert summary.typical == Decimal("100000.00")
