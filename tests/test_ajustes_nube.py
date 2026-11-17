"""Cloud providers in the settings dialog and the chat connection (block 2C, S3)."""

import json
import threading

import keyring.core
import pytest

from app import secretos
from app.auth import MENSAJE_CANCELADO, ErrorAuth
from app.config import PROVEEDORES, Config, cargar_config, guardar_config
from app.llm.base import ErrorLLM
from app.ui import ajustes, chat
from app.ui.ajustes import DialogoAjustes
from app.ui.chat import ROJO_ERROR
from tests.keyring_falso import KeyringFalso, KeyringRoto
from tests.test_ui import FakePage, _pantalla, _valores

CLAVE = "sk-secreta-123"
CLAVE_INVALIDA = "La clave de OpenAI no es válida. Revísala en Ajustes."
ERROR_ALMACEN = "No pude usar el almacén seguro del sistema: almacén bloqueado"


@pytest.fixture(autouse=True)
def datos(tmp_path, monkeypatch):
    monkeypatch.setenv("EVA_DATA_DIR", str(tmp_path / "datos"))
    return tmp_path / "datos"


@pytest.fixture
def almacen(monkeypatch):
    backend = KeyringFalso()
    monkeypatch.setattr(keyring.core, "_keyring_backend", backend)
    return backend


class NubeFalsa:
    """Stands in for ``crear_cliente``: lists models only for known keys."""

    def __init__(self, modelos=("gpt-a", "gpt-b"), claves=(CLAVE,)):
        self.modelos = list(modelos)
        self.claves = set(claves)
        self.llamadas: list[tuple[str, str]] = []

    def __call__(self, proveedor, modelo, clave, http_client=None):
        nube = self

        class Cliente:
            def listar_modelos(self):
                nube.llamadas.append((proveedor, clave))
                if clave not in nube.claves:
                    raise ErrorLLM(CLAVE_INVALIDA)
                return list(nube.modelos)

        return Cliente()


def _dialogo(monkeypatch, config, nube=None, page=None):
    nube = nube or NubeFalsa()
    monkeypatch.setattr(ajustes, "crear_cliente", nube)
    page = page or FakePage()
    aplicadas: list[Config] = []
    dialogo = DialogoAjustes(page, config, aplicadas.append)
    dialogo.abrir()
    return dialogo, nube, page, aplicadas


def _config_en_disco(datos):
    return json.loads((datos / "config.json").read_text(encoding="utf-8"))


# ----- config -----


def test_the_cloud_providers_are_offered_after_the_local_ones():
    assert [(k, p.nombre, p.nube) for k, p in PROVEEDORES.items()] == [
        ("lmstudio", "LM Studio", False),
        ("ollama", "Ollama", False),
        ("openai", "OpenAI", True),
        ("anthropic", "Claude", True),
        ("gemini", "Gemini", True),
        ("openrouter", "OpenRouter", True),
        ("chatgpt", "ChatGPT", True),
    ]


def test_a_saved_cloud_provider_is_read_back(tmp_path):
    ruta = tmp_path / "config.json"
    guardar_config(Config(proveedor="gemini", modelo="gemini-2.5-flash"), ruta)

    config = cargar_config(ruta)
    assert (config.proveedor, config.modelo) == ("gemini", "gemini-2.5-flash")


# ----- settings dialog -----


def test_the_key_field_shows_only_for_cloud_providers(monkeypatch, almacen):
    local, *_ = _dialogo(monkeypatch, Config())
    nube, *_ = _dialogo(monkeypatch, Config(proveedor="openai"))
    openrouter, *_ = _dialogo(monkeypatch, Config(proveedor="openrouter"))

    assert local.fila_nube.visible is False
    assert nube.fila_nube.visible is True
    assert nube.clave.password is True
    assert nube.clave.label == "Clave de API"
    assert nube.boton_openrouter.visible is False
    assert openrouter.boton_openrouter.visible is True
    assert openrouter.boton_openrouter.content == "Iniciar sesión con OpenRouter"


