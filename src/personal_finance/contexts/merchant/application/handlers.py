from __future__ import annotations

from collections.abc import Mapping
import dataclasses
import enum

from personal_finance.contexts.merchant.application.commands import (
    ConfirmMerchantCommand,
    EditMerchantCommand,
    MergeMerchantsCommand,
    MoveAliasCommand,
    RecordSightingCommand,
    SplitAliasCommand,
)
from personal_finance.contexts.merchant.application.ports import (
    MerchantAdvisor,
    MerchantCandidate,
    MerchantRepository,
    ProcessedEventStore,
)
from personal_finance.contexts.merchant.domain.entities import Merchant
from personal_finance.contexts.merchant.domain.normalization import is_sub_brand_of
from personal_finance.contexts.merchant.domain.value_objects import (
    AliasFingerprint,
    AliasOrigin,
    CounterpartyKind,
    MerchantCategory,
    MerchantId,
    MerchantRootKey,
)
from personal_finance.shared.application.ports import EventPublisher
from personal_finance.shared.domain.value_objects import UserId


# How many of a user's merchants are offered to the advisor at once.
MAX_ADVISOR_CANDIDATES = 60


class MerchantNotFoundError(Exception):
    """Raised when a command names a merchant this user does not own."""


class SameMerchantError(Exception):
    """Raised when a move or a merge names one merchant twice."""


class Resolution(enum.Enum):
    """How a counterparty found its merchant."""

    # The spelling was already known. The overwhelmingly common case.
    KNOWN = "known"
    # A new spelling of a name we already have, modulo store numbers and the
    # rest of the noise. Applied without asking.
    DERIVED = "derived"
    # A new spelling that only *looks* like a branded variant of an existing
    # merchant. Applied, but flagged for the user to confirm or undo.
    SUGGESTED = "suggested"
    # The rules had nothing, and the model recognised the spelling as one of
    # the merchants this user already has. Flagged for review like any guess.
    ADVISED = "advised"
    # Nothing matched: a merchant was created for it.
    CREATED = "created"
    # This integration event had already been applied.
    DUPLICATE = "duplicate"


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class ResolveMerchantResult:
    resolution: Resolution
    merchant: Merchant | None = None


