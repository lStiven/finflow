"""De quién es un intento, que es donde viven los falsos positivos.

Equivocarse aquí no deja la puerta abierta: la deja cerrada para la persona
equivocada. Si la dirección se lee de una cabecera que cualquiera puede
escribir, el límite no limita a nadie; si se lee del socket habiendo un
proxy delante, **todo el mundo comparte un balde** y el primer ataque bloquea
a todos los demás. Por eso «no sé de dónde viene» es una respuesta de primera
clase, y lo que hace es saltarse la dimensión de dirección, no inventarla.
"""

from __future__ import annotations

import base64
import json

from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

from personal_finance.shared.domain.throttling import RateLimit
from personal_finance.shared.domain.value_objects import PosixTime
from personal_finance.shared.presentation.throttling import Door, Guard, client_ip


MINUTE = 60


class FakeCounter:
    def __init__(self) -> None:
        self.hits: dict[str, int] = {}
        self.cleared: list[str] = []

    def spent(self, bucket: str) -> int:
        return self.hits.get(bucket, 0)

    def record(self, *, bucket: str, expires_at: PosixTime) -> int:
        del expires_at
        self.hits[bucket] = self.hits.get(bucket, 0) + 1

        return self.hits[bucket]

    def clear(self, bucket: str) -> None:
        self.cleared.append(bucket)
        self.hits.pop(bucket, None)


def seen(headers: dict[str, str] | None = None, *, trust_proxy: bool) -> str | None:
    """Qué dirección ve el guardia para una petición con estas cabeceras."""
    app = FastAPI()
    answers: list[str | None] = []

    @app.get("/")
    def _(request: Request) -> dict[str, str]:
        answers.append(client_ip(request, trust_proxy=trust_proxy))

        return {}

    TestClient(app).get("/", headers=headers or {})

    return answers[0]


def context_header(source_ip: str, *, encoded: bool = False) -> dict[str, str]:
    raw = json.dumps({"http": {"sourceIp": source_ip, "method": "POST"}})

    return {
        "x-amzn-request-context": (
            base64.b64encode(raw.encode()).decode() if encoded else raw
        ),
    }


# ----------------------------------------------------------------------
# De dónde viene
# ----------------------------------------------------------------------


def test_sin_nada_delante_la_direccion_es_el_socket() -> None:
    assert seen(trust_proxy=False) == "testclient"


def test_sin_nada_delante_las_cabeceras_no_valen_nada() -> None:
    """La mitad que importa: quien no tiene un proxy delante no puede dejar
    que una cabecera decida de quién es un intento, porque la escribe el que
    ataca."""
    forged = context_header("9.9.9.9") | {"x-forwarded-for": "9.9.9.9"}

    assert seen(forged, trust_proxy=False) == "testclient"


def test_con_proxy_delante_manda_el_contexto_que_arma_aws() -> None:
    assert seen(context_header("200.1.2.3"), trust_proxy=True) == "200.1.2.3"


def test_el_contexto_tambien_se_lee_en_base64() -> None:
    header = context_header("200.1.2.3", encoded=True)

    assert seen(header, trust_proxy=True) == "200.1.2.3"


def test_el_contexto_le_gana_a_la_cabecera_que_manda_el_cliente() -> None:
    """Un atacante puede escribir `X-Forwarded-For`; el contexto lo arma AWS
    y el adaptador lo sobrescribe. Entre los dos, gana el que no se puede
    inventar."""
    headers = context_header("200.1.2.3") | {"x-forwarded-for": "9.9.9.9"}

    assert seen(headers, trust_proxy=True) == "200.1.2.3"


def test_sin_contexto_se_lee_el_ultimo_salto_del_forwarded_for() -> None:
    """El primero es lo que escribió el cliente; el último es lo que añadió
    la infraestructura de enfrente."""
    headers = {"x-forwarded-for": "9.9.9.9, 10.0.0.1, 200.1.2.3"}

    assert seen(headers, trust_proxy=True) == "200.1.2.3"


def test_un_contexto_ilegible_no_tumba_la_peticion() -> None:
    headers = {"x-amzn-request-context": "{no es json", "x-forwarded-for": "200.1.2.3"}

    assert seen(headers, trust_proxy=True) == "200.1.2.3"


def test_cuando_no_hay_forma_de_saberlo_la_respuesta_es_que_no_se_sabe() -> None:
    """Y no el socket, que detrás del adaptador es siempre el mismo — leerlo
    metería a todo el mundo en un balde y el primer ataque bloquearía al
    resto."""
    assert seen(trust_proxy=True) is None


# ----------------------------------------------------------------------
# Qué hace el guardia con eso
# ----------------------------------------------------------------------


def guarded(door: Door, counter: FakeCounter, verb: str = "spend") -> TestClient:
    app = FastAPI()
    guard = Guard(counter, trust_proxy=True)

    @app.post("/")
    def _(request: Request, subject: str | None = None) -> dict[str, str]:
        getattr(guard, verb)(door, request, subject=subject)

        return {"ok": "sí"}

    return TestClient(app)


def test_sin_direccion_conocida_el_limite_por_cuenta_sigue_aplicando() -> None:
    """El caso que decide si esto es seguro cuando la dirección no se puede
    establecer: se pierde la dimensión que protege de una pulverización, no
    la que protege una cuenta."""
    counter = FakeCounter()
    door = Door(
        name="prueba",
        address=RateLimit(attempts=100, window_seconds=MINUTE),
        subject=RateLimit(attempts=2, window_seconds=MINUTE),
    )
    client = guarded(door, counter)

    assert client.post("/?subject=ana@x.com").status_code == 200
    assert client.post("/?subject=ana@x.com").status_code == 200
    assert client.post("/?subject=ana@x.com").status_code == 429
    # Y nadie más queda encerrado por eso.
    assert client.post("/?subject=otra@x.com").status_code == 200


