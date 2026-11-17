"""Tests for "Iniciar sesión con ChatGPT" (OAuth with PKCE, dynamic registration).

A fake browser reads the authorize URL Eva opens, checks every parameter and
calls the loopback callback with a real HTTP request. Discovery, JWKS, token
and revocation endpoints are an httpx.MockTransport; the ID tokens are signed
here with a generated RSA key whose public JWK is served as the JWKS. No test
touches the real network or OpenAI.
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
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

import app.auth_chatgpt as auth_chatgpt
from app.auth_chatgpt import (
    ErrorAuth,
    ErrorSesionVencida,
    cerrar_sesion,
    iniciar_sesion_chatgpt,
    renovar,
)

HOST_ID = "urn:uuid:7c9e6679-7425-40de-944b-e07fc1f90ae7"
CLIENTE_EMITIDO = "oaiapp_eva_123"
CODIGO = "codigo-de-un-solo-uso-chatgpt"
ACCESS = "at-secreto-de-prueba"
REFRESH = "rt-secreto-de-prueba"
REFRESH_NUEVO = "rt-secreto-rotado"
SUJETO = "user-abc123"
CORREO = "ana@example.com"
ID_TOKEN_PREVIO = "id-token-previo-de-prueba"

ISS = "https://auth.openai.com"
AUTHORIZE = ("https", "auth.openai.com", "/api/accounts/authorize")
DISCOVERY_URL = "https://auth.openai.com/.well-known/openid-configuration"
JWKS_URL = "https://auth.openai.com/.well-known/jwks.json"
TOKEN_URL = "https://auth.openai.com/api/accounts/oauth/token"
REVOKE_URL = "https://auth.openai.com/api/accounts/oauth/revoke"
RESOURCE = "https://api.openai.com/v1"
SCOPES = "openid profile email offline_access resource.invoke chatgpt.tokens.use.direct"

MENSAJE_NO_AUTORIZASTE = "No autorizaste a Eva en ChatGPT."
MENSAJE_NO_AUTORIZO = "ChatGPT no autorizó el inicio de sesión."
MENSAJE_REGISTRO = "ChatGPT no completó el registro de Eva. Inténtalo de nuevo."
MENSAJE_CODIGO = "El código de ChatGPT ya no sirve. Inicia sesión de nuevo."
MENSAJE_SIN_CONEXION = "No pude conectarme con ChatGPT. Revisa tu conexión a internet."
MENSAJE_IDENTIDAD = "No pude validar la identidad de tu cuenta de ChatGPT."
MENSAJE_OTRA_CUENTA = "Iniciaste sesión con otra cuenta de ChatGPT. Elige esa cuenta o agrégala como nueva."
MENSAJE_SIN_NAVEGADOR = "No pude abrir el navegador. Revisa que tengas uno instalado e inténtalo de nuevo."
MENSAJE_CANCELADO = "Se canceló el inicio de sesión con ChatGPT."
MENSAJE_TIMEOUT = "No se completó el inicio de sesión con ChatGPT a tiempo. Inténtalo de nuevo."
MENSAJE_VENCIDA = "Tu sesión de ChatGPT venció. Vuelve a iniciar sesión en Ajustes."
MENSAJE_LISTO = "Listo. Ya puedes volver a Eva."
MENSAJE_ENLACE_VIEJO = "Este enlace de inicio de sesión ya no sirve."
MENSAJE_NO_DISPONIBLE = "ChatGPT no está disponible en este momento. Inténtalo más tarde."

CLAVE_RSA = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTRA_CLAVE_RSA = rsa.generate_private_key(public_exponent=65537, key_size=2048)
KID = "clave-de-prueba"


def _jwk_publico() -> dict:
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(CLAVE_RSA.public_key()))
    jwk.update({"kid": KID, "alg": "RS256", "use": "sig"})
    return jwk


@dataclass
class Pagina:
    status_code: int
    content_type: str
    text: str


def _s256(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def _consulta_nueva(p):
    return {"code": CODIGO, "state": p["state"], "client_id": CLIENTE_EMITIDO}


class NavegadorFalso:
    """Plays the user's browser: records the authorize URL and hits the callback.

    ``consulta`` builds the callback query from the authorize parameters; None
    means the user never comes back. Assertion errors raised while reading the
    URL are re-raised by ``esperar`` so they are not mistaken for "no browser".
    """

    def __init__(self, consulta=_consulta_nueva):
        self.consulta = consulta
        self.params: dict[str, str] = {}
        self.repetidos: list[str] = []
        self.puerto: int | None = None
        self.respuesta: Pagina | None = None
        self.hilo: threading.Thread | None = None
        self.fallo: BaseException | None = None

    def __call__(self, url: str) -> bool:
        try:
            partes = urlsplit(url)
            assert (partes.scheme, partes.netloc, partes.path) == AUTHORIZE
            crudos = parse_qs(partes.query)
            self.repetidos = [k for k, v in crudos.items() if len(v) != 1]
            self.params = {k: v[0] for k, v in crudos.items()}
            callback = urlsplit(self.params["redirect_uri"])
            assert (callback.scheme, callback.hostname, callback.path) == (
                "http",
                "127.0.0.1",
                "/auth/callback",
            )
            self.puerto = callback.port
        except BaseException as exc:  # noqa: BLE001 (re-raised in esperar)
            self.fallo = exc
            raise
        if self.consulta is not None:
            destino = f"{self.params['redirect_uri']}?{urlencode(self.consulta(self.params))}"
            self.hilo = threading.Thread(target=self._visitar, args=(destino,), daemon=True)
            self.hilo.start()
        return True

    def _visitar(self, destino: str) -> None:
        self.respuesta = _pedir(destino)

    def esperar(self) -> None:
        if self.hilo is not None:
            self.hilo.join(5)
        if self.fallo is not None:
            raise self.fallo


def _pedir(destino: str) -> Pagina:
    # Plain http.client (no logging): the request line carries the code.
    partes = urlsplit(destino)
    conexion = http.client.HTTPConnection(partes.hostname, partes.port, timeout=5)
    try:
        conexion.request("GET", f"{partes.path}?{partes.query}")
        r = conexion.getresponse()
        return Pagina(r.status, r.getheader("Content-Type", ""), r.read().decode())
    finally:
        conexion.close()


class NavegadorConEnlaceViejo(NavegadorFalso):
    """A stale tab hits the callback with ``viejo`` before the real redirect."""

    def __init__(self, viejo, consulta=_consulta_nueva):
        super().__init__(consulta)
        self.viejo = viejo
        self.vieja: Pagina | None = None

    def _visitar(self, destino: str) -> None:
        self.vieja = _pedir(f"{self.params['redirect_uri']}?{urlencode(self.viejo(self.params))}")
        super()._visitar(destino)


def _verificar_autorizacion(
    params: dict[str, str],
    *,
    nuevo: bool,
    id_token_hint: str | None = None,
    login_hint: str | None = None,
    consentimiento: bool = False,
) -> None:
    esperados = {
        "client_id",
        "ext_agent_host_id",
        "response_type",
        "redirect_uri",
        "scope",
        "resource",
        "state",
        "nonce",
        "code_challenge_method",
        "code_challenge",
    }
    if nuevo:
        esperados.add("agent_name_hint")
    if id_token_hint:
        esperados.add("id_token_hint")
    if login_hint:
        esperados.add("login_hint")
    if consentimiento:
        esperados.add("prompt")
    assert set(params) == esperados
    assert params["client_id"] == ("dynamic_agent_client" if nuevo else CLIENTE_EMITIDO)
    if nuevo:
        assert params["agent_name_hint"] == "Eva"
    assert params["ext_agent_host_id"] == HOST_ID
    if id_token_hint:
        assert params["id_token_hint"] == id_token_hint
    if login_hint:
        assert params["login_hint"] == login_hint
    if consentimiento:
        assert params["prompt"] == "consent"
    assert params["response_type"] == "code"
    assert params["scope"] == SCOPES
    assert params["resource"] == RESOURCE
    assert len(params["state"]) >= 32
    assert len(params["nonce"]) >= 32
    assert params["code_challenge_method"] == "S256"
    assert len(params["code_challenge"]) == 43
    assert "=" not in params["code_challenge"]


def _id_token(reclamos: dict, clave=CLAVE_RSA, kid=KID) -> str:
    return jwt.encode(reclamos, clave, algorithm="RS256", headers={"kid": kid})


class OpenAIFalso:
    """Stands in for OpenAI's discovery, JWKS, token and revocation endpoints."""

    def __init__(
        self,
        navegador: NavegadorFalso | None = None,
        *,
        token=None,
        reclamos=None,
        firmar_con=CLAVE_RSA,
        cliente_esperado=CLIENTE_EMITIDO,
        scope=SCOPES,
        revocar=None,
        refrescar=None,
        descubrimiento=None,
        claves=None,
    ):
        self.navegador = navegador
        self.descubrimiento = descubrimiento
        self.claves = claves
        self.token = token
        self.reclamos = reclamos or (lambda r: r)
        self.firmar_con = firmar_con
        self.cliente_esperado = cliente_esperado
        self.scope = scope
        self.revocar = revocar
        self.refrescar = refrescar
        self.pedidos: list[httpx.Request] = []
        self.canjes: list[dict[str, str]] = []
        self.revocaciones: list[dict[str, str]] = []
        self.refrescos: list[dict[str, str]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.pedidos.append(request)
        url = str(request.url)
        assert "authorization" not in request.headers
        if request.method == "GET" and url == DISCOVERY_URL and self.descubrimiento:
            return self.descubrimiento(request)
        if request.method == "GET" and url == JWKS_URL and self.claves:
            return self.claves(request)
        if request.method == "GET" and url == DISCOVERY_URL:
            return httpx.Response(
                200,
                json={
                    "issuer": ISS,
                    "authorization_endpoint": "https://auth.openai.com/api/accounts/authorize",
                    "token_endpoint": TOKEN_URL,
                    "jwks_uri": JWKS_URL,
                    "revocation_endpoint": REVOKE_URL,
                },
            )
        if request.method == "GET" and url == JWKS_URL:
            return httpx.Response(200, json={"keys": [_jwk_publico()]})
        assert request.method == "POST"
        assert request.headers["content-type"].startswith("application/x-www-form-urlencoded")
        cuerpo = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
        if url == REVOKE_URL:
            self.revocaciones.append(cuerpo)
            return self.revocar(request) if self.revocar else httpx.Response(200)
        assert url == TOKEN_URL
        if cuerpo.get("grant_type") == "refresh_token":
            self.refrescos.append(cuerpo)
            if self.refrescar:
                return self.refrescar(request)
            return httpx.Response(
                200,
                json={
                    "access_token": ACCESS,
                    "refresh_token": REFRESH_NUEVO,
                    "expires_in": 3600,
                    "token_type": "Bearer",
                    "scope": self.scope,
                },
            )
        self.canjes.append(cuerpo)
        params = self.navegador.params
        assert set(cuerpo) == {
            "grant_type",
            "client_id",
            "code",
            "code_verifier",
            "redirect_uri",
            "resource",
        }
        assert cuerpo["grant_type"] == "authorization_code"
        assert cuerpo["client_id"] == self.cliente_esperado
        assert cuerpo["code"] == CODIGO
        assert cuerpo["redirect_uri"] == params["redirect_uri"]
        assert cuerpo["resource"] == RESOURCE
        assert 43 <= len(cuerpo["code_verifier"]) <= 128
        assert _s256(cuerpo["code_verifier"]) == params["code_challenge"]
        if self.token is not None:
            return self.token(request)
        ahora = int(time.time())
        reclamos = self.reclamos(
            {
                "iss": ISS,
                "aud": self.cliente_esperado,
                "sub": SUJETO,
                "email": CORREO,
                "iat": ahora,
                "exp": ahora + 3600,
                "nonce": params["nonce"],
            }
        )
        datos = {
            "access_token": ACCESS,
            "refresh_token": REFRESH,
            "id_token": _id_token(reclamos, self.firmar_con),
            "expires_in": 3600,
            "token_type": "Bearer",
        }
        if self.scope is not None:
            datos["scope"] = self.scope
        return httpx.Response(200, json=datos)

    def cliente(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self))


