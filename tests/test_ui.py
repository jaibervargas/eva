"""Tests for the chat screen and the settings dialog.

A fake page stands in for Flet's (``run_thread`` runs the work at once, or keeps
it pending to observe the waiting state) and a fake server stands in for LM
Studio, so no window or model server is needed.
"""

import asyncio
import json
import logging
import sqlite3

import httpx
import pytest

from app.config import Config, cargar_config
from app.llm.local_client import MENSAJE_CONEXION_PERDIDA, ClienteLocal, conectar
from app.memory.history import Historial
from app.ui import ajustes, chat
from app.ui.ajustes import DialogoAjustes
from app.ui.chat import ROJO_ERROR, PantallaChat
from tests.test_local_client import FakeServer, _chunk

SIN_CONEXION = (
    "No pude conectarme con el modelo local en http://localhost:1234/v1. "
    "Revisa que LM Studio u Ollama esté abierto con su servidor activo."
)


class FakePage:
    def __init__(self, ejecutar: bool = True) -> None:
        self.ejecutar = ejecutar
        self.pendientes: list = []
        self.dialogos: list = []

    def update(self, *controles) -> None:
        pass

    def show_dialog(self, dialogo) -> None:
        self.dialogos.append(dialogo)

    def pop_dialog(self):
        return self.dialogos.pop() if self.dialogos else None

    def run_thread(self, funcion, *args) -> None:
        if self.ejecutar:
            funcion(*args)
        else:
            self.pendientes.append((funcion, args))

    def correr_pendientes(self) -> None:
        while self.pendientes:
            funcion, args = self.pendientes.pop(0)
            funcion(*args)


class Servidor(FakeServer):
    """Fake server that can be switched off."""

    def __init__(self, chunks=None, models=None, apagado=False):
        super().__init__(chunks=chunks, models=models)
        self.apagado = apagado

    def __call__(self, request):
        if self.apagado:
            raise httpx.ConnectError("refused", request=request)
        return super().__call__(request)


def _http(server):
    return httpx.Client(transport=httpx.MockTransport(server))


@pytest.fixture(autouse=True)
def datos(tmp_path, monkeypatch):
    monkeypatch.setenv("EVA_DATA_DIR", str(tmp_path / "datos"))
    return tmp_path / "datos"


def _usar_servidor(monkeypatch, server):
    monkeypatch.setattr(chat, "conectar", lambda url, modelo: conectar(url, modelo, http_client=_http(server)))
    monkeypatch.setattr(
        ajustes,
        "ClienteLocal",
        lambda modelo, base_url: ClienteLocal(modelo=modelo, base_url=base_url, http_client=_http(server)),
    )


def _pantalla(tmp_path, page=None, config=None):
    pantalla = PantallaChat(page or FakePage(), Historial(tmp_path / "eva.db"), config or Config())

    async def sin_foco():
        pass

    pantalla.campo.focus = sin_foco  # the real one needs a Flet window
    return pantalla


def _textos(pantalla):
    return [b.content.controls[0].content for b in pantalla.mensajes.controls]


def _valores(pantalla):
    return [t.value for t in _textos(pantalla)]


def _enviar(pantalla, texto):
    pantalla.campo.value = texto
    asyncio.run(pantalla._enviar(None))


# ----- settings dialog -----


def test_save_is_disabled_when_the_provider_has_no_chat_models(monkeypatch):
    _usar_servidor(monkeypatch, Servidor(models=["text-embedding-x"]))
    dialogo = DialogoAjustes(FakePage(), Config(), lambda c: None)
    dialogo.abrir()

    assert dialogo.boton_guardar.disabled is True
    assert dialogo.estado.value == "El servidor no tiene modelos de chat instalados."
    assert dialogo.modelo.value is None


def test_save_is_disabled_when_the_provider_is_offline(monkeypatch):
    _usar_servidor(monkeypatch, Servidor(apagado=True))
    dialogo = DialogoAjustes(FakePage(), Config(), lambda c: None)
    dialogo.abrir()

    assert dialogo.boton_guardar.disabled is True
    assert (dialogo.estado.value, dialogo.estado.color) == (SIN_CONEXION, ROJO_ERROR)


