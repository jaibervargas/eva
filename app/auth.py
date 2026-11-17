"""Sign in with an OpenRouter account (OAuth with PKCE).

OpenRouter needs no app registration: Eva opens the browser on OpenRouter's
auth page, the user signs in and sets their own spending limit, and OpenRouter
redirects back to a short-lived server on this computer with a one-time code.
Eva trades that code (plus the PKCE verifier only Eva knows) for an API key
tied to the user's account.

This module only obtains the key; saving it is the caller's job (``secretos``).
The verifier, the code and the key are never logged.
"""

import base64
import hashlib
import json
import secrets
import threading
import time
import webbrowser
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx

AUTH_URL = "https://openrouter.ai/auth"
KEYS_URL = "https://openrouter.ai/api/v1/auth/keys"

TIMEOUT_LOGIN = 300.0
TIMEOUT_CANJE = 15.0
# How often the wait checks whether the UI cancelled the login.
_INTERVALO_ESPERA = 0.05

MENSAJE_LISTO = "Listo. Ya puedes volver a Eva."
MENSAJE_NO_AUTORIZO = "OpenRouter no autorizó el inicio de sesión."
MENSAJE_TIMEOUT = "No se completó el inicio de sesión con OpenRouter a tiempo. Inténtalo de nuevo."
MENSAJE_CANCELADO = "Se canceló el inicio de sesión con OpenRouter."
MENSAJE_SIN_CONEXION = "No pude conectarme con OpenRouter. Revisa tu conexión a internet."
MENSAJE_RECHAZO = "OpenRouter rechazó el inicio de sesión: {detalle}"


class ErrorAuth(Exception):
    """A login failure, with a message meant to be shown to the user."""


def _code_challenge(verifier: str) -> str:
    """S256 PKCE challenge: base64url of the SHA-256 digest, without padding."""
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def _pagina(mensaje: str) -> bytes:
    return (
        '<!doctype html><html lang="es"><head><meta charset="utf-8"><title>Eva</title></head>'
        f'<body style="font-family: sans-serif; text-align: center; margin-top: 4em">'
        f"<p>{mensaje}</p></body></html>"
    ).encode("utf-8")


class _Servidor(ThreadingHTTPServer):
    """Loopback server that receives a single OpenRouter callback."""

    # On Windows SO_REUSEADDR would let another program bind the same port and
    # steal the code; each login takes a fresh free port anyway.
    allow_reuse_address = False
    daemon_threads = True
    # Closing must not wait for a stalled browser connection (e.g. a preconnect).
    block_on_close = False

    def __init__(self, state: str) -> None:
        super().__init__(("127.0.0.1", 0), _ManejadorCallback)
        self.state = state
        self.codigo: str | None = None
        self.error: str | None = None
        self.terminado = threading.Event()
        self._candado = threading.Lock()

    def registrar(self, consulta: dict[str, list[str]]) -> bool:
        """Record the first callback; True when it brings a valid code."""
        state = consulta.get("state", [""])[0]
        codigo = consulta.get("code", [""])[0]
        with self._candado:
            if self.terminado.is_set():
                return False
            if codigo and secrets.compare_digest(state.encode(), self.state.encode()):
                self.codigo = codigo
            else:
                self.error = MENSAJE_NO_AUTORIZO
            self.terminado.set()
            return self.codigo is not None


class _ManejadorCallback(BaseHTTPRequestHandler):
    server: _Servidor
    # Read timeout so idle browser connections do not hang a handler thread.
    timeout = 10

    def do_GET(self) -> None:  # noqa: N802 (name required by http.server)
        partes = urlsplit(self.path)
        if partes.path != "/callback":
            self._responder(404, "No encontrado.")
            return
        valido = self.server.registrar(parse_qs(partes.query))
        self._responder(200, MENSAJE_LISTO if valido else MENSAJE_NO_AUTORIZO)

    def _responder(self, estado: int, mensaje: str) -> None:
        cuerpo = _pagina(mensaje)
        self.send_response(estado)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(cuerpo)))
        self.end_headers()
        self.wfile.write(cuerpo)

    def log_message(self, format: str, *args) -> None:  # noqa: A002
        # The default logs the request line, which carries the one-time code.
        pass


