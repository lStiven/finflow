"""What the model-suspension notice says, by email and by chat.

The email is the copy the owner approved on 2026-10-30
(`docs/model-strikes-copy.md`), word for word but for one gap: it names a
customer-service channel that does not exist yet, so it says only that one is
coming. Telegram and the in-app inbox use the shorter on-screen notice.

The only value here a person typed is their own name, and it only ever lands
in the body: collapsed to one line for the text part, escaped for the HTML.
"""

from __future__ import annotations

import dataclasses
import html
from zoneinfo import ZoneInfo

from personal_finance.contexts.alerts.application.messages import ModelPause
from personal_finance.shared.domain.value_objects import PosixTime


#: The banks a template reads, as the copy names them. A test holds this to
#: Ingestion's own list, so a parser added there cannot be left out here.
TEMPLATE_BANKS = "Bancolombia y Lulo"

MAX_NAME_LENGTH = 80

_MONTHS = (
    "enero",
    "febrero",
    "marzo",
    "abril",
    "mayo",
    "junio",
    "julio",
    "agosto",
    "septiembre",
    "octubre",
    "noviembre",
    "diciembre",
)


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class NoticeMail:
    subject: str
    text: str
    html: str


def format_long_moment(moment: PosixTime, *, timezone: str) -> str:
    """`3 de noviembre a las 9:05 a. m.`, in the deployment's own zone."""
    local = moment.to_datetime().astimezone(ZoneInfo(timezone))
    hour = local.hour % 12 or 12
    half = "a. m." if local.hour < 12 else "p. m."

    return (
        f"{local.day} de {_MONTHS[local.month - 1]} a las "
        f"{hour}:{local.minute:02d} {half}"
    )


def compose_model_pause_chat(pause: ModelPause, *, timezone: str) -> str:
    if pause.paused_until is None:
        return (
            "Finflow ya no lee los correos que no reconoce por sí solo. Las "
            f"alertas de {TEMPLATE_BANKS} se siguen registrando y tu cuenta "
            "sigue activa. Para reactivar la lectura, comunícate con servicio "
            "al cliente (muy pronto)."
        )

    until = format_long_moment(pause.paused_until, timezone=timezone)
    lines = [
        f"Pausamos hasta el {until} la lectura de los correos que Finflow no "
        "reconoce: llegaron correos que no eran de tu banco. Las alertas de "
        f"{TEMPLATE_BANKS} se siguen registrando; lo demás puedes anotarlo a "
        "mano.",
    ]

    if pause.strike == 1:
        lines.append("Si vuelve a pasar, la pausa será de 3 días.")
    else:
        lines.append(
            "Es el último aviso: si vuelve a pasar, Finflow dejará de leer esos "
            "correos de forma definitiva.",
        )

    return "\n\n".join(lines)


def compose_model_pause_mail(
    pause: ModelPause,
    *,
    name: str | None,
    timezone: str,
) -> NoticeMail:
    greeting = _greeting(name)

    if pause.paused_until is None:
        subject = "Finflow dejó de leer los correos que no reconoce"
        paragraphs = _final_paragraphs()
    else:
        until = format_long_moment(pause.paused_until, timezone=timezone)

        if pause.strike == 1:
            subject = "Pausamos la lectura de tus correos por 24 horas"
            paragraphs = _first_paragraphs(offenses=pause.offenses, until=until)
        else:
            subject = "Segundo y último aviso: pausa de 3 días"
            paragraphs = _second_paragraphs(offenses=pause.offenses, until=until)

    return NoticeMail(
        subject=subject,
        text="\n\n".join([greeting, *paragraphs]) + "\n\n— Finflow",
        html=_page([greeting, *paragraphs]),
    )


def _greeting(name: str | None) -> str:
    cleaned = " ".join((name or "").split())[:MAX_NAME_LENGTH]

    return f"Hola, {cleaned}:" if cleaned else "Hola:"


def _first_paragraphs(*, offenses: int, until: str) -> list[str]:
    return [
        f"En menos de una hora llegaron a tu dirección de Finflow {offenses} "
        "correos que no eran alertas de tu banco. Cada correo que Finflow no "
        "reconoce pasa por un lector automático, y cada lectura tiene un costo.",
        f"Por eso pausamos hasta el {until} la lectura de los correos que "
        "Finflow no reconoce por sí solo. Las alertas de los bancos que ya "
        f"conocemos ({TEMPLATE_BANKS}) se siguen registrando solas; las demás "
        "que lleguen en este tiempo no se van a leer. Tu cuenta sigue "
        "funcionando igual: puedes entrar, revisar tus cuentas y anotar a mano "
        "lo que gastes.",
        "Lo más probable es que un filtro de reenvío esté dejando pasar de más. "
        "Revisa que solo reenvíe las alertas de tu banco; la guía para conectar "
        "el banco te muestra cómo.",
        "Finflow está para ayudarte con tu plata y queremos seguir haciéndolo, "
        "pero el lector es solo para alertas bancarias. Este es un primer "
        "aviso: si vuelve a pasar, la pausa será de 3 días.",
    ]


def _second_paragraphs(*, offenses: int, until: str) -> list[str]:
    return [
        "Otra vez llegaron a tu dirección de Finflow correos que no eran "
        f"alertas de tu banco ({offenses} en menos de una hora). Como te "
        f"habíamos avisado, pausamos hasta el {until} la lectura de los correos "
        "que Finflow no reconoce por sí solo. Las alertas de "
        f"{TEMPLATE_BANKS} se siguen registrando.",
        "Te lo decimos claro porque queremos que sigas con nosotros: este es "
        "el último aviso. Si vuelve a pasar, Finflow dejará de leer esos "
        "correos de forma definitiva. Podrás seguir usando la app y anotando a "
        "mano, pero los movimientos de los bancos que no reconocemos ya no se "
        "registrarán solos.",
        "Antes de que termine la pausa, revisa que a Finflow solo le lleguen "
        "las alertas de tu banco.",
    ]


def _final_paragraphs() -> list[str]:
    return [
        "Después de dos avisos, siguieron llegando correos que no eran alertas "
        "bancarias, así que Finflow ya no leerá los correos que no reconoce por "
        f"sí solo. Las alertas de {TEMPLATE_BANKS} se seguirán registrando "
        "solas; las de otros bancos tendrás que anotarlas a mano.",
        "Tu cuenta sigue activa: tus cuentas, movimientos, presupuestos y "
        "reportes siguen ahí. Si crees que es un error o quieres reactivar la "
        "lectura, comunícate con servicio al cliente (disponible muy pronto).",
        "Lamentamos llegar a este punto. Finflow existe para ayudarte, y para "
        "eso el lector tiene que usarse para lo que fue hecho.",
    ]


_STYLE = (
    "font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,"
    "Helvetica,Arial,sans-serif;font-size:15px;line-height:1.6;color:#1a1a1a"
)


def _page(paragraphs: list[str]) -> str:
    body = "".join(f"<p>{html.escape(paragraph)}</p>" for paragraph in paragraphs)

    return f'<div style="{_STYLE}">{body}<p style="color:#777">— Finflow</p></div>'
