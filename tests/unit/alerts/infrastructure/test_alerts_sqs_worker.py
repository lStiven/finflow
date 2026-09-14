"""What the worker does with messages it cannot act on.

The happy path runs end to end against a real bus in `tests/integration`;
what matters here is the split nothing else exercises directly: a message
that will never become valid must leave the queue, and one that merely
arrived too early — or failed for a reason that may not repeat — must not.

Deleting the wrong one loses an alert, and an alert nobody receives is a
purchase its owner never heard about.
"""

from decimal import Decimal
import json
import logging
from typing import Any, cast
import uuid

from mypy_boto3_sqs.client import SQSClient
import pytest

from personal_finance.contexts.alerts.application.commands import (
    DeliverMovementAlertCommand,
)
from personal_finance.contexts.alerts.application.handlers import (
    DeliverMovementAlertUseCase,
)
from personal_finance.contexts.alerts.application.ports import (
    TransportUnavailableError,
)
from personal_finance.contexts.alerts.infrastructure.messaging.inbound import (
    FINANCIAL_SOURCE,
    MOVEMENT_RECORDED,
)
from personal_finance.contexts.alerts.infrastructure.messaging.sqs_worker import (
    SQSAlertsWorker,
)
from personal_finance.shared.infrastructure.messaging.sqs_polling import MessageOutcome


QUEUE_URL = "https://sqs.us-east-1.amazonaws.com/000000000000/alerts-events"
USER_ID = "11111111-1111-1111-1111-111111111111"


class FakeSQSClient:
    def receive_message(self, **kwargs: Any) -> dict[str, Any]:  # noqa: ANN401
        del kwargs

        return {}

    def delete_message(self, **kwargs: Any) -> None:  # noqa: ANN401
        del kwargs


class StubUseCase(DeliverMovementAlertUseCase):
    """A use case that answers however the test needs it to."""

    def __init__(self, *, raises: Exception | None = None, delivered: int = 1) -> None:
        self._raises = raises
        self._delivered = delivered
        self.commands: list[DeliverMovementAlertCommand] = []

    def execute(self, command: DeliverMovementAlertCommand) -> int:
        self.commands.append(command)

        if self._raises is not None:
            raise self._raises

        return self._delivered


def _worker(use_case: DeliverMovementAlertUseCase) -> SQSAlertsWorker:
    return SQSAlertsWorker(
        client=cast("SQSClient", FakeSQSClient()),
        queue_url=QUEUE_URL,
        use_case=use_case,
    )


def _body(
    *,
    source: str = FINANCIAL_SOURCE,
    detail_type: str = MOVEMENT_RECORDED,
    **overrides: Any,  # noqa: ANN401
) -> str:
    detail: dict[str, Any] = {
        "version": 1,
        "event_id": str(uuid.uuid4()),
        "user_id": USER_ID,
        "direction": "outgoing",
        "amount": "84300",
        "currency": "COP",
        "movement_occurred_at": 1_757_800_000,
        # What the transport writes over its own payload key of this name.
        "occurred_at": 1_757_999_999,
        "counterparty": "COMPRA EN *PAYU*COL",
        "bank": "Bancolombia",
        "origin": "bank_alert",
        "unassigned": False,
    }
    detail.update(overrides)

    return json.dumps(
        {"source": source, "detail-type": detail_type, "detail": detail},
    )


# ----------------------------------------------------------------------
# Reading the payload
# ----------------------------------------------------------------------


def test_a_movement_is_read_into_a_command() -> None:
    use_case = StubUseCase()

    assert _worker(use_case).handle(_body()) is MessageOutcome.HANDLED

    [command] = use_case.commands
    assert command.alert.amount.amount == Decimal("84300")
    assert command.alert.counterparty == "COMPRA EN *PAYU*COL"


