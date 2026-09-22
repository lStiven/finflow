"""The attempt counter, in the one place every instance of the API can see it.

One row per bucket, one atomic `ADD` per attempt, and a TTL that removes the
row when its window is over. There is no sweep to run and nothing to clean up:
a window ends because the next attempt belongs to a different key, and the old
key disappears on DynamoDB's own schedule.

**It fails open.** If the table cannot be reached, the attempt is allowed and
the failure is logged. That is a deliberate trade and worth saying out loud: a
counter that refused everything while DynamoDB was having a bad minute would
be an outage of the login screen caused by the thing protecting it, and the
protection it gives up is measured in the seconds that blip lasts.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from botocore.exceptions import BotoCoreError, ClientError

from personal_finance.shared.domain.value_objects import PosixTime


if TYPE_CHECKING:
    # Type-only: the Lambda image installs the runtime dependencies and
    # nothing else, so reaching for the stubs at import time is a function
    # that never finishes starting.
    from mypy_boto3_dynamodb.client import DynamoDBClient


_logger = logging.getLogger(__name__)

PARTITION_KEY = "bucket"
HITS_ATTRIBUTE = "hits"
#: The same name the rest of this deployment's tables use for their TTL, so
#: provisioning has one answer for every table it makes.
TTL_ATTRIBUTE = "expires_at"


class DynamoDBAttemptCounter:
    """`AttemptCounter` over a table of counters with a TTL."""

    def __init__(self, *, client: DynamoDBClient, table_name: str) -> None:
        self._client = client
        self._table_name = table_name

    def spent(self, bucket: str) -> int:
        """What this bucket holds, read without spending anything.

        Eventually consistent on purpose: a strongly consistent read costs
        twice and would only matter for the attempt that lands in the same
        millisecond as the one before it — which the `record` below counts
        atomically anyway. Reading slightly stale here can let one extra
        attempt through, never one fewer.
        """
        try:
            item = self._client.get_item(
                TableName=self._table_name,
                Key={PARTITION_KEY: {"S": bucket}},
            ).get("Item")
        except (BotoCoreError, ClientError):
            _logger.exception("could not read an attempt counter", extra=_where(bucket))

            return 0

        if item is None:
            return 0

        return _hits(item.get(HITS_ATTRIBUTE, {}).get("N"))

    def record(self, *, bucket: str, expires_at: PosixTime) -> int:
        """Count one attempt, atomically, and answer the new total.

        `ADD` rather than read-then-write: the whole point of this row is to
        be correct when two requests arrive at once, and a read-modify-write
        across two Lambda instances is exactly the race that makes a limit
        stop limiting.

        `if_not_exists` on the expiry: the row dies when its window was always
        going to end. Refreshing it on every attempt would let a steady
        trickle of guesses keep one bucket alive for ever.
        """
        try:
            answered = self._client.update_item(
                TableName=self._table_name,
                Key={PARTITION_KEY: {"S": bucket}},
                UpdateExpression=(
                    f"ADD {HITS_ATTRIBUTE} :one "
                    f"SET {TTL_ATTRIBUTE} = if_not_exists({TTL_ATTRIBUTE}, :expires_at)"
                ),
                ExpressionAttributeValues={
                    ":one": {"N": "1"},
                    ":expires_at": {"N": str(expires_at.as_epoch_seconds())},
                },
                ReturnValues="UPDATED_NEW",
            )
        except (BotoCoreError, ClientError):
            _logger.exception("could not count an attempt", extra=_where(bucket))

            return 0

        return _hits(answered.get("Attributes", {}).get(HITS_ATTRIBUTE, {}).get("N"))

    def clear(self, bucket: str) -> None:
        try:
            self._client.delete_item(
                TableName=self._table_name,
                Key={PARTITION_KEY: {"S": bucket}},
            )
        except (BotoCoreError, ClientError):
            # Losing this is a person carrying a few failures they had already
            # made up for. Worth a log and not worth an error: the request
            # that triggered it succeeded.
            _logger.warning("could not clear an attempt counter", extra=_where(bucket))


#: What a counter answers when it cannot be read. Large enough to exceed any
#: door's budget, so the window it belongs to is refused rather than waved
#: through — and bounded by that window, which is what keeps a corrupt row
#: from locking anybody out for longer than its own minutes.
UNREADABLE = 1_000_000


def _hits(value: str | None) -> int:
    if value is None:
        # Not there is a real answer, and the ordinary one: nobody has
        # knocked on this door in this window.
        return 0

    try:
        return max(int(value), 0)
    except ValueError:
        # Nothing but `ADD :one` ever writes here, so a value that is not a
        # number means something else has been in this table. Reading it as
        # "no attempts yet" would be the one answer that opens the door.
        _logger.error("an attempt counter is not a number")

        return UNREADABLE


def _where(bucket: str) -> dict[str, str]:
    """What is safe to log about a bucket.

    The kind of door, never the key: a bucket carries an address or an IP, and
    a log line is the wrong place for either.
    """
    return {"door": bucket.split("|", 1)[0]}
