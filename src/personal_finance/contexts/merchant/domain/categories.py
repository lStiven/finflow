"""A category one user wrote for themselves.

The app ships a vocabulary — `MerchantCategory` — that everybody shares,
because a first run with an empty dropdown would classify nothing. What lives
here is the other half: the categories a person adds because the shipped ones
do not describe how they actually spend. They belong to that person alone,
exactly like their merchants do.

The name is held to something short on purpose. It is read in a dropdown, in
a chip beside a movement, and in the legend of a donut on a phone, and a
category called "Cosas de la casa y del carro" makes all three unreadable —
by pushing the useful ones off the edge rather than by being wrong.

Name and identity are deliberately separate. The key is opaque and permanent;
the name is a label over it, and correcting a typo touches nothing but this
one record. `name_key` is the *current* name folded down — what uniqueness is
enforced on, so no two of somebody's categories ever read the same — and it
moves with the name rather than being the identity itself.
"""

from __future__ import annotations

import dataclasses
from typing import Self

from personal_finance.contexts.merchant.domain.exceptions import (
    InvalidCategoryLabelError,
)
from personal_finance.contexts.merchant.domain.normalization import derive_slug
from personal_finance.contexts.merchant.domain.value_objects import CategoryKey
from personal_finance.shared.domain.entities import Entity
from personal_finance.shared.domain.value_objects import PosixTime, UserId


MIN_CATEGORY_LABEL_LENGTH = 2
MAX_CATEGORY_LABEL_LENGTH = 24


@dataclasses.dataclass(eq=False, slots=True)
class Category(Entity[CategoryKey]):
    """A name the user chose, over a key that outlives every version of it."""

    user_id: UserId
    label: str
    created_at: PosixTime

    @classmethod
    def create(cls, *, user_id: UserId, label: str, created_at: PosixTime) -> Self:
        return cls(
            id=CategoryKey.new_custom(),
            user_id=user_id,
            label=valid_category_label(label),
            created_at=created_at,
        )

    @property
    def name_key(self) -> str:
        """This name folded down, which is what two categories may not share.

        Derived on demand rather than stored: it is a function of the name,
        and a second copy of it is a second thing that can fall out of step
        with the name after a rename.
        """
        return derive_slug(self.label)

    def rename(self, label: str) -> None:
        """Fix the name. Nothing filed under this category moves, because
        nothing was ever filed under the name.
        """
        self.label = valid_category_label(label)


def valid_category_label(value: str) -> str:
    """The name as it will be shown, or a refusal saying which rule it broke."""
    name = " ".join(value.split())

    if not derive_slug(name):
        # Punctuation and emoji alone: a name with nothing to fold down to,
        # so nothing that could tell it apart from the next one.
        raise InvalidCategoryLabelError(
            "A category name needs at least one letter or number",
        )

    if len(name) < MIN_CATEGORY_LABEL_LENGTH:
        raise InvalidCategoryLabelError(
            f"A category name needs at least {MIN_CATEGORY_LABEL_LENGTH} characters",
        )

    if len(name) > MAX_CATEGORY_LABEL_LENGTH:
        raise InvalidCategoryLabelError(
            f"A category name is at most {MAX_CATEGORY_LABEL_LENGTH} characters",
        )

    return name
