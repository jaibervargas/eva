"""ChatGPT accounts in the settings dialog and the chat (Fase 2D, S4).

Fake sign-in, sign-out and ChatGPT client functions, an in-memory keyring and
a temporary EVA_DATA_DIR are used: nothing reaches OpenAI, the OS secret store
or the user's data folder.
"""

import json
import threading
import time

import keyring.core
import pytest

from app.auth_chatgpt import MENSAJE_CANCELADO, ErrorAuth, ResultadoSesion, Tokens
from app.config import PROVEEDORES, Config
from app.cuentas_chatgpt import AlmacenCuentas, CuentaChatGPT
from app.llm import sesion_chatgpt
from app.llm.base import ErrorLLM
from app.llm.conversacion import MemoriaUsuario
from app.llm.prompts import PROMPT_EXTRACCION
from app.memory.history import Historial
from app.ui import panel_chatgpt
from app.ui.ajustes import DialogoAjustes
from app.ui.chat import GRIS_TEXTO, ROJO_ERROR, PantallaChat
from tests.keyring_falso import KeyringFalso
from tests.memoria_falsa import MemoriaFalsa
from tests.test_memoria_usuario import HECHOS_ANA
from tests.test_ui import FakePage, Servidor, _enviar, _pantalla, _usar_servidor, _valores
from tests.test_local_client import _chunk

PLAN = "chatgpt.tokens.use.direct"
ALCANCES = ["openid", "profile", "email", "offline_access", "resource.invoke", PLAN]
INICIA_SESION = "Inicia sesión con ChatGPT en Ajustes."
PIDE_SESION = "Inicia sesión con ChatGPT para ver sus modelos."
SIN_PLAN = "Tu cuenta inició sesión, pero no autorizó usar tu plan de ChatGPT en Eva."
SIN_MODELOS = "Tu cuenta de ChatGPT no tiene modelos disponibles."
VENCIDA = "Tu sesión de ChatGPT venció. Vuelve a iniciar sesión en Ajustes."
SIN_CONFIRMAR = (
    "Cerré tu sesión en Eva, pero no pude confirmarlo con OpenAI. "
    "Puedes desconectar Eva en la configuración de ChatGPT."
)
SESION_CERRADA = "Cerré tu sesión de ChatGPT en Eva."
USO = "Uso del plan: chatgpt.com/settings/usage"
ESPERANDO = "Esperando el inicio de sesión en el navegador…"
MODELOS = [("gpt-5", "GPT-5"), ("gpt-5-mini", "GPT-5 mini")]
ANA = "Me llamo Ana"


@pytest.fixture(autouse=True)
def datos(tmp_path, monkeypatch):
    monkeypatch.setenv("EVA_DATA_DIR", str(tmp_path / "datos"))
    return tmp_path / "datos"


@pytest.fixture(autouse=True)
def almacen_seguro(monkeypatch):
    backend = KeyringFalso()
    monkeypatch.setattr(keyring.core, "_keyring_backend", backend)
    return backend


class OpenAIFalso:
    """Stands in for ``ClienteChatGPT``: lists ``modelos`` and answers with a script."""

    def __init__(self, modelos=MODELOS, respuesta="Hola, Ana", extraccion="[]", error=None):
        self.modelos = list(modelos)
        self.respuesta = respuesta
        self.extraccion = extraccion
        self.error = error
        self.creados: list = []
        self.tokens: list[str] = []
        self.recibidos: list[list[dict]] = []

    def __call__(self, modelo, obtener_token, http_client=None):
        falso = self

        class Cliente:
            nombre = "ChatGPT"
            proveedor = "chatgpt"

            def __init__(self):
                self.modelo = modelo
                self._obtener_token = obtener_token

            def listar_modelos(self):
                falso.tokens.append(self._obtener_token())
                if falso.error is not None:
                    raise falso.error
                return list(falso.modelos)

            def responder(self, mensajes):
                falso.tokens.append(self._obtener_token())
                falso.recibidos.append([dict(m) for m in mensajes])
                yield falso.extraccion if mensajes[0]["content"] == PROMPT_EXTRACCION else falso.respuesta

        cliente = Cliente()
        self.creados.append(cliente)
        return cliente


