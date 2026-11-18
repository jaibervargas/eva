"""Tests for speech to text (app.voice.stt): a fake Whisper model stands in
for faster-whisper, so nothing is downloaded. One optional test uses the real
``small`` model only when it is already in the user's data folder."""

import logging
import os
import struct
import threading
import time
import wave
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.voice.stt import ErrorVoz, Transcriptor

SIN_DESCARGA = "No pude descargar el modelo de voz. Revisa tu conexión a internet."


class ModeloFalso:
    def __init__(self, textos=(" Hola", " Eva. "), error=None, duracion=2.5):
        self.textos = textos
        self.error = error
        self.duracion = duracion
        self.llamadas = []

    def transcribe(self, ruta, **opciones):
        self.llamadas.append((ruta, opciones))
        if self.error is not None:
            raise self.error
        segmentos = (SimpleNamespace(text=t) for t in self.textos)
        return segmentos, SimpleNamespace(duration=self.duracion)


class Fabrica:
    """Fake ``WhisperModel`` constructor: records every load."""

    def __init__(self, modelo=None, error=None, espera=0.0):
        self.modelo = modelo or ModeloFalso()
        self.error = error
        self.espera = espera
        self.cargas = []

    def __call__(self, nombre, **opciones):
        self.cargas.append((nombre, opciones))
        time.sleep(self.espera)
        if self.error is not None:
            raise self.error
        return self.modelo


def _descargado(carpeta: Path, modelo="small") -> None:
    snapshot = carpeta / f"models--Systran--faster-whisper-{modelo}" / "snapshots" / "abc123"
    snapshot.mkdir(parents=True)
    (snapshot / "model.bin").write_bytes(b"x")
    (snapshot / "config.json").write_text("{}", encoding="utf-8")


def test_transcribes_in_spanish_with_vad_joining_stripped_segments(tmp_path):
    fabrica = Fabrica()
    transcriptor = Transcriptor(carpeta=tmp_path, fabrica=fabrica)

    assert transcriptor.transcribir("audio.wav") == "Hola Eva."
    assert fabrica.modelo.llamadas == [("audio.wav", {"language": "es", "vad_filter": True})]
    assert fabrica.cargas == [
        (
            "small",
            {"device": "cpu", "compute_type": "int8", "download_root": str(tmp_path), "local_files_only": False},
        )
    ]


def test_a_downloaded_model_loads_without_touching_the_network(tmp_path):
    _descargado(tmp_path)
    fabrica = Fabrica()
    Transcriptor(carpeta=tmp_path, fabrica=fabrica).transcribir("a.wav")

    assert fabrica.cargas[0][1]["local_files_only"] is True


def test_the_model_loads_once_across_calls(tmp_path):
    fabrica = Fabrica()
    transcriptor = Transcriptor(carpeta=tmp_path, fabrica=fabrica)
    transcriptor.transcribir("a.wav")
    transcriptor.transcribir("b.wav")

    assert len(fabrica.cargas) == 1
    assert [r for r, _ in fabrica.modelo.llamadas] == ["a.wav", "b.wav"]


def test_the_model_loads_once_across_threads(tmp_path):
    fabrica = Fabrica(espera=0.05)
    transcriptor = Transcriptor(carpeta=tmp_path, fabrica=fabrica)
    resultados = []
    hilos = [threading.Thread(target=lambda: resultados.append(transcriptor.transcribir("a.wav"))) for _ in range(5)]
    for hilo in hilos:
        hilo.start()
    for hilo in hilos:
        hilo.join(timeout=10)

    assert len(fabrica.cargas) == 1
    assert resultados == ["Hola Eva."] * 5


def test_the_model_is_not_loaded_until_the_first_transcription(tmp_path):
    fabrica = Fabrica()
    transcriptor = Transcriptor(carpeta=tmp_path, fabrica=fabrica)

    assert fabrica.cargas == []
    transcriptor.cargar()
    transcriptor.cargar()
    assert len(fabrica.cargas) == 1