class ResolveMerchantUseCase:
    """Turns a counterparty into a merchant, learning as it goes.

    Three tiers, in order of how much they assume:

    1. **the exact spelling** — certain, and where nearly everything lands
       once a user's merchants have settled;
    2. **the root key** — the same name with store numbers, addresses, legal
       forms and generic category words removed. Same name, same parent;
    3. **a sub-brand guess** — the root extends an existing merchant's root on
       a token boundary. Only offered for businesses, never for the transfers
       that name people, and recorded as a suggestion so one click undoes it.
    4. **the advisor**, when one is configured — plan B, asked only once every
       rule above has missed. It knows things a normalizer cannot: that
       `BANCOLOMBIA NEQUI` and `NEQUI` are one company, that a name is a
       person rather than a shop. It also proposes a category.

    Anything else becomes a merchant of its own. That is the deliberate bias:
    a wrong grouping mislabels money and hides, a missing one costs a
    correction that is then permanent, because a user's decision is stored on
    the alias and no rule ever re-derives it. Nothing the advisor says escapes
    that bias either — its groupings arrive as `SUGGESTED` and its categories
    as proposals, so the merchant stays in the review queue.
    """

    def __init__(
        self,
        *,
        repository: MerchantRepository,
        processed_events: ProcessedEventStore,
        event_publisher: EventPublisher,
        advisor: MerchantAdvisor | None = None,
    ) -> None:
        self._repository = repository
        self._processed_events = processed_events
        self._event_publisher = event_publisher
        self._advisor = advisor

    def execute(self, command: RecordSightingCommand) -> ResolveMerchantResult:
        if not self._processed_events.claim(
            user_id=command.user_id,
            event_id=command.event_id,
        ):
            return ResolveMerchantResult(resolution=Resolution.DUPLICATE)

        fingerprint = AliasFingerprint.from_raw(command.counterparty)
        known = self._repository.find_by_alias(
            user_id=command.user_id,
            fingerprint=fingerprint,
        )

        if known is not None:
            known.record_sighting(
                fingerprint=fingerprint,
                raw_text=command.counterparty,
                seen_at=command.occurred_at,
            )

            return self._finish(known, Resolution.KNOWN)

        return self._link_new_spelling(command, fingerprint)

    def _link_new_spelling(
        self,
        command: RecordSightingCommand,
        fingerprint: AliasFingerprint,
    ) -> ResolveMerchantResult:
        root_key = MerchantRootKey.from_fingerprint(fingerprint)
        roots = self._repository.list_root_keys(command.user_id)
        parent = self._parent_for(command, root_key=root_key, roots=roots)

        if parent is not None:
            merchant, origin = parent
            merchant.link_alias(
                fingerprint=fingerprint,
                raw_text=command.counterparty,
                origin=origin,
                seen_at=command.occurred_at,
            )

            return self._finish(
                merchant,
                Resolution.DERIVED
                if origin is AliasOrigin.DERIVED
                else Resolution.SUGGESTED,
            )

        return self._ask_advisor(command, fingerprint)

    def _ask_advisor(
        self,
        command: RecordSightingCommand,
        fingerprint: AliasFingerprint,
    ) -> ResolveMerchantResult:
        """Last resort: a merchant of its own, unless the model knows better.

        Reads the user's merchants only here, on the rare path where a
        spelling is genuinely new — the common case never gets this far.
        """
        advice = (
            self._advisor.advise(
                counterparty=command.counterparty,
                kind=command.kind,
                candidates=self._candidates(command.user_id),
            )
            if self._advisor is not None
            else None
        )

        if advice is not None and advice.parent is not None:
            parent = self._repository.find(
                user_id=command.user_id,
                merchant_id=advice.parent,
            )

            if parent is not None:
                parent.link_alias(
                    fingerprint=fingerprint,
                    raw_text=command.counterparty,
                    origin=AliasOrigin.SUGGESTED,
                    seen_at=command.occurred_at,
                )

                return self._finish(parent, Resolution.ADVISED)

        created = Merchant.seed(
            user_id=command.user_id,
            fingerprint=fingerprint,
            raw_text=command.counterparty,
            seen_at=command.occurred_at,
        )

        if advice is not None:
            created.propose_category(advice.category)

        return self._finish(created, Resolution.CREATED)

    def _candidates(self, user_id: UserId) -> list[MerchantCandidate]:
        """The merchants worth offering, busiest first.

        Capped because the list travels into a prompt: a user with hundreds of
        merchants would otherwise pay for all of them on every new spelling,
        and the ones they actually shop at are the ones a new spelling is
        likely to belong to.
        """
        owned = sorted(
            self._repository.list_by_user(user_id),
            key=lambda merchant: merchant.times_seen,
            reverse=True,
        )

        return [
            MerchantCandidate(
                merchant_id=merchant.id,
                display_name=merchant.display_name,
                aliases=tuple(alias.fingerprint.value for alias in merchant.children),
            )
            for merchant in owned[:MAX_ADVISOR_CANDIDATES]
        ]

    def _parent_for(
        self,
        command: RecordSightingCommand,
        *,
        root_key: MerchantRootKey,
        roots: Mapping[MerchantRootKey, MerchantId],
    ) -> tuple[Merchant, AliasOrigin] | None:
        exact = self._load(command.user_id, roots.get(root_key))

        if exact is not None:
            return exact, AliasOrigin.DERIVED

        if command.kind is not CounterpartyKind.BUSINESS:
            # A transfer names a person. `JUAN VALDEZ` must never adopt
            # `JUAN PEREZ`, so the guessing tier is not offered here at all.
            return None

        # The longest match wins: `EXITO EXPRESS` is a closer parent for
        # `EXITO EXPRESS CALLE 80` than `EXITO` is.
        candidates = sorted(
            (
                candidate
                for candidate in roots
                if is_sub_brand_of(candidate=root_key.value, parent=candidate.value)
            ),
            key=lambda candidate: len(candidate.value),
            reverse=True,
        )

        for candidate in candidates:
            merchant = self._load(command.user_id, roots[candidate])

            if merchant is not None:
                return merchant, AliasOrigin.SUGGESTED

        return None

    def _load(self, user_id: UserId, merchant_id: MerchantId | None) -> Merchant | None:
        """Resolve a grouping key to its merchant, tolerating a dangling one.

        A merchant is written before the keys that point at it, so a crash in
        between can leave a key aimed at nothing. Treating that as "no match"
        creates a duplicate the user can merge; trusting it would raise on
        every future sighting of that name.
        """
        if merchant_id is None:
            return None

        return self._repository.find(user_id=user_id, merchant_id=merchant_id)

    def _finish(
        self,
        merchant: Merchant,
        resolution: Resolution,
    ) -> ResolveMerchantResult:
        # Persist before publishing, so no event announces a state that was
        # never stored.
        self._repository.save(merchant)
        self._event_publisher.publish(merchant.pull_events())

        return ResolveMerchantResult(resolution=resolution, merchant=merchant)


