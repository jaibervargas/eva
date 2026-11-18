"""Sign in with ChatGPT (OAuth with PKCE and dynamic registration).

OpenAI's "Sign in with ChatGPT" for open-source local apps lets Eva use the
user's ChatGPT Plus/Pro plan. On the first sign-in Eva registers itself on the
fly (``client_id=dynamic_agent_client``) and OpenAI returns an issued client id
(``oaiapp_...``) in the callback, which later sign-ins and refreshes reuse.

The flow mirrors the OpenRouter login in ``app.auth``: a short-lived loopback
server on ``127.0.0.1`` receives the one-time code, Eva trades it (plus the
PKCE verifier) for tokens and validates the ID token against OpenAI's JWKS.

This module only obtains, refreshes and revokes tokens; saving them is the
caller's job. Codes, verifiers and tokens are never logged or shown.
"""

import json
import secrets
import threading
import time
import webbrowser
from collections.abc import Callable
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx
import jwt

from app.auth import ErrorAuth, _code_challenge, _pagina

__all__ = [
    "ErrorAuth",
    "ErrorSesionVencida",
    "ResultadoSesion",
    "Tokens",
    "cerrar_sesion",
    "iniciar_sesion_chatgpt",
    "renovar",
]

EMISOR = "https://auth.openai.com"
AUTHORIZE_URL = "https://auth.openai.com/api/accounts/authorize"
TOKEN_URL = "https://auth.openai.com/api/accounts/oauth/token"
DISCOVERY_URL = "https://auth.openai.com/.well-known/openid-configuration"
RESOURCE = "https://api.openai.com/v1"
SCOPES = "openid profile email offline_access resource.invoke chatgpt.tokens.use.direct"
CLIENTE_DINAMICO = "dynamic_agent_client"
NOMBRE_AGENTE = "Eva"

PUERTO_PREFERIDO = 1455
RUTA_CALLBACK = "/auth/callback"

TIMEOUT_LOGIN = 300.0
TIMEOUT_HTTP = 15.0
# How often the wait checks whether the UI cancelled the login.
_INTERVALO_ESPERA = 0.05
# Pause before each revocation attempt after the first (seconds).
_ESPERAS_REVOCACION = (0.5, 1.0)
_INTENTOS_REVOCACION = 3
# Clock skew tolerated when checking the ID token's expiry (seconds).
_MARGEN_RELOJ = 60
_ALGORITMOS_ID_TOKEN = ["RS256", "RS384", "RS512", "PS256", "PS384", "PS512", "ES256", "ES384"]

_CODIGOS_SESION_VENCIDA = frozenset(
    {
        "invalid_grant",
        "invalid_refresh_token",
        "token_expired",
        "refresh_token_expired",
        "refresh_token_invalidated",
        "refresh_token_reused",
    }
)

MENSAJE_LISTO = "Listo. Ya puedes volver a Eva."
MENSAJE_ENLACE_VIEJO = "Este enlace de inicio de sesión ya no sirve."
MENSAJE_NO_AUTORIZASTE = "No autorizaste a Eva en ChatGPT."
MENSAJE_NO_AUTORIZO = "ChatGPT no autorizó el inicio de sesión."
MENSAJE_REGISTRO = "ChatGPT no completó el registro de Eva. Inténtalo de nuevo."
MENSAJE_CODIGO_VENCIDO = "El código de ChatGPT ya no sirve. Inicia sesión de nuevo."
MENSAJE_RECHAZO = "ChatGPT rechazó el inicio de sesión: {detalle}"
MENSAJE_SIN_CONEXION = "No pude conectarme con ChatGPT. Revisa tu conexión a internet."
MENSAJE_IDENTIDAD = "No pude validar la identidad de tu cuenta de ChatGPT."
MENSAJE_OTRA_CUENTA = (
    "Iniciaste sesión con otra cuenta de ChatGPT. Elige esa cuenta o agrégala como nueva."
)
MENSAJE_SIN_NAVEGADOR = (
    "No pude abrir el navegador. Revisa que tengas uno instalado e inténtalo de nuevo."
)
MENSAJE_CANCELADO = "Se canceló el inicio de sesión con ChatGPT."
MENSAJE_TIMEOUT = "No se completó el inicio de sesión con ChatGPT a tiempo. Inténtalo de nuevo."
MENSAJE_SIN_SERVIDOR = "No pude preparar el inicio de sesión con ChatGPT: {detalle}"
MENSAJE_SESION_VENCIDA = "Tu sesión de ChatGPT venció. Vuelve a iniciar sesión en Ajustes."
MENSAJE_RENOVACION_RECHAZADA = "ChatGPT rechazó la renovación de tu sesión: {detalle}"
MENSAJE_NO_DISPONIBLE = "ChatGPT no está disponible en este momento. Inténtalo más tarde."


