"""Recognising Google's "confirm this forwarding request" mail.

Setting up automatic forwarding is the one step of connecting a bank that a
user cannot finish alone: Gmail mails a confirmation link to the *destination*,
and the destination is an alias on the one mailbox this deployment owns. Only
the operator can read it, so without this every new user waits on somebody
fishing their link out by hand.

This message is not a bank notification and must never be treated as one. It
carries no transaction, it is not from a sender anybody approved, and the
approved-sender filter would discard its body — the link with it. It is
ingestion's own control-plane mail, recognised before that filter and routed
somewhere else entirely.

**The URL is untrusted input, and this module is what makes following it
safe.** Everything about the link is pinned rather than parsed loosely: the
scheme, the exact host, and the path prefix that distinguishes *confirm* from
*cancel*. A link this does not recognise yields `None`, and nothing is
fetched.

Worth being explicit about what confirming does and does not grant. It does
not widen who can reach a user's alias: anybody holding that address can
already mail it directly, and the approved-sender filter is what stands
between a message and the ledger either way. Forwarding is a delivery route,
not a permission.
"""

from __future__ import annotations

import dataclasses
import re
from typing import Self

from personal_finance.contexts.ingestion.domain.parsing.text import (
    decode_quoted_printable,
    looks_quoted_printable,
)
from personal_finance.contexts.ingestion.domain.value_objects import EmailAddress
from personal_finance.shared.domain.value_objects import ValueObject


# The exact address, not a domain: `google.com` also sends everything else
# Google has ever mailed anybody, and this is the one that means this.
GOOGLE_FORWARDING_SENDER = EmailAddress("forwarding-noreply@google.com")

# Google sends the link on either host — both observed in real mail, days
# apart, for the same request. Accepting only one dropped the other in silence:
# not a confirmation, and not from an approved sender either.
CONFIRMATION_HOSTS = frozenset({"mail-settings.google.com", "mail.google.com"})

# `vf-` is confirm and `uf-` is cancel — the same mail carries both, one
# sentence apart. Matching the prefix loosely would make the feature undo
# itself, silently, on every message it handled.
_CONFIRMATION_URL = re.compile(
    r"https://mail(?:-settings)?\.google\.com/mail/vf-[A-Za-z0-9%_.\-]+",
)


@dataclasses.dataclass(frozen=True, slots=True)
class ForwardingConfirmation(ValueObject):
    """A link that finishes one user's forwarding setup."""

    url: str

    def __post_init__(self) -> None:
        # Re-checked here and not only at the regex above: this value object
        # is what an adapter is handed, and it is the last place that can
        # refuse before something makes a network call with it.
        if not _CONFIRMATION_URL.fullmatch(self.url):
            raise ValueError("Not a Gmail forwarding confirmation URL")

    @classmethod
    def from_email(
        cls,
        *,
        sender: EmailAddress,
        raw_content: str,
    ) -> Self | None:
        """The confirmation this mail carries, or `None` if it is not one.

        `None` rather than an exception: every message on the mailbox is
        offered to this, and almost none of them are confirmations.
        """
        if sender != GOOGLE_FORWARDING_SENDER:
            return None

        # The link is long enough that quoted-printable always splits it
        # across lines. Decoding first is what makes it one string again.
        body = (
            decode_quoted_printable(raw_content)
            if looks_quoted_printable(raw_content)
            else raw_content
        )
        match = _CONFIRMATION_URL.search(body)

        if match is None:
            return None

        return cls(url=match.group())