def test_silence_gives_an_empty_text(tmp_path):
    fabrica = Fabrica(ModeloFalso(textos=()))
    assert Transcriptor(carpeta=tmp_path, fabrica=fabrica).transcribir("a.wav") == ""

    fabrica = Fabrica(ModeloFalso(textos=("  ", "")))
    assert Transcriptor(carpeta=tmp_path, fabrica=fabrica).transcribir("a.wav") == ""


def test_a_failed_download_asks_to_check_the_connection_and_retries_later(tmp_path, caplog):
    fabrica = Fabrica(error=ConnectionError("sin red"))
    transcriptor = Transcriptor(carpeta=tmp_path, fabrica=fabrica)

    with caplog.at_level(logging.ERROR), pytest.raises(ErrorVoz) as error:
        transcriptor.transcribir("a.wav")
    assert str(error.value) == SIN_DESCARGA
    assert "sin red" in caplog.text

    fabrica.error = None
    assert transcriptor.transcribir("a.wav") == "Hola Eva."
    assert len(fabrica.cargas) == 2


def test_a_model_that_cannot_load_reports_the_detail(tmp_path):
    _descargado(tmp_path)
    transcriptor = Transcriptor(carpeta=tmp_path, fabrica=Fabrica(error=RuntimeError("archivo dañado")))

    with pytest.raises(ErrorVoz) as error:
        transcriptor.transcribir("a.wav")
    assert str(error.value) == "No pude transcribir el audio: archivo dañado"


def test_a_failed_transcription_reports_the_detail(tmp_path, caplog):
    fabrica = Fabrica(ModeloFalso(error=RuntimeError("formato inválido")))
    transcriptor = Transcriptor(carpeta=tmp_path, fabrica=fabrica)

    with caplog.at_level(logging.ERROR), pytest.raises(ErrorVoz) as error:
        transcriptor.transcribir("a.wav")
    assert str(error.value) == "No pude transcribir el audio: formato inválido"
    assert "formato inválido" in caplog.text


def test_each_transcription_logs_its_duration(tmp_path, caplog):
    transcriptor = Transcriptor(carpeta=tmp_path, fabrica=Fabrica())

    with caplog.at_level(logging.INFO, logger="app.voice.stt"):
        transcriptor.transcribir("a.wav")
        transcriptor.transcribir("b.wav")

    duraciones = [r for r in caplog.records if r.levelno == logging.INFO and "Transcribed" in r.getMessage()]
    assert len(duraciones) == 2


def test_needs_download_until_the_model_files_are_there(tmp_path):
    transcriptor = Transcriptor(carpeta=tmp_path, fabrica=Fabrica())
    assert transcriptor.necesita_descarga() is True

    _descargado(tmp_path, "base")  # another model does not count
    assert transcriptor.necesita_descarga() is True

    _descargado(tmp_path)
    assert transcriptor.necesita_descarga() is False


def test_default_folder_is_inside_the_data_folder(tmp_path, monkeypatch):
    monkeypatch.setenv("EVA_DATA_DIR", str(tmp_path / "datos"))
    transcriptor = Transcriptor(fabrica=Fabrica())

    assert transcriptor.carpeta == tmp_path / "datos" / "modelos" / "whisper"
    assert transcriptor.modelo == "small"


# ----- optional: the real model, only if it is already downloaded -----

_CARPETA_REAL = Path(os.environ.get("APPDATA") or Path.home()) / "Eva" / "modelos" / "whisper"


@pytest.mark.skipif(
    Transcriptor(carpeta=_CARPETA_REAL, fabrica=Fabrica()).necesita_descarga(),
    reason="the real 'small' model is not in %APPDATA%/Eva/modelos/whisper (tests never download it)",
)
def test_the_real_small_model_transcribes_a_short_silent_wav(tmp_path):
    ruta = tmp_path / "silencio.wav"
    with wave.open(str(ruta), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(struct.pack("<h", 0) * 16000)

    assert isinstance(Transcriptor(carpeta=_CARPETA_REAL).transcribir(str(ruta)), str)
