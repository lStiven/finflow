from __future__ import annotations

from mypy_boto3_dynamodb.client import DynamoDBClient
from mypy_boto3_dynamodb.type_defs import AttributeValueTypeDef

from personal_finance.contexts.ingestion.domain.entities import UserInbox
from personal_finance.contexts.ingestion.domain.policies import AuthorizedSenderPolicy
from personal_finance.contexts.ingestion.domain.value_objects import EmailAddress
from personal_finance.shared.domain.value_objects import UserId


INBOX_PARTITION_KEY = "address"
ALLOWED_ADDRESSES = "allowed_addresses"
ALLOWED_DOMAINS = "allowed_domains"


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
        "user_id": {"S": str(inbox.user_id.value)},
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
    user_id = item.get("user_id", {}).get("S")
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

    def save(self, inbox: UserInbox) -> None:
        self._client.put_item(TableName=self._table_name, Item=to_item(inbox))
