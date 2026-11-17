"""Tests for the OpenRouter account login (OAuth with PKCE).

A fake browser reads the auth URL Eva opens and calls the loopback callback
with a real HTTP request; the code exchange goes to an httpx.MockTransport, so
no test touches the real network or OpenRouter.
"""

import base64
import hashlib
import http.client
import json
import socket
import threading
from dataclasses import dataclass
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx
import pytest

from app.auth import ErrorAuth, iniciar_sesion_openrouter

CLAVE = "sk-or-v1-clave-de-prueba"
CODIGO = "codigo-de-un-solo-uso"

MENSAJE_NO_AUTORIZO = "OpenRouter no autorizó el inicio de sesión."
MENSAJE_TIMEOUT = "No se completó el inicio de sesión con OpenRouter a tiempo. Inténtalo de nuevo."
MENSAJE_CANCELADO = "Se canceló el inicio de sesión con OpenRouter."
MENSAJE_SIN_CONEXION = "No pude conectarme con OpenRouter. Revisa tu conexión a internet."


@dataclass
class Pagina:
    status_code: int
    content_type: str
    text: str


def _s256(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


class NavegadorFalso:
    """Plays the user's browser: checks the auth URL and hits the callback.

    ``consulta`` builds the callback query from the auth URL parameters; None
    means the user never comes back (nothing is requested).
    """

    def __init__(self, consulta=lambda p: {"code": CODIGO, "state": p["state"]}):
        self.consulta = consulta
        self.params: dict[str, str] = {}
        self.puerto: int | None = None
        self.respuesta: Pagina | None = None
        self.hilo: threading.Thread | None = None

    def __call__(self, url: str) -> bool:
        partes = urlsplit(url)
        assert (partes.scheme, partes.netloc, partes.path) == ("https", "openrouter.ai", "/auth")
        self.params = {k: v[0] for k, v in parse_qs(partes.query).items()}
        assert self.params["code_challenge_method"] == "S256"
        assert self.params["state"]
        assert self.params["code_challenge"]
        assert "=" not in self.params["code_challenge"]
        callback = urlsplit(self.params["callback_url"])
        assert callback.scheme == "http"
        assert callback.hostname == "localhost"
        assert callback.path == "/callback"
        self.puerto = callback.port
        if self.consulta is not None:
            # 127.0.0.1 instead of "localhost": same server, but avoids the slow
            # IPv6 fallback on Windows.
            destino = f"http://127.0.0.1:{self.puerto}/callback?{urlencode(self.consulta(self.params))}"
            self.hilo = threading.Thread(target=self._visitar, args=(destino,), daemon=True)
            self.hilo.start()
        return True

    def _visitar(self, destino: str) -> None:
        # Plain http.client (no proxies, no logging): the request line carries
        # the code, and the log test must only see what Eva itself logs.
        partes = urlsplit(destino)
        conexion = http.client.HTTPConnection(partes.hostname, partes.port, timeout=5)
        try:
            conexion.request("GET", f"{partes.path}?{partes.query}")
            r = conexion.getresponse()
            self.respuesta = Pagina(r.status, r.getheader("Content-Type", ""), r.read().decode())
        finally:
            conexion.close()

    def esperar(self) -> None:
        if self.hilo is not None:
            self.hilo.join(5)


class OpenRouterFalso:
    """Stands in for POST https://openrouter.ai/api/v1/auth/keys."""

    def __init__(self, navegador: NavegadorFalso, respuesta=None):
        self.navegador = navegador
        self.respuesta = respuesta or httpx.Response(200, json={"key": CLAVE, "user_id": "u1"})
        self.pedidos: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.pedidos.append(request)
        assert request.method == "POST"
        assert str(request.url) == "https://openrouter.ai/api/v1/auth/keys"
        cuerpo = json.loads(request.content)
        assert set(cuerpo) == {"code", "code_verifier", "code_challenge_method"}
        assert cuerpo["code"] == CODIGO
        assert cuerpo["code_challenge_method"] == "S256"
        assert 43 <= len(cuerpo["code_verifier"]) <= 128
        assert _s256(cuerpo["code_verifier"]) == self.navegador.params["code_challenge"]
        return self.respuesta

    def cliente(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self))