def _puerto_libre(puerto: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", puerto))
        except OSError:
            return False
    return True


def _iniciar(navegador, openai=None, **kwargs):
    openai = openai or OpenAIFalso(navegador)
    kwargs.setdefault("timeout", 5)
    try:
        return iniciar_sesion_chatgpt(
            HOST_ID, abrir_navegador=navegador, http_client=openai.cliente(), **kwargs
        )
    finally:
        navegador.esperar()


def _sin_canje(request):
    pytest.fail(f"unexpected request {request.method} {request.url}")


# --- iniciar_sesion_chatgpt -------------------------------------------------


def test_new_registration_returns_the_issued_client_and_the_tokens():
    navegador = NavegadorFalso()
    openai = OpenAIFalso(navegador)
    antes = int(time.time())

    resultado = _iniciar(navegador, openai)

    _verificar_autorizacion(navegador.params, nuevo=True)
    assert navegador.repetidos == []
    assert resultado.client_id == CLIENTE_EMITIDO
    assert resultado.email == CORREO
    assert resultado.subject == SUJETO
    assert resultado.access_token == ACCESS
    assert resultado.refresh_token == REFRESH
    assert jwt.decode(resultado.id_token, options={"verify_signature": False})["sub"] == SUJETO
    assert antes + 3600 <= resultado.expira <= int(time.time()) + 3600
    assert list(resultado.scopes) == SCOPES.split()
    assert len(openai.canjes) == 1
    assert navegador.respuesta.status_code == 200
    assert "text/html" in navegador.respuesta.content_type
    assert MENSAJE_LISTO in navegador.respuesta.text
    assert _puerto_libre(navegador.puerto)


def test_the_result_repr_hides_the_tokens():
    navegador = NavegadorFalso()
    resultado = _iniciar(navegador)

    texto = repr(resultado)
    for secreto in (ACCESS, REFRESH, resultado.id_token):
        assert secreto not in texto


def test_reauthorization_sends_the_saved_client_and_hints_and_keeps_the_client():
    navegador = NavegadorFalso()
    resultado = _iniciar(
        navegador,
        client_id=CLIENTE_EMITIDO,
        id_token_hint=ID_TOKEN_PREVIO,
        login_hint=CORREO,
        subject_esperado=SUJETO,
    )

    _verificar_autorizacion(
        navegador.params, nuevo=False, id_token_hint=ID_TOKEN_PREVIO, login_hint=CORREO
    )
    assert resultado.client_id == CLIENTE_EMITIDO
    assert resultado.subject == SUJETO


def test_reauthorization_without_client_in_the_callback_keeps_the_saved_one():
    navegador = NavegadorFalso(lambda p: {"code": CODIGO, "state": p["state"]})

    resultado = _iniciar(navegador, client_id=CLIENTE_EMITIDO)

    _verificar_autorizacion(navegador.params, nuevo=False)
    assert resultado.client_id == CLIENTE_EMITIDO


def test_consent_is_requested_only_when_asked():
    navegador = NavegadorFalso()
    _iniciar(navegador, client_id=CLIENTE_EMITIDO, pedir_consentimiento=True)

    _verificar_autorizacion(navegador.params, nuevo=False, consentimiento=True)


def test_reauthorization_rejects_a_different_client_in_the_callback():
    navegador = NavegadorFalso(
        lambda p: {"code": CODIGO, "state": p["state"], "client_id": "oaiapp_otro"}
    )
    openai = OpenAIFalso(navegador)

    with pytest.raises(ErrorAuth) as exc:
        _iniciar(navegador, openai, client_id=CLIENTE_EMITIDO)

    assert str(exc.value) == MENSAJE_NO_AUTORIZO
    assert openai.pedidos == []
    assert MENSAJE_NO_AUTORIZO in navegador.respuesta.text
    assert _puerto_libre(navegador.puerto)


def test_access_denied_is_reported_without_exchanging():
    navegador = NavegadorFalso(lambda p: {"error": "access_denied", "state": p["state"]})
    openai = OpenAIFalso(navegador)

    with pytest.raises(ErrorAuth) as exc:
        _iniciar(navegador, openai)

    assert str(exc.value) == MENSAJE_NO_AUTORIZASTE
    assert openai.pedidos == []
    assert MENSAJE_NO_AUTORIZASTE in navegador.respuesta.text
    assert MENSAJE_LISTO not in navegador.respuesta.text
    assert _puerto_libre(navegador.puerto)


def test_other_oauth_error_is_not_authorized():
    navegador = NavegadorFalso(lambda p: {"error": "server_error", "state": p["state"]})
    openai = OpenAIFalso(navegador)

    with pytest.raises(ErrorAuth) as exc:
        _iniciar(navegador, openai)

    assert str(exc.value) == MENSAJE_NO_AUTORIZO
    assert openai.pedidos == []
    assert _puerto_libre(navegador.puerto)


@pytest.mark.parametrize(
    "extra", [{}, {"client_id": "dynamic_agent_client"}], ids=["sin-client-id", "placeholder"]
)
def test_new_registration_without_an_issued_client_fails(extra):
    navegador = NavegadorFalso(lambda p: {"code": CODIGO, "state": p["state"], **extra})
    openai = OpenAIFalso(navegador)

    with pytest.raises(ErrorAuth) as exc:
        _iniciar(navegador, openai)

    assert str(exc.value) == MENSAJE_REGISTRO
    assert openai.pedidos == []
    assert MENSAJE_REGISTRO in navegador.respuesta.text
    assert _puerto_libre(navegador.puerto)


def test_a_wrong_state_alone_is_ignored_until_the_timeout():
    navegador = NavegadorFalso(
        lambda p: {"code": CODIGO, "state": "otro-estado", "client_id": CLIENTE_EMITIDO}
    )
    openai = OpenAIFalso(navegador)

    with pytest.raises(ErrorAuth) as exc:
        _iniciar(navegador, openai, timeout=1)

    assert str(exc.value) == MENSAJE_TIMEOUT
    assert openai.pedidos == []
    assert navegador.respuesta.status_code == 400
    assert MENSAJE_ENLACE_VIEJO in navegador.respuesta.text
    assert MENSAJE_LISTO not in navegador.respuesta.text
    assert _puerto_libre(navegador.puerto)


@pytest.mark.parametrize(
    "viejo",
    [
        lambda p: {"code": "codigo-viejo", "state": "estado-viejo", "client_id": CLIENTE_EMITIDO},
        lambda p: {"code": "codigo-viejo", "client_id": CLIENTE_EMITIDO},
        lambda p: {"error": "access_denied", "state": "estado-viejo"},
        lambda p: {"error": "access_denied"},
    ],
    ids=["state-equivocado", "sin-state", "denegado-state-equivocado", "denegado-sin-state"],
)
def test_a_stale_callback_is_ignored_and_the_valid_one_completes(viejo):
    navegador = NavegadorConEnlaceViejo(viejo)
    openai = OpenAIFalso(navegador)

    resultado = _iniciar(navegador, openai)

    assert resultado.client_id == CLIENTE_EMITIDO
    assert resultado.access_token == ACCESS
    assert navegador.vieja.status_code == 400
    assert MENSAJE_ENLACE_VIEJO in navegador.vieja.text
    assert MENSAJE_LISTO not in navegador.vieja.text
    assert MENSAJE_LISTO in navegador.respuesta.text
    assert len(openai.canjes) == 1
    assert _puerto_libre(navegador.puerto)


def test_each_attempt_uses_a_new_state_nonce_and_verifier():
    primero, segundo = NavegadorFalso(), NavegadorFalso()
    _iniciar(primero)
    _iniciar(segundo)

    for clave in ("state", "nonce", "code_challenge"):
        assert primero.params[clave] != segundo.params[clave]


def test_invalid_grant_says_the_code_no_longer_works():
    navegador = NavegadorFalso()
    openai = OpenAIFalso(
        navegador,
        token=lambda r: httpx.Response(
            400, json={"error": "invalid_grant", "error_description": "Code expired"}
        ),
    )

    with pytest.raises(ErrorAuth) as exc:
        _iniciar(navegador, openai)

    assert str(exc.value) == MENSAJE_CODIGO
    assert _puerto_libre(navegador.puerto)


def test_other_exchange_error_shows_the_detail():
    navegador = NavegadorFalso()
    openai = OpenAIFalso(
        navegador,
        token=lambda r: httpx.Response(
            400, json={"error": "invalid_request", "error_description": "Invalid code_verifier"}
        ),
    )

    with pytest.raises(ErrorAuth) as exc:
        _iniciar(navegador, openai)

    assert str(exc.value) == "ChatGPT rechazó el inicio de sesión: Invalid code_verifier"


def test_exchange_error_detail_never_echoes_the_code():
    navegador = NavegadorFalso()
    openai = OpenAIFalso(
        navegador,
        token=lambda r: httpx.Response(
            400, json={"error": "invalid_request", "error_description": f"bad code {CODIGO}"}
        ),
    )

    with pytest.raises(ErrorAuth) as exc:
        _iniciar(navegador, openai)

    assert str(exc.value).startswith("ChatGPT rechazó el inicio de sesión: ")
    assert CODIGO not in str(exc.value)


def test_exchange_without_tokens_is_rejected():
    navegador = NavegadorFalso()
    openai = OpenAIFalso(navegador, token=lambda r: httpx.Response(200, json={"token_type": "Bearer"}))

    with pytest.raises(ErrorAuth) as exc:
        _iniciar(navegador, openai)

    assert str(exc.value).startswith("ChatGPT rechazó el inicio de sesión: ")


def test_no_connection_during_the_exchange():
    navegador = NavegadorFalso()

    def sin_red(request):
        raise httpx.ConnectError("sin red", request=request)

    with pytest.raises(ErrorAuth) as exc:
        iniciar_sesion_chatgpt(
            HOST_ID,
            abrir_navegador=navegador,
            timeout=5,
            http_client=httpx.Client(transport=httpx.MockTransport(sin_red)),
        )
    navegador.esperar()

    assert str(exc.value) == MENSAJE_SIN_CONEXION
    assert _puerto_libre(navegador.puerto)


@pytest.mark.parametrize(
    "caso",
    ["firma", "aud", "iss", "vencido", "nonce", "sin-nonce"],
)
def test_an_invalid_id_token_is_an_identity_error(caso):
    navegador = NavegadorFalso()
    firmar_con = OTRA_CLAVE_RSA if caso == "firma" else CLAVE_RSA
    cambios = {
        "aud": {"aud": "oaiapp_otro"},
        "iss": {"iss": "https://evil.example.com"},
        "vencido": {"iat": int(time.time()) - 7200, "exp": int(time.time()) - 3600},
        "nonce": {"nonce": "otro-nonce"},
    }

    def reclamos(r):
        r.update(cambios.get(caso, {}))
        if caso == "sin-nonce":
            del r["nonce"]
        return r

    openai = OpenAIFalso(navegador, reclamos=reclamos, firmar_con=firmar_con)

    with pytest.raises(ErrorAuth) as exc:
        _iniciar(navegador, openai)

    assert str(exc.value) == MENSAJE_IDENTIDAD
    assert _puerto_libre(navegador.puerto)


def test_a_different_subject_than_expected_is_rejected():
    navegador = NavegadorFalso()

    with pytest.raises(ErrorAuth) as exc:
        _iniciar(navegador, client_id=CLIENTE_EMITIDO, subject_esperado="user-otro")

    assert str(exc.value) == MENSAJE_OTRA_CUENTA


def test_missing_plan_scope_is_not_an_error():
    navegador = NavegadorFalso()
    openai = OpenAIFalso(navegador, scope="openid profile email offline_access resource.invoke")

    resultado = _iniciar(navegador, openai)

    assert "chatgpt.tokens.use.direct" not in resultado.scopes
    assert list(resultado.scopes) == ["openid", "profile", "email", "offline_access", "resource.invoke"]
    assert resultado.access_token == ACCESS


def test_cancel_before_the_callback_stops_waiting():
    cancelado = threading.Event()
    navegador = NavegadorFalso(consulta=None)
    openai = OpenAIFalso(navegador)
    threading.Timer(0.2, cancelado.set).start()

    with pytest.raises(ErrorAuth) as exc:
        _iniciar(navegador, openai, timeout=30, cancelado=cancelado)

    assert str(exc.value) == MENSAJE_CANCELADO
    assert openai.pedidos == []
    assert _puerto_libre(navegador.puerto)


def test_cancel_after_the_exchange_discards_the_tokens():
    cancelado = threading.Event()
    navegador = NavegadorFalso()
    openai = OpenAIFalso(navegador)

    def canjear_y_cancelar(request):
        respuesta = openai(request)
        if str(request.url) == TOKEN_URL:
            cancelado.set()  # the user closes the dialog while the exchange runs
        return respuesta

    with pytest.raises(ErrorAuth) as exc:
        iniciar_sesion_chatgpt(
            HOST_ID,
            abrir_navegador=navegador,
            timeout=5,
            http_client=httpx.Client(transport=httpx.MockTransport(canjear_y_cancelar)),
            cancelado=cancelado,
        )
    navegador.esperar()

    assert str(exc.value) == MENSAJE_CANCELADO
    for secreto in (ACCESS, REFRESH):
        assert secreto not in str(exc.value)
    assert len(openai.canjes) == 1
    assert openai.revocaciones == [
        {"token": REFRESH, "token_type_hint": "refresh_token", "client_id": CLIENTE_EMITIDO}
    ]
    assert _puerto_libre(navegador.puerto)


def _revocacion(modo):
    if modo == "confirma":
        return None
    if modo == "rechaza":
        return lambda r: httpx.Response(400, json={"error": "invalid_client"})

    def sin_red(request):
        raise httpx.ConnectError("sin red", request=request)

    return sin_red


@pytest.mark.parametrize("revocacion", ["confirma", "rechaza", "sin-red"])
@pytest.mark.parametrize(
    ("fallo", "mensaje"),
    [
        ("cancelado", MENSAJE_CANCELADO),
        ("identidad", MENSAJE_IDENTIDAD),
        ("otra-cuenta", MENSAJE_OTRA_CUENTA),
    ],
)
def test_a_failure_after_the_exchange_revokes_the_new_refresh_token(
    sin_esperas, fallo, mensaje, revocacion
):
    cancelado = threading.Event()
    navegador = NavegadorFalso()
    openai = OpenAIFalso(
        navegador,
        firmar_con=OTRA_CLAVE_RSA if fallo == "identidad" else CLAVE_RSA,
        revocar=_revocacion(revocacion),
    )

    def manejar(request):
        respuesta = openai(request)
        if fallo == "cancelado" and str(request.url) == TOKEN_URL:
            cancelado.set()
        return respuesta

    extra = (
        {"client_id": CLIENTE_EMITIDO, "subject_esperado": "user-otro"}
        if fallo == "otra-cuenta"
        else {}
    )
    with pytest.raises(ErrorAuth) as exc:
        iniciar_sesion_chatgpt(
            HOST_ID,
            abrir_navegador=navegador,
            timeout=5,
            http_client=httpx.Client(transport=httpx.MockTransport(manejar)),
            cancelado=cancelado,
            **extra,
        )
    navegador.esperar()

    assert str(exc.value) == mensaje
    assert openai.revocaciones
    assert all(
        r == {"token": REFRESH, "token_type_hint": "refresh_token", "client_id": CLIENTE_EMITIDO}
        for r in openai.revocaciones
    )
    assert _puerto_libre(navegador.puerto)


def test_a_successful_sign_in_revokes_nothing():
    navegador = NavegadorFalso()
    openai = OpenAIFalso(navegador)

    _iniciar(navegador, openai)

    assert openai.revocaciones == []


def _sin_red(request):
    raise httpx.ConnectError("sin red", request=request)


def _decodificacion_rota(request):
    raise httpx.DecodingError("contenido ilegible", request=request)


@pytest.mark.parametrize("donde", ["descubrimiento", "claves"])
@pytest.mark.parametrize(
    ("falla", "mensaje"),
    [
        (_sin_red, MENSAJE_SIN_CONEXION),
        (_decodificacion_rota, MENSAJE_SIN_CONEXION),
        (lambda r: httpx.Response(503), MENSAJE_NO_DISPONIBLE),
        (lambda r: httpx.Response(500, json={"error": "server_error"}), MENSAJE_NO_DISPONIBLE),
    ],
    ids=["sin-red", "decodificacion", "503", "500"],
)
def test_discovery_and_jwks_failures_are_not_identity_errors(sin_esperas, donde, falla, mensaje):
    navegador = NavegadorFalso()
    openai = OpenAIFalso(navegador, **{donde: falla})

    with pytest.raises(ErrorAuth) as exc:
        _iniciar(navegador, openai)

    assert str(exc.value) == mensaje
    assert len(openai.canjes) == 1


def test_a_decoding_error_during_the_exchange_is_no_connection():
    navegador = NavegadorFalso()

    def manejar(request):
        if str(request.url) == TOKEN_URL:
            raise httpx.DecodingError("contenido ilegible", request=request)
        return OpenAIFalso(navegador)(request)

    with pytest.raises(ErrorAuth) as exc:
        iniciar_sesion_chatgpt(
            HOST_ID,
            abrir_navegador=navegador,
            timeout=5,
            http_client=httpx.Client(transport=httpx.MockTransport(manejar)),
        )
    navegador.esperar()

    assert str(exc.value) == MENSAJE_SIN_CONEXION
    assert _puerto_libre(navegador.puerto)


def test_timeout_when_the_user_never_comes_back():
    navegador = NavegadorFalso(consulta=None)
    openai = OpenAIFalso(navegador)

    with pytest.raises(ErrorAuth) as exc:
        _iniciar(navegador, openai, timeout=0.3)

    assert str(exc.value) == MENSAJE_TIMEOUT
    assert openai.pedidos == []
    assert _puerto_libre(navegador.puerto)


def _navegador_que_falla(urls, resultado):
    def abrir(url):
        urls.append(url)
        if isinstance(resultado, Exception):
            raise resultado
        return resultado

    return abrir


def _puerto_de(url):
    return urlsplit(parse_qs(urlsplit(url).query)["redirect_uri"][0]).port


SIN_NAVEGADOR = pytest.mark.parametrize(
    "resultado", [False, OSError("sin navegador")], ids=["devuelve-false", "lanza"]
)


@SIN_NAVEGADOR
def test_without_a_browser_and_no_callback_it_fails_at_once(resultado):
    urls: list[str] = []
    inicio = time.monotonic()

    with pytest.raises(ErrorAuth) as exc:
        iniciar_sesion_chatgpt(
            HOST_ID,
            abrir_navegador=_navegador_que_falla(urls, resultado),
            timeout=30,
            http_client=httpx.Client(transport=httpx.MockTransport(_sin_canje)),
        )

    assert time.monotonic() - inicio < 5
    (url,) = urls
    assert url.startswith("https://auth.openai.com/api/accounts/authorize?")
    assert str(exc.value) == MENSAJE_SIN_NAVEGADOR
    assert _puerto_libre(_puerto_de(url))


@SIN_NAVEGADOR
def test_without_a_browser_the_manual_path_completes(resultado):
    urls: list[str] = []
    mostradas: list[str] = []
    navegador = NavegadorFalso()  # the user opens the shown URL by hand

    def mostrar(url):
        mostradas.append(url)
        navegador(url)

    resultado_sesion = iniciar_sesion_chatgpt(
        HOST_ID,
        abrir_navegador=_navegador_que_falla(urls, resultado),
        timeout=5,
        http_client=OpenAIFalso(navegador).cliente(),
        al_sin_navegador=mostrar,
    )
    navegador.esperar()

    assert resultado_sesion.client_id == CLIENTE_EMITIDO
    assert mostradas == urls
    assert MENSAJE_LISTO in navegador.respuesta.text
    assert _puerto_libre(navegador.puerto)


@SIN_NAVEGADOR
def test_without_a_browser_the_manual_wait_can_be_cancelled(resultado):
    urls: list[str] = []
    mostradas: list[str] = []
    cancelado = threading.Event()
    threading.Timer(0.2, cancelado.set).start()

    with pytest.raises(ErrorAuth) as exc:
        iniciar_sesion_chatgpt(
            HOST_ID,
            abrir_navegador=_navegador_que_falla(urls, resultado),
            timeout=30,
            http_client=httpx.Client(transport=httpx.MockTransport(_sin_canje)),
            cancelado=cancelado,
            al_sin_navegador=mostradas.append,
        )

    assert str(exc.value) == MENSAJE_CANCELADO
    assert mostradas == urls
    assert _puerto_libre(_puerto_de(urls[0]))


def test_preferred_port_1455_is_used_when_free():
    if not _puerto_libre(1455):
        pytest.skip("port 1455 is busy on this machine")
    navegador = NavegadorFalso()

    _iniciar(navegador)

    assert navegador.puerto == 1455
    assert _puerto_libre(1455)


def test_another_port_is_used_when_1455_is_busy():
    ocupante = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        try:
            ocupante.bind(("127.0.0.1", 1455))
        except OSError:
            pytest.skip("port 1455 is held by another program")
        ocupante.listen(1)
        navegador = NavegadorFalso()

        resultado = _iniciar(navegador)
    finally:
        ocupante.close()

    assert resultado.client_id == CLIENTE_EMITIDO
    assert navegador.puerto not in (None, 1455)
    assert _puerto_libre(navegador.puerto)


def test_server_start_error_is_an_error_auth(monkeypatch):
    def sin_puerto(self):
        raise OSError("puerto ocupado")

    monkeypatch.setattr(socketserver.TCPServer, "server_bind", sin_puerto)
    abiertas: list[str] = []

    with pytest.raises(ErrorAuth) as exc:
        iniciar_sesion_chatgpt(HOST_ID, abrir_navegador=abiertas.append, timeout=1)

    assert str(exc.value) == "No pude preparar el inicio de sesión con ChatGPT: puerto ocupado"
    assert abiertas == []


def test_the_server_listens_only_on_loopback(monkeypatch):
    direcciones = []
    enlazar = socketserver.TCPServer.server_bind

    def espiar(self):
        enlazar(self)
        direcciones.append(self.socket.getsockname()[0])

    monkeypatch.setattr(socketserver.TCPServer, "server_bind", espiar)
    _iniciar(NavegadorFalso())

    assert direcciones and set(direcciones) == {"127.0.0.1"}


def test_secrets_never_reach_the_logs_or_the_page(caplog, capfd):
    correcto = NavegadorFalso()
    openai = OpenAIFalso(correcto)
    with caplog.at_level("DEBUG"):
        resultado = _iniciar(correcto, openai)
        equivocado = NavegadorFalso(
            lambda p: {"code": CODIGO, "state": "otro", "client_id": CLIENTE_EMITIDO}
        )
        with pytest.raises(ErrorAuth):
            _iniciar(equivocado, timeout=1)

    salida = capfd.readouterr()
    verifier = openai.canjes[0]["code_verifier"]
    for secreto in (CODIGO, verifier, ACCESS, REFRESH, resultado.id_token):
        assert secreto not in caplog.text
        assert secreto not in salida.out
        assert secreto not in salida.err
        assert secreto not in correcto.respuesta.text
        assert secreto not in equivocado.respuesta.text


# --- renovar ----------------------------------------------------------------


def _cliente(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_refresh_returns_the_rotated_tokens():
    openai = OpenAIFalso()
    antes = int(time.time())

    tokens = renovar(CLIENTE_EMITIDO, REFRESH, http_client=openai.cliente())

    assert openai.refrescos == [
        {
            "grant_type": "refresh_token",
            "client_id": CLIENTE_EMITIDO,
            "refresh_token": REFRESH,
            "resource": RESOURCE,
        }
    ]
    assert tokens.access_token == ACCESS
    assert tokens.refresh_token == REFRESH_NUEVO
    assert antes + 3600 <= tokens.expira <= int(time.time()) + 3600
    assert list(tokens.scopes) == SCOPES.split()
    for secreto in (ACCESS, REFRESH_NUEVO):
        assert secreto not in repr(tokens)


@pytest.mark.parametrize(
    "codigo",
    [
        "invalid_grant",
        "invalid_refresh_token",
        "token_expired",
        "refresh_token_expired",
        "refresh_token_invalidated",
        "refresh_token_reused",
    ],
)
@pytest.mark.parametrize(
    "forma", ["plano", "objeto"], ids=["error-texto", "error-objeto"]
)
def test_an_unusable_refresh_token_means_the_session_expired(codigo, forma):
    cuerpo = {"error": codigo} if forma == "plano" else {"error": {"code": codigo, "message": "x"}}
    openai = OpenAIFalso(refrescar=lambda r: httpx.Response(401, json=cuerpo))

    with pytest.raises(ErrorSesionVencida) as exc:
        renovar(CLIENTE_EMITIDO, REFRESH, http_client=openai.cliente())

    assert str(exc.value) == MENSAJE_VENCIDA


@pytest.mark.parametrize("estado", [500, 502, 503])
def test_a_server_error_on_refresh_is_not_an_expired_session(estado):
    openai = OpenAIFalso(refrescar=lambda r: httpx.Response(estado, json={"error": "server_error"}))

    with pytest.raises(ErrorAuth) as exc:
        renovar(CLIENTE_EMITIDO, REFRESH, http_client=openai.cliente())

    assert not isinstance(exc.value, ErrorSesionVencida)
    assert REFRESH not in str(exc.value)


def test_no_connection_on_refresh_is_not_an_expired_session():
    def sin_red(request):
        raise httpx.ConnectError("sin red", request=request)

    with pytest.raises(ErrorAuth) as exc:
        renovar(CLIENTE_EMITIDO, REFRESH, http_client=_cliente(sin_red))

    assert not isinstance(exc.value, ErrorSesionVencida)
    assert str(exc.value) == MENSAJE_SIN_CONEXION


def test_a_decoding_error_on_refresh_is_no_connection():
    def roto(request):
        raise httpx.DecodingError("contenido ilegible", request=request)

    with pytest.raises(ErrorAuth) as exc:
        renovar(CLIENTE_EMITIDO, REFRESH, http_client=_cliente(roto))

    assert not isinstance(exc.value, ErrorSesionVencida)
    assert str(exc.value) == MENSAJE_SIN_CONEXION


# --- cerrar_sesion ----------------------------------------------------------


@pytest.fixture
def sin_esperas(monkeypatch):
    monkeypatch.setattr(auth_chatgpt, "_ESPERAS_REVOCACION", (0, 0, 0))


def test_sign_out_revokes_the_refresh_token(sin_esperas):
    openai = OpenAIFalso()

    assert cerrar_sesion(CLIENTE_EMITIDO, REFRESH, http_client=openai.cliente()) is True

    assert openai.revocaciones == [
        {"token": REFRESH, "token_type_hint": "refresh_token", "client_id": CLIENTE_EMITIDO}
    ]
    assert [str(p.url) for p in openai.pedidos][0] == DISCOVERY_URL


def test_sign_out_retries_server_errors_then_gives_up(sin_esperas):
    openai = OpenAIFalso(revocar=lambda r: httpx.Response(503))

    assert cerrar_sesion(CLIENTE_EMITIDO, REFRESH, http_client=openai.cliente()) is False

    assert len(openai.revocaciones) == 3


def test_sign_out_succeeds_after_a_retry(sin_esperas):
    respuestas = iter([httpx.Response(500), httpx.Response(200, text="")])
    openai = OpenAIFalso(revocar=lambda r: next(respuestas))

    assert cerrar_sesion(CLIENTE_EMITIDO, REFRESH, http_client=openai.cliente()) is True

    assert len(openai.revocaciones) == 2


def test_sign_out_without_connection_returns_false(sin_esperas):
    intentos = []

    def sin_red(request):
        intentos.append(request)
        raise httpx.ConnectError("sin red", request=request)

    assert cerrar_sesion(CLIENTE_EMITIDO, REFRESH, http_client=_cliente(sin_red)) is False
    assert 1 <= len(intentos) <= 3


def test_sign_out_rejected_by_the_server_returns_false(sin_esperas):
    openai = OpenAIFalso(revocar=lambda r: httpx.Response(400, json={"error": "invalid_client"}))

    assert cerrar_sesion(CLIENTE_EMITIDO, REFRESH, http_client=openai.cliente()) is False
    assert len(openai.revocaciones) == 1


def test_sign_out_never_raises_on_a_broken_discovery(sin_esperas):
    def roto(request):
        return httpx.Response(200, text="no es json")

    assert cerrar_sesion(CLIENTE_EMITIDO, REFRESH, http_client=_cliente(roto)) is False
