"""Cloud fallback for fact extraction (block 2C, S5): only when the local model fails."""

import json

import keyring.core
import pytest

from app import secretos
from app.config import Config
from app.llm.base import ErrorLLM
from app.llm.conversacion import MemoriaUsuario, leer_hechos
from app.memory.history import Historial
from app.ui import ajustes, chat
from app.ui.ajustes import DialogoAjustes
from app.ui.chat import PantallaChat
from tests.keyring_falso import KeyringFalso
from tests.memoria_falsa import MemoriaFalsa
from tests.test_memoria_usuario import HECHOS_ANA, LLMGuion
from tests.test_ui import FakePage, Servidor, _enviar, _usar_servidor
from tests.test_local_client import _chunk

ANA = "Me llamo Ana"


@pytest.fixture(autouse=True)
def datos(tmp_path, monkeypatch):
    monkeypatch.setenv("EVA_DATA_DIR", str(tmp_path / "datos"))
    return tmp_path / "datos"


@pytest.fixture
def almacen(monkeypatch):
    backend = KeyringFalso()
    monkeypatch.setattr(keyring.core, "_keyring_backend", backend)
    return backend


def _usuario():
    falsa = MemoriaFalsa()
    memoria = MemoriaUsuario(falsa)
    memoria.cargar()
    return memoria, falsa


def _titulos(falsa):
    return sorted(r.titulo for r in falsa.listar())


# ----- leer_hechos -----


@pytest.mark.parametrize("salida", ["", "no sé", "[{malo", '{"titulo": "x"}', "<think>pensando"])
def test_unreadable_output_is_none(salida):
    assert leer_hechos(salida) is None


def test_a_valid_empty_list_is_not_unreadable():
    assert leer_hechos("```json\n[]\n```") == []


# ----- MemoriaUsuario.aprender -----


def test_unreadable_local_output_is_retried_once_with_the_cloud():
    memoria, falsa = _usuario()
    local, nube = LLMGuion(extraccion="no entendí"), LLMGuion(extraccion=HECHOS_ANA)
    pedidos = []

    guardados = memoria.aprender(local, ANA, respaldo=lambda: pedidos.append(1) or nube)

    assert pedidos == [1]
    assert len(nube.recibidos) == 1 and nube.recibidos[0][1]["content"] == ANA
    assert [r.titulo for r in guardados] == ["Nombre", "Bebida favorita"]
    assert _titulos(falsa) == ["Bebida favorita", "Nombre"]


def test_a_local_error_is_retried_with_the_cloud():
    memoria, falsa = _usuario()
    local = LLMGuion(error_extraccion=ErrorLLM("sin conexión"))

    memoria.aprender(local, ANA, respaldo=lambda: LLMGuion(extraccion=HECHOS_ANA))

    assert _titulos(falsa) == ["Bebida favorita", "Nombre"]


def test_a_valid_empty_answer_never_calls_the_cloud():
    memoria, falsa = _usuario()
    pedidos = []

    memoria.aprender(LLMGuion(extraccion="[]"), ANA, respaldo=lambda: pedidos.append(1))

    assert pedidos == []
    assert _titulos(falsa) == []


def test_readable_local_facts_never_call_the_cloud():
    memoria, falsa = _usuario()
    pedidos = []

    memoria.aprender(LLMGuion(extraccion=HECHOS_ANA), ANA, respaldo=lambda: pedidos.append(1))

    assert pedidos == []
    assert _titulos(falsa) == ["Bebida favorita", "Nombre"]


@pytest.mark.parametrize(
    "respaldo",
    [
        lambda: None,  # no cloud provider with a key
        lambda: LLMGuion(error_extraccion=ErrorLLM("La clave de OpenAI no es válida.")),
        lambda: LLMGuion(extraccion="tampoco"),
        lambda: (_ for _ in ()).throw(RuntimeError("roto")),  # building the client fails
    ],
)
def test_a_failed_fallback_saves_nothing_and_never_raises(respaldo):
    memoria, falsa = _usuario()

    assert memoria.aprender(LLMGuion(extraccion="no entendí"), ANA, respaldo=respaldo) == []
    assert _titulos(falsa) == []


# ----- settings remember the fallback -----