class _MerchantCommandUseCase:
    """Shared plumbing for the commands a user issues from the app."""

    def __init__(
        self,
        *,
        repository: MerchantRepository,
        event_publisher: EventPublisher,
    ) -> None:
        self._repository = repository
        self._event_publisher = event_publisher

    def _require(self, *, user_id: UserId, merchant_id: MerchantId) -> Merchant:
        merchant = self._repository.find(user_id=user_id, merchant_id=merchant_id)

        if merchant is None:
            # Same answer whether it never existed or belongs to somebody
            # else: one user must not be able to probe another's merchants.
            raise MerchantNotFoundError(f"No merchant {merchant_id.value}")

        return merchant

    def _save(self, *merchants: Merchant) -> None:
        for merchant in merchants:
            self._repository.save(merchant)

        for merchant in merchants:
            self._event_publisher.publish(merchant.pull_events())


class EditMerchantUseCase(_MerchantCommandUseCase):
    """Renames a merchant, recategorizes it, or both."""

    def execute(self, command: EditMerchantCommand) -> Merchant:
        merchant = self._require(
            user_id=command.user_id,
            merchant_id=command.merchant_id,
        )

        if command.display_name is not None:
            merchant.rename(command.display_name)

        if command.category is not None:
            merchant.recategorize(command.category)

        self._save(merchant)

        return merchant


class ConfirmMerchantUseCase(_MerchantCommandUseCase):
    """Accepts a merchant as it stands, guessed children included."""

    def execute(self, command: ConfirmMerchantCommand) -> Merchant:
        merchant = self._require(
            user_id=command.user_id,
            merchant_id=command.merchant_id,
        )
        merchant.confirm()
        self._save(merchant)

        return merchant


class MoveAliasUseCase(_MerchantCommandUseCase):
    """Reattaches one spelling to a different merchant.

    This is the correction the whole strategy leans on: the moved alias is
    marked as a decision, so every later sighting of that spelling resolves
    here on the exact-match tier and no rule ever second-guesses it.
    """

    def execute(self, command: MoveAliasCommand) -> Merchant:
        if command.merchant_id == command.target_merchant_id:
            raise SameMerchantError("An alias cannot be moved onto its own merchant")

        source = self._require(
            user_id=command.user_id,
            merchant_id=command.merchant_id,
        )
        target = self._require(
            user_id=command.user_id,
            merchant_id=command.target_merchant_id,
        )
        target.adopt_alias(source.detach_alias(command.fingerprint))
        self._save(source, target)

        return target


class SplitAliasUseCase(_MerchantCommandUseCase):
    """Pulls one spelling out into a merchant of its own.

    The way out of a wrong grouping when there is nowhere to move the child
    to yet.
    """

    def execute(self, command: SplitAliasCommand) -> Merchant:
        source = self._require(
            user_id=command.user_id,
            merchant_id=command.merchant_id,
        )
        alias = source.detach_alias(command.fingerprint)
        created = Merchant.seed(
            user_id=command.user_id,
            fingerprint=alias.fingerprint,
            raw_text=alias.raw_text,
            seen_at=alias.first_seen,
            display_name=command.display_name,
            category=command.category or MerchantCategory.UNCATEGORIZED,
            origin=AliasOrigin.MANUAL,
        )
        # The child keeps the history it earned under its old parent.
        created.aliases[alias.fingerprint] = alias.claimed_by_user()
        self._save(source, created)

        return created


class MergeMerchantsUseCase(_MerchantCommandUseCase):
    """Folds one merchant into another, which then owns every spelling.

    The survivor becomes reachable by the absorbed merchant's grouping keys
    too, so the variants that produced the duplicate stop producing it.
    """

    def execute(self, command: MergeMerchantsCommand) -> Merchant:
        if command.merchant_id == command.absorbed_merchant_id:
            raise SameMerchantError("A merchant cannot be merged into itself")

        survivor = self._require(
            user_id=command.user_id,
            merchant_id=command.merchant_id,
        )
        absorbed = self._require(
            user_id=command.user_id,
            merchant_id=command.absorbed_merchant_id,
        )
        survivor.absorb(absorbed)
        self._save(survivor)
        # Deleted last: until the survivor holds the children, dropping the
        # record would lose them.
        self._repository.delete(
            user_id=command.user_id,
            merchant_id=absorbed.id,
        )

        return survivor