@pytest.fixture
def openai(monkeypatch):
    falso = OpenAIFalso()
    monkeypatch.setattr(sesion_chatgpt, "ClienteChatGPT", falso)
    return falso


class InicioFalso:
    """Stands in for ``iniciar_sesion_chatgpt``: records its arguments."""

    def __init__(self, resultado=None, error=None, antes=None):
        self.resultado = resultado
        self.error = error
        self.antes = antes  # called with the keyword arguments before returning
        self.llamadas: list[dict] = []

    def __call__(self, host_id, **kw):
        self.llamadas.append({"host_id": host_id, **kw})
        if self.antes is not None:
            self.antes(kw)
        if self.error is not None:
            raise self.error
        return self.resultado


def _resultado(subject="sub-ana", client_id="oaiapp-1", email="ana@example.com", scopes=ALCANCES, n=1):
    return ResultadoSesion(
        client_id=client_id,
        email=email,
        subject=subject,
        id_token=f"idt-{n}",
        access_token=f"acc-secreto-{n}",
        refresh_token=f"ref-secreto-{n}",
        expira=int(time.time()) + 3600,
        scopes=list(scopes),
    )


def _iniciar(monkeypatch, inicio: InicioFalso) -> InicioFalso:
    monkeypatch.setattr(panel_chatgpt, "iniciar_sesion_chatgpt", inicio)
    return inicio


def _cuenta(subject="sub-ana", email="ana@example.com", scopes=ALCANCES, conectada=True, client_id="oaiapp-1"):
    cuenta = CuentaChatGPT(
        email=email,
        subject=subject,
        client_id=client_id,
        id_token="idt-0" if conectada else "",
        access_token="acc-secreto-0" if conectada else "",
        refresh_token="ref-secreto-0" if conectada else "",
        expira=time.time() + 3600 if conectada else 0,
        scopes=list(scopes) if conectada else [],
    )
    AlmacenCuentas().guardar(cuenta)
    return cuenta


def _dialogo(config=None, page=None):
    page = page or FakePage()
    aplicadas: list[Config] = []
    dialogo = DialogoAjustes(page, config or Config(proveedor="chatgpt"), aplicadas.append)
    dialogo.abrir()
    return dialogo, page, aplicadas


def _cuentas(dialogo):
    return [(o.key, o.text) for o in dialogo.chatgpt.cuenta.options]


def _estado(dialogo):
    return dialogo.estado.value, dialogo.estado.color


def _config_en_disco(datos):
    return json.loads((datos / "config.json").read_text(encoding="utf-8"))


# ----- provider -----


def test_chatgpt_is_a_cloud_provider_signed_in_with_an_account():
    assert PROVEEDORES["chatgpt"].nombre == "ChatGPT"
    assert PROVEEDORES["chatgpt"].nube is True
    assert PROVEEDORES["chatgpt"].cuenta is True
    assert [k for k, p in PROVEEDORES.items() if p.cuenta] == ["chatgpt"]


def test_chatgpt_shows_its_account_controls_instead_of_a_key(openai):
    dialogo, *_ = _dialogo()

    assert dialogo.fila_nube.visible is False
    assert dialogo.chatgpt.contenedor.visible is True
    assert dialogo.chatgpt.boton_continuar.content == "Continuar con ChatGPT"
    assert dialogo.chatgpt.uso.value == USO
    assert _cuentas(dialogo) == [(panel_chatgpt.NUEVA, "Agregar cuenta…")]
    assert dialogo.chatgpt.cuenta.value == panel_chatgpt.NUEVA
    assert dialogo.chatgpt.boton_cerrar.visible is False
    assert dialogo.chatgpt.boton_permitir.visible is False
    assert _estado(dialogo) == (PIDE_SESION, GRIS_TEXTO)
    assert dialogo.boton_guardar.disabled is True
    assert openai.creados == []