def test_without_a_key_no_models_are_requested_and_save_is_disabled(monkeypatch, almacen):
    dialogo, nube, *_ = _dialogo(monkeypatch, Config(proveedor="openai"))

    assert nube.llamadas == []
    assert dialogo.estado.value == "Escribe tu clave de API de OpenAI para ver sus modelos."
    assert dialogo.boton_guardar.disabled is True
    assert dialogo.clave.hint_text in (None, "")


def test_openrouter_without_a_key_suggests_signing_in(monkeypatch, almacen):
    dialogo, *_ = _dialogo(monkeypatch, Config(proveedor="openrouter"))

    assert dialogo.estado.value == "Escribe tu clave de API de OpenRouter o inicia sesión para ver sus modelos."


def test_a_typed_key_lists_models_and_is_saved_in_the_keyring_not_in_config(monkeypatch, almacen, datos):
    dialogo, nube, page, aplicadas = _dialogo(monkeypatch, Config(proveedor="openai"))
    dialogo.clave.value = CLAVE
    dialogo._cargar_modelos()

    assert nube.llamadas[-1] == ("openai", CLAVE)
    assert dialogo.estado.value == "2 modelos disponibles en OpenAI"
    assert [o.key for o in dialogo.modelo.options] == ["gpt-a", "gpt-b"]
    dialogo.modelo.value = "gpt-b"
    dialogo._guardar(None)

    assert page.dialogos == []
    assert [(c.proveedor, c.modelo) for c in aplicadas] == [("openai", "gpt-b")]
    assert secretos.leer_clave("openai") == CLAVE
    assert _config_en_disco(datos) == {
        "proveedor": "openai",
        "modelo": "gpt-b",
        "respaldo": {"proveedor": "openai", "modelo": "gpt-b"},
    }
    assert CLAVE not in (datos / "config.json").read_text(encoding="utf-8")


def test_a_saved_key_is_used_and_kept_when_the_field_stays_empty(monkeypatch, almacen, datos):
    secretos.guardar_clave("openai", CLAVE)
    dialogo, nube, _, aplicadas = _dialogo(monkeypatch, Config(proveedor="openai", modelo="gpt-b"))

    assert nube.llamadas == [("openai", CLAVE)]
    assert dialogo.clave.hint_text == "Clave guardada"
    assert dialogo.modelo.value == "gpt-b"
    dialogo._guardar(None)

    assert secretos.leer_clave("openai") == CLAVE
    assert [(c.proveedor, c.modelo) for c in aplicadas] == [("openai", "gpt-b")]


def test_an_invalid_key_shows_the_provider_error_and_disables_save(monkeypatch, almacen):
    dialogo, *_ = _dialogo(monkeypatch, Config(proveedor="openai"))
    dialogo.clave.value = "sk-mala"
    dialogo._cargar_modelos()

    assert (dialogo.estado.value, dialogo.estado.color) == (CLAVE_INVALIDA, ROJO_ERROR)
    assert dialogo.boton_guardar.disabled is True
    assert dialogo.modelo.options == []


def test_deleting_the_key_removes_it_from_the_keyring(monkeypatch, almacen):
    secretos.guardar_clave("openai", CLAVE)
    secretos.guardar_clave("gemini", "otra")
    dialogo, *_ = _dialogo(monkeypatch, Config(proveedor="openai"))
    dialogo._borrar_clave(None)

    assert secretos.leer_clave("openai") is None
    assert secretos.leer_clave("gemini") == "otra"
    assert dialogo.estado.value == "Clave de OpenAI borrada."
    assert dialogo.boton_guardar.disabled is True
    assert dialogo.clave.hint_text in (None, "")


def test_a_broken_keyring_keeps_the_dialog_open_and_writes_nothing(monkeypatch, almacen, datos):
    dialogo, _, page, aplicadas = _dialogo(monkeypatch, Config(proveedor="openai"))
    dialogo.clave.value = CLAVE
    dialogo._cargar_modelos()
    monkeypatch.setattr(keyring.core, "_keyring_backend", KeyringRoto())
    dialogo._guardar(None)

    assert page.dialogos == [dialogo.dialogo]
    assert aplicadas == []
    assert (dialogo.estado.value, dialogo.estado.color) == (ERROR_ALMACEN, ROJO_ERROR)
    assert not (datos / "config.json").exists()