def test_the_time_read_is_when_the_money_moved_not_when_it_was_recorded() -> None:
    """The transport flattens its own `occurred_at` into the payload.

    Reading that one would say "acabas de gastar" about a bank alert that
    arrived three days late.
    """
    use_case = StubUseCase()

    _worker(use_case).handle(_body())

    [command] = use_case.commands
    assert command.alert.occurred_at.as_epoch_seconds() == 1_757_800_000


# ----------------------------------------------------------------------
# Discarded: never going to become readable
# ----------------------------------------------------------------------


def test_a_body_that_is_not_json_is_discarded() -> None:
    assert _worker(StubUseCase()).handle("{not json") is MessageOutcome.DISCARDED


def test_an_event_from_another_source_is_discarded() -> None:
    """This queue is Alerts' alone; anything else is a misrouted rule."""
    body = _body(source="finflow.ingestion")

    assert _worker(StubUseCase()).handle(body) is MessageOutcome.DISCARDED


def test_an_event_of_another_type_is_discarded() -> None:
    body = _body(detail_type="AccountBalanceChanged")

    assert _worker(StubUseCase()).handle(body) is MessageOutcome.DISCARDED


@pytest.mark.parametrize(
    "overrides",
    [
        {"amount": "1E+1000000"},
        {"amount": "-5"},
        {"amount": "NaN"},
        {"counterparty": "   "},
        {"movement_occurred_at": 99_999_999_999_999},
        {"user_id": "not-a-uuid"},
    ],
)
def test_a_payload_that_cannot_be_read_is_discarded(overrides: dict[str, Any]) -> None:
    body = _body(**overrides)

    assert _worker(StubUseCase()).handle(body) is MessageOutcome.DISCARDED


# ----------------------------------------------------------------------
# Retried: might be readable by the very next deploy
# ----------------------------------------------------------------------


def test_a_newer_version_waits_on_the_queue() -> None:
    body = _body(version=2)

    assert _worker(StubUseCase()).handle(body) is MessageOutcome.RETRY


@pytest.mark.parametrize(
    "overrides",
    [{"currency": "EUR"}, {"direction": "sideways"}, {"origin": "something_new"}],
)
def test_a_vocabulary_this_deploy_does_not_know_waits(
    overrides: dict[str, Any],
) -> None:
    body = _body(**overrides)

    assert _worker(StubUseCase()).handle(body) is MessageOutcome.RETRY


def test_a_transport_that_is_down_leaves_the_alert_on_the_queue() -> None:
    use_case = StubUseCase(raises=TransportUnavailableError("telegram is down"))

    assert _worker(use_case).handle(_body()) is MessageOutcome.RETRY


def test_an_unexpected_failure_leaves_the_alert_on_the_queue() -> None:
    use_case = StubUseCase(raises=RuntimeError("the table is throttling"))

    assert _worker(use_case).handle(_body()) is MessageOutcome.RETRY


# ----------------------------------------------------------------------
# Handled even when nothing was sent
# ----------------------------------------------------------------------


def test_a_movement_nobody_wanted_is_still_done_with() -> None:
    """No channel, or every channel below its floor. Nothing to retry."""
    use_case = StubUseCase(delivered=0)

    assert _worker(use_case).handle(_body()) is MessageOutcome.HANDLED


def test_an_accrual_leaves_the_queue_without_being_announced() -> None:
    use_case = StubUseCase(delivered=0)
    body = _body(origin="accrual")

    assert _worker(use_case).handle(body) is MessageOutcome.HANDLED


def test_a_refused_payload_never_writes_its_values_to_the_log(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A Pydantic error renders `input_value=` for every field it refused,
    and those fields are somebody's amount, counterparty and bank."""
    body = _body(amount="-5", counterparty="COMPRA EN *PAYU*COL")

    with caplog.at_level(logging.DEBUG):
        assert _worker(StubUseCase()).handle(body) is MessageOutcome.DISCARDED

    written = caplog.text
    assert "PAYU" not in written
    assert "Bancolombia" not in written
    assert "discarding malformed" in written
