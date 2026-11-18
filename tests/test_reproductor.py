"""Tests for Eva's audio player and voice (app.ui.reproductor).

A fake ``flet_audio.Audio`` and a fake synthesizer stand in for Flet's player
and Piper, so nothing is played, downloaded or synthesized for real.
"""

import asyncio
import logging
import threading
from pathlib import Path

import pytest

from app.ui import reproductor as modulo
from app.ui.reproductor import Reproductor, VozEva, borrar_audios
from app.voice.stt import ErrorVoz
from app.voice.tts import VOZ_POR_DEFECTO
from tests.test_ui import FakePage


class PaginaVoz(FakePage):
    """Fake page with services and ``run_task`` (run at once, in its own loop),
    that also tells whether the code runs inside ``run_thread``."""

    def __init__(self, ejecutar: bool = True) -> None:
        super().__init__(ejecutar)
        self.services: list = []
        self.en_hilo = 0
        self.actualizaciones = 0

    def update(self, *controles) -> None:
        self.actualizaciones += 1

    def run_thread(self, funcion, *args) -> None:
        def envuelta(*a):
            self.en_hilo += 1
            try:
                funcion(*a)
            finally:
                self.en_hilo -= 1

        super().run_thread(envuelta, *args)

    def run_task(self, corrutina, *args):
        # Its own thread and loop: callers may already be inside a running loop.
        errores = []

        def correr():
            try:
                asyncio.run(corrutina(*args))
            except BaseException as exc:  # pragma: no cover - surfaced below
                errores.append(exc)

        hilo = threading.Thread(target=correr)
        hilo.start()
        hilo.join(timeout=10)
        if errores:
            raise errores[0]


class AudioFalso:
    """Async API of ``flet_audio.Audio`` used by the player."""

    creados: list["AudioFalso"] = []

    def __init__(self, src=None, autoplay=False, error_liberar=None):
        self.src = src
        self.autoplay = autoplay
        self.error_liberar = error_liberar
        self.llamadas: list[str] = []
        AudioFalso.creados.append(self)

    async def release(self):
        self.llamadas.append("release")
        if self.error_liberar is not None:
            raise self.error_liberar

    async def pause(self):
        self.llamadas.append("pause")


@pytest.fixture(autouse=True)
def limpio():
    AudioFalso.creados = []
    yield
    borrar_audios()


def _wav(tmp_path, nombre="a.wav") -> str:
    ruta = tmp_path / nombre
    ruta.write_bytes(b"RIFF")
    return str(ruta)


def _reproductor(page=None, crear_audio=None):
    page = page or PaginaVoz()
    return Reproductor(page, crear_audio=crear_audio or (lambda ruta: AudioFalso(src=ruta, autoplay=True))), page


# ----- player -----


def test_each_playback_creates_a_new_autoplaying_audio_on_the_page(tmp_path):
    reproductor, page = _reproductor()
    ruta = _wav(tmp_path)
    asyncio.run(reproductor.reproducir(ruta))

    (audio,) = AudioFalso.creados
    assert (audio.src, audio.autoplay) == (ruta, True)
    assert page.services == [audio]
    assert page.actualizaciones >= 1
    assert audio.llamadas == []
    assert Path(ruta).exists()  # kept while it plays


def test_a_new_playback_releases_removes_and_deletes_the_previous_one(tmp_path):
    reproductor, page = _reproductor()
    primera, segunda = _wav(tmp_path, "1.wav"), _wav(tmp_path, "2.wav")
    asyncio.run(reproductor.reproducir(primera))
    asyncio.run(reproductor.reproducir(segunda))

    vieja, nueva = AudioFalso.creados
    assert vieja.llamadas == ["release"]
    assert page.services == [nueva] and page.services[0] is nueva
    assert not Path(primera).exists()
    assert Path(segunda).exists()
    assert nueva.llamadas == []