def test_a_broken_keyring_while_loading_shows_its_error(monkeypatch):
    monkeypatch.setattr(keyring.core, "_keyring_backend", KeyringRoto())
    dialogo, nube, *_ = _dialogo(monkeypatch, Config(proveedor="openai"))

    assert nube.llamadas == []
    assert (dialogo.estado.value, dialogo.estado.color) == (ERROR_ALMACEN, ROJO_ERROR)
    assert dialogo.boton_guardar.disabled is True


def test_signing_in_with_openrouter_saves_its_key_and_lists_models(monkeypatch, almacen):
    monkeypatch.setattr(ajustes, "iniciar_sesion_openrouter", lambda cancelado=None, al_sin_navegador=None: "sk-or-1")
    nube = NubeFalsa(modelos=["anthropic/claude-x"], claves=["sk-or-1"])
    dialogo, *_ = _dialogo(monkeypatch, Config(proveedor="openrouter"), nube=nube)
    dialogo._iniciar_sesion(None)

    assert secretos.leer_clave("openrouter") == "sk-or-1"
    assert nube.llamadas[-1] == ("openrouter", "sk-or-1")
    assert dialogo.modelo.value == "anthropic/claude-x"
    assert dialogo.estado.value == "1 modelos disponibles en OpenRouter"
    assert dialogo.boton_guardar.disabled is False
    assert dialogo.boton_openrouter.content == "Iniciar sesión con OpenRouter"


def test_a_failed_openrouter_sign_in_shows_why_and_saves_nothing(monkeypatch, almacen):
    def falla(cancelado=None, al_sin_navegador=None):
        raise ErrorAuth("OpenRouter no autorizó el inicio de sesión.")

    monkeypatch.setattr(ajustes, "iniciar_sesion_openrouter", falla)
    dialogo, *_ = _dialogo(monkeypatch, Config(proveedor="openrouter"))
    dialogo._iniciar_sesion(None)

    assert (dialogo.estado.value, dialogo.estado.color) == ("OpenRouter no autorizó el inicio de sesión.", ROJO_ERROR)
    assert secretos.leer_clave("openrouter") is None


def test_the_sign_in_button_cancels_a_pending_sign_in(monkeypatch, almacen):
    recibido: list[threading.Event] = []

    def espera(cancelado=None, al_sin_navegador=None):
        recibido.append(cancelado)
        if cancelado is not None and cancelado.is_set():
            raise ErrorAuth(MENSAJE_CANCELADO)
        return "sk-or-1"

    monkeypatch.setattr(ajustes, "iniciar_sesion_openrouter", espera)
    page = FakePage(ejecutar=False)
    dialogo, _, page, _ = _dialogo(monkeypatch, Config(proveedor="openrouter"), page=page)
    page.correr_pendientes()
    dialogo._iniciar_sesion(None)

    assert dialogo.estado.value == "Esperando el inicio de sesión en el navegador…"
    assert dialogo.boton_openrouter.content == "Cancelar inicio de sesión"
    dialogo._iniciar_sesion(None)  # second click cancels
    page.correr_pendientes()

    assert dialogo.estado.value == MENSAJE_CANCELADO
    assert secretos.leer_clave("openrouter") is None
    assert dialogo.boton_openrouter.content == "Iniciar sesión con OpenRouter"


def test_closing_the_dialog_cancels_a_pending_sign_in(monkeypatch, almacen):
    recibido: list[threading.Event] = []
    monkeypatch.setattr(
        ajustes, "iniciar_sesion_openrouter", lambda cancelado=None, al_sin_navegador=None: recibido.append(cancelado)
    )
    page = FakePage(ejecutar=False)
    dialogo, _, page, _ = _dialogo(monkeypatch, Config(proveedor="openrouter"), page=page)
    dialogo._iniciar_sesion(None)
    dialogo._cancelar(None)
    page.correr_pendientes()

    assert recibido and recibido[0].is_set()
    assert page.dialogos == []