def test_dialog_preselects_the_saved_model_or_the_first_one(monkeypatch):
    _usar_servidor(monkeypatch, Servidor(models=["a", "b"]))
    guardado = DialogoAjustes(FakePage(), Config(modelo="b"), lambda c: None)
    guardado.abrir()
    borrado = DialogoAjustes(FakePage(), Config(modelo="borrado"), lambda c: None)
    borrado.abrir()

    assert guardado.modelo.value == "b"
    assert borrado.modelo.value == "a"
    assert guardado.boton_guardar.disabled is False
    assert [o.key for o in guardado.modelo.options] == ["a", "b"]


def test_dialog_loads_models_off_the_ui_loop_showing_connecting(monkeypatch):
    _usar_servidor(monkeypatch, Servidor(models=["a"]))
    page = FakePage(ejecutar=False)
    dialogo = DialogoAjustes(page, Config(), lambda c: None)
    dialogo.abrir()

    assert dialogo.estado.value == "Conectando…"
    assert dialogo.boton_guardar.disabled is True
    page.correr_pendientes()
    assert dialogo.modelo.value == "a"
    assert dialogo.boton_guardar.disabled is False


def test_saving_settings_writes_config_closes_and_applies(monkeypatch, datos):
    _usar_servidor(monkeypatch, Servidor(models=["a", "b"]))
    page = FakePage()
    aplicadas = []
    dialogo = DialogoAjustes(page, Config(extras={"voz": "es_MX"}), aplicadas.append)
    dialogo.abrir()
    dialogo.modelo.value = "b"
    dialogo._guardar(None)

    assert page.dialogos == []
    assert [(c.proveedor, c.modelo) for c in aplicadas] == [("lmstudio", "b")]
    assert json.loads((datos / "config.json").read_text(encoding="utf-8")) == {
        "proveedor": "lmstudio",
        "modelo": "b",
        "voz": "es_MX",
    }


def test_settings_that_cannot_be_saved_keep_the_dialog_open(monkeypatch, datos):
    _usar_servidor(monkeypatch, Servidor(models=["a"]))

    def bloqueado(config, ruta=None):
        raise PermissionError(13, "Acceso denegado")

    monkeypatch.setattr(ajustes, "guardar_config", bloqueado)
    page = FakePage()
    aplicadas = []
    dialogo = DialogoAjustes(page, Config(), aplicadas.append)
    dialogo.abrir()
    dialogo._guardar(None)

    assert page.dialogos == [dialogo.dialogo]
    assert aplicadas == []
    assert dialogo.estado.value == "No pude guardar los ajustes: [Errno 13] Acceso denegado"
    assert dialogo.estado.color == ROJO_ERROR
    assert not (datos / "config.json").exists()


# ----- chat screen -----


def test_a_turn_adopts_the_new_conversation_id(monkeypatch, tmp_path):
    _usar_servidor(monkeypatch, Servidor(models=["m"], chunks=[_chunk(content="Hola"), _chunk(content=" Ana")]))
    pantalla = _pantalla(tmp_path)
    pantalla.iniciar()
    assert pantalla.etiqueta_modelo.value == "LM Studio · m"

    _enviar(pantalla, "Soy Ana")

    (resumen,) = pantalla.historial.listar_conversaciones()
    assert pantalla.conversacion_id == resumen.id
    assert _valores(pantalla) == ["Soy Ana", "Hola Ana"]
    assert pantalla.ocupado is False and pantalla.boton.disabled is False
    assert [t.title.value for t in pantalla.lista.controls] == ["Soy Ana"]


def test_busy_screen_ignores_new_open_settings_and_send(monkeypatch, tmp_path):
    _usar_servidor(monkeypatch, Servidor(models=["m"], chunks=[_chunk(content="ok")]))
    page = FakePage()
    pantalla = _pantalla(tmp_path, page)
    pantalla.iniciar()
    otra = pantalla.historial.guardar_turno(None, "otra", [{"role": "user", "content": "x"}])
    _enviar(pantalla, "hola")
    actual = pantalla.conversacion_id

    pantalla.ocupado = True
    pantalla._nueva()
    pantalla._abrir(otra)
    pantalla._abrir_ajustes()
    _enviar(pantalla, "otra vez")

    assert pantalla.conversacion_id == actual
    assert _valores(pantalla) == ["hola", "ok"]
    assert page.dialogos == []


