"""A mailbox the project hosts itself, standing in for a real provider.

It exists so the whole path — provider says "something changed", we fetch only
what the user authorized, we ingest it — can be run and tested without an
OAuth flow or a Google account. It behaves like a provider in the ways that
matter:

* it holds messages we cannot see until we ask for them;
* it only hands back what the sender filter asks for;
* it reports an opaque position, and resuming from that position is what
  makes a second notification cheap.

Backed by DynamoDB rather than memory on purpose: seeding happens in a CLI and
reading happens in the API, two processes that share nothing else.
"""

from __future__ import annotations

from collections.abc import Sequence

from mypy_boto3_dynamodb.client import DynamoDBClient
from mypy_boto3_dynamodb.type_defs import AttributeValueTypeDef

from personal_finance.contexts.ingestion.application.mailbox import (
    InboundEmail,
    MailboxBatch,
    MailboxConnection,
    MailboxProvider,
)
from personal_finance.contexts.ingestion.domain.value_objects import (
    EmailAddress,
    EmailMessageId,
)
from personal_finance.shared.domain.value_objects import PosixTime


MAILBOX_PARTITION_KEY = "address"
MAILBOX_SORT_KEY = "sequence"


class CorruptSimulatedEmailError(Exception):
    """Raised when a stored message does not match the expected shape."""


def _read_string(item: dict[str, AttributeValueTypeDef], key: str) -> str:
    value = item.get(key, {}).get("S")

    if value is None:
        raise CorruptSimulatedEmailError(
            f"Missing string attribute {key!r} on stored simulated email",
        )

    return value


def _matches(sender: str, senders: Sequence[str]) -> bool:
    """Same rule the user's own allow-list uses: an exact address, or a whole
    domain.
    """
    domain = sender.rsplit("@", 1)[-1]

    return sender in senders or domain in senders


class SimulatedMailboxStore:
    """Writes into the fake mailbox. Only the seeding CLI uses this.

    Kept apart from the reader because the reader is what the application
    depends on, and it must stay strictly read-only — a mailbox is someone's
    correspondence, and nothing in the ingestion path should be able to change
    one.
    """

    def __init__(self, *, client: DynamoDBClient, table_name: str) -> None:
        self._client = client
        self._table_name = table_name

    def _next_sequence(self, address: EmailAddress) -> int:
        response = self._client.query(
            TableName=self._table_name,
            KeyConditionExpression=f"{MAILBOX_PARTITION_KEY} = :address",
            ExpressionAttributeValues={":address": {"S": address.value}},
            ScanIndexForward=False,
            Limit=1,
        )
        items = response.get("Items", [])

        if not items:
            return 1

        return int(items[0].get(MAILBOX_SORT_KEY, {}).get("N", "0")) + 1

    def deliver(self, email: InboundEmail) -> int:
        """Drop one message into the mailbox and return its position."""
        sequence = self._next_sequence(email.recipient)

        self._client.put_item(
            TableName=self._table_name,
            Item={
                MAILBOX_PARTITION_KEY: {"S": email.recipient.value},
                MAILBOX_SORT_KEY: {"N": str(sequence)},
                "message_id": {"S": email.message_id.value},
                "sender": {"S": email.sender.value},
                "subject": {"S": email.subject},
                "raw_content": {"S": email.raw_content},
                "received_at": {"N": str(email.received_at.as_epoch_seconds())},
            },
        )

        return sequence


class SimulatedMailboxReader:
    """`MailboxReader` over the hosted mailbox. Read-only by construction."""

    provider = MailboxProvider.SIMULATED

    def __init__(self, *, client: DynamoDBClient, table_name: str) -> None:
        self._client = client
        self._table_name = table_name

    def fetch_new(
        self,
        *,
        connection: MailboxConnection,
        senders: Sequence[str],
    ) -> MailboxBatch:
        after = int(connection.cursor) if connection.cursor else 0
        items = self._query_after(connection.address, after=after)
        emails: list[InboundEmail] = []
        highest = after

        for item in items:
            highest = max(highest, int(item.get(MAILBOX_SORT_KEY, {}).get("N", "0")))
            sender = _read_string(item, "sender")

            # The filter is applied to what we take, not merely to what we
            # keep: a message from anyone else is never turned into an
            # `InboundEmail` at all.
            if not _matches(sender, senders):
                continue

            emails.append(
                InboundEmail(
                    message_id=EmailMessageId(_read_string(item, "message_id")),
                    recipient=connection.address,
                    sender=EmailAddress(sender),
                    subject=_read_string(item, "subject"),
                    raw_content=_read_string(item, "raw_content"),
                    received_at=PosixTime.from_epoch_seconds(
                        int(item.get("received_at", {}).get("N", "0")),
                    ),
                ),
            )

        # The cursor advances past everything examined, including the messages
        # the filter skipped: they were seen and deliberately not taken, and
        # re-examining them on every future notification would grow without
        # bound.
        return MailboxBatch(
            emails=tuple(emails),
            cursor=str(highest) if highest else None,
        )

    def _query_after(
        self,
        address: EmailAddress,
        *,
        after: int,
    ) -> list[dict[str, AttributeValueTypeDef]]:
        items: list[dict[str, AttributeValueTypeDef]] = []
        start_key: dict[str, AttributeValueTypeDef] | None = None
        condition = (
            f"{MAILBOX_PARTITION_KEY} = :address AND {MAILBOX_SORT_KEY} > :after"
        )
        values: dict[str, AttributeValueTypeDef] = {
            ":address": {"S": address.value},
            ":after": {"N": str(after)},
        }

        while True:
            response = (
                self._client.query(
                    TableName=self._table_name,
                    KeyConditionExpression=condition,
                    ExpressionAttributeValues=values,
                    ExclusiveStartKey=start_key,
                )
                if start_key is not None
                else self._client.query(
                    TableName=self._table_name,
                    KeyConditionExpression=condition,
                    ExpressionAttributeValues=values,
                )
            )
            items.extend(response.get("Items", []))
            start_key = response.get("LastEvaluatedKey") or None

            if start_key is None:
                return items
