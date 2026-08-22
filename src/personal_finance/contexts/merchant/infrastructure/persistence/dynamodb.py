"""Storage for merchants, their children, and the keys they are found by.

One table, partitioned by user, with the sort key carrying the record type:

    MERCHANT#<id>     the merchant itself, aliases inline
    ALIAS#<print>     a spelling -> the merchant that owns it
    ROOT#<key>        a grouping key -> the merchant it reaches
    EVENT#<id>        an integration event already applied, TTL'd away later

The pointers exist because resolving a counterparty happens on every parsed
email and must cost one read, not a scan of the user's merchants. They are
never cleaned up on write: a move or a merge saves the new owner, whose put
overwrites the pointer, and a pointer that survives its merchant is verified
away on read. That is deliberate — cleanup would have to run in the right
order relative to the other merchant's save, and getting it wrong would drop
a pointer that had just been rewritten.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
import datetime
import uuid

from mypy_boto3_dynamodb.client import DynamoDBClient
from mypy_boto3_dynamodb.type_defs import AttributeValueTypeDef, QueryInputTypeDef

from personal_finance.contexts.merchant.domain.entities import Merchant
from personal_finance.contexts.merchant.domain.value_objects import (
    AliasFingerprint,
    AliasOrigin,
    MerchantAlias,
    MerchantCategory,
    MerchantId,
    MerchantRootKey,
    MerchantStatus,
)
from personal_finance.shared.domain.value_objects import PosixTime, UserId


PARTITION_KEY = "user_id"
SORT_KEY = "entity_id"
TTL_ATTRIBUTE = "expires_at"

MERCHANT_PREFIX = "MERCHANT#"
ALIAS_PREFIX = "ALIAS#"
ROOT_PREFIX = "ROOT#"
EVENT_PREFIX = "EVENT#"

MERCHANT_ID_ATTRIBUTE = "merchant_id"

# How long a handled event is remembered. Comfortably longer than the 14 days
# SQS will keep retrying a message, so a redelivery can never outlive the
# record that recognises it.
PROCESSED_EVENT_RETENTION_DAYS = 30


class CorruptMerchantItemError(Exception):
    """Raised when a stored merchant does not match the expected shape."""


def _string(item: Mapping[str, AttributeValueTypeDef], key: str) -> str | None:
    return item.get(key, {}).get("S")


def _number(item: Mapping[str, AttributeValueTypeDef], key: str) -> int:
    raw = item.get(key, {}).get("N")

    return int(raw) if raw is not None else 0


def _alias_to_item(alias: MerchantAlias) -> AttributeValueTypeDef:
    return {
        "M": {
            "fingerprint": {"S": alias.fingerprint.value},
            "root_key": {"S": alias.root_key.value},
            "raw_text": {"S": alias.raw_text},
            "origin": {"S": alias.origin.value},
            "times_seen": {"N": str(alias.times_seen)},
            "first_seen": {"N": str(alias.first_seen.as_epoch_seconds())},
            "last_seen": {"N": str(alias.last_seen.as_epoch_seconds())},
        },
    }


def _alias_to_entity(item: Mapping[str, AttributeValueTypeDef]) -> MerchantAlias:
    fingerprint = _string(item, "fingerprint")
    root_key = _string(item, "root_key")

    if fingerprint is None or root_key is None:
        raise CorruptMerchantItemError("Stored alias is missing its keys")

    return MerchantAlias(
        fingerprint=AliasFingerprint(value=fingerprint),
        root_key=MerchantRootKey(value=root_key),
        raw_text=_string(item, "raw_text") or fingerprint,
        origin=AliasOrigin(_string(item, "origin") or AliasOrigin.SEED.value),
        times_seen=max(_number(item, "times_seen"), 1),
        first_seen=PosixTime.from_epoch_seconds(_number(item, "first_seen")),
        last_seen=PosixTime.from_epoch_seconds(_number(item, "last_seen")),
    )


def to_item(merchant: Merchant) -> dict[str, AttributeValueTypeDef]:
    return {
        PARTITION_KEY: {"S": str(merchant.user_id.value)},
        SORT_KEY: {"S": f"{MERCHANT_PREFIX}{merchant.id.value}"},
        MERCHANT_ID_ATTRIBUTE: {"S": str(merchant.id.value)},
        "display_name": {"S": merchant.display_name},
        "category": {"S": merchant.category.value},
        "status": {"S": merchant.status.value},
        "created_at": {"N": str(merchant.created_at.as_epoch_seconds())},
        "aliases": {"L": [_alias_to_item(alias) for alias in merchant.children]},
    }


def to_entity(item: Mapping[str, AttributeValueTypeDef]) -> Merchant:
    user_id = _string(item, PARTITION_KEY)
    merchant_id = _string(item, MERCHANT_ID_ATTRIBUTE)
    display_name = _string(item, "display_name")

    if user_id is None or merchant_id is None or display_name is None:
        raise CorruptMerchantItemError("Stored merchant is missing its identity")

    aliases = [
        _alias_to_entity(element["M"])
        for element in item.get("aliases", {}).get("L", [])
        if "M" in element
    ]

    return Merchant(
        id=MerchantId.from_string(merchant_id),
        user_id=UserId.from_string(user_id),
        display_name=display_name,
        category=MerchantCategory(
            _string(item, "category") or MerchantCategory.UNCATEGORIZED.value,
        ),
        status=MerchantStatus(
            _string(item, "status") or MerchantStatus.AUTOMATIC.value
        ),
        created_at=PosixTime.from_epoch_seconds(_number(item, "created_at")),
        aliases={alias.fingerprint: alias for alias in aliases},
    )


class DynamoDBMerchantRepository:
    """`MerchantRepository` over the single per-user partition described above."""

    def __init__(self, *, client: DynamoDBClient, table_name: str) -> None:
        self._client = client
        self._table_name = table_name

    def find(self, *, user_id: UserId, merchant_id: MerchantId) -> Merchant | None:
        response = self._client.get_item(
            TableName=self._table_name,
            Key=self._key(user_id, f"{MERCHANT_PREFIX}{merchant_id.value}"),
        )
        item = response.get("Item")

        return to_entity(item) if item else None

    def find_by_alias(
        self,
        *,
        user_id: UserId,
        fingerprint: AliasFingerprint,
    ) -> Merchant | None:
        response = self._client.get_item(
            TableName=self._table_name,
            Key=self._key(user_id, f"{ALIAS_PREFIX}{fingerprint.value}"),
        )
        pointer = response.get("Item")

        if pointer is None:
            return None

        merchant_id = _string(pointer, MERCHANT_ID_ATTRIBUTE)

        if merchant_id is None:
            return None

        merchant = self.find(
            user_id=user_id,
            merchant_id=MerchantId.from_string(merchant_id),
        )

        # A pointer that outlived its merchant, or one whose alias has since
        # moved, answers None: the caller then treats the spelling as new.
        return merchant if merchant and merchant.has_alias(fingerprint) else None

    def list_root_keys(self, user_id: UserId) -> Mapping[MerchantRootKey, MerchantId]:
        keys: dict[MerchantRootKey, MerchantId] = {}

        for item in self._query_prefix(user_id, ROOT_PREFIX):
            sort_value = _string(item, SORT_KEY)
            merchant_id = _string(item, MERCHANT_ID_ATTRIBUTE)

            if sort_value is None or merchant_id is None:
                continue

            keys[MerchantRootKey(value=sort_value.removeprefix(ROOT_PREFIX))] = (
                MerchantId.from_string(merchant_id)
            )

        return keys

    def list_by_user(self, user_id: UserId) -> Sequence[Merchant]:
        return [
            to_entity(item) for item in self._query_prefix(user_id, MERCHANT_PREFIX)
        ]

    def save(self, merchant: Merchant) -> None:
        # The merchant first: a pointer aimed at a record that does not exist
        # yet is recoverable, a record nothing points at is not.
        self._client.put_item(TableName=self._table_name, Item=to_item(merchant))

        for alias in merchant.children:
            self._put_pointer(
                merchant,
                sort_value=f"{ALIAS_PREFIX}{alias.fingerprint.value}",
            )

        for root_key in sorted(key.value for key in merchant.root_keys):
            self._put_pointer(merchant, sort_value=f"{ROOT_PREFIX}{root_key}")

    def delete(self, *, user_id: UserId, merchant_id: MerchantId) -> None:
        """Drop the record only.

        Its pointers were already rewritten to the surviving merchant, and any
        that were not are verified away on read.
        """
        self._client.delete_item(
            TableName=self._table_name,
            Key=self._key(user_id, f"{MERCHANT_PREFIX}{merchant_id.value}"),
        )

    def _put_pointer(self, merchant: Merchant, *, sort_value: str) -> None:
        self._client.put_item(
            TableName=self._table_name,
            Item={
                **self._key(merchant.user_id, sort_value),
                MERCHANT_ID_ATTRIBUTE: {"S": str(merchant.id.value)},
            },
        )

    def _query_prefix(
        self,
        user_id: UserId,
        prefix: str,
    ) -> Iterator[dict[str, AttributeValueTypeDef]]:
        """Every item of one kind for one user, following pagination.

        A query returns at most 1 MB. A personal account never reaches that,
        but a truncated page here would silently hide a user's own merchants
        from them.
        """
        request: QueryInputTypeDef = {
            "TableName": self._table_name,
            "KeyConditionExpression": (
                f"{PARTITION_KEY} = :user_id AND begins_with({SORT_KEY}, :prefix)"
            ),
            "ExpressionAttributeValues": {
                ":user_id": {"S": str(user_id.value)},
                ":prefix": {"S": prefix},
            },
        }

        while True:
            response = self._client.query(**request)
            yield from response.get("Items", [])
            start_key = response.get("LastEvaluatedKey")

            if not start_key:
                return

            request["ExclusiveStartKey"] = start_key

    def _key(
        self, user_id: UserId, sort_value: str
    ) -> dict[str, AttributeValueTypeDef]:
        return {
            PARTITION_KEY: {"S": str(user_id.value)},
            SORT_KEY: {"S": sort_value},
        }


class DynamoDBProcessedEventStore:
    """`ProcessedEventStore` backed by a conditional write.

    The condition is the whole point: two workers draining the same queue can
    receive the same redelivered message at the same moment, and only the one
    whose write wins is allowed to count the sighting.
    """

    def __init__(
        self,
        *,
        client: DynamoDBClient,
        table_name: str,
        retention_days: int = PROCESSED_EVENT_RETENTION_DAYS,
    ) -> None:
        self._client = client
        self._table_name = table_name
        self._retention_days = retention_days

    def claim(self, *, user_id: UserId, event_id: uuid.UUID) -> bool:
        expires_at = int(
            (
                datetime.datetime.now(tz=datetime.UTC)
                + datetime.timedelta(days=self._retention_days)
            ).timestamp(),
        )

        try:
            self._client.put_item(
                TableName=self._table_name,
                Item={
                    PARTITION_KEY: {"S": str(user_id.value)},
                    SORT_KEY: {"S": f"{EVENT_PREFIX}{event_id}"},
                    TTL_ATTRIBUTE: {"N": str(expires_at)},
                },
                ConditionExpression=f"attribute_not_exists({SORT_KEY})",
            )
        except self._client.exceptions.ConditionalCheckFailedException:
            return False

        return True
