"""Tests for the microphone button: record with one click, stop with another,
transcribe off the UI loop and send the text like a typed question.

A fake recorder stands in for ``flet_audio_recorder.AudioRecorder`` and a fake
transcriber for Whisper, so no microphone, model or network is needed.
"""

import asyncio
import threading
from pathlib import Path

import flet as ft
import pytest

from app.config import Config
from app.memory.history import Historial
from app.ui.chat import ROJO_ERROR, PantallaChat
from app.voice.stt import ErrorVoz
from tests.test_local_client import _chunk
from tests.test_ui import FakePage, Servidor, _usar_servidor, _valores, _textos

SIN_PERMISO = (
    "Eva no tiene permiso para usar el micrófono. Actívalo en la configuración de privacidad de Windows."
)
SIN_MICROFONO = "No encontré ningún micrófono conectado."
NO_TE_ENTENDI = "No te entendí. Intenta hablar de nuevo."
TRANSCRIBIENDO = "Transcribiendo…"
DESCARGANDO = "Descargando el modelo de voz (unos 460 MB). Solo pasa la primera vez…"


@pytest.fixture(autouse=True)
def datos(tmp_path, monkeypatch):
    monkeypatch.setenv("EVA_DATA_DIR", str(tmp_path / "datos"))


class GrabadorFalso:
    """Async API of ``AudioRecorder``; writes a small file where it records."""

    def __init__(self, permiso=True, dispositivos=("Micrófono",), error_inicio=None, error_fin=None):
        self.permiso = permiso
        self.dispositivos = list(dispositivos)
        self.error_inicio = error_inicio
        self.error_fin = error_fin
        self.llamadas: list[str] = []
        self.rutas: list[str] = []
        self.al_pedir_permiso = None  # optional async hook run inside has_permission

    async def has_permission(self):
        self.llamadas.append("permiso")
        if self.al_pedir_permiso is not None:
            await self.al_pedir_permiso()
        return self.permiso

    async def get_input_devices(self):
        self.llamadas.append("dispositivos")
        return self.dispositivos

    async def start_recording(self, output_path=None, configuration=None, upload=None):
        self.llamadas.append("empezar")
        self.rutas.append(output_path)
        Path(output_path).write_bytes(b"RIFF")
        if self.error_inicio is not None:
            raise self.error_inicio
        return True

    async def stop_recording(self):
        self.llamadas.append("detener")
        if self.error_fin is not None:
            raise self.error_fin
        return self.rutas[-1]

    async def cancel_recording(self):
        self.llamadas.append("cancelar")


class TranscriptorFalso:
    def __init__(self, texto="¿Qué clima hace hoy?", error=None, descarga=False):
        self.texto = texto
        self.error = error
        self.descarga = descarga
        self.pantalla = None
        self.hilos: list[int] = []
        self.vistos: list = []  # (step, chat texts, mic disabled) at each call
        self.rutas: list[str] = []

    def necesita_descarga(self):
        return self.descarga

    def _ver(self, paso):
        self.hilos.append(threading.get_ident())
        p = self.pantalla
        self.vistos.append((paso, _valores(p), p.microfono.boton.disabled, p.ocupado))

    def cargar(self):
        self._ver("cargar")
        self.descarga = False

    def transcribir(self, ruta):
        self._ver("transcribir")
        self.rutas.append(ruta)
        assert Path(ruta).exists()
        if self.error is not None:
            raise self.error
        return self.texto


def _pantalla(monkeypatch, tmp_path, page=None, grabador=None, transcriptor=None, chunks=("Soleado",)):
    _usar_servidor(monkeypatch, Servidor(models=["m"], chunks=[_chunk(content=c) for c in chunks]))
    grabador = grabador or GrabadorFalso()
    transcriptor = transcriptor or TranscriptorFalso()
    pantalla = PantallaChat(
        page or FakePage(),
        Historial(tmp_path / "eva.db"),
        Config(),
        transcriptor=transcriptor,
        grabador=grabador,
    )
    transcriptor.pantalla = pantalla

    async def sin_foco():
        pass

    pantalla.campo.focus = sin_foco  # the real one needs a Flet window
    return pantalla, grabador, transcriptor


def _clic(pantalla):
    asyncio.run(pantalla.microfono.boton.on_click(None))


