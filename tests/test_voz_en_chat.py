"""Eva reads her answers aloud in the chat (Fase 4, S3 and S4).

Fakes stand in for the server, the microphone, Piper and Flet's player: no
model, sound, microphone or network is used.
"""

import asyncio
from pathlib import Path

import flet as ft
import pytest

from app.config import Config
from app.memory.history import Historial
from app.ui import chat
from app.ui.chat import GRIS_TEXTO, ROJO_ERROR, PantallaChat
from app.ui.reproductor import Reproductor, VozEva, borrar_audios
from app.voice.stt import ErrorVoz
from tests.test_local_client import _chunk
from tests.test_microfono import GrabadorFalso, TranscriptorFalso
from tests.test_reproductor import AudioFalso, PaginaVoz, SintetizadorFalso
from tests.test_ui import Servidor, _textos, _usar_servidor, _valores

NO_PUDE_LEER = "No pude leer la respuesta en voz alta."
DESCARGANDO = "Descargando la voz de Eva (unos 60–115 MB). Solo pasa la primera vez…"
SIN_DESCARGA = "No pude descargar la voz de Eva. Revisa tu conexión a internet."
PREGUNTA = "¿Qué clima hace hoy?"


@pytest.fixture(autouse=True)
def datos(tmp_path, monkeypatch):
    monkeypatch.setenv("EVA_DATA_DIR", str(tmp_path / "datos"))
    AudioFalso.creados = []
    yield
    borrar_audios()


def _pantalla(monkeypatch, tmp_path, extras=None, chunks=("Soleado",), page=None, sintetizador=None):
    _usar_servidor(monkeypatch, Servidor(models=["m"], chunks=[_chunk(content=c) for c in chunks]))
    page = page or PaginaVoz()
    sintetizador = sintetizador or SintetizadorFalso(tmp_path)
    sintetizador.pagina = page
    reproductor = Reproductor(page, crear_audio=lambda ruta: AudioFalso(src=ruta, autoplay=True))
    transcriptor = TranscriptorFalso(texto=PREGUNTA)
    pantalla = PantallaChat(
        page,
        Historial(tmp_path / "eva.db"),
        Config(extras=dict(extras or {})),
        transcriptor=transcriptor,
        grabador=GrabadorFalso(),
        voz=VozEva(page, sintetizador=sintetizador, reproductor=reproductor),
    )
    transcriptor.pantalla = pantalla

    async def sin_foco():
        pass

    pantalla.campo.focus = sin_foco
    pantalla.iniciar()
    return pantalla, sintetizador, page


def _escribir(pantalla, texto=PREGUNTA):
    pantalla.campo.value = texto
    asyncio.run(pantalla._enviar(None))


def _hablar(pantalla):
    clic = pantalla.microfono.boton.on_click
    asyncio.run(clic(None))  # starts recording
    asyncio.run(clic(None))  # stops, transcribes and sends


def _dichos(sintetizador):
    return [texto for paso, _, texto in sintetizador.pasos if paso == "sintetizar"]


def _boton_escuchar(burbuja):
    controles = burbuja.content.controls
    return controles[1] if len(controles) > 1 else None


def _escuchar(pantalla, indice=-1):
    _boton_escuchar(pantalla.mensajes.controls[indice]).on_click(None)


# ----- when Eva speaks on her own -----


def test_by_default_a_typed_question_is_answered_only_in_text(monkeypatch, tmp_path):
    pantalla, sintetizador, _ = _pantalla(monkeypatch, tmp_path)
    _escribir(pantalla)

    assert _valores(pantalla) == [PREGUNTA, "Soleado"]
    assert _dichos(sintetizador) == []
    assert AudioFalso.creados == []


@pytest.mark.parametrize("extras", [{}, {"responder_en_voz": "voz"}, {"responder_en_voz": "raro"}])
def test_by_default_a_spoken_question_is_answered_aloud(monkeypatch, tmp_path, extras):
    pantalla, sintetizador, page = _pantalla(monkeypatch, tmp_path, extras=extras)
    _hablar(pantalla)

    assert _valores(pantalla) == [PREGUNTA, "Soleado"]
    assert _dichos(sintetizador) == ["Soleado"]
    assert sintetizador.en_hilo == [True]  # off the UI loop
    (audio,) = AudioFalso.creados
    assert (audio.src, audio.autoplay) == (sintetizador.rutas[0], True)
    assert page.services == [audio]


