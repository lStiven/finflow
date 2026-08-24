"""End-to-end proof that a parsed bank alert becomes a balance.

The chain up to Financial's boundary is entirely real: ingestion's own
translator puts `TransactionExtracted` on a real EventBridge bus, a rule
routes it to a real queue, and Financial's own Pydantic schema reads what
comes off that queue back into a command. Nothing in the payload is
hand-built, which is the point — a field ingestion renames, or a number it
serializes differently, breaks here rather than in production.

What is *not* real yet is the last hop: Financial has no worker and no
persistence, so `_record` stands in for the use case that will own the
conditional write. It is written to make that gap visible rather than to
paper over it — see the assertions about redelivery, which show what the
ledger will have to enforce and what it can rely on the domain for.
"""

from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal

from mypy_boto3_events.client import EventBridgeClient
from mypy_boto3_sqs.client import SQSClient
import pytest

from personal_finance.contexts.financial.application.commands import (
    RecordMovementCommand,
)
from personal_finance.contexts.financial.domain.entities import Account, Transaction
from personal_finance.contexts.financial.domain.value_objects import (
    AccountCategory,
    BalanceSign,
    MovementDirection,
    TransactionStatus,
)
from personal_finance.contexts.financial.infrastructure.messaging.inbound import (
    INGESTION_SOURCE,
    TRANSACTION_EXTRACTED,
    IntegrationEventEnvelope,
    TransactionExtractedDetail,
)
from personal_finance.contexts.ingestion.application.integration_events import (
    IngestionIntegrationEventTranslator,
)
from personal_finance.contexts.ingestion.domain.events import TransactionExtracted
from personal_finance.contexts.ingestion.domain.transactions import (
    ExtractedTransaction,
    Instrument,
    InstrumentKind,
    TransactionDirection,
    TransactionKind,
)
from personal_finance.contexts.ingestion.domain.value_objects import (
    EmailMessageId,
    NotificationId,
)
from personal_finance.shared.domain.value_objects import (
    Currency,
    Money,
    PosixTime,
    UserId,
)
from personal_finance.shared.infrastructure.aws.eventbridge import (
    EventBridgeEventPublisher,
)
from personal_finance.shared.infrastructure.aws.provisioning import (
    provision_context_subscription,
    provision_event_bus,
)


BUS = "finflow"
QUEUE = "financial-events"
# Named here rather than imported: Financial has no worker yet, so production
# owns no rule for this queue. When it does, these move into provisioning.
RULE = "finflow-financial-transactions"
TARGET_ID = "financial-events-queue"

USER_ID = UserId.from_string("11111111-1111-1111-1111-111111111111")
MESSAGE_ID = EmailMessageId("<abc@bancolombia.com.co>")
PURCHASE_TIME = PosixTime.from_datetime(datetime(2026, 8, 23, 14, 5, tzinfo=UTC))

CREDIT_CARD = Instrument(kind=InstrumentKind.CREDIT_CARD, last_four="7653")
DEBIT_CARD = Instrument(kind=InstrumentKind.DEBIT_CARD, last_four="1234")


@pytest.fixture
def queue_url(
    eventbridge_client: EventBridgeClient,
    sqs_client: SQSClient,
) -> str:
    provision_event_bus(eventbridge_client, event_bus_name=BUS)
    url, _ = provision_context_subscription(
        eventbridge_client,
        sqs_client,
        event_bus_name=BUS,
        queue_name=QUEUE,
        rule_name=RULE,
        target_id=TARGET_ID,
        event_pattern={
            "source": [INGESTION_SOURCE],
            "detail-type": [TRANSACTION_EXTRACTED],
        },
    )

    return url


@pytest.fixture
def publisher(eventbridge_client: EventBridgeClient) -> EventBridgeEventPublisher:
    return EventBridgeEventPublisher(
        client=eventbridge_client,
        event_bus_name=BUS,
        translator=IngestionIntegrationEventTranslator(),
    )


