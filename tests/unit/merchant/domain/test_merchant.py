import pytest

from personal_finance.contexts.merchant.domain.entities import Merchant
from personal_finance.contexts.merchant.domain.exceptions import (
    LastAliasError,
    MerchantOwnershipError,
    UnknownAliasError,
)
from personal_finance.contexts.merchant.domain.value_objects import (
    AliasFingerprint,
    AliasOrigin,
    MerchantCategory,
    MerchantRootKey,
    MerchantStatus,
)
from personal_finance.shared.domain.value_objects import PosixTime, UserId


USER_ID = UserId.from_string("11111111-1111-1111-1111-111111111111")
OTHER_USER_ID = UserId.from_string("22222222-2222-2222-2222-222222222222")
NOW = PosixTime.from_epoch_seconds(1_700_000_000)
LATER = PosixTime.from_epoch_seconds(1_700_086_400)


def _merchant(raw: str = "TIENDAS ARA 123", *, user_id: UserId = USER_ID) -> Merchant:
    merchant = Merchant.seed(
        user_id=user_id,
        fingerprint=AliasFingerprint.from_raw(raw),
        raw_text=raw,
        seen_at=NOW,
    )
    merchant.pull_events()

    return merchant


def test_a_seeded_merchant_is_named_after_what_the_bank_wrote() -> None:
    merchant = Merchant.seed(
        user_id=USER_ID,
        fingerprint=AliasFingerprint.from_raw("TIENDAS ARA"),
        raw_text="Tiendas Ara",
        seen_at=NOW,
    )

    assert merchant.display_name == "Tiendas Ara"
    assert merchant.category is MerchantCategory.UNCATEGORIZED
    assert merchant.needs_review
    assert [type(event).__name__ for event in merchant.pull_events()] == [
        "MerchantIdentified",
        "MerchantAliasLinked",
    ]


def test_a_merchant_is_reachable_by_the_root_its_children_derive() -> None:
    merchant = _merchant("TIENDAS ARA 123")

    assert merchant.root_keys == frozenset({MerchantRootKey(value="ARA")})


def test_linking_a_second_spelling_widens_how_the_merchant_is_found() -> None:
    merchant = _merchant("TIENDAS ARA 123")
    merchant.link_alias(
        fingerprint=AliasFingerprint.from_raw("ARA EXPRESS"),
        raw_text="ARA EXPRESS",
        origin=AliasOrigin.SUGGESTED,
        seen_at=LATER,
    )

    assert merchant.root_keys == {
        MerchantRootKey(value="ARA"),
        MerchantRootKey(value="ARA EXPRESS"),
    }
    assert len(merchant.children) == 2


def test_a_guess_sends_a_reviewed_merchant_back_to_the_review_queue() -> None:
    merchant = _merchant()
    merchant.confirm()
    merchant.pull_events()

    merchant.link_alias(
        fingerprint=AliasFingerprint.from_raw("ARA EXPRESS"),
        raw_text="ARA EXPRESS",
        origin=AliasOrigin.SUGGESTED,
        seen_at=LATER,
    )

    # The user reviewed a merchant that did not have this child yet.
    assert merchant.needs_review


def test_a_derived_child_does_not_reopen_a_reviewed_merchant() -> None:
    merchant = _merchant()
    merchant.confirm()

    merchant.link_alias(
        fingerprint=AliasFingerprint.from_raw("ARA CALLE 80"),
        raw_text="ARA CALLE 80",
        origin=AliasOrigin.DERIVED,
        seen_at=LATER,
    )

    assert merchant.status is MerchantStatus.CONFIRMED


def test_seeing_a_spelling_again_counts_it_without_duplicating_it() -> None:
    merchant = _merchant()
    fingerprint = AliasFingerprint.from_raw("TIENDAS ARA 123")

    merchant.record_sighting(
        fingerprint=fingerprint,
        raw_text="TIENDAS ARA 123",
        seen_at=LATER,
    )

    assert len(merchant.children) == 1
    assert merchant.times_seen == 2
    assert merchant.last_seen == LATER
    assert merchant.first_seen == NOW