@pytest.mark.parametrize("hablada", [False, True])
def test_always_mode_speaks_typed_and_spoken_answers(monkeypatch, tmp_path, hablada):
    pantalla, sintetizador, _ = _pantalla(monkeypatch, tmp_path, extras={"responder_en_voz": "siempre"})
    _hablar(pantalla) if hablada else _escribir(pantalla)

    assert _dichos(sintetizador) == ["Soleado"]
    assert len(AudioFalso.creados) == 1


@pytest.mark.parametrize("hablada", [False, True])
def test_never_mode_speaks_nothing(monkeypatch, tmp_path, hablada):
    pantalla, sintetizador, _ = _pantalla(monkeypatch, tmp_path, extras={"responder_en_voz": "nunca"})
    _hablar(pantalla) if hablada else _escribir(pantalla)

    assert _valores(pantalla) == [PREGUNTA, "Soleado"]
    assert _dichos(sintetizador) == []
    assert AudioFalso.creados == []


def test_eva_speaks_with_the_saved_voice_once_the_screen_is_free(monkeypatch, tmp_path):
    extras = {"responder_en_voz": "siempre", "voz_eva": "es_MX-ald-medium"}
    pantalla, sintetizador, _ = _pantalla(monkeypatch, tmp_path, extras=extras)
    libre = []
    sintetizador.al_sintetizar = lambda: libre.append((pantalla.ocupado, pantalla.boton.disabled))
    _escribir(pantalla)

    assert [voz for paso, voz, _ in sintetizador.pasos] == ["es_MX-ald-medium"]
    assert libre == [(False, False)]  # the chat works while Eva speaks


def test_only_the_readable_part_of_the_answer_is_spoken(monkeypatch, tmp_path):
    chunks = ("<think>calculo</think>", "**Hoy** hace sol 😊\n", "```\nx = 1\n```")
    pantalla, sintetizador, _ = _pantalla(monkeypatch, tmp_path, extras={"responder_en_voz": "siempre"}, chunks=chunks)
    _escribir(pantalla)

    assert _dichos(sintetizador) == ["Hoy hace sol"]


def test_an_answer_with_nothing_readable_is_not_spoken(monkeypatch, tmp_path):
    pantalla, sintetizador, _ = _pantalla(
        monkeypatch, tmp_path, extras={"responder_en_voz": "siempre"}, chunks=("```\nx = 1\n```",)
    )
    _escribir(pantalla)

    assert _dichos(sintetizador) == []
    assert _valores(pantalla) == [PREGUNTA, "```\nx = 1\n```"]


def test_errors_are_never_spoken(monkeypatch, tmp_path):
    pantalla, sintetizador, _ = _pantalla(monkeypatch, tmp_path, extras={"responder_en_voz": "siempre"}, chunks=())
    _escribir(pantalla)

    assert _valores(pantalla) == [PREGUNTA, "(Eva no devolvió texto)"]
    assert _dichos(sintetizador) == []
    assert _boton_escuchar(pantalla.mensajes.controls[-1]) is None


# ----- the "Escuchar" button -----


def test_every_eva_answer_has_a_listen_button_and_user_bubbles_do_not(monkeypatch, tmp_path):
    pantalla, _, _ = _pantalla(monkeypatch, tmp_path)
    _escribir(pantalla)

    usuario, respuesta = pantalla.mensajes.controls
    boton = _boton_escuchar(respuesta)
    assert isinstance(boton, ft.IconButton)
    assert (boton.icon, boton.tooltip) == (ft.Icons.VOLUME_UP, "Escuchar")
    assert _boton_escuchar(usuario) is None