def _en_reposo(pantalla):
    boton = pantalla.microfono.boton
    return (boton.icon, boton.tooltip, boton.disabled, pantalla.ocupado) == (ft.Icons.MIC, "Hablar", False, False)


def _ultimo_error(pantalla):
    texto = _textos(pantalla)[-1]
    return texto.value if texto.color == ROJO_ERROR else None


def test_the_microphone_sits_next_to_the_send_button(monkeypatch, tmp_path):
    pantalla, _, _ = _pantalla(monkeypatch, tmp_path)
    vista = pantalla.vista()
    fila = vista.controls[1].controls[1].content

    assert fila.controls == [pantalla.campo, pantalla.microfono.boton, pantalla.boton]
    assert _en_reposo(pantalla)


def test_first_click_checks_the_microphone_and_starts_recording_a_wav(monkeypatch, tmp_path):
    pantalla, grabador, _ = _pantalla(monkeypatch, tmp_path)
    pantalla.iniciar()
    vistos = []

    async def al_pedir_permiso():
        boton = pantalla.microfono.boton
        vistos.append((boton.icon, boton.tooltip, boton.disabled))

    grabador.al_pedir_permiso = al_pedir_permiso
    _clic(pantalla)

    boton = pantalla.microfono.boton
    assert grabador.llamadas == ["permiso", "dispositivos", "empezar"]
    assert grabador.rutas[0].endswith(".wav")
    # It turns into the stop button at once, locked until recording starts.
    assert vistos == [(ft.Icons.STOP, "Detener", True)]
    assert (boton.icon, boton.tooltip, boton.disabled) == (ft.Icons.STOP, "Detener", False)
    assert boton.icon_color == ROJO_ERROR
    assert pantalla.estado.value == "Escuchando…"
    # Nothing typed can be sent while recording.
    assert pantalla.ocupado is True and pantalla.boton.disabled is True
    Path(grabador.rutas[0]).unlink()


def test_second_click_transcribes_off_the_ui_loop_and_sends_the_text(monkeypatch, tmp_path):
    pantalla, grabador, transcriptor = _pantalla(monkeypatch, tmp_path)
    pantalla.iniciar()
    pantalla.campo.value = "borrador"
    _clic(pantalla)
    _clic(pantalla)

    assert grabador.llamadas == ["permiso", "dispositivos", "empezar", "detener"]
    assert transcriptor.vistos == [("transcribir", [TRANSCRIBIENDO], True, True)]
    assert pantalla.estado.value == "Listo"
    assert transcriptor.hilos[0] != threading.get_ident()
    assert transcriptor.rutas == grabador.rutas
    # Same path as typed text: user bubble, answer and history.
    assert _valores(pantalla) == ["¿Qué clima hace hoy?", "Soleado"]
    assert pantalla.historial.mensajes(pantalla.conversacion_id) == [
        {"role": "user", "content": "¿Qué clima hace hoy?"},
        {"role": "assistant", "content": "Soleado"},
    ]
    assert pantalla.campo.value == "borrador"  # what was typed is kept
    assert not Path(grabador.rutas[0]).exists()
    assert _en_reposo(pantalla)


def test_the_first_use_shows_the_model_download_then_transcribing(monkeypatch, tmp_path):
    transcriptor = TranscriptorFalso(descarga=True)
    pantalla, _, _ = _pantalla(monkeypatch, tmp_path, transcriptor=transcriptor)
    pantalla.iniciar()
    _clic(pantalla)
    _clic(pantalla)

    assert [(paso, textos) for paso, textos, _, _ in transcriptor.vistos] == [
        ("cargar", [DESCARGANDO]),
        ("transcribir", [TRANSCRIBIENDO]),
    ]
    assert all(h != threading.get_ident() for h in transcriptor.hilos)
    assert _valores(pantalla) == ["¿Qué clima hace hoy?", "Soleado"]


@pytest.mark.parametrize("texto", ["", "   \n "])
def test_an_empty_transcription_sends_nothing(monkeypatch, tmp_path, texto):
    pantalla, grabador, _ = _pantalla(monkeypatch, tmp_path, transcriptor=TranscriptorFalso(texto=texto))
    pantalla.iniciar()
    _clic(pantalla)
    _clic(pantalla)

    assert _valores(pantalla) == [NO_TE_ENTENDI]
    assert _ultimo_error(pantalla) == NO_TE_ENTENDI
    assert pantalla.historial.listar_conversaciones() == []
    assert not Path(grabador.rutas[0]).exists()
    assert _en_reposo(pantalla)


