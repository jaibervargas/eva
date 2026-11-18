"""Tests for text to speech (app.voice.tts): a fake downloader and a fake voice
stand in for Hugging Face and Piper, so nothing is downloaded. One optional
test uses the real default voice only when it is already in the data folder."""

import logging
import os
import threading
import time
import wave
from pathlib import Path

import pytest

from app.voice.stt import ErrorVoz
from app.voice.tts import (
    URL_BASE,
    VOCES,
    VOZ_POR_DEFECTO,
    Sintetizador,
    limpiar_para_voz,
)

SIN_DESCARGA = "No pude descargar la voz de Eva. Revisa tu conexión a internet."
DESCARGANDO = "Descargando la voz de Eva (unos 60–115 MB). Solo pasa la primera vez…"


class VozFalsa:
    """Fake ``PiperVoice``: writes one second of silence per call."""

    def __init__(self, ruta, error=None):
        self.ruta = ruta
        self.error = error
        self.textos = []

    def synthesize_wav(self, texto, archivo):
        self.textos.append(texto)
        if self.error is not None:
            raise self.error
        archivo.setnchannels(1)
        archivo.setsampwidth(2)
        archivo.setframerate(22050)
        archivo.writeframes(b"\x00\x00" * 22050)


class Cargador:
    """Fake ``PiperVoice.load``: records every load."""

    def __init__(self, error=None, error_voz=None, espera=0.0):
        self.error = error
        self.error_voz = error_voz
        self.espera = espera
        self.cargas = []
        self.voces = []

    def __call__(self, ruta):
        self.cargas.append(ruta)
        time.sleep(self.espera)
        if self.error is not None:
            raise self.error
        voz = VozFalsa(ruta, self.error_voz)
        self.voces.append(voz)
        return voz


class Descargador:
    """Fake downloader: writes the URL into the destination, or fails."""

    def __init__(self, fallar_en=None, parcial=False):
        self.fallar_en = fallar_en  # a file extension that fails
        self.parcial = parcial  # write some bytes before failing
        self.urls = []
        self.destinos = []

    def __call__(self, url, destino):
        self.urls.append(url)
        self.destinos.append(Path(destino))
        if self.fallar_en is not None and url.endswith(self.fallar_en):
            if self.parcial:
                Path(destino).write_bytes(b"medio archivo")
            raise ConnectionError("sin red")
        Path(destino).write_text(url, encoding="utf-8")


def _descargada(carpeta: Path, voz=VOZ_POR_DEFECTO) -> None:
    carpeta.mkdir(parents=True, exist_ok=True)
    (carpeta / f"{voz}.onnx").write_bytes(b"onnx")
    (carpeta / f"{voz}.onnx.json").write_text("{}", encoding="utf-8")


def _sintetizador(tmp_path, voz=VOZ_POR_DEFECTO, descargar=None, cargador=None, descargada=True):
    carpeta = tmp_path / "piper"
    if descargada:
        _descargada(carpeta, voz)
    cargador = cargador or Cargador()
    descargar = descargar or Descargador()
    return Sintetizador(voz=voz, carpeta=carpeta, descargar=descargar, cargador=cargador), cargador, descargar


# ----- catalog -----


def test_the_catalog_has_the_four_spanish_voices_and_daniela_by_default():
    assert {k: v.nombre for k, v in VOCES.items()} == {
        "es_AR-daniela-high": "Daniela (Argentina)",
        "es_MX-claude-high": "Claude (México)",
        "es_MX-ald-medium": "Ald (México)",
        "es_ES-davefx-medium": "Davefx (España)",
    }
    assert VOZ_POR_DEFECTO == "es_AR-daniela-high"
    assert URL_BASE == "https://huggingface.co/rhasspy/piper-voices/resolve/main/"
    assert VOCES["es_AR-daniela-high"].url(".onnx") == (
        URL_BASE + "es/es_AR/daniela/high/es_AR-daniela-high.onnx"
    )
    assert VOCES["es_ES-davefx-medium"].url(".onnx.json") == (
        URL_BASE + "es/es_ES/davefx/medium/es_ES-davefx-medium.onnx.json"
    )


def test_default_voice_and_folder_are_inside_the_data_folder(tmp_path, monkeypatch):
    monkeypatch.setenv("EVA_DATA_DIR", str(tmp_path / "datos"))
    sintetizador = Sintetizador()

    assert sintetizador.voz == VOZ_POR_DEFECTO
    assert sintetizador.carpeta == tmp_path / "datos" / "modelos" / "piper"


def test_an_unknown_voice_falls_back_to_the_default(tmp_path):
    assert Sintetizador(voz="xx_YY-nadie", carpeta=tmp_path).voz == VOZ_POR_DEFECTO


# ----- download -----