def test_stopping_releases_removes_and_deletes_the_current_audio(tmp_path):
    reproductor, page = _reproductor()
    ruta = _wav(tmp_path)
    asyncio.run(reproductor.detener())  # nothing playing: nothing happens
    asyncio.run(reproductor.reproducir(ruta))
    asyncio.run(reproductor.detener())

    (audio,) = AudioFalso.creados
    assert audio.llamadas == ["release"]
    assert page.services == []
    assert not Path(ruta).exists()
    asyncio.run(reproductor.detener())
    assert audio.llamadas == ["release"]


def test_a_release_that_fails_is_logged_and_the_audio_still_goes(tmp_path, caplog):
    reproductor, page = _reproductor(crear_audio=lambda r: AudioFalso(r, True, error_liberar=RuntimeError("roto")))
    ruta = _wav(tmp_path)
    asyncio.run(reproductor.reproducir(ruta))

    with caplog.at_level(logging.WARNING):
        asyncio.run(reproductor.detener())

    assert "roto" in caplog.text
    assert page.services == []
    assert not Path(ruta).exists()


def test_other_page_services_are_kept(tmp_path):
    reproductor, page = _reproductor()
    grabador = object()
    page.services.append(grabador)
    asyncio.run(reproductor.reproducir(_wav(tmp_path)))
    asyncio.run(reproductor.detener())

    assert page.services == [grabador]


def test_the_wav_still_playing_is_deleted_when_the_app_closes(tmp_path):
    reproductor, _ = _reproductor()
    ruta = _wav(tmp_path)
    asyncio.run(reproductor.reproducir(ruta))

    borrar_audios()
    assert not Path(ruta).exists()
    borrar_audios()  # twice is harmless


def test_the_app_deletes_the_wav_still_playing_when_its_window_closes(tmp_path, monkeypatch):
    from app import main as app_main

    monkeypatch.setenv("EVA_DATA_DIR", str(tmp_path / "datos"))
    monkeypatch.setattr(app_main, "crear_memoria", lambda: None)
    ruta = _wav(tmp_path)
    vistas = []

    def run(main):  # the window plays an answer, then closes
        reproductor, _ = _reproductor()
        asyncio.run(reproductor.reproducir(ruta))
        vistas.append(Path(ruta).exists())

    monkeypatch.setattr(app_main.ft, "run", run)
    app_main.ejecutar()

    assert vistas == [True]
    assert not Path(ruta).exists()


def test_the_default_audio_is_a_flet_audio_with_autoplay():
    from flet_audio import Audio

    audio = modulo.crear_audio("C:/x/eva.wav")
    assert isinstance(audio, Audio)
    assert (audio.src, audio.autoplay) == ("C:/x/eva.wav", True)


# ----- Eva's voice: synthesize then play -----


class SintetizadorFalso:
    """Stands in for ``app.voice.tts.Sintetizador``; writes a small WAV per call."""

    def __init__(self, carpeta: Path, descarga=False, error=None, error_carga=None):
        self.carpeta = carpeta
        self.voz = VOZ_POR_DEFECTO
        self.descarga = descarga
        self.error = error
        self.error_carga = error_carga
        self.pagina = None
        self.pasos: list[tuple] = []  # (step, voice, text)
        self.en_hilo: list[bool] = []
        self.al_sintetizar = None  # optional hook run before writing
        self.rutas: list[str] = []

    def necesita_descarga(self):
        return self.descarga

    def cargar(self):
        self.pasos.append(("cargar", self.voz, None))
        if self.error_carga is not None:
            raise self.error_carga
        self.descarga = False

    def sintetizar(self, texto):
        self.pasos.append(("sintetizar", self.voz, texto))
        if self.pagina is not None:
            self.en_hilo.append(self.pagina.en_hilo > 0)
        if self.al_sintetizar is not None:
            self.al_sintetizar()
        if self.error is not None:
            raise self.error
        ruta = self.carpeta / f"voz-{len(self.rutas)}.wav"
        ruta.write_bytes(b"RIFF")
        self.rutas.append(str(ruta))
        return str(ruta)