def test_other_providers_hide_the_chatgpt_controls(monkeypatch):
    _usar_servidor(monkeypatch, Servidor(models=["m"]))
    dialogo, *_ = _dialogo(Config())

    assert dialogo.chatgpt.contenedor.visible is False
    dialogo.proveedor.value = "openai"
    dialogo._cambio_proveedor(None)
    assert dialogo.chatgpt.contenedor.visible is False
    assert dialogo.fila_nube.visible is True


def test_saved_accounts_are_listed_by_email_with_duplicates_numbered(openai):
    ana = _cuenta()
    otra_ana = _cuenta(subject="sub-ana-2")
    beto = _cuenta(subject="sub-beto", email="beto@example.com")
    dialogo, *_ = _dialogo()

    assert _cuentas(dialogo) == [
        (ana.id, "ana@example.com"),
        (otra_ana.id, "ana@example.com (2)"),
        (beto.id, "beto@example.com"),
        (panel_chatgpt.NUEVA, "Agregar cuenta…"),
    ]
    assert dialogo.chatgpt.cuenta.value == ana.id  # the first one by default


def test_a_connected_account_lists_its_models_by_display_name(openai):
    cuenta = _cuenta()
    dialogo, *_ = _dialogo(Config(proveedor="chatgpt", modelo="gpt-5-mini", extras={"chatgpt_cuenta": cuenta.id}))

    assert [(o.key, o.text) for o in dialogo.modelo.options] == MODELOS
    assert dialogo.modelo.value == "gpt-5-mini"
    assert _estado(dialogo) == ("2 modelos disponibles en ChatGPT", GRIS_TEXTO)
    assert openai.tokens == ["acc-secreto-0"]
    assert dialogo.boton_guardar.disabled is False
    assert dialogo.chatgpt.boton_cerrar.visible is True
    assert dialogo.chatgpt.boton_cerrar.content == "Cerrar sesión"
    assert dialogo.chatgpt.boton_permitir.visible is False


def test_the_saved_account_is_preselected(openai):
    _cuenta()
    beto = _cuenta(subject="sub-beto", email="beto@example.com")
    dialogo, *_ = _dialogo(Config(proveedor="chatgpt", extras={"chatgpt_cuenta": beto.id}))

    assert dialogo.chatgpt.cuenta.value == beto.id
    assert openai.tokens == ["acc-secreto-0"]


def test_models_load_off_the_ui_loop(openai):
    cuenta = _cuenta()
    page = FakePage(ejecutar=False)
    dialogo, *_ = _dialogo(Config(proveedor="chatgpt", extras={"chatgpt_cuenta": cuenta.id}), page=page)

    assert dialogo.estado.value == "Conectando…"
    assert openai.creados == []
    page.correr_pendientes()
    assert dialogo.modelo.value == "gpt-5"


def test_choosing_another_account_reloads_and_only_the_latest_load_wins(openai):
    ana = _cuenta()
    beto = _cuenta(subject="sub-beto", email="beto@example.com", conectada=False)
    page = FakePage(ejecutar=False)
    dialogo, *_ = _dialogo(Config(proveedor="chatgpt", extras={"chatgpt_cuenta": ana.id}), page=page)
    dialogo.chatgpt.cuenta.value = beto.id
    dialogo.chatgpt._cambio_cuenta(None)
    primera, segunda = page.pendientes
    page.pendientes = [segunda, primera]  # the older load (Ana) finishes last
    page.correr_pendientes()

    assert dialogo.chatgpt.cuenta.value == beto.id
    assert dialogo.modelo.options == []
    assert _estado(dialogo) == (PIDE_SESION, GRIS_TEXTO)
    assert dialogo.boton_guardar.disabled is True


def test_a_model_list_error_is_shown(openai):
    openai.error = ErrorLLM("ChatGPT no está disponible en este momento. Inténtalo más tarde.")
    _cuenta()
    dialogo, *_ = _dialogo()

    assert _estado(dialogo) == ("ChatGPT no está disponible en este momento. Inténtalo más tarde.", ROJO_ERROR)
    assert dialogo.boton_guardar.disabled is True