class ErrorSesionVencida(ErrorAuth):
    """The refresh token no longer works: the user must sign in again."""


@dataclass
class Tokens:
    """Tokens returned by a refresh. ``expira`` is in epoch seconds."""

    access_token: str = field(repr=False)
    refresh_token: str = field(repr=False)
    expira: int
    scopes: list[str]


@dataclass
class ResultadoSesion:
    """A completed sign-in. ``expira`` is the access token expiry in epoch seconds."""

    client_id: str
    email: str
    subject: str
    id_token: str = field(repr=False)
    access_token: str = field(repr=False)
    refresh_token: str = field(repr=False)
    expira: int
    scopes: list[str]


# --- loopback callback ------------------------------------------------------


class _Servidor(ThreadingHTTPServer):
    """Loopback server that receives a single ChatGPT callback."""

    # On Windows SO_REUSEADDR would let another program bind the same port and
    # steal the code.
    allow_reuse_address = False
    daemon_threads = True
    # Closing must not wait for a stalled browser connection (e.g. a preconnect).
    block_on_close = False

    def __init__(self, puerto: int, state: str, client_id: str | None) -> None:
        super().__init__(("127.0.0.1", puerto), _ManejadorCallback)
        self.state = state
        self.client_id_guardado = client_id
        self.codigo: str | None = None
        self.client_id: str | None = None
        self.error: str | None = None
        self.terminado = threading.Event()
        self._candado = threading.Lock()

    def registrar(self, consulta: dict[str, list[str]]) -> tuple[int, str]:
        """Record the first valid callback; return the status and text for the page.

        A callback without this attempt's ``state`` (a stale tab, another
        attempt) is answered and ignored: the sign-in keeps waiting.
        """
        state = consulta.get("state", [""])[0]
        if not secrets.compare_digest(state.encode(), self.state.encode()):
            return 400, MENSAJE_ENLACE_VIEJO
        with self._candado:
            if self.terminado.is_set():
                return 200, MENSAJE_NO_AUTORIZO
            codigo, client_id, error = self._evaluar(consulta)
            self.codigo, self.client_id, self.error = codigo, client_id, error
            self.terminado.set()
            return 200, error or MENSAJE_LISTO

    def _evaluar(self, consulta: dict[str, list[str]]) -> tuple[str | None, str | None, str | None]:
        """Outcome of a callback whose ``state`` already matched."""
        codigo = consulta.get("code", [""])[0]
        error = consulta.get("error", [""])[0]
        client_id = consulta.get("client_id", [""])[0]
        if error == "access_denied":
            return None, None, MENSAJE_NO_AUTORIZASTE
        if error or not codigo:
            return None, None, MENSAJE_NO_AUTORIZO
        if self.client_id_guardado is None:
            # New registration: the issued id is required, never the placeholder.
            if not client_id or client_id == CLIENTE_DINAMICO:
                return None, None, MENSAJE_REGISTRO
            return codigo, client_id, None
        if client_id and client_id != self.client_id_guardado:
            return None, None, MENSAJE_NO_AUTORIZO
        return codigo, self.client_id_guardado, None


