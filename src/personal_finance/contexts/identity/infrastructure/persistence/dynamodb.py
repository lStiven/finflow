from __future__ import annotations

from typing import TYPE_CHECKING
import uuid

from personal_finance.contexts.identity.domain.entities import User
from personal_finance.contexts.identity.domain.value_objects import Email, PasswordHash
from personal_finance.shared.domain.value_objects import PosixTime, UserId


if TYPE_CHECKING:
    from mypy_boto3_dynamodb.client import DynamoDBClient
    from mypy_boto3_dynamodb.type_defs import AttributeValueTypeDef


PARTITION_KEY = "email"


class CorruptUserItemError(Exception):
    """Raised when a stored user does not match the expected attribute shape."""


def _read_string(item: dict[str, AttributeValueTypeDef], key: str) -> str:
    value = item.get(key, {}).get("S")

    if value is None:
        raise CorruptUserItemError(f"Missing string attribute {key!r} on stored user")

    return value


def to_item(user: User) -> dict[str, AttributeValueTypeDef]:
    return {
        PARTITION_KEY: {"S": user.email.value},
        "user_id": {"S": str(user.id.value)},
        "password_hash": {"S": user.password_hash.value},
        "registered_at": {"N": str(user.registered_at.as_epoch_seconds())},
    }


def to_entity(item: dict[str, AttributeValueTypeDef]) -> User:
    """Rebuild the aggregate from a stored item, with no pending events: what
    a previous attempt already published must never be replayed.
    """
    return User(
        id=UserId(value=uuid.UUID(_read_string(item, "user_id"))),
        email=Email(_read_string(item, PARTITION_KEY)),
        password_hash=PasswordHash(_read_string(item, "password_hash")),
        registered_at=PosixTime.from_epoch_seconds(
            int(item.get("registered_at", {}).get("N", "0")),
        ),
    )


class DynamoDBUserRepository:
    """`UserRepository` backed by a table partitioned by email.

    Uniqueness relies on a conditional `PutItem`: the write only lands when
    the email is free, which is what makes two concurrent registrations for
    the same address resolve to exactly one account.
    """

    def __init__(self, *, client: DynamoDBClient, table_name: str) -> None:
        self._client = client
        self._table_name = table_name

    def add_if_new(self, user: User) -> User | None:
        try:
            self._client.put_item(
                TableName=self._table_name,
                Item=to_item(user),
                ConditionExpression=f"attribute_not_exists({PARTITION_KEY})",
                ReturnValuesOnConditionCheckFailure="ALL_OLD",
            )
        except self._client.exceptions.ConditionalCheckFailedException as error:
            item = error.response.get("Item")

            if item:
                return to_entity(item)

            return self.find_by_email(user.email)

        return None

    def find_by_email(self, email: Email) -> User | None:
        response = self._client.get_item(
            TableName=self._table_name,
            Key={PARTITION_KEY: {"S": email.value}},
            ConsistentRead=True,
        )
        item = response.get("Item")

        return to_entity(item) if item else None
