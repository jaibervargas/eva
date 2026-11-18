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


def test_responsive_shell_starts_with_full_layout_and_hides_panels_on_narrow_width(tmp_path):
    page = FakePage()
    page.width = 1280
    pantalla = _pantalla(tmp_path, page)
    pantalla.vista()

    assert pantalla.estado.value == "Listo"
    assert pantalla.lateral.visible is True
    assert pantalla.panel_eva.visible is True

    page.width = 640
    pantalla._ajustar_layout(None)
    assert pantalla.lateral.visible is False
    assert pantalla.panel_eva.visible is False


def test_busy_state_is_visible_and_restores_ready_state(tmp_path):
    page = FakePage()
    pantalla = _pantalla(tmp_path, page)
    pantalla.vista()

    pantalla._ocupar(True)
    assert pantalla.estado.value == "Pensando…"
    assert pantalla.estado.color == chat.GRIS_TEXTO

    pantalla._ocupar(False)
    assert pantalla.estado.value == "Listo"


def test_connection_state_is_visible(tmp_path):
    page = FakePage(ejecutar=False)
    pantalla = _pantalla(tmp_path, page)
    pantalla.vista()

    pantalla._conectar_en_segundo_plano()
    assert pantalla.estado.value == "Conectando…"


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


# ----- long-term memory (Fase 2B) -----

from app.llm.conversacion import MemoriaUsuario  # noqa: E402
from app.ui.recuerdos import DialogoRecuerdos  # noqa: E402
from app.memory.engram_client import Recuerdo  # noqa: E402
from tests.memoria_falsa import MemoriaFalsa  # noqa: E402
from tests.test_memoria_usuario import ANA, HECHOS_ANA, LLMGuion  # noqa: E402

NO_DISPONIBLE = "Memoria a largo plazo no disponible"
VACIO = "Eva todavía no tiene recuerdos tuyos."


def _con_memoria(monkeypatch, tmp_path, falsa, llm=None, page=None):
    llm = llm or LLMGuion(respuesta="Hola", extraccion=HECHOS_ANA)
    monkeypatch.setattr(chat, "conectar", lambda url, modelo: llm)
    pantalla = PantallaChat(page or FakePage(), Historial(tmp_path / "eva.db"), Config(), MemoriaUsuario(falsa))

    async def sin_foco():
        pass

    pantalla.campo.focus = sin_foco
    return pantalla, llm


def test_memory_note_is_hidden_while_memory_works(monkeypatch, tmp_path):
    pantalla, _ = _con_memoria(monkeypatch, tmp_path, MemoriaFalsa())
    pantalla.iniciar()

    assert pantalla.nota_memoria.value == NO_DISPONIBLE
    assert pantalla.nota_memoria.visible is False
    assert pantalla.boton_recuerdos.disabled is False


def test_unavailable_memory_shows_the_note_and_the_chat_still_answers(monkeypatch, tmp_path):
    pantalla, llm = _con_memoria(monkeypatch, tmp_path, MemoriaFalsa(disponible=False))
    pantalla.iniciar()
    assert pantalla.nota_memoria.visible is True

    _enviar(pantalla, ANA)

    assert _valores(pantalla) == [ANA, "Hola"]
    assert len(llm.recibidos) == 1  # no extraction without memory
    assert pantalla.ocupado is False


def test_without_memory_the_note_shows_and_recuerdos_is_disabled(monkeypatch, tmp_path):
    _usar_servidor(monkeypatch, Servidor(models=["m"], chunks=[_chunk(content="Hola")]))
    pantalla = _pantalla(tmp_path)
    pantalla.iniciar()

    assert pantalla.nota_memoria.visible is True
    assert pantalla.boton_recuerdos.disabled is True
    _enviar(pantalla, "hola")
    assert _valores(pantalla) == ["hola", "Hola"]


def test_memory_loads_off_the_ui_loop_at_startup(monkeypatch, tmp_path):
    falsa = MemoriaFalsa([Recuerdo(1, "Nombre", "Ana")])
    page = FakePage(ejecutar=False)
    pantalla, _ = _con_memoria(monkeypatch, tmp_path, falsa, page=page)
    pantalla.iniciar()

    assert falsa.llamadas == []
    assert pantalla.nota_memoria.visible is False
    page.correr_pendientes()
    assert falsa.llamadas == ["listar"]
    assert pantalla.memoria.recuerdos() == [Recuerdo(1, "Nombre", "Ana")]


