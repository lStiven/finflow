"""Publishing a vocabulary a client has to choose from.

Several endpoints accept an enum where a free string would be accepted by the
type system and rejected by the API. A client cannot guess those members, and
hardcoding them on the other side means two lists that drift apart in
opposite directions — the failure being a 422 nobody sees until a user hits
it. So each context publishes its own, from the same enum the endpoint
validates against, and there is nothing left to keep in step.

Presentation only: no context's rules live here, and the label is a display
convenience, not a translation. A client showing anything but English builds
its own labels from `value`, which is the part that is stable.
"""

from __future__ import annotations

from collections.abc import Iterable
import enum

from pydantic import BaseModel


class CatalogOption(BaseModel):
    """One admissible value, and something to put beside it in a dropdown."""

    value: str
    label: str


def label(value: str) -> str:
    """A display string for one member's value.

    An all-caps value is a code, not a word — `COP` is the currency, `Cop` is
    a typo — so only lower_snake_case members get title-cased.
    """
    if value.isupper():
        return value

    return value.replace("_", " ").capitalize()


def options(members: Iterable[enum.Enum]) -> list[CatalogOption]:
    """Every member of an enum, in declaration order.

    Order is the enum's own, deliberately: it is the one place the order of a
    dropdown can be decided, and it survives a client that renders the list
    as it arrives.
    """
    return [
        CatalogOption(value=str(member.value), label=label(str(member.value)))
        for member in members
    ]
