"""La aritmética de una ventana fija, que es lo único que decide un 429.

Las cifras de cada puerta viven en su contexto; lo que se fija aquí es que
«cinco en quince minutos» signifique siempre lo mismo: que el sexto se
rechace, que la espera que se anuncia acabe cuando la ventana acaba, y que
una ventana nueva empiece de cero sin que nadie tenga que barrer nada.
"""

from __future__ import annotations

import pytest

from personal_finance.shared.domain.throttling import (
    MAX_WINDOW_SECONDS,
    MIN_WINDOW_SECONDS,
    RateLimit,
)
from personal_finance.shared.domain.value_objects import PosixTime


QUARTER_HOUR = 15 * 60


def at(seconds: int) -> PosixTime:
    return PosixTime.from_epoch_seconds(seconds)


def test_lo_que_cabe_en_el_presupuesto_pasa() -> None:
    """`attempt` cuenta el intento que se está juzgando: con cinco de
    presupuesto, el quinto pasa y el sexto no. Escrito así porque la versión
    ambigua de esta frase costó un error de uno que dejaba fuera a la gente
    un intento antes de tiempo."""
    limit = RateLimit(attempts=5, window_seconds=QUARTER_HOUR)

    for attempt in range(1, 6):
        verdict = limit.judge(attempt=attempt, now=at(1_000))

        assert verdict.allowed is True
        assert verdict.retry_after_seconds == 0

    assert limit.judge(attempt=6, now=at(1_000)).allowed is False


def test_cuanto_queda_es_lo_que_queda() -> None:
    limit = RateLimit(attempts=5, window_seconds=QUARTER_HOUR)

    assert limit.judge(attempt=1, now=at(0)).remaining == 4
    assert limit.judge(attempt=5, now=at(0)).remaining == 0
    assert limit.judge(attempt=9, now=at(0)).remaining == 0


def test_la_espera_termina_cuando_termina_la_ventana() -> None:
    """Y nunca es cero: un `Retry-After: 0` se lee como «adelante», que es lo
    contrario de lo que un 429 está diciendo."""
    limit = RateLimit(attempts=1, window_seconds=QUARTER_HOUR)

    # A los diez minutos de una ventana de quince quedan cinco.
    assert limit.judge(attempt=2, now=at(10 * 60)).retry_after_seconds == 5 * 60
    # Y en el último segundo, uno — no cero.
    assert limit.judge(attempt=2, now=at(QUARTER_HOUR - 1)).retry_after_seconds == 1
    assert (
        limit.judge(attempt=2, now=at(QUARTER_HOUR)).retry_after_seconds == QUARTER_HOUR
    )


def test_la_ventana_siguiente_es_otra_clave() -> None:
    """Lo que hace que una ventana termine sola: el intento de después
    pertenece a otro balde, y el viejo expira sin que nadie lo borre."""
    limit = RateLimit(attempts=5, window_seconds=QUARTER_HOUR)

    assert limit.window_of(at(0)) == limit.window_of(at(QUARTER_HOUR - 1))
    assert limit.window_of(at(QUARTER_HOUR)) != limit.window_of(at(0))
    assert limit.window_ends_at(at(10)).as_epoch_seconds() == QUARTER_HOUR


def test_un_presupuesto_de_cero_no_es_un_limite_sino_una_puerta_cerrada() -> None:
    with pytest.raises(ValueError, match="at least one attempt"):
        RateLimit(attempts=0, window_seconds=QUARTER_HOUR)


def test_una_ventana_tiene_que_ser_una_ventana() -> None:
    """Un segundo no limita nada y una semana no es un límite: es un castigo."""
    with pytest.raises(ValueError, match="between"):
        RateLimit(attempts=5, window_seconds=MIN_WINDOW_SECONDS - 1)

    with pytest.raises(ValueError, match="between"):
        RateLimit(attempts=5, window_seconds=MAX_WINDOW_SECONDS + 1)