def _puerto_libre(puerto: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", puerto))
        except OSError:
            return False
    return True


def _iniciar(navegador, openrouter=None, **kwargs):
    openrouter = openrouter or OpenRouterFalso(navegador)
    kwargs.setdefault("timeout", 5)
    try:
        return iniciar_sesion_openrouter(
            abrir_navegador=navegador, http_client=openrouter.cliente(), **kwargs
        )
    finally:
        navegador.esperar()


def test_success_returns_the_key_and_shows_the_done_page():
    navegador = NavegadorFalso()
    openrouter = OpenRouterFalso(navegador)

    assert _iniciar(navegador, openrouter) == CLAVE

    assert len(openrouter.pedidos) == 1
    assert navegador.respuesta.status_code == 200
    assert "text/html" in navegador.respuesta.content_type
    assert "Listo. Ya puedes volver a Eva." in navegador.respuesta.text


def test_each_attempt_uses_a_new_verifier_and_state():
    primero, segundo = NavegadorFalso(), NavegadorFalso()
    _iniciar(primero)
    _iniciar(segundo)

    assert primero.params["state"] != segundo.params["state"]
    assert primero.params["code_challenge"] != segundo.params["code_challenge"]


def test_wrong_state_is_not_authorized_and_skips_the_exchange():
    navegador = NavegadorFalso(lambda p: {"code": CODIGO, "state": "otro-estado"})
    openrouter = OpenRouterFalso(navegador)

    with pytest.raises(ErrorAuth) as exc:
        _iniciar(navegador, openrouter)

    assert str(exc.value) == MENSAJE_NO_AUTORIZO
    assert openrouter.pedidos == []
    assert "Listo" not in navegador.respuesta.text


def test_denied_without_code_is_not_authorized():
    navegador = NavegadorFalso(lambda p: {"error": "access_denied", "state": p["state"]})
    openrouter = OpenRouterFalso(navegador)

    with pytest.raises(ErrorAuth) as exc:
        _iniciar(navegador, openrouter)

    assert str(exc.value) == MENSAJE_NO_AUTORIZO
    assert openrouter.pedidos == []


def test_exchange_rejected_shows_openrouter_detail():
    navegador = NavegadorFalso()
    respuesta = httpx.Response(
        403, json={"error": {"code": 403, "message": "Invalid code_verifier"}}
    )
    openrouter = OpenRouterFalso(navegador, respuesta)

    with pytest.raises(ErrorAuth) as exc:
        _iniciar(navegador, openrouter)

    assert str(exc.value) == "OpenRouter rechazó el inicio de sesión: Invalid code_verifier"


def test_exchange_without_key_is_rejected():
    navegador = NavegadorFalso()
    openrouter = OpenRouterFalso(navegador, httpx.Response(200, json={"user_id": "u1"}))

    with pytest.raises(ErrorAuth) as exc:
        _iniciar(navegador, openrouter)

    assert str(exc.value).startswith("OpenRouter rechazó el inicio de sesión: ")


def test_no_connection_during_exchange():
    navegador = NavegadorFalso()

    def sin_red(request):
        raise httpx.ConnectError("sin red", request=request)

    with pytest.raises(ErrorAuth) as exc:
        iniciar_sesion_openrouter(
            abrir_navegador=navegador,
            timeout=5,
            http_client=httpx.Client(transport=httpx.MockTransport(sin_red)),
        )
    navegador.esperar()

    assert str(exc.value) == MENSAJE_SIN_CONEXION


def test_timeout_when_the_user_never_comes_back():
    navegador = NavegadorFalso(consulta=None)
    openrouter = OpenRouterFalso(navegador)

    with pytest.raises(ErrorAuth) as exc:
        _iniciar(navegador, openrouter, timeout=0.3)

    assert str(exc.value) == MENSAJE_TIMEOUT
    assert openrouter.pedidos == []
    assert _puerto_libre(navegador.puerto)


def test_cancel_from_the_ui_stops_waiting():
    cancelado = threading.Event()
    navegador = NavegadorFalso(consulta=None)
    openrouter = OpenRouterFalso(navegador)
    threading.Timer(0.2, cancelado.set).start()

    with pytest.raises(ErrorAuth) as exc:
        _iniciar(navegador, openrouter, timeout=30, cancelado=cancelado)

    assert str(exc.value) == MENSAJE_CANCELADO
    assert openrouter.pedidos == []
    assert _puerto_libre(navegador.puerto)


def test_server_port_is_released_after_success():
    navegador = NavegadorFalso()
    _iniciar(navegador)

    assert _puerto_libre(navegador.puerto)


def test_server_port_is_released_after_an_error():
    navegador = NavegadorFalso(lambda p: {"code": CODIGO, "state": "otro-estado"})
    with pytest.raises(ErrorAuth):
        _iniciar(navegador)

    assert _puerto_libre(navegador.puerto)


def test_server_closes_when_the_browser_cannot_open():
    puertos = []

    def navegador_roto(url):
        puertos.append(urlsplit(parse_qs(urlsplit(url).query)["callback_url"][0]).port)
        raise OSError("sin navegador")

    with pytest.raises(OSError):
        iniciar_sesion_openrouter(abrir_navegador=navegador_roto, timeout=1)

    assert _puerto_libre(puertos[0])


def test_secrets_never_reach_the_logs(caplog, capfd):
    navegador = NavegadorFalso()
    openrouter = OpenRouterFalso(navegador)
    with caplog.at_level("DEBUG"):
        _iniciar(navegador, openrouter)

    salida = capfd.readouterr()
    verifier = json.loads(openrouter.pedidos[0].content)["code_verifier"]
    for secreto in (CLAVE, CODIGO, verifier):
        assert secreto not in caplog.text
        assert secreto not in salida.out
        assert secreto not in salida.err
