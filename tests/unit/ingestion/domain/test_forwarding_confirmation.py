"""The real message Google sends, and the ways it must not be trusted."""

import pytest

from personal_finance.contexts.ingestion.domain.forwarding_confirmation import (
    GOOGLE_FORWARDING_SENDER,
    ForwardingConfirmation,
)
from personal_finance.contexts.ingestion.domain.value_objects import EmailAddress


# Verbatim from a real request, quoted-printable and all: the confirm link is
# split across three lines by soft breaks, which is exactly what has to be
# undone before any of it can be recognised.
REAL_EMAIL = """Content-Type: text/plain; charset="UTF-8"
Content-Transfer-Encoding: quoted-printable

stiven.ddh@gmail.com solicit=C3=B3 reenviar autom=C3=A1ticamente el correo
electr=C3=B3nico a tu direcci=C3=B3n
finflowingest+d9209500787f4cedaf7612db4cffdc4a@gmail.com.

Para permitir que stiven.ddh@gmail.com reenv=C3=ADe el correo electr=C3=B3n=
ico
autom=C3=A1ticamente a tu direcci=C3=B3n,
haz clic en el siguiente v=C3=ADnculo para confirmar la solicitud:

https://mail-settings.google.com/mail/vf-%5BANGjdJ-p2S7wOXTmSnAXuL2jfMz9cuJ=
dQhBnNC26ypPp3CFHLWigF7CIqSX-JO5DJmdSZZ_SFtawcOwtZzhIUJhCb9qZzadMNKe7vPV6r1=
rvtonsylM2Dw5GcCgLe_7OwuqE_5kQ3wxZbp-WWIfL%5D-VLPaGPr9QJdZzMiCKQCIsAXTjZw

Si no apruebas esta solicitud, no es necesario que realices ninguna otra ac=
ci=C3=B3n.
haz clic en este v=C3=ADnculo para cancelar la
verificaci=C3=B3n:
https://mail-settings.google.com/mail/uf-%5BANGjdJ_pj416lgSTalUILGKJyoR2qm5=
GeiQ58kOkNjHKaUCfSQokPOK2uD2P-OVmZ_mkIWBs7r4H7Nql__2R4MBHbaAD4MHdYDzolI7FmP=
P_gPozCXa3rC1M7TlkKnlXGzYL7kzAdaYBykyCHl8k%5D-VLPaGPr9QJdZzMiCKQCIsAXTjZw
"""


def _from(
    raw: str,
    sender: EmailAddress = GOOGLE_FORWARDING_SENDER,
) -> ForwardingConfirmation | None:
    return ForwardingConfirmation.from_email(sender=sender, raw_content=raw)


def test_the_link_survives_the_soft_breaks_that_split_it() -> None:
    confirmation = _from(REAL_EMAIL)

    assert confirmation is not None
    assert confirmation.url.startswith(
        "https://mail-settings.google.com/mail/vf-%5BANGjdJ-p2S7wOXTmSnAXuL2jfMz9cuJ",
    )
    # Rejoined, not truncated at the first soft break.
    assert confirmation.url.endswith("VLPaGPr9QJdZzMiCKQCIsAXTjZw")


def test_the_cancel_link_in_the_same_mail_is_never_the_one_taken() -> None:
    """Both links sit a sentence apart. Taking the wrong one would undo the
    very thing this exists to do, and report success doing it.
    """
    confirmation = _from(REAL_EMAIL)

    assert confirmation is not None
    assert "/mail/vf-" in confirmation.url
    assert "/mail/uf-" not in confirmation.url


def test_only_googles_own_address_is_read_as_a_confirmation() -> None:
    assert _from(REAL_EMAIL, EmailAddress("attacker@example.com")) is None
    # A lookalike on Google's own domain is not the sender either: the address
    # is pinned whole, not by domain.
    assert _from(REAL_EMAIL, EmailAddress("noreply@google.com")) is None


def test_a_link_on_another_host_is_not_followed() -> None:
    forged = REAL_EMAIL.replace(
        "https://mail-settings.google.com/mail/vf-",
        "https://mail-settings.google.com.evil.test/mail/vf-",
    )

    assert _from(forged) is None


def test_a_plain_http_link_is_not_followed() -> None:
    forged = REAL_EMAIL.replace("https://mail-settings", "http://mail-settings")

    assert _from(forged) is None


def test_ordinary_mail_is_not_a_confirmation() -> None:
    assert _from("Bancolombia: Compraste $10.000 en TIENDAS ARA") is None


def test_the_value_object_refuses_a_url_it_did_not_recognise() -> None:
    """The last refusal before an adapter makes a network call with this."""
    with pytest.raises(ValueError, match="forwarding confirmation URL"):
        ForwardingConfirmation(url="https://evil.test/mail/vf-abc")


# The shape of the 2026-10-04 request: the same mail, with both links on
# `mail.google.com`. The token is made up; the soft breaks are where Google
# put them.
GMAIL_HOST_EMAIL = """Content-Type: text/plain; charset="UTF-8"
Content-Transfer-Encoding: quoted-printable

haz clic en el siguiente v=C3=ADnculo para confirmar la solicitud:

https://mail.google.com/mail/vf-%5BANGjdJ-M-R91hP-4Ky-SLQaZicMILIOtvuwpVsr5=
wg475IMJpuEuLYWOHil4VL7TpO7FVhziImwIPYrJYP9d2E9ODxboy160howkjj7TO4rd2rU3ZMz=
DHKTTRxQsL_J-TbXSghcKUWpOe8JGMsB5%5D-tVtNSigrxWnRJPdZeGYkXlADYq0

v=C3=ADnculo para cancelar la
verificaci=C3=B3n:
https://mail.google.com/mail/uf-%5BANGjdJ9EPBVmkDEPcFN7dHDhiqyecn6483uIezgh=
yH9MRPDvU7UtQLCgwcZ9M7rmeGuyymP_E_JIQ-MyjwY8fJW5y3B60hPIbbW5vmskGnPX6U8QgoV=
tA2hysoh2t_qQPCmlKTC58GL0VEaq6NXF%5D-tVtNSigrxWnRJPdZeGYkXlADYq0
"""


def test_a_link_on_gmails_own_host_is_recognised() -> None:
    """Reading only `mail-settings` dropped this one in silence."""
    confirmation = _from(GMAIL_HOST_EMAIL)

    assert confirmation is not None
    assert confirmation.url.startswith("https://mail.google.com/mail/vf-")
    assert confirmation.url.endswith("tVtNSigrxWnRJPdZeGYkXlADYq0")


def test_a_lookalike_of_gmails_own_host_is_not_followed() -> None:
    forged = GMAIL_HOST_EMAIL.replace(
        "https://mail.google.com/mail/vf-",
        "https://mail.google.com.evil.test/mail/vf-",
    )

    assert _from(forged) is None
