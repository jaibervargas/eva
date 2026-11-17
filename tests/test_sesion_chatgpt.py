"""Tests for the ChatGPT session wiring (Fase 2D, S4): token provider and connection.

A temporary EVA_DATA_DIR, an in-memory keyring, a fake ``renovar`` and an
``httpx.MockTransport`` stand in for the real data folder, OS secret store
and OpenAI, so nothing here reaches the network.
"""

import threading
import time

import httpx
import keyring.core
import pytest

from app.auth_chatgpt import ErrorAuth, ErrorSesionVencida, Tokens
from app.cuentas_chatgpt import AlmacenCuentas, CuentaChatGPT, ErrorCuentas
from app.llm import sesion_chatgpt
from app.llm.base import ErrorLLM
from app.llm.chatgpt_client import ClienteChatGPT
from app.llm.sesion_chatgpt import cliente_respaldo, conectar_chatgpt, proveedor_token
from tests.keyring_falso import KeyringFalso, KeyringRoto

PLAN = "chatgpt.tokens.use.direct"
INICIA_SESION = "Inicia sesión con ChatGPT en Ajustes."
SIN_PLAN = "Tu cuenta inició sesión, pero no autorizó usar tu plan de ChatGPT en Eva."
SIN_MODELOS = "Tu cuenta de ChatGPT no tiene modelos disponibles."
VENCIDA = "Tu sesión de ChatGPT venció. Vuelve a iniciar sesión en Ajustes."
SIN_CONEXION = "No pude conectarme con ChatGPT. Revisa tu conexión a internet."
ILEGIBLE = "No pude leer tus cuentas de ChatGPT guardadas."
ALMACEN_ROTO = "No pude usar el almacén seguro del sistema: almacén bloqueado"


@pytest.fixture(autouse=True)
def datos(tmp_path, monkeypatch):
    monkeypatch.setenv("EVA_DATA_DIR", str(tmp_path / "datos"))
    return tmp_path / "datos"


@pytest.fixture(autouse=True)
def almacen_seguro(monkeypatch):
    backend = KeyringFalso()
    monkeypatch.setattr(keyring.core, "_keyring_backend", backend)
    return backend


class Renovar:
    """Fake ``auth_chatgpt.renovar``: counts calls and hands out new tokens."""

    def __init__(self, error=None, scopes=("openid", PLAN), espera=0.0):
        self.error = error
        self.scopes = list(scopes)
        self.espera = espera
        self.llamadas: list[tuple[str, str]] = []

    def __call__(self, client_id, refresh_token, http_client=None):
        self.llamadas.append((client_id, refresh_token))
        if self.espera:
            time.sleep(self.espera)
        if self.error is not None:
            raise self.error
        n = len(self.llamadas)
        return Tokens(f"acc-nuevo-{n}", f"ref-nuevo-{n}", int(time.time()) + 3600, list(self.scopes))


@pytest.fixture
def renovar(monkeypatch):
    falso = Renovar()
    monkeypatch.setattr(sesion_chatgpt, "renovar", falso)
    return falso


def _cuenta(expira_en=3600, scopes=("openid", PLAN), access="acc-1", refresh="ref-1", email="ana@example.com"):
    return CuentaChatGPT(
        email=email,
        subject="sub-ana",
        client_id="oaiapp-1",
        id_token="idt-1",
        access_token=access,
        refresh_token=refresh,
        expira=time.time() + expira_en,
        scopes=list(scopes),
    )


def _guardada(**kw) -> tuple[AlmacenCuentas, CuentaChatGPT]:
    almacen = AlmacenCuentas()
    cuenta = _cuenta(**kw)
    almacen.guardar(cuenta)
    return almacen, cuenta


# ----- token provider -----


def test_a_fresh_token_is_returned_without_refreshing(renovar):
    almacen, cuenta = _guardada(expira_en=3600)

    assert proveedor_token(almacen, cuenta.id)() == "acc-1"
    assert renovar.llamadas == []


def test_a_token_about_to_expire_is_refreshed_and_saved(renovar):
    almacen, cuenta = _guardada(expira_en=60)  # within 120 s

    assert proveedor_token(almacen, cuenta.id)() == "acc-nuevo-1"

    assert renovar.llamadas == [("oaiapp-1", "ref-1")]
    guardada = almacen.obtener(cuenta.id)
    assert (guardada.access_token, guardada.refresh_token) == ("acc-nuevo-1", "ref-nuevo-1")
    assert guardada.expira > time.time() + 3000
    # The next call uses the stored token: no second refresh.
    assert proveedor_token(almacen, cuenta.id)() == "acc-nuevo-1"
    assert len(renovar.llamadas) == 1


