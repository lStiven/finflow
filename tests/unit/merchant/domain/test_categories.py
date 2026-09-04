"""The rules a category a user wrote has to obey.

The name is theirs to choose; the key it is stored under is not, and the two
must not be able to drift apart.
"""

import pytest

from personal_finance.contexts.merchant.domain.categories import (
    MAX_CATEGORY_LABEL_LENGTH,
    Category,
)
from personal_finance.contexts.merchant.domain.exceptions import (
    InvalidCategoryLabelError,
)
from personal_finance.contexts.merchant.domain.value_objects import (
    CategoryKey,
    MerchantCategory,
)
from personal_finance.shared.domain.value_objects import PosixTime, UserId


USER_ID = UserId.from_string("11111111-1111-1111-1111-111111111111")
NOW = PosixTime.from_epoch_seconds(1_700_000_000)


def _create(label: str) -> Category:
    return Category.create(user_id=USER_ID, label=label, created_at=NOW)


def test_a_category_is_keyed_by_something_its_name_cannot_change() -> None:
    category = _create("Cuidado personal")

    # The name is kept exactly as it was written; the key says nothing about
    # it, which is what makes fixing a typo cost one write.
    assert category.label == "Cuidado personal"
    assert category.id.is_custom
    assert "cuidado" not in category.id.value


def test_two_categories_are_never_the_same_thing() -> None:
    # Even under the same name, which storage refuses separately: identity is
    # not something a name gets to decide.
    assert _create("Mascotas").id != _create("Mascotas").id


def test_renaming_leaves_the_key_alone_and_moves_the_name() -> None:
    """The whole point. Everything filed under a category names the key, so a
    key that followed the name would make every correction a rewrite.
    """
    category = _create("Mascotss")
    key = category.id

    category.rename("Mascotas")

    assert category.id == key
    assert category.label == "Mascotas"
    assert category.name_key == "mascotas"


def test_a_rename_obeys_the_same_rules_the_first_name_did() -> None:
    category = _create("Mascotas")

    with pytest.raises(InvalidCategoryLabelError):
        category.rename("x" * (MAX_CATEGORY_LABEL_LENGTH + 1))

    with pytest.raises(InvalidCategoryLabelError):
        category.rename("!!!")

    # And the refused name never landed.
    assert category.label == "Mascotas"


def test_accents_and_case_fold_to_one_name() -> None:
    # What uniqueness is enforced on, so "Educación" and "EDUCACION" cannot
    # both sit in one dropdown.
    assert _create("Educación").name_key == _create("EDUCACION").name_key


def test_the_name_is_tidied_before_anything_is_derived_from_it() -> None:
    category = _create("  Gastos   del   carro ")

    assert category.label == "Gastos del carro"
    assert category.name_key == "gastos-del-carro"


def test_a_users_category_can_never_collide_with_one_the_app_ships() -> None:
    # A merchant stores only the key. Without the prefix, a default added
    # later under a value somebody had already taken would silently relabel
    # their spending.
    shipped = {CategoryKey.default(category) for category in MerchantCategory}

    assert _create("Fuel").id not in shipped
    assert _create("Other").id not in shipped


def test_a_name_too_short_to_read_is_refused() -> None:
    with pytest.raises(InvalidCategoryLabelError):
        _create("a")


def test_a_name_too_long_for_a_chip_is_refused() -> None:
    # It is read in a dropdown, in a chip beside a movement and in a chart
    # legend on a phone. A sentence makes all three unreadable.
    with pytest.raises(InvalidCategoryLabelError):
        _create("x" * (MAX_CATEGORY_LABEL_LENGTH + 1))


def test_a_name_with_nothing_to_key_it_by_is_refused() -> None:
    with pytest.raises(InvalidCategoryLabelError):
        _create("!!!")


def test_a_name_of_exactly_the_maximum_length_is_accepted() -> None:
    label = "x" * MAX_CATEGORY_LABEL_LENGTH

    assert _create(label).label == label