class _ManejadorCallback(BaseHTTPRequestHandler):
    server: _Servidor
    # Read timeout so idle browser connections do not hang a handler thread.
    timeout = 10

    def do_GET(self) -> None:  # noqa: N802 (name required by http.server)
        partes = urlsplit(self.path)
        if partes.path != RUTA_CALLBACK:
            self._responder(404, "No encontrado.")
            return
        self._responder(*self.server.registrar(parse_qs(partes.query)))

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


def _abrir_servidor(state: str, client_id: str | None) -> _Servidor:
    """Bind the preferred port 1455, or any free port when it is busy."""
    try:
        return _Servidor(PUERTO_PREFERIDO, state, client_id)
    except OSError:
        pass
    try:
        return _Servidor(0, state, client_id)
    except OSError as exc:
        raise ErrorAuth(MENSAJE_SIN_SERVIDOR.format(detalle=str(exc) or type(exc).__name__)) from exc


def _esperar_callback(
    servidor: _Servidor, timeout: float, cancelado: threading.Event | None
) -> tuple[str, str]:
    limite = time.monotonic() + timeout
    while not servidor.terminado.wait(_INTERVALO_ESPERA):
        if cancelado is not None and cancelado.is_set():
            raise ErrorAuth(MENSAJE_CANCELADO)
        if time.monotonic() >= limite:
            raise ErrorAuth(MENSAJE_TIMEOUT)
    if servidor.codigo is None or servidor.client_id is None:
        raise ErrorAuth(servidor.error or MENSAJE_NO_AUTORIZO)
    return servidor.codigo, servidor.client_id


# --- HTTP helpers -----------------------------------------------------------


def _json(respuesta: httpx.Response) -> dict:
    try:
        datos = respuesta.json()
    except (json.JSONDecodeError, ValueError):
        return {}
    return datos if isinstance(datos, dict) else {}


def _codigo_error(datos: dict) -> str:
    """The OAuth error code, whether sent as a string or as ``{"code": ...}``."""
    error = datos.get("error")
    if isinstance(error, str):
        return error
    if isinstance(error, dict) and isinstance(error.get("code"), str):
        return error["code"]
    return datos.get("code") if isinstance(datos.get("code"), str) else ""


def _detalle(respuesta: httpx.Response, ocultar: tuple[str, ...]) -> str:
    """A short error detail from OpenAI, with any of our secrets masked."""
    datos = _json(respuesta)
    error = datos.get("error")
    detalle = ""
    if datos.get("error_description"):
        detalle = str(datos["error_description"])
    elif isinstance(error, dict) and error.get("message"):
        detalle = str(error["message"])
    elif isinstance(error, str) and error:
        detalle = error
    elif datos.get("message"):
        detalle = str(datos["message"])
    elif not datos and respuesta.text.strip():
        detalle = respuesta.text.strip()[:200]
    if not detalle:
        detalle = f"código {respuesta.status_code}"
    for secreto in ocultar:
        if secreto:
            detalle = detalle.replace(secreto, "***")
    return detalle


def _con_cliente(http_client: httpx.Client | None, accion):
    cliente = http_client or httpx.Client(timeout=TIMEOUT_HTTP, headers={"Accept": "application/json"})
    try:
        return accion(cliente)
    finally:
        if http_client is None:
            cliente.close()


def _scopes(datos: dict) -> list[str]:
    # RFC 6749 section 5.1: an omitted scope means the requested one was granted.
    scope = datos.get("scope")
    return scope.split() if isinstance(scope, str) else SCOPES.split()


def _expira(datos: dict) -> int:
    try:
        segundos = int(datos.get("expires_in", 3600))
    except (TypeError, ValueError):
        segundos = 3600
    return int(time.time()) + segundos


def _descubrir(cliente: httpx.Client) -> dict:
    """OpenAI's OpenID configuration (``jwks_uri``, ``revocation_endpoint``)."""
    respuesta = cliente.get(DISCOVERY_URL)
    respuesta.raise_for_status()
    datos = respuesta.json()
    if not isinstance(datos, dict):
        raise ValueError("discovery document is not an object")
    return datos