def test_an_account_without_models_says_so(openai):
    openai.modelos = []
    _cuenta()
    dialogo, *_ = _dialogo()

    assert _estado(dialogo) == (SIN_MODELOS, ROJO_ERROR)
    assert dialogo.boton_guardar.disabled is True


def test_an_expired_session_while_listing_signs_the_account_out(monkeypatch, openai):
    def vencida(client_id, refresh_token, http_client=None):
        raise sesion_chatgpt.ErrorSesionVencida(VENCIDA)

    monkeypatch.setattr(sesion_chatgpt, "renovar", vencida)
    cuenta = CuentaChatGPT(
        email="ana@example.com", subject="s", client_id="c", access_token="a", refresh_token="r",
        expira=time.time() + 10, scopes=ALCANCES,
    )
    AlmacenCuentas().guardar(cuenta)
    dialogo, *_ = _dialogo()

    assert _estado(dialogo) == (VENCIDA, ROJO_ERROR)
    assert AlmacenCuentas().obtener(cuenta.id).conectada is False
    assert dialogo.chatgpt.boton_cerrar.visible is False


def test_an_unreadable_account_file_is_shown(openai, datos):
    _cuenta()
    (datos / "chatgpt.bin").write_bytes(b"basura")
    dialogo, *_ = _dialogo()

    assert _estado(dialogo) == ("No pude leer tus cuentas de ChatGPT guardadas.", ROJO_ERROR)
    assert _cuentas(dialogo) == [(panel_chatgpt.NUEVA, "Agregar cuenta…")]
    assert dialogo.boton_guardar.disabled is True


# ----- signing in -----


def test_adding_an_account_registers_eva_and_lists_its_models(monkeypatch, openai):
    inicio = _iniciar(monkeypatch, InicioFalso(_resultado()))
    dialogo, *_ = _dialogo()
    host = AlmacenCuentas().host_id()
    dialogo.chatgpt._continuar(None)

    [llamada] = inicio.llamadas
    assert llamada["host_id"] == host
    assert llamada["client_id"] is None
    assert llamada["id_token_hint"] is None and llamada["login_hint"] is None
    assert llamada["subject_esperado"] is None
    assert llamada["pedir_consentimiento"] is False
    assert isinstance(llamada["cancelado"], threading.Event)
    [cuenta] = AlmacenCuentas().listar()
    assert (cuenta.email, cuenta.subject, cuenta.client_id) == ("ana@example.com", "sub-ana", "oaiapp-1")
    assert (cuenta.access_token, cuenta.refresh_token, cuenta.id_token) == ("acc-secreto-1", "ref-secreto-1", "idt-1")
    assert AlmacenCuentas().host_id() == host
    assert dialogo.chatgpt.cuenta.value == cuenta.id
    assert _cuentas(dialogo) == [(cuenta.id, "ana@example.com"), (panel_chatgpt.NUEVA, "Agregar cuenta…")]
    assert [o.text for o in dialogo.modelo.options] == ["GPT-5", "GPT-5 mini"]
    assert openai.tokens == ["acc-secreto-1"]
    assert dialogo.boton_guardar.disabled is False
    assert dialogo.chatgpt.boton_continuar.content == "Continuar con ChatGPT"


def test_signing_in_again_reauthorizes_the_saved_account_and_keeps_its_id(monkeypatch, openai):
    guardada = _cuenta()
    host = AlmacenCuentas().host_id()
    inicio = _iniciar(monkeypatch, InicioFalso(_resultado(n=2)))
    dialogo, *_ = _dialogo()
    dialogo.chatgpt._continuar(None)

    [llamada] = inicio.llamadas
    assert llamada["host_id"] == host
    assert (llamada["client_id"], llamada["id_token_hint"]) == ("oaiapp-1", "idt-0")
    assert (llamada["login_hint"], llamada["subject_esperado"]) == ("ana@example.com", "sub-ana")
    assert llamada["pedir_consentimiento"] is False
    [cuenta] = AlmacenCuentas().listar()
    assert cuenta.id == guardada.id
    assert cuenta.access_token == "acc-secreto-2"
    assert dialogo.chatgpt.cuenta.value == guardada.id


