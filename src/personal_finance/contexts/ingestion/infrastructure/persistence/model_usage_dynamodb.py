"""The day's model reads, one row per person per day.

Kept in the notifications table rather than a table of its own: the parse
worker already holds read-write on it, its TTL already sweeps expired rows,
and a row with no `user_id` or `received_at` stays out of the by-user index.
The key prefix is what keeps it apart from a notification, whose key is an
idempotency hash.

The row holds the *set* of notification ids that took a read, not a counter.
A counter cannot tell a new email from the same one delivered twice; the set
can, and its size is the count.
"""

from __future__ import annotations

from collections.abc import Callable
import datetime
from typing import TYPE_CHECKING

from botocore.exceptions import ClientError

from personal_finance.contexts.ingestion.domain.parsing.dates import BOGOTA
from personal_finance.contexts.ingestion.domain.value_objects import NotificationId
from personal_finance.shared.domain.value_objects import PosixTime, UserId


if TYPE_CHECKING:
    from mypy_boto3_dynamodb.client import DynamoDBClient


PARTITION_KEY = "pk"
KEY_PREFIX = "MODEL_USAGE#"
NOTIFICATIONS_ATTRIBUTE = "notifications"
TTL_ATTRIBUTE = "expires_at"
# Long enough to answer "how much did they use yesterday" by hand; nothing in
# the application ever reads a past day.
_KEPT_FOR = datetime.timedelta(days=7)

_TAKE_A_READ = (
    f"ADD {NOTIFICATIONS_ATTRIBUTE} :member "
    f"SET {TTL_ATTRIBUTE} = if_not_exists({TTL_ATTRIBUTE}, :expires)"
)
# Room left, and not this notification already: the second half is what
# turns a redelivery into a refusal the read below can then answer.
_IF_ROOM_FOR_A_NEW_ONE = (
    f"attribute_not_exists({NOTIFICATIONS_ATTRIBUTE}) OR "
    f"(NOT contains({NOTIFICATIONS_ATTRIBUTE}, :id) "
    f"AND size({NOTIFICATIONS_ATTRIBUTE}) < :limit)"
)


def usage_key(*, user_id: UserId, day: datetime.date) -> str:
    return f"{KEY_PREFIX}{user_id.value}#{day.isoformat()}"


class DynamoDBModelUsageQuota:
    """`ModelUsageQuota` as one conditional write per read."""

    def __init__(
        self,
        *,
        client: DynamoDBClient,
        table_name: str,
        daily_limit: Callable[[], int],
    ) -> None:
        self._client = client
        self._table_name = table_name
        # A callable rather than a number: the limit can change while this
        # process lives, and the next reservation should see it.
        self._daily_limit = daily_limit

    def reserve(
        self,
        *,
        user_id: UserId,
        notification_id: NotificationId,
        day: datetime.date,
    ) -> bool:
        """Add the notification to the day's set if there is room, atomically.

        One conditional `ADD`, so two workers reading two emails of the same
        person at once cannot both take the last read. A refusal is checked
        once more against the stored set with a consistent read, because the
        condition fails the same way for "no room" and for "this one is
        already in" — and the second is a yes.

        Anything but a refused condition raises: the email stays queued and
        is tried again, which costs a retry rather than an unmetered read.
        """
        limit = self._daily_limit()
        key = {PARTITION_KEY: {"S": usage_key(user_id=user_id, day=day)}}
        member = str(notification_id.value)

        if limit > 0:
            try:
                self._client.update_item(
                    TableName=self._table_name,
                    Key=key,
                    UpdateExpression=_TAKE_A_READ,
                    ConditionExpression=_IF_ROOM_FOR_A_NEW_ONE,
                    ExpressionAttributeValues={
                        ":member": {"SS": [member]},
                        ":id": {"S": member},
                        ":limit": {"N": str(limit)},
                        ":expires": {"N": str(_expires_at(day))},
                    },
                )
            except ClientError as error:
                code = error.response.get("Error", {}).get("Code")

                if code != "ConditionalCheckFailedException":
                    raise
            else:
                return True

        item = self._client.get_item(
            TableName=self._table_name,
            Key=key,
            ConsistentRead=True,
        ).get("Item", {})

        return member in item.get(NOTIFICATIONS_ATTRIBUTE, {}).get("SS", [])

    def release(
        self,
        *,
        user_id: UserId,
        notification_id: NotificationId,
        day: datetime.date,
    ) -> None:
        """Take the notification out of the day's set. Deleting a member that
        is not there is a no-op, so a second release changes nothing.

        Only on a row that exists: an update creates what it does not find,
        and a row made here would have no expiry to sweep it.
        """
        try:
            self._client.update_item(
                TableName=self._table_name,
                Key={PARTITION_KEY: {"S": usage_key(user_id=user_id, day=day)}},
                UpdateExpression=f"DELETE {NOTIFICATIONS_ATTRIBUTE} :member",
                ConditionExpression=f"attribute_exists({PARTITION_KEY})",
                ExpressionAttributeValues={
                    ":member": {"SS": [str(notification_id.value)]},
                },
            )
        except ClientError as error:
            code = error.response.get("Error", {}).get("Code")

            if code != "ConditionalCheckFailedException":
                raise


def _expires_at(day: datetime.date) -> int:
    end_of_day = datetime.datetime.combine(
        day + datetime.timedelta(days=1),
        datetime.time(),
        tzinfo=BOGOTA,
    )

    return PosixTime.from_datetime(end_of_day + _KEPT_FOR).as_epoch_seconds()