def _esperar_codigo(
    servidor: _Servidor, timeout: float, cancelado: threading.Event | None
) -> str:
    limite = time.monotonic() + timeout
    while not servidor.terminado.wait(_INTERVALO_ESPERA):
        if cancelado is not None and cancelado.is_set():
            raise ErrorAuth(MENSAJE_CANCELADO)
        if time.monotonic() >= limite:
            raise ErrorAuth(MENSAJE_TIMEOUT)
    if servidor.codigo is None:
        raise ErrorAuth(servidor.error or MENSAJE_NO_AUTORIZO)
    return servidor.codigo


def _detalle(respuesta: httpx.Response) -> str:
    """The error message OpenRouter sent, or the HTTP status if there is none."""
    try:
        datos = respuesta.json()
    except (json.JSONDecodeError, ValueError):
        datos = None
    if isinstance(datos, dict):
        error = datos.get("error")
        if isinstance(error, dict) and error.get("message"):
            return str(error["message"])
        if isinstance(error, str) and error:
            return error
        if datos.get("message"):
            return str(datos["message"])
    texto = respuesta.text.strip()
    return texto[:200] if texto else f"código {respuesta.status_code}"


def _canjear(codigo: str, verifier: str, http_client: httpx.Client | None) -> str:
    cliente = http_client or httpx.Client(timeout=TIMEOUT_CANJE)
    try:
        respuesta = cliente.post(
            KEYS_URL,
            json={"code": codigo, "code_verifier": verifier, "code_challenge_method": "S256"},
        )
    except httpx.TransportError as exc:
        raise ErrorAuth(MENSAJE_SIN_CONEXION) from exc
    finally:
        if http_client is None:
            cliente.close()

    if respuesta.status_code >= 400:
        raise ErrorAuth(MENSAJE_RECHAZO.format(detalle=_detalle(respuesta)))
    try:
        clave = respuesta.json().get("key")
    except (json.JSONDecodeError, ValueError, AttributeError):
        clave = None
    if not isinstance(clave, str) or not clave:
        raise ErrorAuth(MENSAJE_RECHAZO.format(detalle="la respuesta no trae una clave"))
    return clave


def iniciar_sesion_openrouter(
    abrir_navegador: Callable[[str], object] = webbrowser.open,
    timeout: float = TIMEOUT_LOGIN,
    http_client: httpx.Client | None = None,
    cancelado: threading.Event | None = None,
) -> str:
    """Sign in with the user's OpenRouter account and return its API key.

    Blocks until the browser comes back, ``timeout`` seconds pass or
    ``cancelado`` is set (run it off the UI loop). Raises ``ErrorAuth`` with a
    message for the user on any failure. Does not save the key.
    """
    verifier = secrets.token_urlsafe(64)
    state = secrets.token_urlsafe(32)
    servidor = _Servidor(state)
    hilo = threading.Thread(
        target=servidor.serve_forever, kwargs={"poll_interval": _INTERVALO_ESPERA}, daemon=True
    )
    hilo.start()
    try:
        puerto = servidor.server_address[1]
        url = AUTH_URL + "?" + urlencode(
            {
                "callback_url": f"http://localhost:{puerto}/callback",
                "code_challenge": _code_challenge(verifier),
                "code_challenge_method": "S256",
                "state": state,
            }
        )
        abrir_navegador(url)
        codigo = _esperar_codigo(servidor, timeout, cancelado)
    finally:
        servidor.shutdown()
        servidor.server_close()
        hilo.join(5)
    return _canjear(codigo, verifier, http_client)
