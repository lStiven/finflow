"""The grouping strategy, stated as tests.

The asymmetry these pin down is deliberate: folding two different businesses
into one mislabels money and is invisible afterwards, while failing to fold
two spellings of the same business costs one correction that is remembered
forever. So the rules here are conservative on purpose.
"""

import pytest

from personal_finance.contexts.merchant.domain.normalization import (
    derive_root_key,
    is_sub_brand_of,
    normalize_counterparty,
    suggest_display_name,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Tiendas Ara", "TIENDAS ARA"),
        ("  tiendas   ara  ", "TIENDAS ARA"),
        ("Almacén Éxito", "ALMACEN EXITO"),
        ("D1 S.A.S.", "D1 SAS"),
        ("RAPPI*RESTAURANTE", "RAPPI RESTAURANTE"),
    ],
)
def test_the_fingerprint_folds_away_case_accents_and_punctuation(
    raw: str,
    expected: str,
) -> None:
    assert normalize_counterparty(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("MERCADOPAGO*SPOTIFY", "SPOTIFY"),
        ("PAYU*NETFLIX", "NETFLIX"),
        ("SQ *CAFE DE LA ESQUINA", "CAFE DE LA ESQUINA"),
    ],
)
def test_a_known_payment_processor_is_not_the_merchant(
    raw: str,
    expected: str,
) -> None:
    assert normalize_counterparty(raw) == expected


def test_an_unknown_processor_stays_part_of_the_name() -> None:
    # Better a merchant with an odd name than two businesses silently merged
    # because something that was not a processor was treated as one.
    assert normalize_counterparty("ACME PAY*SOMETHING") == "ACME PAY SOMETHING"


@pytest.mark.parametrize(
    ("fingerprint", "expected"),
    [
        # A store number is not a business.
        ("TIENDAS ARA 123", "ARA"),
        # Neither is the address a terminal appends.
        ("ARA CALLE 80", "ARA"),
        ("D1 CRA 15 NO 93", "D1"),
        # Nor a legal form, a branch marker or a generic category word.
        ("PANADERIA LA ESPIGA SAS", "PANADERIA ESPIGA"),
        ("DROGUERIAS LA REBAJA SUCURSAL 12", "REBAJA"),
    ],
)
def test_the_root_key_drops_what_varies_between_branches(
    fingerprint: str,
    expected: str,
) -> None:
    assert derive_root_key(fingerprint) == expected


def test_two_spellings_of_one_business_share_a_root() -> None:
    assert derive_root_key("TIENDAS ARA 123") == derive_root_key("ARA CALLE 80")


def test_the_root_key_keeps_every_token_that_names_the_business() -> None:
    # `JUAN VALDEZ` and `JUAN PEREZ` must never collapse: transfers name
    # people, and grouping people by a shared first name would attribute one
    # person's money to another.
    assert derive_root_key("JUAN VALDEZ CAFE") != derive_root_key("JUAN PEREZ")


def test_a_counterparty_that_is_only_a_reference_keeps_its_own_root() -> None:
    # Nothing significant survives, and one shared empty parent for every such
    # record would be worse than leaving them apart.
    assert derive_root_key("CRA 15 NO 93") == "CRA 15 NO 93"


def test_a_branded_variant_is_recognised_as_a_sub_brand() -> None:
    assert is_sub_brand_of(candidate="EXITO EXPRESS", parent="EXITO")


@pytest.mark.parametrize(
    ("candidate", "parent"),
    [
        # The match has to fall on a token boundary.
        ("EXITOSO", "EXITO"),
        # A three-letter parent is a prefix of half the country.
        ("ARA MOTORS", "ARA"),
        # Nothing adopts itself.
        ("EXITO", "EXITO"),
    ],
)
def test_what_is_not_a_sub_brand(candidate: str, parent: str) -> None:
    assert not is_sub_brand_of(candidate=candidate, parent=parent)


def test_a_suggested_name_is_readable() -> None:
    assert suggest_display_name("TIENDAS ARA") == "Tiendas Ara"