def test_without_permission_nothing_is_recorded(monkeypatch, tmp_path):
    pantalla, grabador, _ = _pantalla(monkeypatch, tmp_path, grabador=GrabadorFalso(permiso=False))
    pantalla.iniciar()
    _clic(pantalla)

    assert grabador.llamadas == ["permiso"]
    assert _valores(pantalla) == [SIN_PERMISO]
    assert _ultimo_error(pantalla) == SIN_PERMISO
    assert _en_reposo(pantalla)


def test_without_an_input_device_nothing_is_recorded(monkeypatch, tmp_path):
    pantalla, grabador, _ = _pantalla(monkeypatch, tmp_path, grabador=GrabadorFalso(dispositivos=()))
    pantalla.iniciar()
    _clic(pantalla)

    assert grabador.llamadas == ["permiso", "dispositivos"]
    assert _valores(pantalla) == [SIN_MICROFONO]
    assert _ultimo_error(pantalla) == SIN_MICROFONO
    assert _en_reposo(pantalla)


def test_a_recorder_that_fails_to_start_is_reported_and_reset(monkeypatch, tmp_path, caplog):
    grabador = GrabadorFalso(error_inicio=RuntimeError("Error catastrófico"))
    pantalla, _, _ = _pantalla(monkeypatch, tmp_path, grabador=grabador)
    pantalla.iniciar()
    _clic(pantalla)

    assert _valores(pantalla) == ["No pude grabar el audio: Error catastrófico"]
    assert _ultimo_error(pantalla) == "No pude grabar el audio: Error catastrófico"
    assert "Error catastrófico" in caplog.text
    assert not Path(grabador.rutas[0]).exists()
    assert _en_reposo(pantalla)


def test_a_recorder_that_fails_to_stop_is_reported_and_reset(monkeypatch, tmp_path):
    grabador = GrabadorFalso(error_fin=RuntimeError("dispositivo desconectado"))
    pantalla, _, transcriptor = _pantalla(monkeypatch, tmp_path, grabador=grabador)
    pantalla.iniciar()
    _clic(pantalla)
    _clic(pantalla)

    assert transcriptor.vistos == []
    assert grabador.llamadas[-2:] == ["detener", "cancelar"]
    assert _valores(pantalla) == ["No pude grabar el audio: dispositivo desconectado"]
    assert _ultimo_error(pantalla) == "No pude grabar el audio: dispositivo desconectado"
    assert not Path(grabador.rutas[0]).exists()
    assert _en_reposo(pantalla)

    _clic(pantalla)  # the next try records again
    assert grabador.llamadas.count("empezar") == 2
    Path(grabador.rutas[1]).unlink()


def test_a_transcription_error_is_shown_and_nothing_is_sent(monkeypatch, tmp_path):
    error = ErrorVoz("No pude descargar el modelo de voz. Revisa tu conexión a internet.")
    pantalla, grabador, _ = _pantalla(monkeypatch, tmp_path, transcriptor=TranscriptorFalso(error=error))
    pantalla.iniciar()
    _clic(pantalla)
    _clic(pantalla)

    assert _valores(pantalla) == [str(error)]
    assert _ultimo_error(pantalla) == str(error)
    assert pantalla.historial.listar_conversaciones() == []
    assert not Path(grabador.rutas[0]).exists()
    assert _en_reposo(pantalla)


def test_the_microphone_is_disabled_while_eva_connects_or_answers(monkeypatch, tmp_path):
    page = FakePage(ejecutar=False)
    pantalla, grabador, _ = _pantalla(monkeypatch, tmp_path, page=page)
    pantalla.iniciar()  # connecting

    assert pantalla.microfono.boton.disabled is True
    _clic(pantalla)
    assert grabador.llamadas == []
    page.correr_pendientes()
    assert pantalla.microfono.boton.disabled is False

    _clic(pantalla)
    _clic(pantalla)  # sends; the answer waits off the UI loop
    assert _valores(pantalla) == ["¿Qué clima hace hoy?", "…"]
    assert pantalla.microfono.boton.disabled is True
    _clic(pantalla)
    assert grabador.llamadas.count("empezar") == 1
    page.correr_pendientes()
    assert _valores(pantalla) == ["¿Qué clima hace hoy?", "Soleado"]
    assert _en_reposo(pantalla)


