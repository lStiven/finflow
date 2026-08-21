from __future__ import annotations

from collections.abc import Sequence

from mypy_boto3_dynamodb.client import DynamoDBClient
from mypy_boto3_dynamodb.type_defs import AttributeValueTypeDef

from personal_finance.contexts.ingestion.domain.entities import UserInbox
from personal_finance.contexts.ingestion.domain.policies import AuthorizedSenderPolicy
from personal_finance.contexts.ingestion.domain.value_objects import EmailAddress
from personal_finance.shared.domain.value_objects import UserId


INBOX_PARTITION_KEY = "address"
USER_ID_ATTRIBUTE = "user_id"
ALLOWED_ADDRESSES = "allowed_addresses"
ALLOWED_DOMAINS = "allowed_domains"

# Listing one user's inboxes is a secondary access path: the table is keyed by
# address because that is what an arriving email carries. A scan would answer
# it too, but its cost grows with every other user's data, so the reverse
# lookup gets its own index.
INBOX_BY_USER_INDEX = "by_user"


class CorruptUserInboxItemError(Exception):
    """Raised when a stored inbox does not match the expected attribute shape."""


def _read_string_list(item: dict[str, AttributeValueTypeDef], key: str) -> list[str]:
    # Stored as a List, not a String Set: DynamoDB rejects an empty set, and a
    # user with no approved senders yet is a normal state.
    values: list[str] = []

    for element in item.get(key, {}).get("L", []):
        value = element.get("S")

        if value is not None:
            values.append(value)

    return values


def to_item(inbox: UserInbox) -> dict[str, AttributeValueTypeDef]:
    policy = inbox.sender_policy

    return {
        INBOX_PARTITION_KEY: {"S": inbox.address.value},
        USER_ID_ATTRIBUTE: {"S": str(inbox.user_id.value)},
        ALLOWED_ADDRESSES: {
            "L": [
                {"S": value}
                for value in sorted(
                    address.value for address in policy.allowed_addresses
                )
            ],
        },
        ALLOWED_DOMAINS: {
            "L": [{"S": domain} for domain in sorted(policy.allowed_domains)],
        },
    }


def to_entity(item: dict[str, AttributeValueTypeDef]) -> UserInbox:
    user_id = item.get(USER_ID_ATTRIBUTE, {}).get("S")
    address = item.get(INBOX_PARTITION_KEY, {}).get("S")

    if user_id is None or address is None:
        raise CorruptUserInboxItemError(
            "Stored inbox is missing its user id or address",
        )

    return UserInbox(
        user_id=UserId.from_string(user_id),
        address=EmailAddress(address),
        sender_policy=AuthorizedSenderPolicy(
            allowed_addresses=frozenset(
                EmailAddress(value)
                for value in _read_string_list(item, ALLOWED_ADDRESSES)
            ),
            allowed_domains=frozenset(_read_string_list(item, ALLOWED_DOMAINS)),
        ),
    )


class DynamoDBUserInboxRepository:
    """`UserInboxRepository` backed by a table partitioned by inbound address.

    The approved senders are denormalized onto the address item so attributing
    and filtering an email costs a single read, on the hottest path in the
    context. The list is small and changes rarely, which is what makes the
    denormalization safe.
    """

    def __init__(self, *, client: DynamoDBClient, table_name: str) -> None:
        self._client = client
        self._table_name = table_name

    def find_by_address(self, address: EmailAddress) -> UserInbox | None:
        response = self._client.get_item(
            TableName=self._table_name,
            Key={INBOX_PARTITION_KEY: {"S": address.value}},
        )
        item = response.get("Item")

        return to_entity(item) if item else None

    def find_by_user(self, user_id: UserId) -> Sequence[UserInbox]:
        """Query the `by_user` index rather than scanning the table.

        Paginated because a query returns at most 1 MB: a personal account
        never reaches that, but a truncated list here would silently hide a
        user's own mailboxes from them.
        """
        inboxes: list[UserInbox] = []
        start_key: dict[str, AttributeValueTypeDef] | None = None

        while True:
            response = (
                self._client.query(
                    TableName=self._table_name,
                    IndexName=INBOX_BY_USER_INDEX,
                    KeyConditionExpression=f"{USER_ID_ATTRIBUTE} = :user_id",
                    ExpressionAttributeValues={":user_id": {"S": str(user_id.value)}},
                    ExclusiveStartKey=start_key,
                )
                if start_key is not None
                else self._client.query(
                    TableName=self._table_name,
                    IndexName=INBOX_BY_USER_INDEX,
                    KeyConditionExpression=f"{USER_ID_ATTRIBUTE} = :user_id",
                    ExpressionAttributeValues={":user_id": {"S": str(user_id.value)}},
                )
            )
            inboxes.extend(to_entity(item) for item in response.get("Items", []))
            start_key = response.get("LastEvaluatedKey") or None

            if start_key is None:
                return inboxes

    def save(self, inbox: UserInbox) -> None:
        self._client.put_item(TableName=self._table_name, Item=to_item(inbox))