def test_needs_download_until_both_files_are_there(tmp_path):
    sintetizador, _, _ = _sintetizador(tmp_path, descargada=False)
    carpeta = tmp_path / "piper"
    assert sintetizador.necesita_descarga() is True

    _descargada(carpeta, "es_MX-claude-high")  # another voice does not count
    assert sintetizador.necesita_descarga() is True

    (carpeta / f"{VOZ_POR_DEFECTO}.onnx").write_bytes(b"onnx")  # only one file
    assert sintetizador.necesita_descarga() is True

    (carpeta / f"{VOZ_POR_DEFECTO}.onnx.json").write_text("{}", encoding="utf-8")
    assert sintetizador.necesita_descarga() is False


def test_a_missing_voice_is_downloaded_into_temp_names_then_renamed(tmp_path):
    sintetizador, cargador, descargar = _sintetizador(tmp_path, voz="es_MX-ald-medium", descargada=False)
    sintetizador.cargar()

    carpeta = tmp_path / "piper"
    base = URL_BASE + "es/es_MX/ald/medium/es_MX-ald-medium"
    assert sorted(descargar.urls) == [base + ".onnx", base + ".onnx.json"]
    # Never written straight to the final names.
    assert all(d.parent == carpeta and d.name.endswith(".part") for d in descargar.destinos)
    assert (carpeta / "es_MX-ald-medium.onnx").read_text(encoding="utf-8") == base + ".onnx"
    assert (carpeta / "es_MX-ald-medium.onnx.json").read_text(encoding="utf-8") == base + ".onnx.json"
    assert sorted(p.name for p in carpeta.iterdir()) == ["es_MX-ald-medium.onnx", "es_MX-ald-medium.onnx.json"]
    assert sintetizador.necesita_descarga() is False
    assert cargador.cargas == [str(carpeta / "es_MX-ald-medium.onnx")]


def test_a_voice_on_disk_is_not_downloaded_again(tmp_path):
    sintetizador, cargador, descargar = _sintetizador(tmp_path)
    sintetizador.cargar()

    assert descargar.urls == []
    assert len(cargador.cargas) == 1


@pytest.mark.parametrize("fallar_en", [".onnx", ".onnx.json"])
@pytest.mark.parametrize("parcial", [False, True])
def test_a_failed_or_partial_download_leaves_nothing_and_asks_to_check_the_connection(
    tmp_path, caplog, fallar_en, parcial
):
    descargar = Descargador(fallar_en=fallar_en, parcial=parcial)
    sintetizador, cargador, _ = _sintetizador(tmp_path, descargar=descargar, descargada=False)

    with caplog.at_level(logging.ERROR), pytest.raises(ErrorVoz) as error:
        sintetizador.cargar()

    assert str(error.value) == SIN_DESCARGA
    assert "sin red" in caplog.text
    carpeta = tmp_path / "piper"
    assert not carpeta.exists() or list(carpeta.iterdir()) == []
    assert cargador.cargas == []
    assert sintetizador.necesita_descarga() is True

    descargar.fallar_en = None  # a later try downloads again
    sintetizador.cargar()
    assert sintetizador.necesita_descarga() is False
    assert len(cargador.cargas) == 1


# ----- loading -----


def test_the_voice_loads_once_across_calls_and_threads(tmp_path):
    cargador = Cargador(espera=0.05)
    sintetizador, _, _ = _sintetizador(tmp_path, cargador=cargador)
    assert cargador.cargas == []  # lazy

    hilos = [threading.Thread(target=sintetizador.cargar) for _ in range(5)]
    for hilo in hilos:
        hilo.start()
    for hilo in hilos:
        hilo.join(timeout=10)
    sintetizador.cargar()

    assert len(cargador.cargas) == 1


def test_changing_the_voice_loads_the_new_one(tmp_path):
    _descargada(tmp_path / "piper", "es_MX-claude-high")
    sintetizador, cargador, _ = _sintetizador(tmp_path)
    sintetizador.cargar()

    sintetizador.voz = "es_MX-claude-high"
    sintetizador.sintetizar("Hola")
    sintetizador.sintetizar("Hola otra vez")

    carpeta = tmp_path / "piper"
    assert cargador.cargas == [str(carpeta / f"{VOZ_POR_DEFECTO}.onnx"), str(carpeta / "es_MX-claude-high.onnx")]
    assert cargador.voces[-1].textos == ["Hola", "Hola otra vez"]


def test_a_voice_that_cannot_load_reports_the_detail_and_retries_later(tmp_path):
    cargador = Cargador(error=RuntimeError("archivo dañado"))
    sintetizador, _, _ = _sintetizador(tmp_path, cargador=cargador)

    with pytest.raises(ErrorVoz) as error:
        sintetizador.cargar()
    assert str(error.value) == "No pude generar la voz: archivo dañado"

    cargador.error = None
    sintetizador.cargar()
    assert len(cargador.cargas) == 2


# ----- synthesis -----