def test_an_already_expired_token_is_refreshed(renovar):
    almacen, cuenta = _guardada(expira_en=-10)

    assert proveedor_token(almacen, cuenta.id)() == "acc-nuevo-1"


def test_concurrent_calls_refresh_only_once(monkeypatch):
    falso = Renovar(espera=0.2)
    monkeypatch.setattr(sesion_chatgpt, "renovar", falso)
    almacen, cuenta = _guardada(expira_en=30)
    obtener = proveedor_token(almacen, cuenta.id)
    tokens: list[str] = []
    hilos = [threading.Thread(target=lambda: tokens.append(obtener())) for _ in range(4)]
    for hilo in hilos:
        hilo.start()
    for hilo in hilos:
        hilo.join(10)

    assert len(falso.llamadas) == 1
    assert tokens == ["acc-nuevo-1"] * 4


def test_two_providers_of_the_same_account_share_the_refresh(monkeypatch):
    falso = Renovar(espera=0.2)
    monkeypatch.setattr(sesion_chatgpt, "renovar", falso)
    almacen, cuenta = _guardada(expira_en=30)
    tokens: list[str] = []
    hilos = [
        threading.Thread(target=lambda: tokens.append(proveedor_token(AlmacenCuentas(), cuenta.id)()))
        for _ in range(2)
    ]
    for hilo in hilos:
        hilo.start()
    for hilo in hilos:
        hilo.join(10)

    assert len(falso.llamadas) == 1
    assert tokens == ["acc-nuevo-1"] * 2


def test_an_expired_session_clears_the_tokens_and_says_so(monkeypatch):
    monkeypatch.setattr(sesion_chatgpt, "renovar", Renovar(error=ErrorSesionVencida(VENCIDA)))
    almacen, cuenta = _guardada(expira_en=10)

    with pytest.raises(ErrorLLM) as error:
        proveedor_token(almacen, cuenta.id)()

    assert str(error.value) == VENCIDA
    guardada = almacen.obtener(cuenta.id)
    assert (guardada.access_token, guardada.refresh_token, guardada.id_token) == ("", "", "")
    assert (guardada.email, guardada.client_id) == ("ana@example.com", "oaiapp-1")  # registration kept
    # Signed out now: the next call asks to sign in again without refreshing.
    with pytest.raises(ErrorLLM, match=INICIA_SESION):
        proveedor_token(almacen, cuenta.id)()


def test_a_refresh_network_error_keeps_the_tokens(monkeypatch):
    monkeypatch.setattr(sesion_chatgpt, "renovar", Renovar(error=ErrorAuth(SIN_CONEXION)))
    almacen, cuenta = _guardada(expira_en=10)

    with pytest.raises(ErrorLLM) as error:
        proveedor_token(almacen, cuenta.id)()

    assert str(error.value) == SIN_CONEXION
    guardada = almacen.obtener(cuenta.id)
    assert (guardada.access_token, guardada.refresh_token) == ("acc-1", "ref-1")


def test_a_missing_account_asks_to_sign_in(renovar):
    with pytest.raises(ErrorLLM) as error:
        proveedor_token(AlmacenCuentas(), "no-existe")()

    assert str(error.value) == INICIA_SESION
    assert renovar.llamadas == []


def test_a_signed_out_account_asks_to_sign_in(renovar):
    almacen, cuenta = _guardada()
    almacen.olvidar_tokens(cuenta.id)

    with pytest.raises(ErrorLLM) as error:
        proveedor_token(almacen, cuenta.id)()

    assert str(error.value) == INICIA_SESION
    assert renovar.llamadas == []


def test_an_account_without_plan_permission_says_so(renovar):
    almacen, cuenta = _guardada(scopes=("openid", "email"))

    with pytest.raises(ErrorLLM) as error:
        proveedor_token(almacen, cuenta.id)()

    assert str(error.value) == SIN_PLAN
    assert renovar.llamadas == []


def test_an_unreadable_store_becomes_a_chat_error(renovar, datos):
    almacen, cuenta = _guardada()
    (datos / "chatgpt.bin").write_bytes(b"basura")

    with pytest.raises(ErrorLLM) as error:
        proveedor_token(almacen, cuenta.id)()

    assert str(error.value) == ILEGIBLE