# --- sign-in ----------------------------------------------------------------


def _canjear(
    cliente: httpx.Client, codigo: str, verifier: str, client_id: str, redirect_uri: str
) -> dict:
    try:
        respuesta = cliente.post(
            TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "client_id": client_id,
                "code": codigo,
                "code_verifier": verifier,
                "redirect_uri": redirect_uri,
                "resource": RESOURCE,
            },
        )
    except httpx.HTTPError as exc:  # transport or decoding: OpenAI's answer never arrived whole
        raise ErrorAuth(MENSAJE_SIN_CONEXION) from exc
    if respuesta.status_code >= 400:
        datos = _json(respuesta)
        if _codigo_error(datos) == "invalid_grant":
            raise ErrorAuth(MENSAJE_CODIGO_VENCIDO)
        raise ErrorAuth(MENSAJE_RECHAZO.format(detalle=_detalle(respuesta, (codigo, verifier))))
    datos = _json(respuesta)
    for clave in ("access_token", "refresh_token", "id_token"):
        if not isinstance(datos.get(clave), str) or not datos[clave]:
            raise ErrorAuth(MENSAJE_RECHAZO.format(detalle=f"la respuesta no trae {clave}"))
    return datos


def _validar_id_token(cliente: httpx.Client, id_token: str, client_id: str, nonce: str) -> dict:
    """Verify the ID token's signature, issuer, audience, expiry and nonce.

    Only a problem with the token itself is an identity error; failing to
    fetch OpenAI's keys is a connection or availability problem.
    """
    try:
        jwks_uri = _descubrir(cliente).get("jwks_uri")
        if not isinstance(jwks_uri, str) or not jwks_uri:
            raise ErrorAuth(MENSAJE_NO_DISPONIBLE)
        respuesta = cliente.get(jwks_uri)
        respuesta.raise_for_status()
        claves = respuesta.json()
    except httpx.HTTPStatusError as exc:
        raise ErrorAuth(MENSAJE_NO_DISPONIBLE) from exc
    except httpx.HTTPError as exc:
        raise ErrorAuth(MENSAJE_SIN_CONEXION) from exc
    except ValueError as exc:  # not JSON: a broken answer, not a bad token
        raise ErrorAuth(MENSAJE_NO_DISPONIBLE) from exc
    try:
        juego = jwt.PyJWKSet.from_dict(claves)
        kid = jwt.get_unverified_header(id_token).get("kid")
        candidatas = [k for k in juego.keys if kid is None or k.key_id == kid]
        if len(candidatas) != 1:
            raise ValueError("no unique signing key for the ID token")
        reclamos = jwt.decode(
            id_token,
            candidatas[0].key,
            algorithms=_ALGORITMOS_ID_TOKEN,
            audience=client_id,
            issuer=EMISOR,
            leeway=_MARGEN_RELOJ,
            options={"require": ["exp", "iss", "aud", "sub", "nonce"]},
        )
    except (jwt.PyJWTError, ValueError, TypeError, KeyError, AttributeError) as exc:
        raise ErrorAuth(MENSAJE_IDENTIDAD) from exc
    if not isinstance(reclamos.get("nonce"), str) or not secrets.compare_digest(
        reclamos["nonce"].encode(), nonce.encode()
    ):
        raise ErrorAuth(MENSAJE_IDENTIDAD)
    if not isinstance(reclamos.get("sub"), str) or not reclamos["sub"]:
        raise ErrorAuth(MENSAJE_IDENTIDAD)
    return reclamos


def _correo(reclamos: dict) -> str:
    correo = reclamos.get("email")
    if not isinstance(correo, str):
        perfil = reclamos.get("https://api.openai.com/profile")
        correo = perfil.get("email") if isinstance(perfil, dict) else None
    return correo if isinstance(correo, str) else ""