def _alert(
    *,
    counterparty: str = "TIENDAS ARA 123",
    amount: str = "50000.50",
    instrument: Instrument | None = CREDIT_CARD,
    user_id: UserId = USER_ID,
) -> TransactionExtracted:
    return TransactionExtracted(
        notification_id=NotificationId.for_message(
            user_id=user_id,
            message_id=MESSAGE_ID,
        ),
        user_id=user_id,
        message_id=MESSAGE_ID,
        transaction=ExtractedTransaction(
            kind=TransactionKind.CARD_PURCHASE,
            direction=TransactionDirection.OUTGOING,
            amount=Money(amount=Decimal(amount), currency=Currency.COP),
            occurred_at=PURCHASE_TIME,
            counterparty=counterparty,
            bank="Bancolombia",
            instrument=instrument,
        ),
    )


def _drain(sqs_client: SQSClient, queue_url: str) -> list[RecordMovementCommand]:
    """Everything on the queue, read the way Financial's worker will read it."""
    response = sqs_client.receive_message(
        QueueUrl=queue_url,
        MaxNumberOfMessages=10,
        WaitTimeSeconds=0,
    )
    commands: list[RecordMovementCommand] = []

    for message in response.get("Messages", []):
        envelope = IntegrationEventEnvelope.model_validate_json(message.get("Body", ""))

        assert envelope.source == INGESTION_SOURCE
        assert envelope.detail_type == TRANSACTION_EXTRACTED

        detail = TransactionExtractedDetail.model_validate(envelope.detail)
        commands.append(detail.to_command())

    return commands


def _record(
    command: RecordMovementCommand,
    accounts: dict[tuple[str, str], Account],
) -> Transaction:
    """Stands in for the use case that does not exist yet.

    Deliberately thin: find the account the movement names, open one on first
    sighting, then assign and apply. The conditional write that makes this
    idempotent is the part still missing, which is why the tests below check
    the domain gives it a stable key to write on.

    Accounts are keyed by user *and* fingerprint. `AccountFingerprint` carries
    no user of its own — unlike `MovementFingerprint`, which does — so two
    people at one bank holding cards that end in the same four digits would
    otherwise resolve to a single account holding both their money. The real
    query has to scope the same way, and this is the shape it will copy.
    """
    transaction = Transaction.from_alert(
        user_id=command.user_id,
        bank=command.bank,
        direction=command.direction,
        amount=command.amount,
        occurred_at=command.occurred_at,
        counterparty=command.counterparty,
        instrument_kind=command.instrument_kind,
        last_four=command.last_four,
    )

    if transaction.account_fingerprint is None or transaction.account_kind is None:
        return transaction

    key = (str(command.user_id.value), transaction.account_fingerprint.value)
    account = accounts.get(key)

    if account is None:
        assert command.last_four is not None
        assert command.instrument_kind is not None
        account = Account.open_automatically(
            user_id=command.user_id,
            bank=command.bank,
            instrument_kind=command.instrument_kind,
            last_four=command.last_four,
            kind=transaction.account_kind,
            currency=command.amount.currency,
            opened_at=command.occurred_at,
        )
        accounts[key] = account

    transaction.assign_to(account.id)
    account.apply(transaction.as_movement())

    return transaction


def _publish(
    publisher: EventBridgeEventPublisher,
    events: Sequence[TransactionExtracted],
) -> None:
    publisher.publish(list(events))


def test_a_bank_alert_becomes_a_balance(
    publisher: EventBridgeEventPublisher,
    sqs_client: SQSClient,
    queue_url: str,
) -> None:
    _publish(publisher, [_alert()])

    commands = _drain(sqs_client, queue_url)

    assert len(commands) == 1

    accounts: dict[tuple[str, str], Account] = {}
    transaction = _record(commands[0], accounts)

    # The account nobody declared: discovered from the alert that needed it.
    assert len(accounts) == 1
    account = next(iter(accounts.values()))
    assert account.needs_review
    assert account.category is AccountCategory.LIABILITY
    assert account.name == "Bancolombia ••7653"

    # Spending on a credit card raises what it owes, and the cents survived
    # the bus because the payload carries the amount as a string.
    assert account.balance.amount == Money(
        amount=Decimal("50000.50"),
        currency=Currency.COP,
    )
    assert account.balance.sign is BalanceSign.POSITIVE
    assert transaction.status is TransactionStatus.ASSIGNED
    assert account.movements_applied == 1