def test_listen_speaks_that_answer_even_in_never_mode(monkeypatch, tmp_path):
    pantalla, sintetizador, _ = _pantalla(monkeypatch, tmp_path, extras={"responder_en_voz": "nunca"})
    _escribir(pantalla)
    _escuchar(pantalla)

    assert _dichos(sintetizador) == ["Soleado"]
    assert sintetizador.en_hilo == [True]
    assert len(AudioFalso.creados) == 1


def test_listen_synthesizes_again_each_time_and_stops_the_previous_playback(monkeypatch, tmp_path):
    pantalla, sintetizador, page = _pantalla(monkeypatch, tmp_path)
    _escribir(pantalla)
    _escuchar(pantalla)
    _escuchar(pantalla)

    assert _dichos(sintetizador) == ["Soleado", "Soleado"]
    primero, segundo = AudioFalso.creados
    assert primero.llamadas == ["release"]
    assert page.services == [segundo]
    assert not Path(sintetizador.rutas[0]).exists()
    assert Path(sintetizador.rutas[1]).exists()


def test_saved_answers_opened_from_history_can_be_listened_to(monkeypatch, tmp_path):
    pantalla, sintetizador, _ = _pantalla(monkeypatch, tmp_path)
    cid = pantalla.historial.guardar_turno(
        None, "vieja", [{"role": "user", "content": "hola"}, {"role": "assistant", "content": "Hola, Ana."}]
    )
    pantalla._abrir(cid)

    usuario, respuesta = pantalla.mensajes.controls
    assert _boton_escuchar(usuario) is None
    _escuchar(pantalla)
    assert _dichos(sintetizador) == ["Hola, Ana."]


def test_connection_and_status_bubbles_have_no_listen_button(monkeypatch, tmp_path):
    _usar_servidor(monkeypatch, Servidor(models=["m"], apagado=True))
    page = PaginaVoz()
    sintetizador = SintetizadorFalso(tmp_path)
    pantalla = PantallaChat(
        page,
        Historial(tmp_path / "eva.db"),
        Config(),
        voz=VozEva(page, sintetizador=sintetizador, reproductor=Reproductor(page, crear_audio=AudioFalso)),
    )
    pantalla.iniciar()
    pantalla.mostrar_error("algo")
    pantalla.mostrar_aviso("Transcribiendo…")

    assert all(_boton_escuchar(b) is None for b in pantalla.mensajes.controls)


# ----- the microphone stops Eva -----


def test_starting_to_record_stops_eva_speaking(monkeypatch, tmp_path):
    pantalla, sintetizador, page = _pantalla(monkeypatch, tmp_path)
    _hablar(pantalla)
    (audio,) = AudioFalso.creados

    asyncio.run(pantalla.microfono.boton.on_click(None))  # records again

    assert audio.llamadas == ["release"]
    assert page.services == []
    assert not Path(sintetizador.rutas[0]).exists()
    assert pantalla.microfono.grabando is True
    asyncio.run(pantalla.microfono.boton.on_click(None))  # finish to clean up


# ----- download and failures -----


class PaginaQueMira(PaginaVoz):
    """Records the chat texts at every page.update."""

    def __init__(self) -> None:
        super().__init__()
        self.pantalla = None
        self.vistas: list[list[str]] = []

    def update(self, *controles) -> None:
        super().update(*controles)
        if self.pantalla is not None:
            self.vistas.append([t.value for t in _textos(self.pantalla)])


def test_the_first_time_the_voice_download_is_shown_before_it_starts(monkeypatch, tmp_path):
    page = PaginaQueMira()
    sintetizador = SintetizadorFalso(tmp_path, descarga=True)
    pantalla, _, _ = _pantalla(
        monkeypatch, tmp_path, extras={"responder_en_voz": "siempre"}, page=page, sintetizador=sintetizador
    )
    page.pantalla = pantalla
    vistas_al_cargar = []
    original = sintetizador.cargar

    def cargar():
        vistas_al_cargar.append(list(page.vistas[-1]))
        original()

    sintetizador.cargar = cargar
    _escribir(pantalla)

    assert vistas_al_cargar == [[PREGUNTA, "Soleado", DESCARGANDO]]
    assert _valores(pantalla) == [PREGUNTA, "Soleado"]  # gone once downloaded
    assert page.vistas[-1] == [PREGUNTA, "Soleado"]
    assert len(AudioFalso.creados) == 1