def test_a_broken_keyring_becomes_a_chat_error(monkeypatch, renovar):
    almacen, cuenta = _guardada()
    monkeypatch.setattr(keyring.core, "_keyring_backend", KeyringRoto())
    with pytest.raises(ErrorLLM) as error:
        proveedor_token(almacen, cuenta.id)()

    assert str(error.value) == ALMACEN_ROTO


def test_errors_never_carry_the_tokens(monkeypatch):
    monkeypatch.setattr(sesion_chatgpt, "renovar", Renovar(error=ErrorCuentas(ILEGIBLE)))
    almacen, cuenta = _guardada(expira_en=10)

    with pytest.raises(ErrorLLM) as error:
        proveedor_token(almacen, cuenta.id)()

    assert "acc-1" not in str(error.value) and "ref-1" not in str(error.value)
    assert error.value.__cause__ is None and error.value.__suppress_context__


# ----- connecting -----


def _openai(modelos, vistos=None):
    def servidor(request):
        if vistos is not None:
            vistos.append(request.headers.get("Authorization"))
        assert request.url.path == "/v1/models"
        return httpx.Response(200, json={"models": modelos})

    return httpx.Client(transport=httpx.MockTransport(servidor))


MODELOS = [
    {"slug": "gpt-5", "display_name": "GPT-5", "visibility": "list"},
    {"slug": "oculto", "display_name": "Oculto", "visibility": "hide"},
    {"slug": "gpt-5-mini", "display_name": "GPT-5 mini", "visibility": "list"},
]


def test_connecting_uses_the_saved_model_and_its_display_name(renovar):
    almacen, cuenta = _guardada()
    vistos: list[str] = []

    cliente = conectar_chatgpt(cuenta.id, "gpt-5-mini", almacen=almacen, http_client=_openai(MODELOS, vistos))

    assert isinstance(cliente, ClienteChatGPT)
    assert (cliente.modelo, cliente.nombre_modelo) == ("gpt-5-mini", "GPT-5 mini")
    assert vistos == ["Bearer acc-1"]


def test_connecting_without_the_saved_model_picks_the_first(renovar):
    almacen, cuenta = _guardada()

    cliente = conectar_chatgpt(cuenta.id, "retirado", almacen=almacen, http_client=_openai(MODELOS))

    assert (cliente.modelo, cliente.nombre_modelo) == ("gpt-5", "GPT-5")


def test_connecting_to_an_account_without_models_says_so(renovar):
    almacen, cuenta = _guardada()

    with pytest.raises(ErrorLLM) as error:
        conectar_chatgpt(cuenta.id, "", almacen=almacen, http_client=_openai([MODELOS[1]]))

    assert str(error.value) == SIN_MODELOS


@pytest.mark.parametrize("cuenta_id", [None, "", "no-existe"])
def test_connecting_without_an_account_asks_to_sign_in(renovar, cuenta_id):
    with pytest.raises(ErrorLLM) as error:
        conectar_chatgpt(cuenta_id, "", almacen=AlmacenCuentas(), http_client=_openai(MODELOS))

    assert str(error.value) == INICIA_SESION


def test_connecting_without_plan_permission_says_so(renovar):
    almacen, cuenta = _guardada(scopes=("openid",))

    with pytest.raises(ErrorLLM) as error:
        conectar_chatgpt(cuenta.id, "", almacen=almacen, http_client=_openai(MODELOS))

    assert str(error.value) == SIN_PLAN


# ----- extraction fallback -----


def test_the_fallback_client_uses_the_account_tokens(renovar):
    almacen, cuenta = _guardada(expira_en=10)

    cliente = cliente_respaldo(cuenta.id, "gpt-5", almacen=almacen)

    assert isinstance(cliente, ClienteChatGPT)
    assert cliente.modelo == "gpt-5"
    assert cliente._obtener_token() == "acc-nuevo-1"


@pytest.mark.parametrize("cuenta_id", [None, "", 3, "no-existe"])
def test_no_fallback_without_a_usable_account(renovar, cuenta_id):
    assert cliente_respaldo(cuenta_id, "gpt-5", almacen=AlmacenCuentas()) is None


def test_no_fallback_for_a_signed_out_account_or_without_a_model(renovar):
    almacen, cuenta = _guardada()

    assert cliente_respaldo(cuenta.id, "", almacen=almacen) is None
    almacen.olvidar_tokens(cuenta.id)
    assert cliente_respaldo(cuenta.id, "gpt-5", almacen=almacen) is None