def iniciar_sesion_chatgpt(
    host_id: str,
    client_id: str | None = None,
    id_token_hint: str | None = None,
    login_hint: str | None = None,
    subject_esperado: str | None = None,
    pedir_consentimiento: bool = False,
    abrir_navegador: Callable[[str], object] = webbrowser.open,
    al_sin_navegador: Callable[[str], None] | None = None,
    timeout: float = TIMEOUT_LOGIN,
    cancelado: threading.Event | None = None,
    http_client: httpx.Client | None = None,
    enviar_host_id: bool = False,
) -> ResultadoSesion:
    """Sign in with the user's ChatGPT account and return its tokens.

    Without ``client_id`` Eva registers itself as a new agent on ``host_id``;
    with it, the saved registration is reauthorized (``id_token_hint`` and
    ``login_hint`` preselect the account, ``subject_esperado`` rejects a
    different one). Blocks until the browser comes back, ``timeout`` seconds
    pass or ``cancelado`` is set (run it off the UI loop); a cancel at any
    point before returning discards the tokens. When ``abrir_navegador``
    returns ``False`` or raises, ``al_sin_navegador`` receives the URL so the
    user can open it by hand; without it the login fails at once. Raises
    ``ErrorAuth`` with a message for the user on any failure. Saves nothing.
    """
    # 32 random bytes (43 characters), the same verifier size OpenAI's devkit uses.
    verifier = secrets.token_urlsafe(32)
    state = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(32)
    servidor = _abrir_servidor(state, client_id)
    hilo = threading.Thread(
        target=servidor.serve_forever, kwargs={"poll_interval": _INTERVALO_ESPERA}, daemon=True
    )
    hilo.start()
    try:
        # The address the server binds: "localhost" may resolve to IPv6 first.
        redirect_uri = f"http://127.0.0.1:{servidor.server_address[1]}{RUTA_CALLBACK}"
        parametros = {"client_id": client_id or CLIENTE_DINAMICO}
        if client_id is None:
            parametros["agent_name_hint"] = NOMBRE_AGENTE
        # Off by default, like OpenAI's devkit (`sendHostId`): in a live test the
        # code exchange failed with invalid_grant whenever it was sent.
        if enviar_host_id:
            parametros["ext_agent_host_id"] = host_id
        if client_id is not None and id_token_hint:
            parametros["id_token_hint"] = id_token_hint
        if client_id is not None and login_hint:
            parametros["login_hint"] = login_hint
        parametros.update(
            {
                "response_type": "code",
                "redirect_uri": redirect_uri,
                "scope": SCOPES,
                "resource": RESOURCE,
                "state": state,
                "nonce": nonce,
                "code_challenge_method": "S256",
                "code_challenge": _code_challenge(verifier),
            }
        )
        if pedir_consentimiento:
            parametros["prompt"] = "consent"
        url = AUTHORIZE_URL + "?" + urlencode(parametros)
        try:
            abierto = abrir_navegador(url) is not False
        except Exception:
            abierto = False
        if not abierto:
            if al_sin_navegador is None:
                raise ErrorAuth(MENSAJE_SIN_NAVEGADOR)
            al_sin_navegador(url)  # the server keeps waiting for the manual path
        codigo, client_emitido = _esperar_callback(servidor, timeout, cancelado)
    finally:
        servidor.shutdown()
        servidor.server_close()
        hilo.join(5)

    def completar(cliente: httpx.Client) -> ResultadoSesion:
        datos = _canjear(cliente, codigo, verifier, client_emitido, redirect_uri)
        try:
            if cancelado is not None and cancelado.is_set():
                raise ErrorAuth(MENSAJE_CANCELADO)
            reclamos = _validar_id_token(cliente, datos["id_token"], client_emitido, nonce)
            if subject_esperado is not None and reclamos["sub"] != subject_esperado:
                raise ErrorAuth(MENSAJE_OTRA_CUENTA)
            resultado = ResultadoSesion(
                client_id=client_emitido,
                email=_correo(reclamos),
                subject=reclamos["sub"],
                id_token=datos["id_token"],
                access_token=datos["access_token"],
                refresh_token=datos["refresh_token"],
                expira=_expira(datos),
                scopes=_scopes(datos),
            )
            if cancelado is not None and cancelado.is_set():
                raise ErrorAuth(MENSAJE_CANCELADO)
            return resultado
        except Exception:
            # The tokens are discarded: revoke them so they do not stay valid.
            cerrar_sesion(client_emitido, datos["refresh_token"], http_client=cliente)
            raise

    resultado = _con_cliente(http_client, completar)
    if cancelado is not None and cancelado.is_set():
        # Cancelled while the HTTP client closed: discard and revoke the tokens.
        cerrar_sesion(client_emitido, resultado.refresh_token, http_client=http_client)
        raise ErrorAuth(MENSAJE_CANCELADO)
    return resultado