def test_a_signed_out_account_reauthorizes_without_an_id_token_hint(monkeypatch, openai):
    guardada = _cuenta(conectada=False)
    inicio = _iniciar(monkeypatch, InicioFalso(_resultado()))
    dialogo, *_ = _dialogo()
    dialogo.chatgpt._continuar(None)

    [llamada] = inicio.llamadas
    assert (llamada["client_id"], llamada["id_token_hint"], llamada["login_hint"]) == (
        "oaiapp-1", None, "ana@example.com"
    )
    assert AlmacenCuentas().obtener(guardada.id).conectada is True
    assert dialogo.modelo.value == "gpt-5"


def test_agregar_cuenta_registers_a_new_account_even_with_saved_ones(monkeypatch, openai):
    ana = _cuenta()
    inicio = _iniciar(monkeypatch, InicioFalso(_resultado(subject="sub-beto", email="beto@example.com", client_id="oaiapp-2")))
    dialogo, *_ = _dialogo()
    dialogo.chatgpt.cuenta.value = panel_chatgpt.NUEVA
    dialogo.chatgpt._cambio_cuenta(None)
    dialogo.chatgpt._continuar(None)

    assert inicio.llamadas[0]["client_id"] is None
    ids = [c.id for c in AlmacenCuentas().listar()]
    assert len(ids) == 2 and ids[0] == ana.id
    assert dialogo.chatgpt.cuenta.value == ids[1]


def test_a_failed_sign_in_shows_why_and_saves_nothing(monkeypatch, openai):
    _iniciar(monkeypatch, InicioFalso(error=ErrorAuth("No autorizaste a Eva en ChatGPT.")))
    dialogo, *_ = _dialogo()
    dialogo.chatgpt._continuar(None)

    assert _estado(dialogo) == ("No autorizaste a Eva en ChatGPT.", ROJO_ERROR)
    assert AlmacenCuentas().listar() == []
    assert dialogo.chatgpt.boton_continuar.content == "Continuar con ChatGPT"


def test_the_sign_in_runs_off_the_ui_loop_and_a_second_click_cancels(monkeypatch, openai):
    def espera(kw):
        if kw["cancelado"].is_set():
            raise ErrorAuth(MENSAJE_CANCELADO)

    _iniciar(monkeypatch, InicioFalso(_resultado(), antes=espera))
    page = FakePage(ejecutar=False)
    dialogo, *_ = _dialogo(page=page)
    page.correr_pendientes()
    dialogo.chatgpt._continuar(None)

    assert _estado(dialogo) == (ESPERANDO, GRIS_TEXTO)
    assert dialogo.chatgpt.boton_continuar.content == "Cancelar inicio de sesión"
    dialogo.chatgpt._continuar(None)  # second click cancels
    page.correr_pendientes()

    assert _estado(dialogo) == ("Se canceló el inicio de sesión con ChatGPT.", ROJO_ERROR)
    assert AlmacenCuentas().listar() == []
    assert dialogo.chatgpt.boton_continuar.content == "Continuar con ChatGPT"


def test_closing_the_dialog_cancels_a_pending_sign_in(monkeypatch, openai):
    inicio = _iniciar(monkeypatch, InicioFalso(_resultado()))
    page = FakePage(ejecutar=False)
    dialogo, page, aplicadas = _dialogo(page=page)
    page.correr_pendientes()
    dialogo.chatgpt._continuar(None)
    dialogo._cancelar(None)
    page.correr_pendientes()

    assert inicio.llamadas[0]["cancelado"].is_set()
    assert AlmacenCuentas().listar() == []  # the late result is discarded
    assert page.dialogos == []
    assert aplicadas == []


