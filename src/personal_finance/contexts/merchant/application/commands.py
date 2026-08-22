from __future__ import annotations

import dataclasses
import uuid

from personal_finance.contexts.merchant.domain.value_objects import (
    AliasFingerprint,
    CounterpartyKind,
    MerchantCategory,
    MerchantId,
)
from personal_finance.shared.domain.value_objects import PosixTime, UserId


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class RecordSightingCommand:
    """One counterparty, as some bank wrote it.

    Deliberately no amount: what was spent belongs to the Financial context.
    Merchant only ever learns that a name was seen.
    """

    user_id: UserId
    counterparty: str
    occurred_at: PosixTime
    # Identifies the integration event this came from, so a redelivery is
    # recognised rather than counted twice.
    event_id: uuid.UUID
    kind: CounterpartyKind = CounterpartyKind.UNKNOWN


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class EditMerchantCommand:
    """A rename, a recategorization, or both. Omitted fields are left alone."""

    user_id: UserId
    merchant_id: MerchantId
    display_name: str | None = None
    category: MerchantCategory | None = None


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class ConfirmMerchantCommand:
    user_id: UserId
    merchant_id: MerchantId


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class MoveAliasCommand:
    """Reattach one child to a different parent."""

    user_id: UserId
    merchant_id: MerchantId
    fingerprint: AliasFingerprint
    target_merchant_id: MerchantId


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class SplitAliasCommand:
    """Pull one child out into a merchant of its own."""

    user_id: UserId
    merchant_id: MerchantId
    fingerprint: AliasFingerprint
    display_name: str | None = None
    category: MerchantCategory | None = None


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class MergeMerchantsCommand:
    """`merchant_id` survives and takes everything `absorbed_merchant_id` had."""

    user_id: UserId
    merchant_id: MerchantId
    absorbed_merchant_id: MerchantId
