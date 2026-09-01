"""Where the short-lived proofs live, and how they are spent exactly once.

One table, three shapes, told apart by a prefix on the partition key:

    verify#<email>            the pending code, then the ticket it becomes
    reset-window#<email>      how much reset mail this address has caused
    reset#<sha256(token)>     one live reset link

Two rules run through all of it.

**Time-to-live is housekeeping, never a check.** DynamoDB's sweep is eventual
and routinely hours late, so every expiry that matters is also compared here,
in a condition expression or in the entity. TTL only keeps the table from
growing.

**A one-time secret is spent by a conditional write.** Reading a record and
then deleting it leaves a window in which two requests both saw it alive; a
`DeleteItem` that carries its own condition does not. That is what makes one
verified address into one account and one reset link into one password.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
import uuid

from personal_finance.contexts.identity.domain.credentials import (
    EmailVerification,
    PasswordResetTicket,
    PasswordResetWindow,
    SendWindow,
    VerificationState,
)
from personal_finance.contexts.identity.domain.value_objects import Email, SecretHash
from personal_finance.shared.domain.value_objects import PosixTime, UserId


if TYPE_CHECKING:
    from mypy_boto3_dynamodb.client import DynamoDBClient
    from mypy_boto3_dynamodb.type_defs import AttributeValueTypeDef


PARTITION_KEY = "challenge_id"
# The attribute DynamoDB's own time-to-live sweep watches. Named to match what
# `provisioning.TTL_ATTRIBUTE` enables on every other table here.
#
# It is always the *last* moment anything in the record still matters, which
# is not always the same as the thing the record is about: a challenge holds a
# send window and, once the code is accepted, a ticket that can outlive it.
# Sweeping on the window alone would delete a live ticket out from under
# somebody who took their time filling in the form.
TTL_ATTRIBUTE = "expires_at"
# The window's own end, stored separately for exactly that reason.
WINDOW_EXPIRES_AT_ATTRIBUTE = "window_expires_at"

VERIFICATION_PREFIX = "verify#"
RESET_WINDOW_PREFIX = "reset-window#"
RESET_TICKET_PREFIX = "reset#"


class CorruptChallengeItemError(Exception):
    """Raised when a stored challenge does not match the expected shape."""


def verification_key(email: Email) -> str:
    return f"{VERIFICATION_PREFIX}{email.value}"


def reset_window_key(email: Email) -> str:
    return f"{RESET_WINDOW_PREFIX}{email.value}"


def reset_ticket_key(token_hash: SecretHash) -> str:
    return f"{RESET_TICKET_PREFIX}{token_hash.value}"


def _string(item: dict[str, AttributeValueTypeDef], key: str) -> str:
    value = item.get(key, {}).get("S")

    if value is None:
        raise CorruptChallengeItemError(f"Missing string attribute {key!r}")

    return value


def _number(item: dict[str, AttributeValueTypeDef], key: str) -> int:
    value = item.get(key, {}).get("N")

    if value is None:
        raise CorruptChallengeItemError(f"Missing numeric attribute {key!r}")

    return int(value)


def _optional_number(item: dict[str, AttributeValueTypeDef], key: str) -> int | None:
    value = item.get(key, {}).get("N")

    return int(value) if value is not None else None


def _optional_string(item: dict[str, AttributeValueTypeDef], key: str) -> str | None:
    return item.get(key, {}).get("S")


def verification_to_item(
    verification: EmailVerification,
) -> dict[str, AttributeValueTypeDef]:
    item: dict[str, AttributeValueTypeDef] = {
        PARTITION_KEY: {"S": verification_key(verification.email)},
        "email": {"S": verification.email.value},
        "code_hash": {"S": verification.code_hash.value},
        "code_expires_at": {"N": str(verification.code_expires_at.as_epoch_seconds())},
        "attempts": {"N": str(verification.attempts)},
        "sends": {"N": str(verification.window.sends)},
        "last_sent_at": {"N": str(verification.window.last_sent_at.as_epoch_seconds())},
        WINDOW_EXPIRES_AT_ATTRIBUTE: {
            "N": str(verification.window.expires_at.as_epoch_seconds()),
        },
    }
    keep_until = verification.window.expires_at.as_epoch_seconds()

    if (
        verification.ticket_hash is not None
        and verification.ticket_expires_at is not None
    ):
        item["ticket_hash"] = {"S": verification.ticket_hash.value}
        item["ticket_expires_at"] = {
            "N": str(verification.ticket_expires_at.as_epoch_seconds()),
        }
        # A code accepted near the end of the window buys a ticket that
        # outlives it. The record has to last as long as the ticket does, or
        # registration fails for somebody who did everything right.
        keep_until = max(keep_until, verification.ticket_expires_at.as_epoch_seconds())

    item[TTL_ATTRIBUTE] = {"N": str(keep_until)}

    return item


def verification_to_entity(
    item: dict[str, AttributeValueTypeDef],
) -> EmailVerification:
    ticket_hash = _optional_string(item, "ticket_hash")
    ticket_expires_at = _optional_number(item, "ticket_expires_at")

    return EmailVerification(
        email=Email(_string(item, "email")),
        code_hash=SecretHash(_string(item, "code_hash")),
        code_expires_at=PosixTime.from_epoch_seconds(_number(item, "code_expires_at")),
        window=SendWindow(
            sends=_number(item, "sends"),
            last_sent_at=PosixTime.from_epoch_seconds(_number(item, "last_sent_at")),
            expires_at=PosixTime.from_epoch_seconds(
                _number(item, WINDOW_EXPIRES_AT_ATTRIBUTE),
            ),
        ),
        attempts=_number(item, "attempts"),
        ticket_hash=SecretHash(ticket_hash) if ticket_hash else None,
        ticket_expires_at=(
            PosixTime.from_epoch_seconds(ticket_expires_at)
            if ticket_expires_at is not None
            else None
        ),
    )


class DynamoDBEmailVerificationRepository:
    """`EmailVerificationRepository` over the challenges table."""

    def __init__(self, *, client: DynamoDBClient, table_name: str) -> None:
        self._client = client
        self._table_name = table_name

    def find(self, email: Email) -> EmailVerification | None:
        response = self._client.get_item(
            TableName=self._table_name,
            Key={PARTITION_KEY: {"S": verification_key(email)}},
            ConsistentRead=True,
        )
        item = response.get("Item")

        return verification_to_entity(item) if item else None

    def save(
        self,
        verification: EmailVerification,
        *,
        expected: VerificationState | None,
    ) -> bool:
        """Write the challenge, but only onto the record that was read.

        The counters are the version: every change to a challenge moves one of
        them, so conditioning on both is what keeps two requests racing the
        same code from each counting as the only attempt.
        """
        item = verification_to_item(verification)

        try:
            if expected is None:
                self._client.put_item(
                    TableName=self._table_name,
                    Item=item,
                    ConditionExpression=f"attribute_not_exists({PARTITION_KEY})",
                )
            else:
                self._client.put_item(
                    TableName=self._table_name,
                    Item=item,
                    ConditionExpression="sends = :sends AND attempts = :attempts",
                    ExpressionAttributeValues={
                        ":sends": {"N": str(expected.sends)},
                        ":attempts": {"N": str(expected.attempts)},
                    },
                )
        except self._client.exceptions.ConditionalCheckFailedException:
            return False

        return True

    def consume_ticket(
        self,
        *,
        email: Email,
        ticket_hash: SecretHash,
        now: PosixTime,
    ) -> bool:
        """Spend the registration ticket in the same write that checks it.

        The expiry is compared here rather than trusted to the table's sweep:
        an item whose time-to-live passed an hour ago is still readable, and
        registering off it would make the whole window meaningless.
        """
        try:
            self._client.delete_item(
                TableName=self._table_name,
                Key={PARTITION_KEY: {"S": verification_key(email)}},
                ConditionExpression=(
                    "ticket_hash = :hash AND ticket_expires_at > :now"
                ),
                ExpressionAttributeValues={
                    ":hash": {"S": ticket_hash.value},
                    ":now": {"N": str(now.as_epoch_seconds())},
                },
            )
        except self._client.exceptions.ConditionalCheckFailedException:
            return False

        return True


def reset_window_to_item(
    window: PasswordResetWindow,
) -> dict[str, AttributeValueTypeDef]:
    item: dict[str, AttributeValueTypeDef] = {
        PARTITION_KEY: {"S": reset_window_key(window.email)},
        "email": {"S": window.email.value},
        "sends": {"N": str(window.window.sends)},
        "last_sent_at": {"N": str(window.window.last_sent_at.as_epoch_seconds())},
        TTL_ATTRIBUTE: {"N": str(window.window.expires_at.as_epoch_seconds())},
    }

    if window.issued_token_hash is not None:
        item["issued_token_hash"] = {"S": window.issued_token_hash.value}

    return item


def reset_window_to_entity(
    item: dict[str, AttributeValueTypeDef],
) -> PasswordResetWindow:
    issued = _optional_string(item, "issued_token_hash")

    return PasswordResetWindow(
        email=Email(_string(item, "email")),
        window=SendWindow(
            sends=_number(item, "sends"),
            last_sent_at=PosixTime.from_epoch_seconds(_number(item, "last_sent_at")),
            expires_at=PosixTime.from_epoch_seconds(_number(item, TTL_ATTRIBUTE)),
        ),
        issued_token_hash=SecretHash(issued) if issued else None,
    )


def reset_ticket_to_item(
    ticket: PasswordResetTicket,
) -> dict[str, AttributeValueTypeDef]:
    return {
        PARTITION_KEY: {"S": reset_ticket_key(ticket.token_hash)},
        "email": {"S": ticket.email.value},
        "user_id": {"S": str(ticket.user_id.value)},
        TTL_ATTRIBUTE: {"N": str(ticket.expires_at.as_epoch_seconds())},
    }


def reset_ticket_to_entity(
    item: dict[str, AttributeValueTypeDef],
) -> PasswordResetTicket:
    stored_key = _string(item, PARTITION_KEY)

    return PasswordResetTicket(
        token_hash=SecretHash(stored_key.removeprefix(RESET_TICKET_PREFIX)),
        user_id=UserId(value=uuid.UUID(_string(item, "user_id"))),
        email=Email(_string(item, "email")),
        expires_at=PosixTime.from_epoch_seconds(_number(item, TTL_ATTRIBUTE)),
    )


class DynamoDBPasswordResetRepository:
    """`PasswordResetRepository` over the same challenges table."""

    def __init__(self, *, client: DynamoDBClient, table_name: str) -> None:
        self._client = client
        self._table_name = table_name

    def find_window(self, email: Email) -> PasswordResetWindow | None:
        response = self._client.get_item(
            TableName=self._table_name,
            Key={PARTITION_KEY: {"S": reset_window_key(email)}},
            ConsistentRead=True,
        )
        item = response.get("Item")

        return reset_window_to_entity(item) if item else None

    def save_window(
        self,
        window: PasswordResetWindow,
        *,
        expected_sends: int | None,
    ) -> bool:
        item = reset_window_to_item(window)

        try:
            if expected_sends is None:
                self._client.put_item(
                    TableName=self._table_name,
                    Item=item,
                    ConditionExpression=f"attribute_not_exists({PARTITION_KEY})",
                )
            else:
                self._client.put_item(
                    TableName=self._table_name,
                    Item=item,
                    ConditionExpression="sends = :sends",
                    ExpressionAttributeValues={
                        ":sends": {"N": str(expected_sends)},
                    },
                )
        except self._client.exceptions.ConditionalCheckFailedException:
            return False

        return True

    def save_ticket(self, ticket: PasswordResetTicket) -> None:
        self._client.put_item(
            TableName=self._table_name,
            Item=reset_ticket_to_item(ticket),
        )

    def delete_ticket(self, token_hash: SecretHash) -> None:
        self._client.delete_item(
            TableName=self._table_name,
            Key={PARTITION_KEY: {"S": reset_ticket_key(token_hash)}},
        )

    def consume_ticket(
        self,
        *,
        token_hash: SecretHash,
        now: PosixTime,
    ) -> PasswordResetTicket | None:
        """Spend the link in the write that checks it, and report what it was.

        `ALL_OLD` is what makes this one round trip: the account the link
        belongs to comes back out of the delete, so nothing reads the record
        first and gives a second request a window to read it too.
        """
        try:
            response = self._client.delete_item(
                TableName=self._table_name,
                Key={PARTITION_KEY: {"S": reset_ticket_key(token_hash)}},
                ConditionExpression=f"{TTL_ATTRIBUTE} > :now",
                ExpressionAttributeValues={":now": {"N": str(now.as_epoch_seconds())}},
                ReturnValues="ALL_OLD",
            )
        except self._client.exceptions.ConditionalCheckFailedException:
            return None

        item = response.get("Attributes")

        return reset_ticket_to_entity(item) if item else None