def test_the_same_alert_delivered_twice_names_one_movement(
    publisher: EventBridgeEventPublisher,
    sqs_client: SQSClient,
    queue_url: str,
) -> None:
    """What the ledger's conditional write will key on.

    Two deliveries of one purchase produce two integration events with two
    `event_id`s — and one `MovementId`. Nothing here dedupes yet, so the
    balance doubles: that is precisely the work the conditional write has to
    do, and this pins the key it gets to do it with.
    """
    _publish(publisher, [_alert(), _alert()])

    commands = _drain(sqs_client, queue_url)

    assert len(commands) == 2

    accounts: dict[tuple[str, str], Account] = {}
    first = _record(commands[0], accounts)
    second = _record(commands[1], accounts)

    assert first.id == second.id
    assert first.fingerprint == second.fingerprint
    # One account, not two: the second delivery found the first one's.
    assert len(accounts) == 1


def test_two_cards_at_one_bank_keep_two_balances(
    publisher: EventBridgeEventPublisher,
    sqs_client: SQSClient,
    queue_url: str,
) -> None:
    _publish(
        publisher,
        [
            _alert(),
            _alert(
                counterparty="EXITO EXPRESS",
                amount="20000",
                instrument=DEBIT_CARD,
            ),
        ],
    )

    accounts: dict[tuple[str, str], Account] = {}

    for command in _drain(sqs_client, queue_url):
        _record(command, accounts)

    assert len(accounts) == 2

    by_category = {account.category: account for account in accounts.values()}

    # The debit card is read as the savings account it draws on, so the same
    # spending subtracts here and adds on the credit card.
    assert by_category[AccountCategory.ASSET].balance.sign is BalanceSign.NEGATIVE
    assert by_category[AccountCategory.LIABILITY].balance.sign is BalanceSign.POSITIVE


def test_an_alert_naming_no_card_is_kept_outside_every_balance(
    publisher: EventBridgeEventPublisher,
    sqs_client: SQSClient,
    queue_url: str,
) -> None:
    """The unassigned path, end to end.

    The money moved and the record is kept; what is missing is only which
    account it belongs to, and that waits for a person rather than a guess.
    """
    _publish(publisher, [_alert(instrument=None)])

    commands = _drain(sqs_client, queue_url)
    accounts: dict[tuple[str, str], Account] = {}
    transaction = _record(commands[0], accounts)

    assert accounts == {}
    assert transaction.status is TransactionStatus.UNASSIGNED
    assert not transaction.is_routable
    assert transaction.direction is MovementDirection.OUTGOING
    assert transaction.amount.amount == Decimal("50000.50")
    assert transaction.counterparty == "TIENDAS ARA 123"


def test_two_users_sharing_a_cards_digits_keep_separate_accounts(
    publisher: EventBridgeEventPublisher,
    sqs_client: SQSClient,
    queue_url: str,
) -> None:
    """The isolation `AccountFingerprint` cannot provide on its own.

    Its key is bank, instrument and last four — no user. Two people at one
    bank whose cards end in the same digits therefore produce the same
    account fingerprint, and only a user-scoped lookup keeps their money
    apart.
    """
    other_user = UserId.from_string("22222222-2222-2222-2222-222222222222")

    _publish(publisher, [_alert(), _alert(user_id=other_user)])

    accounts: dict[tuple[str, str], Account] = {}
    fingerprints: set[str] = set()

    for command in _drain(sqs_client, queue_url):
        transaction = _record(command, accounts)
        assert transaction.account_fingerprint is not None
        fingerprints.add(transaction.account_fingerprint.value)

    # One fingerprint, two accounts: the scoping is what separated them.
    assert len(fingerprints) == 1
    assert len(accounts) == 2
    assert {account.user_id for account in accounts.values()} == {USER_ID, other_user}