def test_synthesis_writes_a_wav_in_the_temp_folder_and_logs_its_time(tmp_path, caplog):
    sintetizador, cargador, _ = _sintetizador(tmp_path)

    with caplog.at_level(logging.INFO, logger="app.voice"):
        ruta = sintetizador.sintetizar("Hola, soy Eva.")

    archivo = Path(ruta)
    try:
        assert archivo.parent == Path(__import__("tempfile").gettempdir())
        assert archivo.suffix == ".wav"
        with wave.open(str(archivo), "rb") as audio:
            assert (audio.getnchannels(), audio.getframerate(), audio.getnframes()) == (1, 22050, 22050)
        assert cargador.voces[0].textos == ["Hola, soy Eva."]
        tiempos = [r for r in caplog.records if r.levelno == logging.INFO and r.name.startswith("app.voice")]
        assert any("Synthesized" in r.getMessage() for r in tiempos)
    finally:
        archivo.unlink(missing_ok=True)

    otra = Path(sintetizador.sintetizar("Otra"))
    assert otra != archivo  # each synthesis gets its own file
    otra.unlink()


def test_a_failed_synthesis_reports_the_detail_and_leaves_no_file(tmp_path, caplog, monkeypatch):
    monkeypatch.setattr("tempfile.tempdir", str(tmp_path / "temporal"))
    (tmp_path / "temporal").mkdir()
    cargador = Cargador(error_voz=RuntimeError("fonema desconocido"))
    sintetizador, _, _ = _sintetizador(tmp_path, cargador=cargador)

    with caplog.at_level(logging.ERROR), pytest.raises(ErrorVoz) as error:
        sintetizador.sintetizar("Hola")

    assert str(error.value) == "No pude generar la voz: fonema desconocido"
    assert "fonema desconocido" in caplog.text
    assert list((tmp_path / "temporal").iterdir()) == []


def test_synthesis_downloads_a_missing_voice_first(tmp_path):
    sintetizador, cargador, descargar = _sintetizador(tmp_path, descargada=False)
    ruta = Path(sintetizador.sintetizar("Hola"))
    ruta.unlink()

    assert len(descargar.urls) == 2
    assert len(cargador.cargas) == 1


# ----- text cleanup -----


@pytest.mark.parametrize(
    ("texto", "esperado"),
    [
        ("<think>Debo saludar.</think>Hola, Ana.", "Hola, Ana."),
        ("<think>\nvarias\nlíneas\n</think>\n\nListo.", "Listo."),
        ("Antes.<think>sin cerrar y sigue", "Antes."),
        ("Mira:\n```python\nprint('hola')\n```\nEso es todo.", "Mira: Eso es todo."),
        ("Usa `pip install` para instalar.", "Usa pip install para instalar."),
        ("# Título\n## Subtítulo\nTexto.", "Título Subtítulo Texto."),
        ("Esto es **muy** importante y _cursiva_.", "Esto es muy importante y cursiva."),
        ("> Una cita\n> larga", "Una cita larga"),
        ("- uno\n* dos\n+ tres", "uno dos tres"),
        ("| Día | Clima |\n|-----|-------|\n| Lunes | Sol |", "Día Clima Lunes Sol"),
        ("Mira [la guía](https://ejemplo.com/guia) aquí.", "Mira la guía aquí."),
        ("Visita https://ejemplo.com/a?b=1 o www.ejemplo.com hoy.", "Visita o hoy."),
        ("¡Hola! 😊 Me alegra 👍🏽 verte ❤️", "¡Hola! Me alegra verte"),
        ("  Hola,\n\n\n   ¿cómo   estás?\t ", "Hola, ¿cómo estás?"),
        ("", ""),
        ("<think>solo pienso</think>", ""),
        ("```\nsolo código\n```", ""),
        ("😊🎉", ""),
    ],
)
def test_text_is_cleaned_before_reading_it_aloud(texto, esperado):
    assert limpiar_para_voz(texto) == esperado


def test_cleanup_keeps_plain_spanish_text_and_punctuation():
    texto = "Mañana lloverá en Medellín: 18–22 °C… ¿Llevas paraguas? ¡Sí!"
    assert limpiar_para_voz(texto) == texto


# ----- optional: the real default voice, only if it is already downloaded -----

_CARPETA_REAL = Path(os.environ.get("APPDATA") or Path.home()) / "Eva" / "modelos" / "piper"


@pytest.mark.skipif(
    Sintetizador(carpeta=_CARPETA_REAL, descargar=Descargador(), cargador=Cargador()).necesita_descarga(),
    reason="the real default voice is not in %APPDATA%/Eva/modelos/piper (tests never download it)",
)
def test_the_real_default_voice_synthesizes_a_wav():
    ruta = Path(Sintetizador(carpeta=_CARPETA_REAL, descargar=Descargador()).sintetizar("Hola, soy Eva."))
    try:
        with wave.open(str(ruta), "rb") as audio:
            assert audio.getnframes() > 0
    finally:
        ruta.unlink(missing_ok=True)