def test_changing_the_provider_cancels_a_pending_sign_in(monkeypatch, openai):
    _usar_servidor(monkeypatch, Servidor(models=["m"]))
    inicio = _iniciar(monkeypatch, InicioFalso(_resultado()))
    page = FakePage(ejecutar=False)
    dialogo, *_ = _dialogo(page=page)
    page.correr_pendientes()
    dialogo.chatgpt._continuar(None)
    dialogo.proveedor.value = "lmstudio"
    dialogo._cambio_proveedor(None)
    page.correr_pendientes()

    assert inicio.llamadas[0]["cancelado"].is_set()
    assert AlmacenCuentas().listar() == []


def test_without_a_browser_the_address_is_shown_and_the_sign_in_completes(monkeypatch, openai):
    url = "https://auth.openai.com/api/accounts/authorize?client_id=dynamic_agent_client&state=s"
    vistos: list[tuple] = []
    dialogos: list[DialogoAjustes] = []

    def sin_navegador(kw):
        kw["al_sin_navegador"](url)
        d = dialogos[0]
        vistos.append((d.estado.value, d.estado.color, d.estado.selectable, d.chatgpt.boton_continuar.content))

    _iniciar(monkeypatch, InicioFalso(_resultado(), antes=sin_navegador))
    dialogo, *_ = _dialogo()
    dialogos.append(dialogo)
    dialogo.chatgpt._continuar(None)

    assert vistos == [
        (
            "No pude abrir el navegador. Abre esta dirección para iniciar sesión con ChatGPT: " + url,
            GRIS_TEXTO,
            True,
            "Cancelar inicio de sesión",
        )
    ]
    assert len(AlmacenCuentas().listar()) == 1
    assert dialogo.modelo.value == "gpt-5"


# ----- plan permission -----


def test_an_account_without_plan_permission_offers_to_allow_it(monkeypatch, openai):
    sin_plan = [s for s in ALCANCES if s != PLAN]
    guardada = _cuenta(scopes=sin_plan)
    inicio = _iniciar(monkeypatch, InicioFalso(_resultado(n=3)))
    dialogo, *_ = _dialogo()

    assert _estado(dialogo) == (SIN_PLAN, GRIS_TEXTO)
    assert dialogo.chatgpt.boton_permitir.visible is True
    assert dialogo.chatgpt.boton_permitir.content == "Permitir usar mi plan"
    assert dialogo.boton_guardar.disabled is True
    assert openai.creados == []
    dialogo.chatgpt._permitir(None)

    [llamada] = inicio.llamadas
    assert llamada["pedir_consentimiento"] is True
    assert (llamada["client_id"], llamada["subject_esperado"]) == ("oaiapp-1", "sub-ana")
    assert AlmacenCuentas().obtener(guardada.id).plan_habilitado is True
    assert dialogo.chatgpt.boton_permitir.visible is False
    assert dialogo.modelo.value == "gpt-5"


def test_a_sign_in_without_plan_permission_shows_the_message(monkeypatch, openai):
    _iniciar(monkeypatch, InicioFalso(_resultado(scopes=["openid", "email"])))
    dialogo, *_ = _dialogo()
    dialogo.chatgpt._continuar(None)

    assert len(AlmacenCuentas().listar()) == 1
    assert _estado(dialogo) == (SIN_PLAN, GRIS_TEXTO)
    assert dialogo.chatgpt.boton_permitir.visible is True
    assert dialogo.chatgpt.boton_cerrar.visible is True


# ----- signing out -----


@pytest.mark.parametrize("confirmado,mensaje", [(True, SESION_CERRADA), (False, SIN_CONFIRMAR)])
def test_signing_out_revokes_then_forgets_the_tokens(monkeypatch, openai, confirmado, mensaje):
    guardada = _cuenta()
    revocados: list[tuple[str, str]] = []

    def cerrar_sesion(client_id, refresh_token, http_client=None):
        revocados.append((client_id, refresh_token))
        assert AlmacenCuentas().obtener(guardada.id).conectada  # revoke first, then forget
        return confirmado

    monkeypatch.setattr(panel_chatgpt, "cerrar_sesion", cerrar_sesion)
    dialogo, *_ = _dialogo()
    dialogo.chatgpt._cerrar_sesion(None)

    assert revocados == [("oaiapp-1", "ref-secreto-0")]
    cuenta = AlmacenCuentas().obtener(guardada.id)
    assert cuenta.conectada is False
    assert (cuenta.email, cuenta.client_id) == ("ana@example.com", "oaiapp-1")
    assert _estado(dialogo) == (mensaje, GRIS_TEXTO)
    assert dialogo.modelo.options == []
    assert dialogo.boton_guardar.disabled is True
    assert dialogo.chatgpt.boton_cerrar.visible is False
    assert dialogo.chatgpt.cuenta.value == guardada.id  # still listed, ready to sign in again