def test_broken_stream_shows_the_error_and_keeps_the_conversation(monkeypatch, tmp_path):
    class Cortado(httpx.SyncByteStream):
        def __iter__(self):
            yield f"data: {json.dumps(_chunk(content='Ho'))}\n\n".encode()
            raise httpx.RemoteProtocolError("peer closed connection")

    _usar_servidor(monkeypatch, Servidor(models=["m"]))
    pantalla = _pantalla(tmp_path)
    pantalla.iniciar()

    def roto(request):
        if request.url.path.endswith("/models"):
            return Servidor(models=["m"])(request)
        return httpx.Response(200, stream=Cortado(), headers={"content-type": "text/event-stream"})

    pantalla.cliente = conectar(pantalla.config.servidor, "", http_client=_http(roto))
    _enviar(pantalla, "hola")

    respuesta = _textos(pantalla)[-1]
    assert (respuesta.value, respuesta.color) == (MENSAJE_CONEXION_PERDIDA, ROJO_ERROR)
    assert pantalla.conversacion_id is None
    assert pantalla.ocupado is False


def test_unexpected_error_is_shown_logged_and_releases_the_screen(monkeypatch, tmp_path, caplog):
    _usar_servidor(monkeypatch, Servidor(models=["m"], chunks=[_chunk(content="Hola")]))
    pantalla = _pantalla(tmp_path)
    pantalla.iniciar()
    with sqlite3.connect(tmp_path / "eva.db") as db:
        db.execute("CREATE TRIGGER falla BEFORE INSERT ON mensajes BEGIN SELECT RAISE(ABORT, 'disco lleno'); END")

    with caplog.at_level(logging.ERROR):
        _enviar(pantalla, "hola")

    respuesta = _textos(pantalla)[-1]
    assert (respuesta.value, respuesta.color) == ("Ocurrió un error inesperado: disco lleno", ROJO_ERROR)
    assert pantalla.conversacion_id is None
    assert pantalla.ocupado is False and pantalla.boton.disabled is False
    assert any(r.exc_info and "disco lleno" in str(r.exc_info[1]) for r in caplog.records)


def test_repeated_connection_errors_show_a_single_bubble(monkeypatch, tmp_path):
    server = Servidor(models=["m"], chunks=[_chunk(content="Hola")], apagado=True)
    _usar_servidor(monkeypatch, server)
    pantalla = _pantalla(tmp_path)
    pantalla.iniciar()
    assert _valores(pantalla) == [SIN_CONEXION]
    assert pantalla.etiqueta_modelo.value == "LM Studio · sin conexión"

    _enviar(pantalla, "hola")
    _enviar(pantalla, "hola")
    pantalla._ajustes_guardados(Config())
    assert _valores(pantalla) == [SIN_CONEXION]
    assert pantalla.campo.value == "hola"  # the question is kept for retrying

    server.apagado = False
    _enviar(pantalla, "hola")
    assert _valores(pantalla) == ["hola", "Hola"]
    assert pantalla.etiqueta_modelo.value == "LM Studio · m"


def test_connecting_runs_off_the_ui_loop_and_blocks_sending(monkeypatch, tmp_path):
    server = Servidor(models=["m"], chunks=[_chunk(content="Hola")])
    _usar_servidor(monkeypatch, server)
    page = FakePage(ejecutar=False)
    pantalla = _pantalla(tmp_path, page)

    pantalla.iniciar()
    assert pantalla.etiqueta_modelo.value == "Conectando…"
    assert pantalla.ocupado is True and pantalla.cliente is None
    _enviar(pantalla, "hola")
    assert len(page.pendientes) == 1  # the send was not queued
    page.correr_pendientes()
    assert pantalla.etiqueta_modelo.value == "LM Studio · m"
    assert pantalla.ocupado is False

    pantalla._ajustes_guardados(Config(proveedor="lmstudio", modelo="m"))
    assert pantalla.etiqueta_modelo.value == "Conectando…"
    assert pantalla.ocupado is True
    page.correr_pendientes()
    assert pantalla.ocupado is False


def test_field_is_locked_while_connecting_before_sending(monkeypatch, tmp_path):
    server = Servidor(models=["m"], chunks=[_chunk(content="Hola")], apagado=True)
    _usar_servidor(monkeypatch, server)
    page = FakePage(ejecutar=False)
    pantalla = _pantalla(tmp_path, page)
    pantalla.iniciar()
    page.correr_pendientes()
    server.apagado = False

    _enviar(pantalla, "hola")
    assert pantalla.campo.disabled is True  # nothing can be typed and lost
    page.correr_pendientes()

    assert _valores(pantalla)[-2:] == ["hola", "Hola"]
    assert pantalla.campo.value == ""
    assert pantalla.campo.disabled is False