def test_el_429_dice_cuanto_hay_que_esperar() -> None:
    counter = FakeCounter()
    door = Door(name="prueba", address=RateLimit(attempts=1, window_seconds=MINUTE))
    client = guarded(door, counter)

    client.post("/", headers=context_header("200.1.2.3"))
    refused = client.post("/", headers=context_header("200.1.2.3"))

    assert refused.status_code == 429
    assert 1 <= int(refused.headers["Retry-After"]) <= MINUTE


def test_dos_direcciones_distintas_no_comparten_presupuesto() -> None:
    counter = FakeCounter()
    door = Door(name="prueba", address=RateLimit(attempts=1, window_seconds=MINUTE))
    client = guarded(door, counter)

    assert client.post("/", headers=context_header("200.1.2.3")).status_code == 200
    assert client.post("/", headers=context_header("200.1.2.3")).status_code == 429
    assert client.post("/", headers=context_header("201.9.9.9")).status_code == 200


def test_require_no_gasta_nada() -> None:
    """La puerta del login pregunta antes de intentar; si preguntar costara,
    entrar bien también costaría."""
    counter = FakeCounter()
    door = Door(name="prueba", address=RateLimit(attempts=2, window_seconds=MINUTE))
    client = guarded(door, counter, verb="require")

    for _ in range(10):
        assert client.post("/", headers=context_header("200.1.2.3")).status_code == 200

    assert counter.hits == {}


def test_charge_no_revienta_cuando_el_contador_falla() -> None:
    """Quien la llama ya va camino de un 401: que contar falle no puede
    convertir eso en un 500."""

    class Broken(FakeCounter):
        def record(self, *, bucket: str, expires_at: PosixTime) -> int:
            raise RuntimeError("la tabla no está")

    door = Door(name="prueba", address=RateLimit(attempts=2, window_seconds=MINUTE))
    guard = Guard(Broken(), trust_proxy=True)
    app = FastAPI()

    @app.post("/")
    def _(request: Request) -> dict[str, str]:
        try:
            guard.charge(door, request)
        except RuntimeError:
            raise HTTPException(status_code=500, detail="contó mal") from None

        return {"ok": "sí"}

    # El contador real atrapa sus propios fallos; esto fija que el guardia no
    # añade ninguno propio entre medias.
    response = TestClient(app).post("/", headers=context_header("200.1.2.3"))

    assert response.status_code == 500


def test_perdonar_borra_la_cuenta_y_no_la_direccion() -> None:
    counter = FakeCounter()
    door = Door(
        name="prueba",
        address=RateLimit(attempts=5, window_seconds=MINUTE),
        subject=RateLimit(attempts=5, window_seconds=MINUTE),
    )
    guard = Guard(counter, trust_proxy=True)
    app = FastAPI()

    @app.post("/")
    def _(request: Request) -> dict[str, str]:
        guard.charge(door, request, subject="ana@x.com")
        guard.forgive(door, subject="ana@x.com")

        return {"ok": "sí"}

    TestClient(app).post("/", headers=context_header("200.1.2.3"))

    assert [bucket.split("|")[1] for bucket in counter.cleared] == ["id"]
    assert any(bucket.split("|")[1] == "ip" for bucket in counter.hits)


def test_sin_direccion_una_puerta_que_solo_cuenta_direcciones_queda_sin_limite() -> (
    None
):
    """Lo que cuesta la decisión de arriba, escrito para que nadie lo
    descubra a la mala.

    La alternativa —meter lo que no se puede identificar en un balde común—
    convierte «la puerta de enfrente cambió de forma» en «todo el mundo
    encerrado a la vez», que es peor y además silencioso. Esto no lo es: deja
    un aviso en el log por cada petición.
    """
    counter = FakeCounter()
    door = Door(name="prueba", address=RateLimit(attempts=1, window_seconds=MINUTE))
    client = guarded(door, counter)

    # Sin cabecera que leer y con `trust_proxy` puesto: no hay dirección.
    codes = {client.post("/").status_code for _ in range(5)}

    assert codes == {200}
    assert counter.hits == {}


def test_lo_que_no_es_una_direccion_no_es_una_direccion() -> None:
    """Todo esto llega en una cabecera, y una cabecera es un texto que
    escribió alguien más. Comprobar la forma evita que una clave de longitud
    arbitraria llegue a la tabla del contador, y hace que la basura se trate
    como «no se sabe» en vez de como un balde nuevo por cada variante."""
    assert seen({"x-forwarded-for": "no soy una ip"}, trust_proxy=True) is None
    assert seen(context_header("../../etc/passwd"), trust_proxy=True) is None
    assert seen({"x-forwarded-for": "x" * 5000}, trust_proxy=True) is None
    assert seen(context_header("2001:db8::1"), trust_proxy=True) == "2001:db8::1"


def test_una_misma_direccion_no_tiene_dos_presupuestos_por_escribirse_distinto() -> (
    None
):
    """IPv6 tiene más de una forma de escribir la misma dirección, y dos
    grafías serían dos baldes para el mismo sitio."""
    assert (
        seen(context_header("2001:0db8:0000::0001"), trust_proxy=True) == "2001:db8::1"
    )