def test_a_failed_synthesis_leaves_a_gray_note_and_the_chat_keeps_working(monkeypatch, tmp_path):
    sintetizador = SintetizadorFalso(tmp_path, error=ErrorVoz("No pude generar la voz: fonema"))
    pantalla, _, _ = _pantalla(
        monkeypatch, tmp_path, extras={"responder_en_voz": "siempre"}, sintetizador=sintetizador
    )
    _escribir(pantalla)

    assert _valores(pantalla) == [PREGUNTA, "Soleado", NO_PUDE_LEER]
    nota = _textos(pantalla)[-1]
    assert (nota.color, nota.italic) == (GRIS_TEXTO, True)
    assert all(t.color != ROJO_ERROR for t in _textos(pantalla))
    assert AudioFalso.creados == []
    assert pantalla.ocupado is False

    sintetizador.error = None
    _escribir(pantalla, "¿Y mañana?")
    assert _valores(pantalla) == [PREGUNTA, "Soleado", NO_PUDE_LEER, "¿Y mañana?", "Soleado"]
    assert len(AudioFalso.creados) == 1


def test_an_unexpected_synthesis_error_also_leaves_the_gray_note(monkeypatch, tmp_path, caplog):
    sintetizador = SintetizadorFalso(tmp_path, error=RuntimeError("inesperado"))
    pantalla, _, _ = _pantalla(monkeypatch, tmp_path, sintetizador=sintetizador)
    _escribir(pantalla)
    _escuchar(pantalla)

    assert _valores(pantalla) == [PREGUNTA, "Soleado", NO_PUDE_LEER]
    assert "inesperado" in caplog.text


def test_a_failed_voice_download_says_so_in_gray(monkeypatch, tmp_path):
    sintetizador = SintetizadorFalso(tmp_path, descarga=True, error_carga=ErrorVoz(SIN_DESCARGA))
    pantalla, _, _ = _pantalla(
        monkeypatch, tmp_path, extras={"responder_en_voz": "siempre"}, sintetizador=sintetizador
    )
    _escribir(pantalla)

    assert _valores(pantalla) == [PREGUNTA, "Soleado", SIN_DESCARGA]
    assert _textos(pantalla)[-1].color == GRIS_TEXTO


# ----- settings reach the chat -----


def test_new_settings_change_the_voice_and_the_mode(monkeypatch, tmp_path):
    pantalla, sintetizador, _ = _pantalla(monkeypatch, tmp_path)
    pantalla._ajustes_guardados(Config(extras={"responder_en_voz": "siempre", "voz_eva": "es_ES-davefx-medium"}))
    _escribir(pantalla)

    assert sintetizador.pasos == [("sintetizar", "es_ES-davefx-medium", "Soleado")]


def test_the_settings_dialog_gets_eva_voice(monkeypatch, tmp_path):
    pantalla, _, _ = _pantalla(monkeypatch, tmp_path)
    recibidos = []

    class Dialogo:
        def __init__(self, page, config, al_guardar, voz=None):
            recibidos.append(voz)

        def abrir(self):
            pass

    monkeypatch.setattr(chat, "DialogoAjustes", Dialogo)
    pantalla._abrir_ajustes()

    assert recibidos == [pantalla.voz]


# ----- without a voice (e.g. a page that cannot play audio) -----


def test_a_screen_without_voice_answers_in_text_with_no_listen_button(monkeypatch, tmp_path):
    from tests.test_ui import FakePage

    _usar_servidor(monkeypatch, Servidor(models=["m"], chunks=[_chunk(content="Soleado")]))
    pantalla = PantallaChat(FakePage(), Historial(tmp_path / "eva.db"), Config(extras={"responder_en_voz": "siempre"}))

    async def sin_foco():
        pass

    pantalla.campo.focus = sin_foco
    pantalla.iniciar()
    _escribir(pantalla)

    assert pantalla.voz is None
    assert _valores(pantalla) == [PREGUNTA, "Soleado"]
    assert all(_boton_escuchar(b) is None for b in pantalla.mensajes.controls)