def test_facts_are_learned_in_the_background_after_the_answer(monkeypatch, tmp_path):
    falsa = MemoriaFalsa()
    page = FakePage(ejecutar=False)
    pantalla, llm = _con_memoria(monkeypatch, tmp_path, falsa, page=page)
    pantalla.iniciar()
    page.correr_pendientes()

    _enviar(pantalla, ANA)
    funcion, args = page.pendientes.pop(0)  # the answer
    funcion(*args)

    # The screen is free and answered before anything is extracted or saved.
    assert _valores(pantalla) == [ANA, "Hola"]
    assert pantalla.ocupado is False and pantalla.boton.disabled is False
    assert len(page.pendientes) == 1 and falsa.datos == {}
    assert len(llm.recibidos) == 1

    page.correr_pendientes()
    assert [(r.titulo, r.contenido) for r in falsa.listar_sin_registro()] == [
        ("Bebida favorita", "Le gusta el café"),
        ("Nombre", "El usuario se llama Ana"),
    ]


def test_a_turn_without_text_learns_nothing(monkeypatch, tmp_path):
    falsa = MemoriaFalsa()
    pantalla, llm = _con_memoria(monkeypatch, tmp_path, falsa, llm=LLMGuion(respuesta="", extraccion=HECHOS_ANA))
    pantalla.iniciar()

    _enviar(pantalla, ANA)

    assert _valores(pantalla) == [ANA, "(Eva no devolvió texto)"]
    assert len(llm.recibidos) == 1
    assert falsa.datos == {}


def test_eva_remembers_the_user_in_a_new_conversation_from_the_screen(monkeypatch, tmp_path):
    falsa = MemoriaFalsa()
    pantalla, llm = _con_memoria(monkeypatch, tmp_path, falsa)
    pantalla.iniciar()
    _enviar(pantalla, ANA)
    primera = pantalla.conversacion_id

    pantalla._nueva()
    _enviar(pantalla, "¿Cómo me llamo?")

    assert pantalla.conversacion_id != primera
    ultimo = llm.chats()[-1]
    assert ultimo[1]["role"] == "system"
    assert "- Nombre: El usuario se llama Ana" in ultimo[1]["content"].split("\n")
    assert {"role": "user", "content": ANA} not in ultimo
    assert pantalla.historial.mensajes(pantalla.conversacion_id) == [
        {"role": "user", "content": "¿Cómo me llamo?"},
        {"role": "assistant", "content": "Hola"},
    ]


def test_memory_that_stops_working_keeps_the_chat_and_shows_the_note(monkeypatch, tmp_path, caplog):
    falsa = MemoriaFalsa()
    pantalla, _ = _con_memoria(monkeypatch, tmp_path, falsa)
    pantalla.iniciar()
    assert pantalla.nota_memoria.visible is False

    falsa._disponible = False  # Engram died
    with caplog.at_level(logging.WARNING):
        _enviar(pantalla, ANA)

    assert _valores(pantalla) == [ANA, "Hola"]
    assert pantalla.ocupado is False
    assert pantalla.nota_memoria.visible is True


def test_recuerdos_opens_from_the_sidebar(monkeypatch, tmp_path):
    page = FakePage()
    pantalla, _ = _con_memoria(monkeypatch, tmp_path, MemoriaFalsa([Recuerdo(1, "Nombre", "Ana")]), page=page)
    pantalla.iniciar()

    pantalla._abrir_recuerdos()

    (dialogo,) = page.dialogos
    assert dialogo.title.value == "Recuerdos"


def _filas(dialogo):
    return [(f.title.value, f.subtitle.value) for f in dialogo.lista.controls]


def test_recuerdos_load_off_the_ui_loop_showing_loading():
    falsa = MemoriaFalsa([Recuerdo(1, "Nombre", "El usuario se llama Ana"), Recuerdo(2, "Bebida favorita", "Le gusta el café")])
    page = FakePage(ejecutar=False)
    dialogo = DialogoRecuerdos(page, MemoriaUsuario(falsa))
    dialogo.abrir()

    assert page.dialogos == [dialogo.dialogo]
    assert dialogo.estado.value == "Cargando…"
    assert dialogo.lista.controls == []
    assert falsa.llamadas == []
    page.correr_pendientes()

    assert _filas(dialogo) == [("Bebida favorita", "Le gusta el café"), ("Nombre", "El usuario se llama Ana")]
    assert dialogo.estado.value == ""


