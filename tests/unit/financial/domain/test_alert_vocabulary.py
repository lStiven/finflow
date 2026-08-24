"""Reading ingestion's direction into Financial's own words.

The payload on the bus is JSON, so this arrives as text. Ingestion's enum is
never imported: that is the whole point of the mapping, since either context
must be free to rename its own members.
"""

import pytest

from personal_finance.contexts.financial.domain.value_objects import (
    MovementDirection,
)


def test_a_direction_is_read_however_the_alert_spelled_it() -> None:
    assert MovementDirection.from_alert("outgoing") is MovementDirection.OUTGOING
    assert MovementDirection.from_alert("  INCOMING ") is MovementDirection.INCOMING


def test_a_direction_that_cannot_be_read_refuses_the_alert() -> None:
    # The one field with no safe default: a guessed direction moves a real
    # balance the wrong way and looks correct doing it.
    with pytest.raises(ValueError):
        MovementDirection.from_alert("sideways")
