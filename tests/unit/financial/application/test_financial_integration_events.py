from decimal import Decimal
import json
from typing import Any

from personal_finance.contexts.financial.application.integration_events import (
    ACCOUNT_BALANCE_CHANGED,
    MOVEMENT_RECORDED,
    SOURCE,
    VERSION,
    FinancialIntegrationEventTranslator,
)
from personal_finance.contexts.financial.domain.events import (
    AccountBalanceChanged,
    AccountBalanceRebuilt,
    AccountFingerprintLinked,
    AccountOpened,
    AccountRenamed,
    TransactionAssigned,
    TransactionEdited,
    TransactionErased,
    TransactionRecorded,
    TransactionUnassigned,
)
from personal_finance.contexts.financial.domain.value_objects import (
    AccountCategory,
    AccountFingerprint,
    AccountId,
    AccountKind,
    Balance,
    MovementDirection,
    MovementId,
    TransactionOrigin,
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


USER_ID = UserId.from_string("11111111-1111-1111-1111-111111111111")
ACCOUNT_ID = AccountId.from_string("22222222-2222-2222-2222-222222222222")
MOVEMENT_ID = MovementId(value="abc123")
# When the fact was recorded, which is not when the money moved.
RECORDED_AT = PosixTime.from_epoch_seconds(1_700_086_400)
MOVED_AT = PosixTime.from_epoch_seconds(1_700_000_000)
FINGERPRINT = AccountFingerprint(value="bancolombia:savings:5261")


def _recorded(
    *,
    fingerprint: AccountFingerprint | None = FINGERPRINT,
    account_id: AccountId | None = ACCOUNT_ID,
) -> TransactionRecorded:
    return TransactionRecorded(
        movement_id=MOVEMENT_ID,
        user_id=USER_ID,
        occurred_at=RECORDED_AT,
        direction=MovementDirection.OUTGOING,
        amount=Money(amount=Decimal("45000.50"), currency=Currency.COP),
        movement_occurred_at=MOVED_AT,
        counterparty="EXITO CALI",
        bank="bancolombia",
        origin=TransactionOrigin.BANK_ALERT,
        account_fingerprint=fingerprint,
        account_id=account_id,
    )


def _balance_changed(*, balance: Balance) -> AccountBalanceChanged:
    return AccountBalanceChanged(
        account_id=ACCOUNT_ID,
        user_id=USER_ID,
        occurred_at=RECORDED_AT,
        movement_id=MOVEMENT_ID,
        direction=MovementDirection.OUTGOING,
        amount=Money(amount=Decimal("45000.50"), currency=Currency.COP),
        balance=balance,
    )


def test_a_recorded_movement_leaves_the_context() -> None:
    event = FinancialIntegrationEventTranslator().translate(_recorded())

    assert event is not None
    assert event.source == SOURCE == "finflow.financial"
    assert event.detail_type == MOVEMENT_RECORDED
    assert event.version == VERSION
    assert event.payload == {
        "movement_id": "abc123",
        "user_id": str(USER_ID.value),
        "direction": "outgoing",
        "amount": "45000.50",
        "currency": "COP",
        "movement_occurred_at": 1_700_000_000,
        "counterparty": "EXITO CALI",
        "bank": "bancolombia",
        "origin": "bank_alert",
        "unassigned": False,
    }


def test_the_published_time_is_when_the_money_moved() -> None:
    event = FinancialIntegrationEventTranslator().translate(_recorded())

    assert event is not None
    # The alert can arrive days after the purchase. A subscriber writing
    # "acabas de gastar" needs the purchase's own time in the payload, and
    # the envelope keeps the moment the fact was recorded.
    assert event.payload["movement_occurred_at"] == MOVED_AT.as_epoch_seconds()
    assert event.occurred_at == RECORDED_AT


def test_the_event_id_is_carried_through_for_deduplication() -> None:
    domain_event = _recorded()

    event = FinancialIntegrationEventTranslator().translate(domain_event)

    # Delivery is at-least-once: a subscriber recognises a replay by this.
    assert event is not None
    assert event.event_id == domain_event.event_id


def test_a_movement_that_found_no_account_says_so_without_naming_the_key() -> None:
    event = FinancialIntegrationEventTranslator().translate(
        _recorded(fingerprint=None, account_id=None),
    )

    assert event is not None
    assert event.payload["unassigned"] is True


def test_a_movement_on_an_account_is_not_unassigned_whatever_its_fingerprint() -> None:
    # A confirmed bill and a movement entered by hand sit on an account and
    # carry no fingerprint: reading the fingerprint called every one of them
    # «sin cuenta asignada».
    event = FinancialIntegrationEventTranslator().translate(
        _recorded(fingerprint=None, account_id=ACCOUNT_ID),
    )

    assert event is not None
    assert event.payload["unassigned"] is False


def test_an_alert_whose_card_nobody_declared_is_unassigned() -> None:
    # The other half of the same mistake: a fingerprint and no account.
    event = FinancialIntegrationEventTranslator().translate(
        _recorded(fingerprint=FINGERPRINT, account_id=None),
    )

    assert event is not None
    assert event.payload["unassigned"] is True


def test_the_matching_key_never_crosses_the_boundary() -> None:
    event = FinancialIntegrationEventTranslator().translate(_recorded())

    assert event is not None
    # How Financial matches an alert to an account is its own business.
    assert "account_fingerprint" not in event.payload
    assert FINGERPRINT.value not in str(event.payload)


def test_money_crosses_as_a_string() -> None:
    event = FinancialIntegrationEventTranslator().translate(_recorded())

    assert event is not None
    # Not a float: 45000.50 as JSON would come back having lost the cents.
    assert isinstance(event.payload["amount"], str)
    assert event.payload["amount"] == "45000.50"


def test_a_balance_change_leaves_the_context() -> None:
    event = FinancialIntegrationEventTranslator().translate(
        _balance_changed(
            balance=Balance.from_signed(Decimal("1200000.25"), Currency.COP),
        ),
    )

    assert event is not None
    assert event.detail_type == ACCOUNT_BALANCE_CHANGED
    assert event.payload == {
        "account_id": str(ACCOUNT_ID.value),
        "user_id": str(USER_ID.value),
        "movement_id": "abc123",
        "direction": "outgoing",
        "amount": "45000.50",
        "balance": "1200000.25",
        "currency": "COP",
    }


def test_a_negative_balance_keeps_its_sign_in_the_figure() -> None:
    event = FinancialIntegrationEventTranslator().translate(
        _balance_changed(
            balance=Balance.from_signed(Decimal("-1200.50"), Currency.COP),
        ),
    )

    assert event is not None
    # Signed in the string rather than split into a magnitude and a flag: a
    # reader that drops the flag reads an overdraft as savings.
    assert event.payload["balance"] == "-1200.50"


def test_the_lifecycle_of_an_account_stays_inside() -> None:
    translator = FinancialIntegrationEventTranslator()
    internal = (
        AccountOpened(
            account_id=ACCOUNT_ID,
            user_id=USER_ID,
            occurred_at=RECORDED_AT,
            name="Ahorros",
            kind=AccountKind.SAVINGS,
            category=AccountCategory.ASSET,
            currency=Currency.COP,
            opening_balance=Balance.zero(Currency.COP),
            bank="bancolombia",
        ),
        AccountRenamed(
            account_id=ACCOUNT_ID,
            user_id=USER_ID,
            occurred_at=RECORDED_AT,
            name="Ahorros Bancolombia",
        ),
        AccountFingerprintLinked(
            account_id=ACCOUNT_ID,
            user_id=USER_ID,
            occurred_at=RECORDED_AT,
            fingerprint=FINGERPRINT,
        ),
        AccountBalanceRebuilt(
            account_id=ACCOUNT_ID,
            user_id=USER_ID,
            occurred_at=RECORDED_AT,
            balance=Balance.zero(Currency.COP),
            movements_applied=0,
        ),
    )

    # How this context keeps its books is not a contract anybody may hold.
    assert [translator.translate(event) for event in internal] == [None] * len(internal)


def test_the_rest_of_a_movements_life_stays_inside() -> None:
    translator = FinancialIntegrationEventTranslator()
    internal = (
        TransactionAssigned(
            movement_id=MOVEMENT_ID,
            user_id=USER_ID,
            occurred_at=RECORDED_AT,
            account_id=ACCOUNT_ID,
        ),
        TransactionEdited(
            movement_id=MOVEMENT_ID,
            user_id=USER_ID,
            occurred_at=RECORDED_AT,
            amount=Money(amount=Decimal("1"), currency=Currency.COP),
            movement_occurred_at=MOVED_AT,
            counterparty="EXITO",
        ),
        TransactionUnassigned(
            movement_id=MOVEMENT_ID,
            user_id=USER_ID,
            occurred_at=RECORDED_AT,
            account_id=ACCOUNT_ID,
        ),
        TransactionErased(
            movement_id=MOVEMENT_ID,
            user_id=USER_ID,
            occurred_at=RECORDED_AT,
            direction=MovementDirection.OUTGOING,
            amount=Money(amount=Decimal("1"), currency=Currency.COP),
            account_id=ACCOUNT_ID,
        ),
    )

    # The balance an assignment or an erasure moves is published on its own,
    # with the figures on it; the bookkeeping around it is not.
    assert [translator.translate(event) for event in internal] == [None] * len(internal)


class _RecordingClient:
    """Stands in for EventBridge, keeping what would have gone on the bus."""

    def __init__(self) -> None:
        self.entries: list[dict[str, Any]] = []

    def put_events(self, *, Entries: list[dict[str, Any]]) -> dict[str, Any]:  # noqa: N803
        self.entries.extend(Entries)

        return {"FailedEntryCount": 0, "Entries": []}


def _published(event: object) -> dict[str, Any]:
    """What a subscriber actually receives, envelope and all."""
    client = _RecordingClient()
    EventBridgeEventPublisher(
        client=client,  # type: ignore[arg-type]
        event_bus_name="finflow",
        translator=FinancialIntegrationEventTranslator(),
    ).publish([event])  # type: ignore[list-item]

    assert len(client.entries) == 1
    detail: dict[str, Any] = json.loads(client.entries[0]["Detail"])

    return detail


def test_the_movements_own_time_survives_the_envelope() -> None:
    """The bug this file exists to keep out.

    The transport writes the envelope's `occurred_at` into the same detail
    object as the payload. A payload field by that name is therefore not
    "the time in the payload" — it is overwritten, silently, with the moment
    the fact was recorded. Asserting on the translator alone cannot see it;
    only going through the real publisher can.
    """
    detail = _published(_recorded())

    assert detail["movement_occurred_at"] == MOVED_AT.as_epoch_seconds()
    assert detail["occurred_at"] == RECORDED_AT.as_epoch_seconds()
    assert detail["movement_occurred_at"] != detail["occurred_at"]


def test_a_subscriber_can_deduplicate_and_version_from_the_detail() -> None:
    domain_event = _recorded()

    detail = _published(domain_event)

    # Delivery is at-least-once and a subscriber reading off an SQS target
    # never sees EventBridge's own wrapper: both have to be in the detail.
    assert detail["event_id"] == str(domain_event.event_id)
    assert detail["version"] == VERSION


def test_nothing_internal_reaches_the_bus() -> None:
    client = _RecordingClient()

    EventBridgeEventPublisher(
        client=client,  # type: ignore[arg-type]
        event_bus_name="finflow",
        translator=FinancialIntegrationEventTranslator(),
    ).publish(
        [
            TransactionAssigned(
                movement_id=MOVEMENT_ID,
                user_id=USER_ID,
                occurred_at=RECORDED_AT,
                account_id=ACCOUNT_ID,
            ),
        ],
    )

    assert client.entries == []
