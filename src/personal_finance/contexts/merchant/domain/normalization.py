"""Turning what a bank wrote into something we can group by.

A bank alert names the other side of a purchase in whatever form the acquirer
sent it: `TIENDAS ARA 123`, `ara calle 80`, `MERCADOPAGO*SPOTIFY`,
`EXITO EXPRESS BOGOTA`. Two derived keys make that tractable, and they answer
two different questions:

* the **fingerprint** answers "have I seen exactly this text before?". It is
  the identity of a child alias, so it is conservative: it only folds away
  case, accents and punctuation, and never merges two strings a human would
  keep apart.
* the **root key** answers "which parent does this belong to?". It drops the
  parts that vary between branches of the same business — store numbers,
  address tails, legal forms, generic category words — and keeps every token
  that actually names the business.

The root key deliberately does *not* try to be clever. Merging two merchants
that are not the same business mislabels someone's money and is invisible
once it happens; failing to merge them costs one click that is remembered
forever. So everything beyond "the same name modulo noise" is offered as a
suggestion, never applied silently.
"""

from __future__ import annotations

import re
import unicodedata


# Payment aggregators that prepend themselves to the real merchant name, as in
# `MERCADOPAGO*SPOTIFY`. What we want is on the right of the separator: the
# processor says how the user paid, not who they paid.
PAYMENT_PROCESSORS = frozenset(
    {
        "DLOCAL",
        "EPAYCO",
        "MERCADOPAGO",
        "MERCPAGO",
        "PAYPAL",
        "PAYU",
        "PAYULATAM",
        "PP",
        "PSE",
        "SP",
        "SQ",
        "STRIPE",
        "TPAGA",
        "WOMPI",
    },
)

# Tokens that say nothing about *which* business this is. Four families:
# legal forms, the address tail a POS terminal appends, branch markers, and
# the generic category word many acquirers put in front of the real name
# (`DROGUERIA LA REBAJA` and `LA REBAJA` are one business).
NOISE_TOKENS = frozenset(
    {
        # Legal forms.
        "SA",
        "SAS",
        "LTDA",
        "EU",
        "CIA",
        "INC",
        "LLC",
        "CORP",
        "SL",
        # Address tails.
        "AV",
        "AVENIDA",
        "CALLE",
        "CL",
        "CARRERA",
        "CRA",
        "KR",
        "DIAG",
        "DIAGONAL",
        "TRANSV",
        "TRANSVERSAL",
        "KM",
        "PISO",
        # Branch markers.
        "SUCURSAL",
        "SUC",
        "LOCAL",
        "CC",
        "PDV",
        "POS",
        "NO",
        "NRO",
        # Generic category words.
        "TIENDA",
        "TIENDAS",
        "ALMACEN",
        "ALMACENES",
        "SUPERMERCADO",
        "SUPERMERCADOS",
        "DROGUERIA",
        "DROGUERIAS",
        "RESTAURANTE",
        "PAGO",
        "PAGOS",
        "COMPRA",
        # Stopwords.
        "DE",
        "DEL",
        "EL",
        "LA",
        "LAS",
        "LOS",
        "Y",
    },
)

# The shortest root that may act as a parent in a sub-brand suggestion. Two or
# three characters (`MC`, `ARA`) are far too generic to adopt children.
MINIMUM_PARENT_LENGTH = 4

_PROCESSOR_SEPARATOR = "*"
_NON_ALPHANUMERIC = re.compile(r"[^0-9A-Z]+")
_NON_SLUG = re.compile(r"[^0-9a-z]+")
_WHITESPACE = re.compile(r"\s+")
# A dotted acronym is one word: `S.A.S.` must not become three tokens.
_DOTTED_ACRONYM = re.compile(r"\b(?:[0-9A-Z]\.){2,}")
# A store number, a branch number, an amount of change.
_NUMERIC = re.compile(r"^\d+$")
# A terminal id or an order reference. Length is what separates it from a
# name that happens to carry a digit: `D1` is a supermarket chain, `A1B2C3`
# is not a business.
_CODE = re.compile(r"^(?=.*\d)[0-9A-Z]{4,}$")


def _strip_accents(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value)

    return "".join(
        character for character in decomposed if not unicodedata.combining(character)
    )


def _strip_payment_processor(value: str) -> str:
    """Drop a known aggregator's prefix, keeping the merchant behind it.

    Only known processors are stripped. An unknown one simply becomes part of
    the name until somebody adds it here, which shows up as a merchant with a
    strange name rather than as two businesses silently merged.
    """
    if _PROCESSOR_SEPARATOR not in value:
        return value

    head, _, tail = value.partition(_PROCESSOR_SEPARATOR)
    processor = _NON_ALPHANUMERIC.sub("", head)

    if processor in PAYMENT_PROCESSORS and tail.strip():
        return tail

    return value


def normalize_counterparty(raw: str) -> str:
    """The alias fingerprint: same text, same key, whatever the bank's casing,
    accents or punctuation.
    """
    text = _strip_accents(raw).upper()
    text = _strip_payment_processor(text)
    text = _DOTTED_ACRONYM.sub(lambda match: match.group().replace(".", ""), text)
    text = _NON_ALPHANUMERIC.sub(" ", text)

    return _WHITESPACE.sub(" ", text).strip()


def derive_root_key(fingerprint: str) -> str:
    """The parent grouping key: the fingerprint with the volatile parts gone.

    Falls back to the fingerprint itself when nothing significant survives —
    a counterparty that is only a reference number is still an alias, and
    grouping every such record under one empty parent would be worse than
    leaving them apart.
    """
    significant = [
        token
        for token in fingerprint.split()
        if token not in NOISE_TOKENS
        and not _NUMERIC.match(token)
        and not _CODE.match(token)
    ]

    return " ".join(significant) if significant else fingerprint


def is_sub_brand_of(*, candidate: str, parent: str) -> bool:
    """Whether `candidate` looks like a branded variant of `parent`.

    `EXITO EXPRESS` extends `EXITO`; `EXITOSO` does not, because the match has
    to fall on a token boundary. Short parents are refused outright: `MC` is a
    prefix of half the restaurants in the country.

    This is the one grouping rule that guesses, so its result is never applied
    silently — the caller records it as a suggestion for the user to confirm.
    """
    if len(parent) < MINIMUM_PARENT_LENGTH or candidate == parent:
        return False

    return candidate.startswith(f"{parent} ")


def suggest_display_name(fingerprint: str) -> str:
    """A readable first name for a merchant nobody has named yet.

    `TIENDAS ARA` reads better as `Tiendas Ara` in a list, and the user can
    rename it to anything they like.
    """
    return " ".join(token.capitalize() for token in fingerprint.split())


def derive_slug(raw: str) -> str:
    """A stable key for a name a person typed, or "" when there is none.

    Deliberately not `normalize_counterparty`: that one is tuned for what a
    bank writes, and it would read the `PSE*` in a category called `Pse*algo`
    as a payment processor to strip. This only folds accents and punctuation,
    which is all a label needs before it becomes a key.
    """
    text = _NON_SLUG.sub(" ", _strip_accents(raw).casefold())

    return "-".join(text.split())