def test_a_sign_in_cancelled_while_the_key_arrives_saves_nothing(monkeypatch, almacen):
    # The dialog closes while the login is finishing (e.g. during the code
    # exchange) and the login still hands back a key: it must not be saved.
    dialogos: list[DialogoAjustes] = []

    def cerrar_y_devolver(cancelado=None, al_sin_navegador=None):
        dialogos[0]._cancelar(None)
        return "sk-or-1"

    monkeypatch.setattr(ajustes, "iniciar_sesion_openrouter", cerrar_y_devolver)
    nube = NubeFalsa(modelos=["anthropic/claude-x"], claves=["sk-or-1"])
    dialogo, nube, page, aplicadas = _dialogo(monkeypatch, Config(proveedor="openrouter"), nube=nube)
    dialogos.append(dialogo)
    llamadas_antes = list(nube.llamadas)
    dialogo._iniciar_sesion(None)

    assert secretos.leer_clave("openrouter") is None
    assert almacen.datos == {}
    assert nube.llamadas == llamadas_antes  # models are not reloaded
    assert dialogo.estado.value == MENSAJE_CANCELADO
    assert dialogo.boton_openrouter.content == "Iniciar sesión con OpenRouter"
    assert page.dialogos == []
    assert aplicadas == []


def test_without_a_browser_the_sign_in_url_is_shown_and_the_sign_in_still_completes(monkeypatch, almacen):
    url = "https://openrouter.ai/auth?callback_url=http%3A%2F%2F127.0.0.1%3A5555%2Fcallback&state=s"
    dialogos: list[DialogoAjustes] = []
    vistos: list[tuple] = []

    def sin_navegador(cancelado=None, al_sin_navegador=None):
        al_sin_navegador(url)
        d = dialogos[0]
        vistos.append((d.estado.value, d.estado.color, d.estado.selectable, d.boton_openrouter.content))
        return "sk-or-1"  # the user opened the URL by hand and signed in

    monkeypatch.setattr(ajustes, "iniciar_sesion_openrouter", sin_navegador)
    nube = NubeFalsa(modelos=["anthropic/claude-x"], claves=["sk-or-1"])
    dialogo, *_ = _dialogo(monkeypatch, Config(proveedor="openrouter"), nube=nube)
    dialogos.append(dialogo)
    dialogo._iniciar_sesion(None)

    assert vistos == [
        (
            "No pude abrir el navegador. Abre esta dirección para iniciar sesión con OpenRouter: " + url,
            ajustes.GRIS_TEXTO,
            True,
            "Cancelar inicio de sesión",
        )
    ]
    assert secretos.leer_clave("openrouter") == "sk-or-1"
    assert dialogo.estado.value == "1 modelos disponibles en OpenRouter"
    assert dialogo.modelo.value == "anthropic/claude-x"
    assert dialogo.boton_openrouter.content == "Iniciar sesión con OpenRouter"


# ----- chat connection -----


def test_the_chat_connects_to_a_cloud_provider_with_its_saved_key(monkeypatch, tmp_path, almacen):
    secretos.guardar_clave("anthropic", CLAVE)
    llamadas = []

    class Cliente:
        modelo = "claude-x"

    def conectar_nube(proveedor, modelo, clave):
        llamadas.append((proveedor, modelo, clave))
        return Cliente()

    monkeypatch.setattr(chat, "conectar_nube", conectar_nube)
    pantalla = _pantalla(tmp_path, config=Config(proveedor="anthropic", modelo="claude-x"))
    pantalla.iniciar()

    assert llamadas == [("anthropic", "claude-x", CLAVE)]
    assert pantalla.etiqueta_modelo.value == "Claude · claude-x"
    assert _valores(pantalla) == []


def test_the_chat_without_a_cloud_key_asks_to_add_it(tmp_path, almacen):
    pantalla = _pantalla(tmp_path, config=Config(proveedor="anthropic"))
    pantalla.iniciar()

    assert _valores(pantalla) == ["Falta la clave de Claude. Agrégala en Ajustes."]
    assert pantalla.etiqueta_modelo.value == "Claude · sin conexión"


def test_the_chat_shows_a_broken_keyring_error(monkeypatch, tmp_path):
    monkeypatch.setattr(keyring.core, "_keyring_backend", KeyringRoto())
    pantalla = _pantalla(tmp_path, config=Config(proveedor="openai"))
    pantalla.iniciar()

    assert _valores(pantalla) == [ERROR_ALMACEN]
    assert pantalla.etiqueta_modelo.value == "OpenAI · sin conexión"
