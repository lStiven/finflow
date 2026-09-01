"""What this deployment's credential mail actually says.

In Spanish, because everyone this deployment serves reads it. Plain text
alongside the HTML, because a mail client that shows only the text part must
still show a usable code.

Nothing here interpolates anything a stranger typed. The address is not echoed
into the body, the code is six digits this process generated, and the link is
built from configured base plus a token this process generated — so there is
no path from a registration form into somebody else's inbox. The one value
that is *not* ours is the address in the envelope, and `smtp.py` refuses one
with a line break in it before it reaches a header.
"""

from __future__ import annotations

import dataclasses
import html


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class MailMessage:
    subject: str
    text: str
    html: str


_SIGNATURE_TEXT = "\n\n— Finflow"
_STYLE = (
    "font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,"
    "Helvetica,Arial,sans-serif;font-size:15px;line-height:1.6;color:#1a1a1a"
)


def _page(body: str) -> str:
    return f'<div style="{_STYLE}">{body}<p style="color:#777">— Finflow</p></div>'


def verification_code(*, code: str, expires_in_minutes: int) -> MailMessage:
    return MailMessage(
        subject=f"Tu código de verificación de Finflow: {code}",
        text=(
            "Alguien está creando una cuenta de Finflow con este correo.\n\n"
            f"Tu código es: {code}\n\n"
            f"Vence en {expires_in_minutes} minutos y solo sirve una vez.\n"
            "Si no fuiste tú, ignora este mensaje: sin el código no se crea "
            "ninguna cuenta." + _SIGNATURE_TEXT
        ),
        html=_page(
            "<p>Alguien está creando una cuenta de Finflow con este "
            "correo.</p>"
            f'<p style="font-size:30px;letter-spacing:6px;font-weight:700">'
            f"{html.escape(code)}</p>"
            f"<p>Vence en {expires_in_minutes} minutos y solo sirve una "
            "vez.</p>"
            "<p>Si no fuiste tú, ignora este mensaje: sin el código no se "
            "crea ninguna cuenta.</p>",
        ),
    )


def registration_attempt_on_existing_account() -> MailMessage:
    """Sent instead of a code when the address already has an account.

    It exists so that the endpoint answers the same way either way. It is also
    the only warning the owner would ever get that somebody is trying their
    address, so it says what to do about it.
    """
    return MailMessage(
        subject="Ya tienes una cuenta de Finflow",
        text=(
            "Alguien intentó crear una cuenta de Finflow con este correo, "
            "pero ya existe una.\n\n"
            "Si fuiste tú, inicia sesión con tu contraseña. Si no la "
            "recuerdas, usa la opción de recuperarla.\n"
            "Si no fuiste tú, no hay nada que hacer: no se creó ni se "
            "modificó ninguna cuenta." + _SIGNATURE_TEXT
        ),
        html=_page(
            "<p>Alguien intentó crear una cuenta de Finflow con este correo, "
            "pero ya existe una.</p>"
            "<p>Si fuiste tú, inicia sesión con tu contraseña. Si no la "
            "recuerdas, usa la opción de recuperarla.</p>"
            "<p>Si no fuiste tú, no hay nada que hacer: no se creó ni se "
            "modificó ninguna cuenta.</p>",
        ),
    )


def password_reset(*, link: str, expires_in_minutes: int) -> MailMessage:
    return MailMessage(
        subject="Restablece tu contraseña de Finflow",
        text=(
            "Pediste restablecer la contraseña de tu cuenta de Finflow.\n\n"
            f"Abre este enlace para elegir una nueva:\n{link}\n\n"
            f"Vence en {expires_in_minutes} minutos y solo sirve una vez.\n"
            "Si no fuiste tú, ignora este mensaje: tu contraseña actual "
            "sigue funcionando y nadie puede cambiarla sin este enlace."
            + _SIGNATURE_TEXT
        ),
        html=_page(
            "<p>Pediste restablecer la contraseña de tu cuenta de "
            "Finflow.</p>"
            f'<p><a href="{html.escape(link, quote=True)}">Elegir una nueva '
            "contraseña</a></p>"
            f"<p>Vence en {expires_in_minutes} minutos y solo sirve una "
            "vez.</p>"
            "<p>Si no fuiste tú, ignora este mensaje: tu contraseña actual "
            "sigue funcionando y nadie puede cambiarla sin este enlace.</p>",
        ),
    )


def password_reset_for_unknown_account() -> MailMessage:
    """Sent when the address asking for a reset has no account.

    The alternative — sending nothing — would make the endpoint answer "is
    this address registered?" by whether mail arrives.
    """
    return MailMessage(
        subject="Restablece tu contraseña de Finflow",
        text=(
            "Alguien pidió restablecer la contraseña de una cuenta de "
            "Finflow con este correo, pero no hay ninguna cuenta "
            "registrada aquí.\n\n"
            "Si fuiste tú, revisa si te registraste con otra dirección, o "
            "crea una cuenta nueva.\n"
            "Si no fuiste tú, ignora este mensaje." + _SIGNATURE_TEXT
        ),
        html=_page(
            "<p>Alguien pidió restablecer la contraseña de una cuenta de "
            "Finflow con este correo, pero no hay ninguna cuenta registrada "
            "aquí.</p>"
            "<p>Si fuiste tú, revisa si te registraste con otra dirección, o "
            "crea una cuenta nueva.</p>"
            "<p>Si no fuiste tú, ignora este mensaje.</p>",
        ),
    )
