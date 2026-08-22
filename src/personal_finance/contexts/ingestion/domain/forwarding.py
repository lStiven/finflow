"""Deriving a user's forwarding address from one shared ingest mailbox.

One Gmail account receives mail for every user, distinguished by a `+alias`
local-part — the same trick behind `you+something@gmail.com` landing in
`you@gmail.com`. Deriving it from `user_id` rather than storing a separately
issued token means there is nothing to allocate, nothing that can collide, and
nothing to look up just to answer "what's my address": it is a pure function
of an id every user already has.
"""

from __future__ import annotations

from personal_finance.contexts.ingestion.domain.value_objects import EmailAddress
from personal_finance.shared.domain.value_objects import UserId


def forwarding_address(*, base: EmailAddress, user_id: UserId) -> EmailAddress:
    """The address this user forwards bank email to.

    `base` is the ingest mailbox itself (e.g. `finflowingest@gmail.com`); the
    result plugs the user's id in as a `+` alias
    (`finflowingest+<user id>@gmail.com`). The alias carries the full id
    rather than a shortened one: it is never meant to be typed by hand, only
    copied into a forwarding rule, and a full UUID costs nothing extra there
    while staying unguessable.
    """
    local_part, domain = base.value.split("@", 1)
    alias = str(user_id.value).replace("-", "")

    return EmailAddress(f"{local_part}+{alias}@{domain}")