def test_a_sighting_that_arrives_out_of_order_does_not_move_last_seen_back() -> None:
    merchant = _merchant()
    merchant.record_sighting(
        fingerprint=AliasFingerprint.from_raw("TIENDAS ARA 123"),
        raw_text="TIENDAS ARA 123",
        seen_at=LATER,
    )

    earlier = PosixTime.from_epoch_seconds(1_600_000_000)
    merchant.record_sighting(
        fingerprint=AliasFingerprint.from_raw("TIENDAS ARA 123"),
        raw_text="TIENDAS ARA 123",
        seen_at=earlier,
    )

    assert merchant.last_seen == LATER
    assert merchant.first_seen == earlier


def test_recording_a_sighting_of_an_unknown_spelling_is_refused() -> None:
    merchant = _merchant()

    with pytest.raises(UnknownAliasError):
        merchant.record_sighting(
            fingerprint=AliasFingerprint.from_raw("SOMETHING ELSE"),
            raw_text="SOMETHING ELSE",
            seen_at=LATER,
        )


def test_renaming_counts_as_reviewing() -> None:
    merchant = _merchant()

    merchant.rename("Ara")

    assert merchant.display_name == "Ara"
    assert not merchant.needs_review


def test_a_merchant_needs_a_name() -> None:
    merchant = _merchant()

    with pytest.raises(ValueError, match="display name"):
        merchant.rename("   ")


def test_detaching_a_child_hands_over_its_history() -> None:
    merchant = _merchant()
    merchant.link_alias(
        fingerprint=AliasFingerprint.from_raw("ARA MOTORS"),
        raw_text="ARA MOTORS",
        origin=AliasOrigin.SUGGESTED,
        seen_at=LATER,
    )

    alias = merchant.detach_alias(AliasFingerprint.from_raw("ARA MOTORS"))

    assert alias.times_seen == 1
    assert len(merchant.children) == 1
    assert MerchantRootKey(value="ARA MOTORS") not in merchant.root_keys


def test_the_last_child_cannot_be_detached() -> None:
    # A merchant nothing points at would never resolve again and would sit in
    # the user's list forever.
    merchant = _merchant()

    with pytest.raises(LastAliasError):
        merchant.detach_alias(AliasFingerprint.from_raw("TIENDAS ARA 123"))


def test_an_adopted_child_is_marked_as_a_decision() -> None:
    source = _merchant("ARA MOTORS")
    target = _merchant("TIENDAS ARA 123")
    source.link_alias(
        fingerprint=AliasFingerprint.from_raw("ARA MOTORS SUR"),
        raw_text="ARA MOTORS SUR",
        origin=AliasOrigin.DERIVED,
        seen_at=LATER,
    )

    target.adopt_alias(source.detach_alias(AliasFingerprint.from_raw("ARA MOTORS SUR")))

    moved = target.aliases[AliasFingerprint.from_raw("ARA MOTORS SUR")]
    assert moved.origin is AliasOrigin.MANUAL
    assert not target.needs_review


def test_absorbing_a_merchant_takes_every_child_with_it() -> None:
    survivor = _merchant("TIENDAS ARA 123")
    absorbed = _merchant("ARA EXPRESS")

    survivor.absorb(absorbed)

    assert len(survivor.children) == 2
    # Future sightings of the absorbed spelling now land on the survivor.
    assert MerchantRootKey(value="ARA EXPRESS") in survivor.root_keys
    assert [type(event).__name__ for event in survivor.pull_events()] == [
        "MerchantsMerged",
    ]


def test_a_merchant_cannot_absorb_another_users_merchant() -> None:
    survivor = _merchant(user_id=USER_ID)
    theirs = _merchant("ARA EXPRESS", user_id=OTHER_USER_ID)

    with pytest.raises(MerchantOwnershipError):
        survivor.absorb(theirs)


def test_merging_two_records_of_one_spelling_adds_their_counters_up() -> None:
    survivor = _merchant("TIENDAS ARA")
    duplicate = _merchant("TIENDAS ARA")
    duplicate.record_sighting(
        fingerprint=AliasFingerprint.from_raw("TIENDAS ARA"),
        raw_text="TIENDAS ARA",
        seen_at=LATER,
    )

    survivor.absorb(duplicate)

    assert survivor.times_seen == 3
