from __future__ import annotations

from collections.abc import Sequence

from mypy_boto3_dynamodb.client import DynamoDBClient
from mypy_boto3_dynamodb.type_defs import AttributeValueTypeDef

from personal_finance.contexts.ingestion.application.mailbox import (
    MailboxConnection,
    MailboxConnectionStatus,
    MailboxProvider,
)
from personal_finance.contexts.ingestion.domain.value_objects import EmailAddress
from personal_finance.shared.domain.value_objects import PosixTime, UserId


CONNECTION_PARTITION_KEY = "connection_id"
USER_ID_ATTRIBUTE = "user_id"
STATUS_ATTRIBUTE = "status"

# A provider notification names a provider and a mailbox, never a user, so
# that pair has to be the key: resolving it must cost one read on the hot
# path. Listing a user's mailboxes is the reverse lookup and gets an index.
CONNECTION_BY_USER_INDEX = "by_user"


class CorruptMailboxConnectionItemError(Exception):
    """Raised when a stored connection does not match the expected shape."""


def connection_id(*, provider: MailboxProvider, address: EmailAddress) -> str:
    """The stored identity of one mailbox.

    The same address at two providers is two mailboxes, so both belong in the
    key.
    """
    return f"{provider.value}#{address.value}"


def _read_string(item: dict[str, AttributeValueTypeDef], key: str) -> str:
    value = item.get(key, {}).get("S")

    if value is None:
        raise CorruptMailboxConnectionItemError(
            f"Missing string attribute {key!r} on stored mailbox connection",
        )

    return value


def to_item(connection: MailboxConnection) -> dict[str, AttributeValueTypeDef]:
    item: dict[str, AttributeValueTypeDef] = {
        CONNECTION_PARTITION_KEY: {
            "S": connection_id(
                provider=connection.provider,
                address=connection.address,
            ),
        },
        USER_ID_ATTRIBUTE: {"S": str(connection.user_id.value)},
        "address": {"S": connection.address.value},
        "provider": {"S": connection.provider.value},
        STATUS_ATTRIBUTE: {"S": connection.status.value},
    }

    # Absent rather than empty: a mailbox that has never been read has no
    # position, and "" is a position some provider might legitimately use.
    if connection.cursor is not None:
        item["cursor"] = {"S": connection.cursor}

    if connection.subscription_expires_at is not None:
        item["subscription_expires_at"] = {
            "N": str(connection.subscription_expires_at.as_epoch_seconds()),
        }

    if connection.last_synced_at is not None:
        item["last_synced_at"] = {
            "N": str(connection.last_synced_at.as_epoch_seconds()),
        }

    return item


def _read_time(
    item: dict[str, AttributeValueTypeDef],
    key: str,
) -> PosixTime | None:
    raw = item.get(key, {}).get("N")

    return PosixTime.from_epoch_seconds(int(raw)) if raw else None


def to_entity(item: dict[str, AttributeValueTypeDef]) -> MailboxConnection:
    return MailboxConnection(
        user_id=UserId.from_string(_read_string(item, USER_ID_ATTRIBUTE)),
        address=EmailAddress(_read_string(item, "address")),
        provider=MailboxProvider(_read_string(item, "provider")),
        cursor=item.get("cursor", {}).get("S"),
        status=MailboxConnectionStatus(_read_string(item, STATUS_ATTRIBUTE)),
        subscription_expires_at=_read_time(item, "subscription_expires_at"),
        last_synced_at=_read_time(item, "last_synced_at"),
    )


class DynamoDBMailboxConnectionRepository:
    """`MailboxConnectionRepository` backed by a table keyed by
    provider-and-address.

    No credential ever reaches this table. A connection records that a user
    authorized a mailbox and how far we have read it; the token that makes the
    read possible lives in a secret store the provider adapter owns.
    """

    def __init__(self, *, client: DynamoDBClient, table_name: str) -> None:
        self._client = client
        self._table_name = table_name

    def save(self, connection: MailboxConnection) -> None:
        self._client.put_item(TableName=self._table_name, Item=to_item(connection))

    def find(
        self,
        *,
        provider: MailboxProvider,
        address: EmailAddress,
    ) -> MailboxConnection | None:
        response = self._client.get_item(
            TableName=self._table_name,
            Key={
                CONNECTION_PARTITION_KEY: {
                    "S": connection_id(provider=provider, address=address),
                },
            },
            ConsistentRead=True,
        )
        item = response.get("Item")

        return to_entity(item) if item else None

    def find_by_user(self, user_id: UserId) -> Sequence[MailboxConnection]:
        connections: list[MailboxConnection] = []
        start_key: dict[str, AttributeValueTypeDef] | None = None

        while True:
            response = (
                self._client.query(
                    TableName=self._table_name,
                    IndexName=CONNECTION_BY_USER_INDEX,
                    KeyConditionExpression=f"{USER_ID_ATTRIBUTE} = :user_id",
                    ExpressionAttributeValues={":user_id": {"S": str(user_id.value)}},
                    ExclusiveStartKey=start_key,
                )
                if start_key is not None
                else self._client.query(
                    TableName=self._table_name,
                    IndexName=CONNECTION_BY_USER_INDEX,
                    KeyConditionExpression=f"{USER_ID_ATTRIBUTE} = :user_id",
                    ExpressionAttributeValues={":user_id": {"S": str(user_id.value)}},
                )
            )
            connections.extend(to_entity(item) for item in response.get("Items", []))
            start_key = response.get("LastEvaluatedKey") or None

            if start_key is None:
                return connections

    def list_active(self) -> Sequence[MailboxConnection]:
        """Every mailbox currently worth syncing.

        A scan, deliberately: this answers "all of them, across all users",
        which no key can narrow. It backs periodic maintenance — a catch-up
        pass, later the subscription renewals — never a user request.
        """
        connections: list[MailboxConnection] = []
        start_key: dict[str, AttributeValueTypeDef] | None = None

        while True:
            response = (
                self._client.scan(
                    TableName=self._table_name,
                    FilterExpression="#status = :active",
                    ExpressionAttributeNames={"#status": STATUS_ATTRIBUTE},
                    ExpressionAttributeValues={
                        ":active": {"S": MailboxConnectionStatus.ACTIVE.value},
                    },
                    ExclusiveStartKey=start_key,
                )
                if start_key is not None
                else self._client.scan(
                    TableName=self._table_name,
                    FilterExpression="#status = :active",
                    ExpressionAttributeNames={"#status": STATUS_ATTRIBUTE},
                    ExpressionAttributeValues={
                        ":active": {"S": MailboxConnectionStatus.ACTIVE.value},
                    },
                )
            )
            connections.extend(to_entity(item) for item in response.get("Items", []))
            start_key = response.get("LastEvaluatedKey") or None

            if start_key is None:
                return connections
