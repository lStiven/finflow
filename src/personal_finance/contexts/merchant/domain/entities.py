from __future__ import annotations

from dataclasses import dataclass, field
from typing import Self

from personal_finance.contexts.merchant.domain.events import (
    MerchantAliasDetached,
    MerchantAliasLinked,
    MerchantConfirmed,
    MerchantIdentified,
    MerchantReclassified,
    MerchantRenamed,
    MerchantSightingRecorded,
    MerchantsMerged,
)
from personal_finance.contexts.merchant.domain.exceptions import (
    LastAliasError,
    MerchantOwnershipError,
    UnknownAliasError,
)
from personal_finance.contexts.merchant.domain.normalization import (
    suggest_display_name,
)
from personal_finance.contexts.merchant.domain.value_objects import (
    AliasFingerprint,
    AliasOrigin,
    CategoryKey,
    MerchantAlias,
    MerchantId,
    MerchantRootKey,
    MerchantStatus,
)
from personal_finance.shared.domain.entities import AggregateRoot
from personal_finance.shared.domain.value_objects import PosixTime, UserId


MAX_DISPLAY_NAME_LENGTH = 120


@dataclass(slots=True)
class Merchant(AggregateRoot[MerchantId]):
    """A canonical merchant and every spelling that resolves to it.

    The parent is this aggregate; the children are its aliases. A merchant is
    reachable only through them — by an exact fingerprint, or by the root key
    an alias derives — so the alias set is not a decoration on the record, it
    *is* how the record is found.
    """

    user_id: UserId
    display_name: str
    category: CategoryKey
    status: MerchantStatus
    created_at: PosixTime
    aliases: dict[AliasFingerprint, MerchantAlias] = field(
        default_factory=lambda: dict[AliasFingerprint, MerchantAlias](),
    )

    def __post_init__(self) -> None:
        self.display_name = _valid_display_name(self.display_name)

    @classmethod
    def seed(
        cls,
        *,
        user_id: UserId,
        fingerprint: AliasFingerprint,
        raw_text: str,
        seen_at: PosixTime,
        display_name: str | None = None,
        category: CategoryKey | None = None,
        origin: AliasOrigin = AliasOrigin.SEED,
    ) -> Self:
        """Create a merchant around the first spelling we saw of it."""
        merchant = cls(
            id=MerchantId.new(),
            user_id=user_id,
            display_name=display_name or suggest_display_name(fingerprint.value),
            category=category or CategoryKey.uncategorized(),
            status=(
                MerchantStatus.CONFIRMED
                if origin is AliasOrigin.MANUAL
                else MerchantStatus.AUTOMATIC
            ),
            created_at=seen_at,
            aliases={
                fingerprint: MerchantAlias.first_sighting(
                    fingerprint=fingerprint,
                    raw_text=raw_text,
                    origin=origin,
                    seen_at=seen_at,
                ),
            },
        )
        merchant.record_event(
            MerchantIdentified(
                merchant_id=merchant.id,
                user_id=merchant.user_id,
                display_name=merchant.display_name,
                category=merchant.category,
            ),
        )
        merchant.record_event(
            MerchantAliasLinked(
                merchant_id=merchant.id,
                user_id=merchant.user_id,
                fingerprint=fingerprint,
                origin=origin,
            ),
        )

        return merchant

    @property
    def root_keys(self) -> frozenset[MerchantRootKey]:
        """Derived, never stored as its own state.

        Every way of reaching this merchant comes from an alias, so a move or
        a merge carries the reachability with it and there is no second
        collection that can fall out of step.
        """
        return frozenset(alias.root_key for alias in self.aliases.values())

    @property
    def children(self) -> list[MerchantAlias]:
        return sorted(self.aliases.values(), key=lambda alias: alias.fingerprint.value)

    @property
    def times_seen(self) -> int:
        return sum(alias.times_seen for alias in self.aliases.values())

    @property
    def first_seen(self) -> PosixTime:
        return min(
            (alias.first_seen for alias in self.aliases.values()),
            key=lambda moment: moment.as_epoch_seconds(),
            default=self.created_at,
        )

    @property
    def last_seen(self) -> PosixTime:
        return max(
            (alias.last_seen for alias in self.aliases.values()),
            key=lambda moment: moment.as_epoch_seconds(),
            default=self.created_at,
        )

    @property
    def needs_review(self) -> bool:
        return self.status is MerchantStatus.AUTOMATIC

    def has_alias(self, fingerprint: AliasFingerprint) -> bool:
        return fingerprint in self.aliases

    def record_sighting(
        self,
        *,
        fingerprint: AliasFingerprint,
        raw_text: str,
        seen_at: PosixTime,
    ) -> None:
        alias = self.aliases.get(fingerprint)

        if alias is None:
            raise UnknownAliasError(
                f"{fingerprint.value!r} does not resolve to this merchant",
            )

        self.aliases[fingerprint] = alias.seen_again(
            raw_text=raw_text,
            seen_at=seen_at,
        )
        self.record_event(
            MerchantSightingRecorded(
                merchant_id=self.id,
                user_id=self.user_id,
                fingerprint=fingerprint,
            ),
        )

    def link_alias(
        self,
        *,
        fingerprint: AliasFingerprint,
        raw_text: str,
        origin: AliasOrigin,
        seen_at: PosixTime,
    ) -> None:
        """Attach a spelling we had not seen before."""
        if fingerprint in self.aliases:
            self.record_sighting(
                fingerprint=fingerprint,
                raw_text=raw_text,
                seen_at=seen_at,
            )

            return

        self.aliases[fingerprint] = MerchantAlias.first_sighting(
            fingerprint=fingerprint,
            raw_text=raw_text,
            origin=origin,
            seen_at=seen_at,
        )

        if origin is AliasOrigin.SUGGESTED:
            # A guess landed on a merchant somebody had already reviewed. It
            # goes back in the review queue rather than riding on a decision
            # that was made before this child existed.
            self.status = MerchantStatus.AUTOMATIC

        self.record_event(
            MerchantAliasLinked(
                merchant_id=self.id,
                user_id=self.user_id,
                fingerprint=fingerprint,
                origin=origin,
            ),
        )

    def detach_alias(self, fingerprint: AliasFingerprint) -> MerchantAlias:
        """Remove a child and hand it to the caller, counters intact."""
        alias = self.aliases.get(fingerprint)

        if alias is None:
            raise UnknownAliasError(
                f"{fingerprint.value!r} does not resolve to this merchant",
            )

        if len(self.aliases) == 1:
            raise LastAliasError(
                f"{fingerprint.value!r} is the only way to reach this merchant",
            )

        del self.aliases[fingerprint]
        self.record_event(
            MerchantAliasDetached(
                merchant_id=self.id,
                user_id=self.user_id,
                fingerprint=fingerprint,
            ),
        )

        return alias

    def adopt_alias(self, alias: MerchantAlias) -> None:
        """Take in a child a user moved here, keeping its history.

        The alias is marked `MANUAL`, which is what makes the correction
        stick: nothing re-derives a grouping a person chose.
        """
        claimed = alias.claimed_by_user()
        existing = self.aliases.get(alias.fingerprint)

        self.aliases[alias.fingerprint] = (
            claimed
            if existing is None
            else _merge_alias_history(existing=existing, incoming=claimed)
        )
        self.status = MerchantStatus.CONFIRMED
        self.record_event(
            MerchantAliasLinked(
                merchant_id=self.id,
                user_id=self.user_id,
                fingerprint=alias.fingerprint,
                origin=AliasOrigin.MANUAL,
            ),
        )

    def rename(self, display_name: str) -> None:
        self.display_name = _valid_display_name(display_name)
        # Naming it is reviewing it: the user looked at this merchant and
        # decided what it is.
        self.status = MerchantStatus.CONFIRMED
        self.record_event(
            MerchantRenamed(
                merchant_id=self.id,
                user_id=self.user_id,
                display_name=self.display_name,
            ),
        )

    def recategorize(self, category: CategoryKey) -> None:
        self.category = category
        self.status = MerchantStatus.CONFIRMED
        self.record_event(
            MerchantReclassified(
                merchant_id=self.id,
                user_id=self.user_id,
                category=category,
            ),
        )

    def uncategorize(self) -> None:
        """Put this merchant back in the default bucket, review status intact.

        For the one case nobody decided anything: the category it was filed
        under stopped existing. `recategorize` would mark it confirmed, which
        would quietly take a merchant nobody has looked at out of the review
        queue on somebody else's behalf.
        """
        if self.category.is_uncategorized:
            return

        self.category = CategoryKey.uncategorized()
        self.record_event(
            MerchantReclassified(
                merchant_id=self.id,
                user_id=self.user_id,
                category=self.category,
            ),
        )

    def propose_category(self, category: CategoryKey) -> None:
        """Record a category nobody has approved yet.

        Distinct from `recategorize`, which is a person deciding and therefore
        confirms the merchant. A proposal comes from a rule or a model, so it
        fills the field but leaves the merchant in the review queue — the user
        still gets to see it and disagree.
        """
        if not self.category.is_uncategorized:
            # Never overwrite a category that is already there: it may be the
            # user's, and this is only a suggestion.
            return

        self.category = category
        self.record_event(
            MerchantReclassified(
                merchant_id=self.id,
                user_id=self.user_id,
                category=category,
            ),
        )

    def confirm(self) -> None:
        """Accept the merchant as it stands, guesses included."""
        self.status = MerchantStatus.CONFIRMED
        self.record_event(
            MerchantConfirmed(merchant_id=self.id, user_id=self.user_id),
        )

    def absorb(self, other: Merchant) -> None:
        """Take every child of `other`, which the caller then deletes."""
        if other.user_id != self.user_id:
            raise MerchantOwnershipError("Merchants belong to different users")

        if other.id == self.id:
            raise MerchantOwnershipError("A merchant cannot absorb itself")

        for alias in other.children:
            existing = self.aliases.get(alias.fingerprint)
            self.aliases[alias.fingerprint] = (
                alias.claimed_by_user()
                if existing is None
                else _merge_alias_history(existing=existing, incoming=alias)
            )

        self.status = MerchantStatus.CONFIRMED
        self.record_event(
            MerchantsMerged(
                merchant_id=self.id,
                user_id=self.user_id,
                absorbed_merchant_id=other.id,
            ),
        )


def _merge_alias_history(
    *,
    existing: MerchantAlias,
    incoming: MerchantAlias,
) -> MerchantAlias:
    """Two records of the same spelling, folded into one.

    Only reachable when the same fingerprint lived under both merchants, which
    a merge can produce. Counters add up rather than one overwriting the other.
    """
    return MerchantAlias(
        fingerprint=existing.fingerprint,
        root_key=existing.root_key,
        raw_text=existing.raw_text,
        origin=AliasOrigin.MANUAL,
        times_seen=existing.times_seen + incoming.times_seen,
        first_seen=min(
            existing.first_seen,
            incoming.first_seen,
            key=lambda moment: moment.as_epoch_seconds(),
        ),
        last_seen=max(
            existing.last_seen,
            incoming.last_seen,
            key=lambda moment: moment.as_epoch_seconds(),
        ),
    )


def _valid_display_name(value: str) -> str:
    name = " ".join(value.split())

    if not name:
        raise ValueError("A merchant needs a display name")

    if len(name) > MAX_DISPLAY_NAME_LENGTH:
        raise ValueError(
            f"Merchant name is longer than {MAX_DISPLAY_NAME_LENGTH} characters",
        )

    return name