def test_saving_a_cloud_provider_remembers_it_as_the_fallback_without_the_key(monkeypatch, almacen, datos):
    secretos.guardar_clave("openai", "sk-1")

    class Cliente:
        def listar_modelos(self):
            return ["gpt-a"]

    monkeypatch.setattr(ajustes, "crear_cliente", lambda p, m, c, http_client=None: Cliente())
    dialogo = DialogoAjustes(FakePage(), Config(proveedor="openai", extras={"voz": "x"}), lambda c: None)
    dialogo.abrir()
    dialogo._guardar(None)

    assert json.loads((datos / "config.json").read_text(encoding="utf-8")) == {
        "proveedor": "openai",
        "modelo": "gpt-a",
        "voz": "x",
        "respaldo": {"proveedor": "openai", "modelo": "gpt-a"},
    }


def test_saving_a_local_provider_keeps_the_previous_fallback(monkeypatch, datos):
    _usar_servidor(monkeypatch, Servidor(models=["m"]))
    respaldo = {"proveedor": "gemini", "modelo": "gemini-x"}
    dialogo = DialogoAjustes(FakePage(), Config(extras={"respaldo": respaldo}), lambda c: None)
    dialogo.abrir()
    dialogo._guardar(None)

    assert json.loads((datos / "config.json").read_text(encoding="utf-8"))["respaldo"] == respaldo


# ----- chat screen -----


def _chat_local(monkeypatch, tmp_path, config, extraccion):
    _usar_servidor(monkeypatch, Servidor(models=["m"], chunks=[_chunk(content=extraccion)]))
    memoria, falsa = _usuario()
    pantalla = PantallaChat(FakePage(), Historial(tmp_path / "eva.db"), config, memoria)

    async def sin_foco():
        pass

    pantalla.campo.focus = sin_foco
    pantalla.iniciar()
    return pantalla, falsa


def test_the_chat_uses_the_saved_cloud_fallback_when_the_local_model_fails(monkeypatch, tmp_path, almacen):
    secretos.guardar_clave("anthropic", "sk-ant")
    creados = []

    def crear_cliente(proveedor, modelo, clave, http_client=None):
        creados.append((proveedor, modelo, clave))
        return LLMGuion(extraccion=HECHOS_ANA)

    monkeypatch.setattr(chat, "crear_cliente", crear_cliente)
    config = Config(extras={"respaldo": {"proveedor": "anthropic", "modelo": "claude-x"}})
    pantalla, falsa = _chat_local(monkeypatch, tmp_path, config, "Hola")  # "Hola" is not JSON
    _enviar(pantalla, ANA)

    assert creados == [("anthropic", "claude-x", "sk-ant")]
    assert _titulos(falsa) == ["Bebida favorita", "Nombre"]


def test_the_chat_has_no_fallback_without_a_saved_key(monkeypatch, tmp_path, almacen):
    creados = []
    monkeypatch.setattr(chat, "crear_cliente", lambda *a, **k: creados.append(a))
    config = Config(extras={"respaldo": {"proveedor": "anthropic", "modelo": "claude-x"}})
    pantalla, falsa = _chat_local(monkeypatch, tmp_path, config, "Hola")
    _enviar(pantalla, ANA)

    assert creados == []
    assert _titulos(falsa) == []


def test_a_cloud_chat_gets_no_second_attempt(monkeypatch, tmp_path, almacen):
    secretos.guardar_clave("openai", "sk-1")
    nube = LLMGuion(respuesta="Hola", extraccion="no entendí")
    nube.proveedor = "openai"
    creados = []
    monkeypatch.setattr(chat, "conectar_nube", lambda p, m, c: nube)
    monkeypatch.setattr(chat, "crear_cliente", lambda *a, **k: creados.append(a))
    config = Config(proveedor="openai", extras={"respaldo": {"proveedor": "openai", "modelo": "m"}})
    memoria, falsa = _usuario()
    pantalla = PantallaChat(FakePage(), Historial(tmp_path / "eva.db"), config, memoria)

    async def sin_foco():
        pass

    pantalla.campo.focus = sin_foco
    pantalla.iniciar()
    _enviar(pantalla, ANA)

    assert len(nube.recibidos) == 2  # the answer and one extraction
    assert creados == []
    assert _titulos(falsa) == []