def _voz(tmp_path, **opciones):
    page = PaginaVoz()
    sintetizador = SintetizadorFalso(tmp_path, **opciones)
    sintetizador.pagina = page
    reproductor, _ = _reproductor(page)
    return VozEva(page, sintetizador=sintetizador, reproductor=reproductor), sintetizador, page


def test_speaking_cleans_the_text_synthesizes_with_the_voice_and_plays_it(tmp_path):
    voz, sintetizador, page = _voz(tmp_path)

    assert voz.hablar("**Hola** 😊 mira `esto`", "es_MX-claude-high") is True

    assert sintetizador.pasos == [("sintetizar", "es_MX-claude-high", "Hola mira esto")]
    (audio,) = AudioFalso.creados
    assert audio.src == sintetizador.rutas[0]
    assert page.services == [audio]


def test_nothing_is_spoken_when_nothing_is_left_after_cleaning(tmp_path):
    voz, sintetizador, page = _voz(tmp_path)

    assert voz.hablar("<think>hmm</think>```\ncódigo\n```", VOZ_POR_DEFECTO) is False
    assert sintetizador.pasos == []
    assert AudioFalso.creados == []


def test_an_unknown_voice_speaks_with_the_default_one(tmp_path):
    voz, sintetizador, _ = _voz(tmp_path)
    voz.hablar("Hola", "xx-nadie")

    assert sintetizador.pasos == [("sintetizar", VOZ_POR_DEFECTO, "Hola")]


def test_a_missing_voice_warns_before_downloading(tmp_path):
    voz, sintetizador, _ = _voz(tmp_path, descarga=True)
    avisos = []
    voz.hablar("Hola", VOZ_POR_DEFECTO, al_descargar=lambda: avisos.append(list(sintetizador.pasos)))

    assert avisos == [[]]  # warned before anything slow
    assert [p[0] for p in sintetizador.pasos] == ["cargar", "sintetizar"]


def test_a_voice_already_there_does_not_warn(tmp_path):
    voz, _, _ = _voz(tmp_path)
    avisos = []
    voz.hablar("Hola", VOZ_POR_DEFECTO, al_descargar=lambda: avisos.append(1))

    assert avisos == []


def test_speech_errors_reach_the_caller_and_nothing_plays(tmp_path):
    voz, _, _ = _voz(tmp_path, error=ErrorVoz("No pude generar la voz: x"))

    with pytest.raises(ErrorVoz):
        voz.hablar("Hola", VOZ_POR_DEFECTO)
    assert AudioFalso.creados == []


def test_silencing_while_synthesizing_drops_that_audio(tmp_path):
    voz, sintetizador, page = _voz(tmp_path)
    sintetizador.al_sintetizar = lambda: asyncio.run(voz.callar())

    assert voz.hablar("Hola", VOZ_POR_DEFECTO) is False
    assert AudioFalso.creados == []
    assert not Path(sintetizador.rutas[0]).exists()


def test_silencing_stops_what_is_playing(tmp_path):
    voz, sintetizador, page = _voz(tmp_path)
    voz.hablar("Hola", VOZ_POR_DEFECTO)
    asyncio.run(voz.callar())

    (audio,) = AudioFalso.creados
    assert audio.llamadas == ["release"]
    assert page.services == []
    assert not Path(sintetizador.rutas[0]).exists()


def test_a_second_answer_stops_the_first_one(tmp_path):
    voz, sintetizador, page = _voz(tmp_path)
    voz.hablar("Uno", VOZ_POR_DEFECTO)
    voz.hablar("Dos", VOZ_POR_DEFECTO)

    primero, segundo = AudioFalso.creados
    assert primero.llamadas == ["release"]
    assert page.services == [segundo]
    assert not Path(sintetizador.rutas[0]).exists()
    assert Path(sintetizador.rutas[1]).exists()
