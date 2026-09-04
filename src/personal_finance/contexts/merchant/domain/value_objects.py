from __future__ import annotations

import dataclasses
import enum
import re
from typing import Self
import uuid

from personal_finance.contexts.merchant.domain.normalization import (
    derive_root_key,
    normalize_counterparty,
)
from personal_finance.shared.domain.value_objects import (
    JsonValue,
    PosixTime,
    ValueObject,
)


class MerchantCategory(enum.Enum):
    """What kind of spending this merchant represents.

    Explicit string values: the category is persisted and shown in a dropdown,
    so reordering the members must not rewrite anybody's data.
    """

    UNCATEGORIZED = "uncategorized"
    GROCERIES = "groceries"
    RESTAURANTS = "restaurants"
    TRANSPORT = "transport"
    FUEL = "fuel"
    SHOPPING = "shopping"
    ENTERTAINMENT = "entertainment"
    SUBSCRIPTIONS = "subscriptions"
    UTILITIES = "utilities"
    HEALTH = "health"
    EDUCATION = "education"
    TRAVEL = "travel"
    FEES = "fees"
    TRANSFERS = "transfers"
    INCOME = "income"
    OTHER = "other"


# What a category a user wrote for themselves is keyed by. The prefix is the
# whole point: a merchant stores only the key, so a default shipped later
# under a slug somebody had already chosen would silently relabel their
# spending. Two vocabularies, one field, and no way for them to collide.
CUSTOM_CATEGORY_PREFIX = "custom:"

_CATEGORY_KEY = re.compile(r"[a-z0-9]+(?:[-_][a-z0-9]+)*")


@dataclasses.dataclass(frozen=True, slots=True)
class CategoryKey(ValueObject):
    """The category a merchant is filed under, shipped or homemade.

    Shape is all that is checked here. Which keys a given user may actually
    choose from is the application layer's answer — it depends on what that
    user has created — and a key read back from storage is shown as it stands
    rather than rejected, so one unrecognised value cannot take a whole
    merchant list down with it.
    """

    value: str

    def __post_init__(self) -> None:
        body = self.value.removeprefix(CUSTOM_CATEGORY_PREFIX)

        if not _CATEGORY_KEY.fullmatch(body):
            raise ValueError(f"Invalid category key: {self.value!r}")

    @classmethod
    def default(cls, category: MerchantCategory) -> Self:
        return cls(value=category.value)

    @classmethod
    def new_custom(cls) -> Self:
        """A fresh identity for a category somebody is about to name.

        Random rather than derived from that name. A name is a thing people
        get wrong and fix — the whole reason renaming exists — and a key built
        out of one would make every correction a rewrite of every merchant
        filed under it, which is a multi-write that can fail halfway and leave
        somebody's spending split across a bucket that no longer exists.
        """
        return cls(value=f"{CUSTOM_CATEGORY_PREFIX}{uuid.uuid4().hex}")

    @classmethod
    def custom(cls, key: str) -> Self:
        """An existing custom key, read back from storage or from a request."""
        return cls(value=f"{CUSTOM_CATEGORY_PREFIX}{key}")

    @classmethod
    def uncategorized(cls) -> Self:
        return cls(value=MerchantCategory.UNCATEGORIZED.value)

    @property
    def is_custom(self) -> bool:
        return self.value.startswith(CUSTOM_CATEGORY_PREFIX)

    @property
    def is_uncategorized(self) -> bool:
        return self.value == MerchantCategory.UNCATEGORIZED.value

    def to_dict(self) -> JsonValue:
        return self.value


class MerchantStatus(enum.Enum):
    # Everything about this merchant was derived, and nobody has looked at it.
    AUTOMATIC = "automatic"
    # A user named it, categorized it or accepted it as it stands.
    CONFIRMED = "confirmed"


class AliasOrigin(enum.Enum):
    """Why this alias hangs off this merchant.

    Kept per alias rather than only per merchant so a review screen can say
    which child is a guess and which one the user put there.
    """

    # It created the merchant: the first spelling we ever saw.
    SEED = "seed"
    # Its root key matched the merchant's — the same name modulo noise.
    DERIVED = "derived"
    # It only looks like a branded variant of the merchant. A guess.
    SUGGESTED = "suggested"
    # A user moved or split it here. Outranks every rule, forever.
    MANUAL = "manual"