def test_signing_out_forgets_the_tokens_even_if_the_revocation_raises(monkeypatch, openai):
    guardada = _cuenta()

    def cerrar_sesion(client_id, refresh_token, http_client=None):
        raise RuntimeError("revocación rota")

    monkeypatch.setattr(panel_chatgpt, "cerrar_sesion", cerrar_sesion)
    dialogo, *_ = _dialogo()
    dialogo.chatgpt._cerrar_sesion(None)

    assert AlmacenCuentas().obtener(guardada.id).conectada is False
    assert _estado(dialogo) == (SIN_CONFIRMAR, GRIS_TEXTO)
    assert dialogo.chatgpt.boton_cerrar.visible is False
    assert dialogo.boton_guardar.disabled is True


def test_signing_out_runs_off_the_ui_loop(monkeypatch, openai):
    _cuenta()
    monkeypatch.setattr(panel_chatgpt, "cerrar_sesion", lambda c, r, http_client=None: True)
    page = FakePage(ejecutar=False)
    dialogo, *_ = _dialogo(page=page)
    page.correr_pendientes()
    dialogo.chatgpt._cerrar_sesion(None)

    assert dialogo.chatgpt.boton_cerrar.disabled is True
    page.correr_pendientes()
    assert _estado(dialogo) == (SESION_CERRADA, GRIS_TEXTO)


# ----- saving -----


def test_saving_stores_the_account_and_model_without_any_token(openai, datos, almacen_seguro):
    cuenta = _cuenta()
    dialogo, page, aplicadas = _dialogo(Config(proveedor="chatgpt", extras={"voz": "x"}))
    dialogo.modelo.value = "gpt-5-mini"
    dialogo._guardar(None)

    assert page.dialogos == []
    assert [(c.proveedor, c.modelo) for c in aplicadas] == [("chatgpt", "gpt-5-mini")]
    assert _config_en_disco(datos) == {
        "proveedor": "chatgpt",
        "modelo": "gpt-5-mini",
        "voz": "x",
        "chatgpt_cuenta": cuenta.id,
        "respaldo": {"proveedor": "chatgpt", "modelo": "gpt-5-mini", "cuenta": cuenta.id},
    }
    texto = (datos / "config.json").read_text(encoding="utf-8")
    assert "secreto" not in texto and "idt-0" not in texto
    assert [usuario for _, usuario in almacen_seguro.datos] == ["chatgpt-cifrado"]


# ----- chat -----


def _chat(tmp_path, config, memoria=None):
    pantalla = PantallaChat(FakePage(), Historial(tmp_path / "eva.db"), config, memoria)

    async def sin_foco():
        pass

    pantalla.campo.focus = sin_foco
    pantalla.iniciar()
    return pantalla


def test_the_chat_connects_to_the_saved_account_and_shows_the_display_name(tmp_path, openai):
    _cuenta()
    beto = _cuenta(subject="sub-beto", email="beto@example.com")
    AlmacenCuentas().actualizar_tokens(
        beto.id, Tokens("acc-beto", "ref-beto", int(time.time()) + 3600, ALCANCES)
    )
    pantalla = _chat(tmp_path, Config(proveedor="chatgpt", modelo="gpt-5-mini", extras={"chatgpt_cuenta": beto.id}))

    assert pantalla.etiqueta_modelo.value == "ChatGPT · GPT-5 mini"
    assert pantalla.cliente.modelo == "gpt-5-mini"
    assert _valores(pantalla) == []
    _enviar(pantalla, "hola")
    assert _valores(pantalla) == ["hola", "Hola, Ana"]
    assert set(openai.tokens) == {"acc-beto"}


