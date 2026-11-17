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
import socketserver
import threading
import time
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
MENSAJE_SIN_NAVEGADOR = "No pude abrir el navegador. Revisa que tengas uno instalado e inténtalo de nuevo."


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
        assert callback.hostname == "127.0.0.1"
        assert callback.path == "/callback"
        self.puerto = callback.port
        if self.consulta is not None:
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


def _navegador_que_falla(urls, resultado):
    def abrir(url):
        urls.append(url)
        if isinstance(resultado, Exception):
            raise resultado
        return resultado

    return abrir


def _puerto_de(url):
    return urlsplit(parse_qs(urlsplit(url).query)["callback_url"][0]).port


SIN_NAVEGADOR = pytest.mark.parametrize(
    "resultado", [False, OSError("sin navegador")], ids=["devuelve-false", "lanza"]
)


@SIN_NAVEGADOR
def test_without_a_browser_and_no_callback_it_fails_at_once(resultado):
    urls: list[str] = []
    inicio = time.monotonic()

    with pytest.raises(ErrorAuth) as exc:
        iniciar_sesion_openrouter(
            abrir_navegador=_navegador_que_falla(urls, resultado),
            timeout=30,
            http_client=httpx.Client(transport=httpx.MockTransport(lambda r: pytest.fail("no exchange"))),
        )

    assert time.monotonic() - inicio < 5
    (url,) = urls
    assert url.startswith("https://openrouter.ai/auth?")
    assert str(exc.value) == MENSAJE_SIN_NAVEGADOR
    assert _puerto_libre(_puerto_de(url))


@SIN_NAVEGADOR
def test_without_a_browser_the_callback_gets_the_url_and_the_manual_path_completes(resultado):
    urls: list[str] = []
    mostradas: list[str] = []
    navegador = NavegadorFalso()  # the user opens the shown URL by hand

    def mostrar(url):
        mostradas.append(url)
        navegador(url)

    clave = iniciar_sesion_openrouter(
        abrir_navegador=_navegador_que_falla(urls, resultado),
        timeout=5,
        http_client=OpenRouterFalso(navegador).cliente(),
        al_sin_navegador=mostrar,
    )
    navegador.esperar()

    assert clave == CLAVE
    assert mostradas == urls
    assert navegador.respuesta.status_code == 200
    assert "Listo. Ya puedes volver a Eva." in navegador.respuesta.text
    assert _puerto_libre(navegador.puerto)


@SIN_NAVEGADOR
def test_without_a_browser_the_manual_wait_can_be_cancelled(resultado):
    urls: list[str] = []
    mostradas: list[str] = []
    cancelado = threading.Event()
    threading.Timer(0.2, cancelado.set).start()

    with pytest.raises(ErrorAuth) as exc:
        iniciar_sesion_openrouter(
            abrir_navegador=_navegador_que_falla(urls, resultado),
            timeout=30,
            http_client=httpx.Client(transport=httpx.MockTransport(lambda r: pytest.fail("no exchange"))),
            cancelado=cancelado,
            al_sin_navegador=mostradas.append,
        )

    assert str(exc.value) == MENSAJE_CANCELADO
    assert mostradas == urls
    assert _puerto_libre(_puerto_de(urls[0]))


@SIN_NAVEGADOR
def test_without_a_browser_the_manual_wait_times_out(resultado):
    urls: list[str] = []
    mostradas: list[str] = []

    with pytest.raises(ErrorAuth) as exc:
        iniciar_sesion_openrouter(
            abrir_navegador=_navegador_que_falla(urls, resultado),
            timeout=0.3,
            http_client=httpx.Client(transport=httpx.MockTransport(lambda r: pytest.fail("no exchange"))),
            al_sin_navegador=mostradas.append,
        )

    assert str(exc.value) == MENSAJE_TIMEOUT
    assert mostradas == urls
    assert _puerto_libre(_puerto_de(urls[0]))


def test_a_browser_returning_none_still_waits_for_the_callback():
    # Only an explicit False means "no browser"; other callables may return None.
    navegador = NavegadorFalso()
    abrir = lambda url: navegador(url) and None  # noqa: E731

    assert iniciar_sesion_openrouter(
        abrir_navegador=abrir, timeout=5, http_client=OpenRouterFalso(navegador).cliente()
    ) == CLAVE
    navegador.esperar()


def test_server_start_error_is_an_error_auth(monkeypatch):
    def sin_puerto(self):
        raise OSError("puerto ocupado")

    monkeypatch.setattr(socketserver.TCPServer, "server_bind", sin_puerto)
    abiertas: list[str] = []

    with pytest.raises(ErrorAuth) as exc:
        iniciar_sesion_openrouter(abrir_navegador=abiertas.append, timeout=1)

    assert str(exc.value) == "No pude preparar el inicio de sesión con OpenRouter: puerto ocupado"
    assert abiertas == []


def test_cancel_during_the_code_exchange_discards_the_key():
    cancelado = threading.Event()
    navegador = NavegadorFalso()
    openrouter = OpenRouterFalso(navegador)
    responder = openrouter.__call__

    def canjear_y_cancelar(request):
        respuesta = responder(request)
        cancelado.set()  # the user closes the dialog while the exchange runs
        return respuesta

    with pytest.raises(ErrorAuth) as exc:
        iniciar_sesion_openrouter(
            abrir_navegador=navegador,
            timeout=5,
            http_client=httpx.Client(transport=httpx.MockTransport(canjear_y_cancelar)),
            cancelado=cancelado,
        )
    navegador.esperar()

    assert str(exc.value) == MENSAJE_CANCELADO
    assert CLAVE not in str(exc.value)
    assert len(openrouter.pedidos) == 1
    assert _puerto_libre(navegador.puerto)


def test_the_callback_page_never_shows_the_code():
    correcto = NavegadorFalso()
    _iniciar(correcto)
    equivocado = NavegadorFalso(lambda p: {"code": CODIGO, "state": "otro-estado"})
    with pytest.raises(ErrorAuth):
        _iniciar(equivocado)

    for navegador in (correcto, equivocado):
        assert CODIGO not in navegador.respuesta.text


def test_the_server_listens_only_on_loopback(monkeypatch):
    direcciones = []
    enlazar = socketserver.TCPServer.server_bind

    def espiar(self):
        enlazar(self)
        direcciones.append(self.socket.getsockname()[0])

    monkeypatch.setattr(socketserver.TCPServer, "server_bind", espiar)
    navegador = NavegadorFalso()
    _iniciar(navegador)

    assert direcciones == ["127.0.0.1"]


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