class CounterpartyKind(enum.Enum):
    """Whether the counterparty is a business or a person.

    Merchant's own vocabulary, mapped at the boundary from whatever the
    publishing context calls its transaction kinds. Transfers name people, and
    people must never be grouped by a shared first name, so the sub-brand
    suggestion is only offered for businesses.
    """

    BUSINESS = "business"
    PERSON = "person"
    UNKNOWN = "unknown"


@dataclasses.dataclass(frozen=True, slots=True)
class MerchantId(ValueObject):
    value: uuid.UUID

    @classmethod
    def new(cls) -> Self:
        return cls(value=uuid.uuid4())

    @classmethod
    def from_string(cls, value: str) -> Self:
        try:
            return cls(value=uuid.UUID(value))
        except ValueError as error:
            raise ValueError(f"Invalid merchant id: {value!r}") from error

    def to_dict(self) -> JsonValue:
        return str(self.value)


@dataclasses.dataclass(frozen=True, slots=True)
class AliasFingerprint(ValueObject):
    """The identity of one spelling of a counterparty.

    Built through `from_raw` so every caller normalizes the same way; the
    constructor stays open for reading a stored value back.
    """

    value: str

    def __post_init__(self) -> None:
        if not self.value.strip():
            raise ValueError("Alias fingerprint cannot be empty")

    @classmethod
    def from_raw(cls, raw: str) -> Self:
        fingerprint = normalize_counterparty(raw)

        if not fingerprint:
            raise ValueError(f"Counterparty has no recognisable text: {raw!r}")

        return cls(value=fingerprint)

    def to_dict(self) -> JsonValue:
        return self.value


@dataclasses.dataclass(frozen=True, slots=True)
class MerchantRootKey(ValueObject):
    """The grouping key a parent merchant is recognised by."""

    value: str

    def __post_init__(self) -> None:
        if not self.value.strip():
            raise ValueError("Merchant root key cannot be empty")

    @classmethod
    def from_fingerprint(cls, fingerprint: AliasFingerprint) -> Self:
        return cls(value=derive_root_key(fingerprint.value))

    def to_dict(self) -> JsonValue:
        return self.value


@dataclasses.dataclass(frozen=True, slots=True)
class MerchantAlias(ValueObject):
    """One spelling of a merchant, as some bank wrote it.

    Carries its own root key so detaching it takes its grouping with it: after
    a move, the alias stops making its old parent reachable and starts making
    the new one reachable, with no separate bookkeeping to keep in step.

    Counters are sightings of the *name*, not money. How much was spent is the
    Financial context's business, and no amount is ever stored here.
    """

    fingerprint: AliasFingerprint
    root_key: MerchantRootKey
    # The last raw form seen, kept only so a review screen can show the user
    # what their bank actually wrote.
    raw_text: str
    origin: AliasOrigin
    times_seen: int
    first_seen: PosixTime
    last_seen: PosixTime

    def __post_init__(self) -> None:
        if self.times_seen < 1:
            raise ValueError("An alias exists because it was seen at least once")

    @classmethod
    def first_sighting(
        cls,
        *,
        fingerprint: AliasFingerprint,
        raw_text: str,
        origin: AliasOrigin,
        seen_at: PosixTime,
    ) -> Self:
        return cls(
            fingerprint=fingerprint,
            root_key=MerchantRootKey.from_fingerprint(fingerprint),
            raw_text=raw_text.strip(),
            origin=origin,
            times_seen=1,
            first_seen=seen_at,
            last_seen=seen_at,
        )

    def seen_again(self, *, raw_text: str, seen_at: PosixTime) -> Self:
        return dataclasses.replace(
            self,
            raw_text=raw_text.strip() or self.raw_text,
            times_seen=self.times_seen + 1,
            # A sighting can arrive out of order: the queue is at-least-once
            # and a retry may be older than what we already recorded.
            first_seen=min(self.first_seen, seen_at, key=_epoch),
            last_seen=max(self.last_seen, seen_at, key=_epoch),
        )

    def claimed_by_user(self) -> Self:
        """The same alias, marked as a decision instead of a derivation."""
        return dataclasses.replace(self, origin=AliasOrigin.MANUAL)


def _epoch(moment: PosixTime) -> int:
    return moment.as_epoch_seconds()
