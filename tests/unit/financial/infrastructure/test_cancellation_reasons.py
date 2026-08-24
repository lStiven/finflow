"""Telling "already recorded" apart from "the write did not happen".

DynamoDB cancels a transaction for several reasons that look identical from
the outside. Reading them all as a refused condition is how a real movement
disappears: the caller reports a duplicate, the worker deletes the message,
and nothing was ever written.
"""

from personal_finance.contexts.financial.infrastructure.persistence.dynamodb import (
    refused_by_condition,
)


class _CancelledError(Exception):
    def __init__(self, *codes: str) -> None:
        super().__init__("cancelled")
        self.response = {"CancellationReasons": [{"Code": code} for code in codes]}


def test_a_refused_condition_on_the_row_asked_about_is_a_duplicate() -> None:
    assert refused_by_condition(
        _CancelledError("ConditionalCheckFailed", "None"), index=0
    )


def test_a_conflict_or_a_throttle_is_not_a_duplicate() -> None:
    # Nobody wrote anything. Reporting a duplicate here deletes the message.
    assert not refused_by_condition(
        _CancelledError("TransactionConflict", "None"), index=0
    )
    assert not refused_by_condition(
        _CancelledError("ThrottlingError", "None"),
        index=0,
    )


def test_a_condition_failing_elsewhere_is_not_this_rows_duplicate() -> None:
    # The account update's condition failed, not the ledger row's: the
    # movement was not recorded and has to be retried.
    assert not refused_by_condition(
        _CancelledError("None", "ConditionalCheckFailed"), index=0
    )


def test_without_an_index_any_refused_condition_counts() -> None:
    # What `add` needs: whichever item lost, somebody already holds that
    # identity.
    assert refused_by_condition(_CancelledError("None", "ConditionalCheckFailed"))
    assert not refused_by_condition(_CancelledError("None", "TransactionConflict"))


def test_a_cancellation_with_no_reasons_is_not_a_duplicate() -> None:
    assert not refused_by_condition(_CancelledError(), index=0)
    assert not refused_by_condition(Exception("no response at all"), index=0)
