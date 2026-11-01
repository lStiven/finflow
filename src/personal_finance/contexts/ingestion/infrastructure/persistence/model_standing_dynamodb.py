"""One person's standing with the model, one item per person.

In the notifications table for the same reasons as the day's reads: the parse
worker already owns it, and an item with no `user_id` or `received_at` stays
out of the by-user index. Unlike those rows it never expires — a suspension
for good has to outlive any sweep.

The whole item is written at once, guarded by its version: a strike is a
decision made on everything the record holds, so a write made on a stale
copy has to lose rather than merge.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
import uuid

from botocore.exceptions import ClientError

from personal_finance.contexts.ingestion.domain.model_standing import ModelStanding
from personal_finance.contexts.ingestion.domain.value_objects import NotificationId
from personal_finance.shared.domain.value_objects import PosixTime, UserId


if TYPE_CHECKING:
    from mypy_boto3_dynamodb.client import DynamoDBClient
    from mypy_boto3_dynamodb.type_defs import AttributeValueTypeDef


PARTITION_KEY = "pk"
KEY_PREFIX = "MODEL_STANDING#"


class StoredStandingError(Exception):
    """Raised when a stored standing does not have the expected shape."""


def standing_key(user_id: UserId) -> str:
    return f"{KEY_PREFIX}{user_id.value}"


class DynamoDBModelStandings:
    """`ModelStandingRepository` over one item per person."""

    def __init__(self, *, client: DynamoDBClient, table_name: str) -> None:
        self._client = client
        self._table_name = table_name

    def get(self, user_id: UserId) -> ModelStanding:
        item = self._client.get_item(
            TableName=self._table_name,
            Key={PARTITION_KEY: {"S": standing_key(user_id)}},
            # The read a strike is decided on: a lagging copy would only lose
            # the version check below and cost a retry.
            ConsistentRead=True,
        ).get("Item")

        if item is None:
            return ModelStanding.clean(user_id)

        return _from_item(user_id, item)

    def save(self, standing: ModelStanding) -> bool:
        item: dict[str, AttributeValueTypeDef] = {
            PARTITION_KEY: {"S": standing_key(standing.user_id)},
            "offenses": {
                "M": {
                    str(notification_id.value): {"N": str(arrived.as_epoch_seconds())}
                    for notification_id, arrived in standing.offenses.items()
                },
            },
            "strikes": {
                "L": [
                    {"N": str(given.as_epoch_seconds())} for given in standing.strikes
                ],
            },
            "revoked": {"BOOL": standing.revoked},
            "version": {"N": str(standing.version + 1)},
        }

        if standing.paused_until is not None:
            item["paused_until"] = {"N": str(standing.paused_until.as_epoch_seconds())}

        try:
            if standing.version == 0:
                self._client.put_item(
                    TableName=self._table_name,
                    Item=item,
                    ConditionExpression="attribute_not_exists(pk)",
                )
            else:
                self._client.put_item(
                    TableName=self._table_name,
                    Item=item,
                    ConditionExpression="version = :expected",
                    ExpressionAttributeValues={
                        ":expected": {"N": str(standing.version)},
                    },
                )
        except ClientError as error:
            if error.response.get("Error", {}).get("Code") == (
                "ConditionalCheckFailedException"
            ):
                return False

            raise

        standing.version += 1

        return True


def _from_item(
    user_id: UserId, item: dict[str, AttributeValueTypeDef]
) -> ModelStanding:
    try:
        offenses = {
            NotificationId(value=uuid.UUID(key)): _moment(value)
            for key, value in item.get("offenses", {}).get("M", {}).items()
        }
        strikes = tuple(
            _moment(value) for value in item.get("strikes", {}).get("L", [])
        )
        paused = item.get("paused_until")
        version = int(item.get("version", {}).get("N", ""))
    except (KeyError, ValueError) as error:
        raise StoredStandingError(
            f"The stored standing of {user_id.value} is unreadable",
        ) from error

    return ModelStanding(
        id=user_id,
        offenses=offenses,
        strikes=strikes,
        paused_until=None if paused is None else _moment(paused),
        revoked=bool(item.get("revoked", {}).get("BOOL", False)),
        version=version,
    )


def _moment(value: AttributeValueTypeDef) -> PosixTime:
    # A missing number raises `ValueError` here, read as an unreadable record.
    return PosixTime.from_epoch_seconds(int(value.get("N", "")))