def test_field_is_unlocked_even_if_connecting_raises(monkeypatch, tmp_path):
    server = Servidor(models=["m"], apagado=True)
    _usar_servidor(monkeypatch, server)
    page = FakePage(ejecutar=False)
    pantalla = _pantalla(tmp_path, page)
    pantalla.iniciar()
    page.correr_pendientes()

    def romper():
        raise RuntimeError("inesperado")

    pantalla._conectar = romper
    _enviar(pantalla, "hola")
    page.correr_pendientes()

    assert pantalla.campo.disabled is False
    assert pantalla.ocupado is False
    assert pantalla.campo.value == "hola"


def test_field_is_unlocked_when_reconnecting_fails(monkeypatch, tmp_path):
    server = Servidor(models=["m"], apagado=True)
    _usar_servidor(monkeypatch, server)
    page = FakePage(ejecutar=False)
    pantalla = _pantalla(tmp_path, page)
    pantalla.iniciar()
    page.correr_pendientes()

    vistos = []
    page.update = lambda *c: vistos.append((pantalla.campo.disabled, pantalla.boton.disabled))
    _enviar(pantalla, "hola")
    page.correr_pendientes()

    assert pantalla.campo.disabled is False
    assert pantalla.campo.value == "hola"  # kept to retry
    assert vistos[-1] == (False, False)  # the unlock reaches the screen


def test_reconnecting_before_sending_runs_off_the_ui_loop(monkeypatch, tmp_path):
    server = Servidor(models=["m"], chunks=[_chunk(content="Hola")], apagado=True)
    _usar_servidor(monkeypatch, server)
    page = FakePage(ejecutar=False)
    pantalla = _pantalla(tmp_path, page)
    pantalla.iniciar()
    page.correr_pendientes()
    server.apagado = False

    _enviar(pantalla, "hola")
    assert pantalla.etiqueta_modelo.value == "Conectando…"
    assert pantalla.ocupado is True
    _enviar(pantalla, "otra")
    assert len(page.pendientes) == 1  # the second Enter is not queued
    page.correr_pendientes()

    assert _valores(pantalla) == ["hola", "Hola"]
    assert pantalla.ocupado is False


def test_clearing_the_connection_error_keeps_an_identical_saved_message(monkeypatch, tmp_path):
    server = Servidor(models=["m"], chunks=[_chunk(content="Hola")], apagado=True)
    _usar_servidor(monkeypatch, server)
    pantalla = _pantalla(tmp_path)
    pantalla.iniciar()
    cid = pantalla.historial.guardar_turno(
        None, "vieja", [{"role": "user", "content": "x"}, {"role": "assistant", "content": SIN_CONEXION}]
    )
    pantalla._abrir(cid)
    _enviar(pantalla, "hola")
    assert _valores(pantalla) == ["x", SIN_CONEXION, SIN_CONEXION]

    server.apagado = False
    _enviar(pantalla, "hola")
    assert _valores(pantalla) == ["x", SIN_CONEXION, "hola", "Hola"]


def test_dialog_unexpected_error_does_not_stay_connecting(monkeypatch):
    def roto(modelo, base_url):
        raise RuntimeError("x")

    monkeypatch.setattr(ajustes, "ClienteLocal", roto)
    d = DialogoAjustes(FakePage(), Config(), lambda c: None)
    d.abrir()

    assert d.estado.value == "Ocurrió un error inesperado: x"
    assert d.estado.color == ROJO_ERROR
    assert d.boton_guardar.disabled is True


def test_dialog_keeps_only_the_latest_model_load(monkeypatch):
    # Answers in execution order: the newer load runs first, the older one last.
    respuestas = iter([["nuevo"], ["viejo"]])

    class Cliente:
        def __init__(self, modelo, base_url):
            self.modelos = next(respuestas)

        def listar_modelos(self):
            return self.modelos

    monkeypatch.setattr(ajustes, "ClienteLocal", Cliente)
    page = FakePage(ejecutar=False)
    d = DialogoAjustes(page, Config(), lambda c: None)
    d._cargar_modelos()
    d._cargar_modelos()
    primera, segunda = page.pendientes
    page.pendientes = [segunda, primera]  # the older load finishes last
    page.correr_pendientes()

    assert [opcion.text for opcion in d.modelo.options] == ["nuevo"]