def test_the_chat_without_an_account_asks_to_sign_in(tmp_path, openai):
    pantalla = _chat(tmp_path, Config(proveedor="chatgpt"))

    assert _valores(pantalla) == [INICIA_SESION]
    assert pantalla.etiqueta_modelo.value == "ChatGPT · sin conexión"


def test_the_chat_without_plan_permission_says_so(tmp_path, openai):
    cuenta = _cuenta(scopes=["openid"])
    pantalla = _chat(tmp_path, Config(proveedor="chatgpt", extras={"chatgpt_cuenta": cuenta.id}))

    assert _valores(pantalla) == [SIN_PLAN]
    assert pantalla.etiqueta_modelo.value == "ChatGPT · sin conexión"


def test_the_chat_shows_chatgpt_errors_in_red(tmp_path, openai):
    openai.error = ErrorLLM("Tu cuenta de ChatGPT no puede usar su plan en Eva (hace falta Plus o Pro).")
    cuenta = _cuenta()
    pantalla = _chat(tmp_path, Config(proveedor="chatgpt", extras={"chatgpt_cuenta": cuenta.id}))

    assert _valores(pantalla) == ["Tu cuenta de ChatGPT no puede usar su plan en Eva (hace falta Plus o Pro)."]
    assert pantalla.mensajes.controls[-1].content.controls[0].content.color == ROJO_ERROR


def _usuario():
    falsa = MemoriaFalsa()
    memoria = MemoriaUsuario(falsa)
    memoria.cargar()
    return memoria, falsa


def test_a_chatgpt_fallback_extracts_memories_when_the_local_model_fails(monkeypatch, tmp_path, openai):
    openai.extraccion = HECHOS_ANA
    cuenta = _cuenta()
    _usar_servidor(monkeypatch, Servidor(models=["m"], chunks=[_chunk(content="Hola")]))  # "Hola" is not JSON
    memoria, falsa = _usuario()
    respaldo = {"proveedor": "chatgpt", "modelo": "gpt-5", "cuenta": cuenta.id}
    pantalla = _chat(tmp_path, Config(extras={"respaldo": respaldo}), memoria)
    _enviar(pantalla, ANA)

    assert [c.modelo for c in openai.creados] == ["gpt-5"]
    assert openai.recibidos[0][1]["content"] == ANA
    assert openai.tokens == ["acc-secreto-0"]
    assert sorted(r.titulo for r in falsa.listar()) == ["Bebida favorita", "Nombre"]


def test_no_chatgpt_fallback_for_a_signed_out_account(monkeypatch, tmp_path, openai):
    cuenta = _cuenta(conectada=False)
    _usar_servidor(monkeypatch, Servidor(models=["m"], chunks=[_chunk(content="Hola")]))
    memoria, falsa = _usuario()
    respaldo = {"proveedor": "chatgpt", "modelo": "gpt-5", "cuenta": cuenta.id}
    pantalla = _chat(tmp_path, Config(extras={"respaldo": respaldo}), memoria)
    _enviar(pantalla, ANA)

    assert openai.creados == []
    assert falsa.listar() == []


def test_a_chatgpt_chat_gets_no_second_extraction(tmp_path, openai):
    openai.extraccion = "no entendí"
    cuenta = _cuenta()
    memoria, falsa = _usuario()
    respaldo = {"proveedor": "chatgpt", "modelo": "gpt-5", "cuenta": cuenta.id}
    config = Config(proveedor="chatgpt", extras={"chatgpt_cuenta": cuenta.id, "respaldo": respaldo})
    pantalla = _chat(tmp_path, config, memoria)
    _enviar(pantalla, ANA)

    assert len(openai.creados) == 1  # only the chat client
    assert len(openai.recibidos) == 2  # the answer and one extraction
    assert falsa.listar() == []
