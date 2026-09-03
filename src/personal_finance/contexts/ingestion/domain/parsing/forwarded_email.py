from __future__ import annotations

import re

from personal_finance.contexts.ingestion.domain.parsing.text import (
    decode_quoted_printable,
    looks_quoted_printable,
)
from personal_finance.contexts.ingestion.domain.value_objects import EmailAddress


# Gmail writes the marker in English whatever the interface language, but the
# field beside it follows the user's locale — the same message carries
# "Forwarded message" and "De:". Other clients translate both, so both halves
# accept either language.
_MARKER = r"(?:forwarded\s+message|mensaje\s+reenviado)"

# No colon and no quotes, so an address written as markup —
# `href="mailto:alertas@banco.com"` — yields the address and not the whole
# attribute. Real addresses contain none of these.
_ADDRESS = r"[^\s<>@,;:\"'()\[\]]+@[^\s<>@,;:\"'()\[\]]+"

# Deliberately anchored to the marker instead of scanning the body for the
# first address that looks like a bank's.
#
# A Bancolombia alert closes by asking the reader to report anything
# suspicious to `correosospechoso@bancolombia.com.co`, so a body scan would
# read that footer as the sender and attribute *any* email quoting it to
# Bancolombia. The origin of a forward is stated in exactly one place, right
# under the marker, and that is the only place this looks.
#
# The gap after the field name absorbs a display name (`De: Bancolombia
# <alertas@…>`) and the markup an HTML forward puts between the two, without
# reaching the next quoted message. Gmail's HTML spends about a hundred
# characters there on its own — `<strong class="gmail_sendername" …>` and the
# `mailto:` anchor — before any name the bank chose to use.
#
# The field name is guarded by a lookbehind rather than by whitespace: an HTML
# forward writes `<br>De:`, with markup and no space in front of it. The
# lookbehind still refuses a match inside a word, which is what the guard is
# for — "grande:" must not read as "de:".
_FORWARDED_SENDER = re.compile(
    _MARKER + r".{0,200}?(?<![A-Za-z])(?:de|from)\s*:.{0,200}?(" + _ADDRESS + r")",
    re.IGNORECASE | re.DOTALL,
)


def forwarded_sender(raw: str) -> EmailAddress | None:
    """Who sent the message that was forwarded, when this body is a forward.

    A bank is identified by the domain the alert came from, and a hand-forward
    replaces that with the address of whoever pressed forward: the bank then
    survives only inside the body, in the header block the client writes. This
    reads that block, and returns None for anything that is not a forward —
    which leaves the caller exactly where it was.

    Takes the **raw** body rather than the text the parsers read. `extract_text`
    treats `<alertas@banco.com>` as a tag and drops it, so by the time an alert
    reaches a template the forwarded header says only "De:" with nothing after
    it. Transport encoding is still decoded here, because a quoted-printable
    body hides the header the same way.

    Never a substitute for the envelope sender. The address here is content,
    so it may not decide whether an email is read at all; approval still
    answers that, upstream and against the real sender. All this decides is
    which bank's templates are worth trying on text already accepted.
    """
    body = decode_quoted_printable(raw) if looks_quoted_printable(raw) else raw
    match = _FORWARDED_SENDER.search(body)

    if match is None:
        return None

    try:
        return EmailAddress(value=match.group(1))
    except ValueError:
        # A malformed address in a forwarded header is not a reason to reject
        # the email: it only means the bank cannot be named this way.
        return None
