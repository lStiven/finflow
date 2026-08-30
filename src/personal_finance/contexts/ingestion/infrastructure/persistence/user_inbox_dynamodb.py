from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

from personal_finance.contexts.ingestion.domain.entities import UserInbox
from personal_finance.contexts.ingestion.domain.policies import AuthorizedSenderPolicy
from personal_finance.contexts.ingestion.domain.value_objects import EmailAddress
from personal_finance.shared.domain.value_objects import PosixTime, UserId


if TYPE_CHECKING:
    from mypy_boto3_dynamodb.client import DynamoDBClient
    from mypy_boto3_dynamodb.type_defs import AttributeValueTypeDef


INBOX_PARTITION_KEY = "address"
USER_ID_ATTRIBUTE = "user_id"
ALLOWED_ADDRESSES = "allowed_addresses"
ALLOWED_DOMAINS = "allowed_domains"
# Epoch seconds, written once each and never cleared. Absent on every inbox
# registered before they existed, which reads back as "not yet" — the same
# answer as a user who has genuinely not got there.
FORWARDING_CONFIRMED_AT = "forwarding_confirmed_at"
FIRST_ACCEPTED_AT = "first_accepted_at"

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


def _read_time(
    item: dict[str, AttributeValueTypeDef],
    key: str,
) -> PosixTime | None:
    raw = item.get(key, {}).get("N")

    return PosixTime.from_epoch_seconds(int(raw)) if raw else None


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
        forwarding_confirmed_at=_read_time(item, FORWARDING_CONFIRMED_AT),
        first_accepted_at=_read_time(item, FIRST_ACCEPTED_AT),
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
        """Write the fields a user (or their registration) owns, and only
        those.

        An update rather than a `put_item`: the milestones live on this same
        item and are written by the ingest worker, so replacing the record
        wholesale here would let somebody editing their approved senders
        erase a forwarding confirmation that landed in between — a checkmark
        that never comes back, because Google does not send the mail twice.
        """
        policy = inbox.sender_policy
        self._client.update_item(
            TableName=self._table_name,
            Key={INBOX_PARTITION_KEY: {"S": inbox.address.value}},
            UpdateExpression=(
                f"SET {USER_ID_ATTRIBUTE} = :user_id, "
                f"{ALLOWED_ADDRESSES} = :addresses, "
                f"{ALLOWED_DOMAINS} = :domains"
            ),
            ExpressionAttributeValues={
                ":user_id": {"S": str(inbox.user_id.value)},
                ":addresses": {
                    "L": [
                        {"S": value}
                        for value in sorted(
                            address.value for address in policy.allowed_addresses
                        )
                    ],
                },
                ":domains": {
                    "L": [{"S": domain} for domain in sorted(policy.allowed_domains)],
                },
            },
        )

    def mark_forwarding_confirmed(
        self,
        *,
        address: EmailAddress,
        confirmed_at: PosixTime,
    ) -> bool:
        return self._mark_once(
            address=address,
            attribute=FORWARDING_CONFIRMED_AT,
            at=confirmed_at,
        )

    def mark_first_accepted(
        self,
        *,
        address: EmailAddress,
        accepted_at: PosixTime,
    ) -> bool:
        return self._mark_once(
            address=address,
            attribute=FIRST_ACCEPTED_AT,
            at=accepted_at,
        )

    def _mark_once(
        self,
        *,
        address: EmailAddress,
        attribute: str,
        at: PosixTime,
    ) -> bool:
        """Set one milestone without disturbing anything else on the item.

        `if_not_exists` rather than a plain assignment: mail is delivered at
        least once, so the same confirmation may be handled twice, and the
        honest answer to "when did this happen" is the first time, not the
        retry. The condition is what keeps an alias nobody registered from
        being conjured into an inbox by a write.
        """
        try:
            self._client.update_item(
                TableName=self._table_name,
                Key={INBOX_PARTITION_KEY: {"S": address.value}},
                UpdateExpression=(f"SET {attribute} = if_not_exists({attribute}, :at)"),
                ConditionExpression=f"attribute_exists({INBOX_PARTITION_KEY})",
                ExpressionAttributeValues={
                    ":at": {"N": str(at.as_epoch_seconds())},
                },
            )
        except self._client.exceptions.ConditionalCheckFailedException:
            return False

        return True