# --- refresh and sign-out ---------------------------------------------------


def renovar(client_id: str, refresh_token: str, http_client: httpx.Client | None = None) -> Tokens:
    """Trade a refresh token for new (rotated) tokens.

    Raises ``ErrorSesionVencida`` when the refresh token no longer works (the
    caller should forget the tokens) and ``ErrorAuth`` for network or server
    problems (the caller should keep them and try later).
    """

    def pedir(cliente: httpx.Client) -> httpx.Response:
        return cliente.post(
            TOKEN_URL,
            data={
                "grant_type": "refresh_token",
                "client_id": client_id,
                "refresh_token": refresh_token,
                "resource": RESOURCE,
            },
        )

    try:
        respuesta = _con_cliente(http_client, pedir)
    except httpx.HTTPError as exc:  # transport or decoding: OpenAI's answer never arrived whole
        raise ErrorAuth(MENSAJE_SIN_CONEXION) from exc
    datos = _json(respuesta)
    if respuesta.status_code >= 500:
        raise ErrorAuth(MENSAJE_NO_DISPONIBLE)
    if respuesta.status_code >= 400:
        if _codigo_error(datos) in _CODIGOS_SESION_VENCIDA:
            raise ErrorSesionVencida(MENSAJE_SESION_VENCIDA)
        raise ErrorAuth(
            MENSAJE_RENOVACION_RECHAZADA.format(detalle=_detalle(respuesta, (refresh_token,)))
        )
    access = datos.get("access_token")
    if not isinstance(access, str) or not access:
        raise ErrorAuth(MENSAJE_RENOVACION_RECHAZADA.format(detalle="la respuesta no trae access_token"))
    nuevo = datos.get("refresh_token")
    return Tokens(
        access_token=access,
        refresh_token=nuevo if isinstance(nuevo, str) and nuevo else refresh_token,
        expira=_expira(datos),
        scopes=_scopes(datos),
    )


def cerrar_sesion(
    client_id: str, refresh_token: str, http_client: httpx.Client | None = None
) -> bool:
    """Revoke the refresh token at OpenAI. True only when OpenAI confirms it.

    Network problems and server errors are retried a few times; nothing here
    raises for them, so the caller can always forget the local tokens.
    """

    def revocar(cliente: httpx.Client) -> bool:
        endpoint: str | None = None
        for intento in range(_INTENTOS_REVOCACION):
            if intento:
                time.sleep(_ESPERAS_REVOCACION[min(intento - 1, len(_ESPERAS_REVOCACION) - 1)])
            try:
                if endpoint is None:
                    endpoint = _descubrir(cliente).get("revocation_endpoint")
                    if not isinstance(endpoint, str) or not endpoint:
                        return False
                respuesta = cliente.post(
                    endpoint,
                    data={
                        "token": refresh_token,
                        "token_type_hint": "refresh_token",
                        "client_id": client_id,
                    },
                )
            except httpx.TransportError:
                continue
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code >= 500:
                    continue
                return False
            except (httpx.HTTPError, ValueError, TypeError):
                return False
            if respuesta.status_code == 200:
                return True
            if respuesta.status_code < 500:
                return False
        return False

    try:
        return _con_cliente(http_client, revocar)
    except Exception:  # sign-out must never fail on the caller
        return False
