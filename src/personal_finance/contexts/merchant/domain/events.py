from __future__ import annotations

import dataclasses

from personal_finance.contexts.merchant.domain.value_objects import (
    AliasFingerprint,
    AliasOrigin,
    MerchantCategory,
    MerchantId,
)
from personal_finance.shared.domain.events import Event
from personal_finance.shared.domain.value_objects import UserId


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class MerchantEvent(Event):
    """Every merchant fact belongs to exactly one user.

    Merchants are per-user by design: one person renaming `ARA` to their own
    shorthand must never rewrite what anybody else sees.
    """

    merchant_id: MerchantId
    user_id: UserId


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class MerchantIdentified(MerchantEvent):
    display_name: str
    category: MerchantCategory


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class MerchantAliasLinked(MerchantEvent):
    """A spelling now resolves to this merchant.

    Published outward: a context holding past transactions needs this to
    re-attribute them after a user corrects a grouping.
    """

    fingerprint: AliasFingerprint
    origin: AliasOrigin


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class MerchantAliasDetached(MerchantEvent):
    fingerprint: AliasFingerprint


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class MerchantSightingRecorded(MerchantEvent):
    fingerprint: AliasFingerprint


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class MerchantRenamed(MerchantEvent):
    display_name: str


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class MerchantReclassified(MerchantEvent):
    category: MerchantCategory


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class MerchantConfirmed(MerchantEvent):
    pass


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class MerchantsMerged(MerchantEvent):
    """`merchant_id` survived; `absorbed_merchant_id` no longer exists.

    Published outward because anything holding the absorbed id is now holding
    a dangling reference.
    """

    absorbed_merchant_id: MerchantId