def test_recuerdos_empty_state():
    dialogo = DialogoRecuerdos(FakePage(), MemoriaUsuario(MemoriaFalsa()))
    dialogo.abrir()

    assert dialogo.estado.value == VACIO
    assert dialogo.lista.controls == []


def test_recuerdos_load_error_is_shown(caplog):
    dialogo = DialogoRecuerdos(FakePage(), MemoriaUsuario(MemoriaFalsa(fallar="Engram no respondió a tiempo")))
    dialogo.abrir()

    assert (dialogo.estado.value, dialogo.estado.color) == (
        "No pude cargar los recuerdos: Engram no respondió a tiempo",
        ROJO_ERROR,
    )


def _pedir_borrar(page, dialogo, fila):
    dialogo.lista.controls[fila].trailing.on_click(None)
    confirmacion = page.dialogos[-1]
    assert confirmacion is not dialogo.dialogo
    return confirmacion


def test_deleting_a_memory_asks_first_and_cancel_keeps_it():
    falsa = MemoriaFalsa([Recuerdo(1, "Nombre", "Ana")])
    page = FakePage()
    dialogo = DialogoRecuerdos(page, MemoriaUsuario(falsa))
    dialogo.abrir()

    confirmacion = _pedir_borrar(page, dialogo, 0)
    assert confirmacion.title.value == "¿Borrar este recuerdo?"
    assert confirmacion.content.value == "Eva olvidará para siempre «Nombre»."
    cancelar, borrar = confirmacion.actions
    assert (cancelar.content, borrar.content) == ("Cancelar", "Borrar")
    cancelar.on_click(None)

    assert page.dialogos == [dialogo.dialogo]
    assert "borrar" not in falsa.llamadas
    assert _filas(dialogo) == [("Nombre", "Ana")]


def test_confirmed_delete_is_permanent_and_updates_the_list():
    falsa = MemoriaFalsa([Recuerdo(1, "Nombre", "Ana"), Recuerdo(2, "Ciudad", "Medellín")])
    memoria = MemoriaUsuario(falsa)
    memoria.cargar()
    page = FakePage()
    dialogo = DialogoRecuerdos(page, memoria)
    dialogo.abrir()

    _pedir_borrar(page, dialogo, 1).actions[1].on_click(None)

    assert page.dialogos == [dialogo.dialogo]
    assert 1 not in falsa.datos
    assert _filas(dialogo) == [("Ciudad", "Medellín")]
    assert memoria.recuerdos() == [Recuerdo(2, "Ciudad", "Medellín")]  # gone from the chat context too

    _pedir_borrar(page, dialogo, 0).actions[1].on_click(None)
    assert dialogo.estado.value == VACIO


def test_failed_delete_is_shown_and_keeps_the_memory():
    falsa = MemoriaFalsa([Recuerdo(1, "Nombre", "Ana")])
    page = FakePage()
    dialogo = DialogoRecuerdos(page, MemoriaUsuario(falsa))
    dialogo.abrir()
    falsa.fallar = "Engram no respondió a tiempo"

    _pedir_borrar(page, dialogo, 0).actions[1].on_click(None)

    assert (dialogo.estado.value, dialogo.estado.color) == (
        "No pude borrar el recuerdo: Engram no respondió a tiempo",
        ROJO_ERROR,
    )
    assert _filas(dialogo) == [("Nombre", "Ana")]


def test_delete_runs_off_the_ui_loop():
    falsa = MemoriaFalsa([Recuerdo(1, "Nombre", "Ana")])
    page = FakePage(ejecutar=False)
    dialogo = DialogoRecuerdos(page, MemoriaUsuario(falsa))
    dialogo.abrir()
    page.correr_pendientes()

    _pedir_borrar(page, dialogo, 0).actions[1].on_click(None)

    assert dialogo.estado.value == "Borrando…"
    assert 1 in falsa.datos
    page.correr_pendientes()
    assert falsa.datos == {}