def test_a_click_while_one_is_being_handled_never_starts_again(monkeypatch, tmp_path):
    pantalla, grabador, _ = _pantalla(monkeypatch, tmp_path)
    pantalla.iniciar()

    async def otro_clic():
        await pantalla.microfono.boton.on_click(None)

    grabador.al_pedir_permiso = otro_clic
    _clic(pantalla)

    assert grabador.llamadas == ["permiso", "dispositivos", "empezar"]
    Path(grabador.rutas[0]).unlink()


def test_typed_messages_still_work_with_the_microphone_there(monkeypatch, tmp_path):
    pantalla, grabador, transcriptor = _pantalla(monkeypatch, tmp_path)
    pantalla.iniciar()
    pantalla.campo.value = "hola"
    asyncio.run(pantalla._enviar(None))

    assert _valores(pantalla) == ["hola", "Soleado"]
    assert pantalla.campo.value == ""
    assert grabador.llamadas == [] and transcriptor.vistos == []
    assert _en_reposo(pantalla)


def test_spoken_text_waits_for_the_connection_like_typed_text(monkeypatch, tmp_path):
    server = Servidor(models=["m"], chunks=[_chunk(content="Soleado")], apagado=True)
    pantalla, _, _ = _pantalla(monkeypatch, tmp_path)
    _usar_servidor(monkeypatch, server)
    pantalla.iniciar()  # fails: the connection-error bubble shows
    assert pantalla.cliente is None

    _clic(pantalla)
    _clic(pantalla)
    # Still offline: the spoken question is kept in the field for retrying.
    assert pantalla.campo.value == "¿Qué clima hace hoy?"

    server.apagado = False
    asyncio.run(pantalla._enviar(None))
    assert _valores(pantalla) == ["¿Qué clima hace hoy?", "Soleado"]


class PaginaQueMira(FakePage):
    """Records what the chat shows at every page.update (what the user sees)."""

    def __init__(self) -> None:
        super().__init__()
        self.pantalla = None
        self.vistas: list[list[str]] = []

    def update(self, *controles) -> None:
        if self.pantalla is not None:
            self.vistas.append([t.value for t in _textos(self.pantalla)])


def test_transcribing_is_on_screen_before_the_transcription_starts(monkeypatch, tmp_path):
    page = PaginaQueMira()
    pantalla, _, transcriptor = _pantalla(monkeypatch, tmp_path, page=page)
    page.pantalla = pantalla
    pantalla.iniciar()
    _clic(pantalla)
    vistas_previas = []
    original = transcriptor.transcribir

    def transcribir(ruta):
        vistas_previas.extend(page.vistas)
        return original(ruta)

    transcriptor.transcribir = transcribir
    _clic(pantalla)

    # The window must have been refreshed with the bubble before the slow work.
    assert vistas_previas and vistas_previas[-1][-1:] == [TRANSCRIBIENDO]


GRABANDO = "Grabando… pulsa el botón rojo para terminar."


def test_recording_is_on_screen_while_the_audio_is_captured(monkeypatch, tmp_path):
    page = PaginaQueMira()
    pantalla, _, _ = _pantalla(monkeypatch, tmp_path, page=page)
    page.pantalla = pantalla
    pantalla.iniciar()
    _clic(pantalla)

    assert page.vistas[-1][-1:] == [GRABANDO]  # shown while recording
    _clic(pantalla)
    assert GRABANDO not in [t.value for t in _textos(pantalla)]
    assert _valores(pantalla) == ["¿Qué clima hace hoy?", "Soleado"]


def test_the_recording_notice_goes_away_when_stopping_fails(monkeypatch, tmp_path):
    pantalla, _, _ = _pantalla(monkeypatch, tmp_path, grabador=GrabadorFalso(error_fin=RuntimeError("roto")))
    pantalla.iniciar()
    _clic(pantalla)
    _clic(pantalla)

    assert GRABANDO not in [t.value for t in _textos(pantalla)]
    assert _en_reposo(pantalla)
