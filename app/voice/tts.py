"""Text to speech with a local Piper voice, run on the CPU.

A missing voice is downloaded automatically the first time it is used, into
``carpeta_datos()/modelos/piper``, and loaded once (lazily, thread-safe).
Each synthesis writes a WAV in the system temp folder.
"""

import contextlib
import logging
import os
import re
import tempfile
import threading
import time
import uuid
import wave
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.config import carpeta_datos
from app.voice.stt import ErrorVoz

URL_BASE = "https://huggingface.co/rhasspy/piper-voices/resolve/main/"
MENSAJE_SIN_DESCARGA = "No pude descargar la voz de Eva. Revisa tu conexión a internet."
PREFIJO_FALLO = "No pude generar la voz: "
DESCARGANDO = "Descargando la voz de Eva (unos 60–115 MB). Solo pasa la primera vez…"
EXTENSIONES = (".onnx", ".onnx.json")

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Voz:
    id: str
    nombre: str
    carpeta: str  # path of its files inside the piper-voices repository

    def url(self, extension: str) -> str:
        return f"{URL_BASE}{self.carpeta}/{self.id}{extension}"


def _voz(id: str, nombre: str) -> Voz:
    idioma, persona, calidad = id.split("-")
    return Voz(id, nombre, f"{idioma.split('_')[0]}/{idioma}/{persona}/{calidad}")


# The Spanish voices tried in the Fase 4 spike.
VOCES = {
    v.id: v
    for v in (
        _voz("es_AR-daniela-high", "Daniela (Argentina)"),
        _voz("es_MX-claude-high", "Claude (México)"),
        _voz("es_MX-ald-medium", "Ald (México)"),
        _voz("es_ES-davefx-medium", "Davefx (España)"),
    )
}
VOZ_POR_DEFECTO = "es_AR-daniela-high"


def voz_valida(voz: Any) -> str:
    """``voz`` if it is in the catalog, else the default voice."""
    return voz if isinstance(voz, str) and voz in VOCES else VOZ_POR_DEFECTO


def _descargar(url: str, destino: Path) -> None:
    """Download ``url`` into ``destino``; an incomplete download raises."""
    import httpx

    with httpx.stream("GET", url, follow_redirects=True, timeout=httpx.Timeout(30.0, read=60.0)) as respuesta:
        respuesta.raise_for_status()
        esperado = respuesta.headers.get("content-length")
        escrito = 0
        with open(destino, "wb") as archivo:
            for parte in respuesta.iter_bytes(1 << 20):
                archivo.write(parte)
                escrito += len(parte)
    if esperado is not None and esperado.isdigit() and int(esperado) != escrito:
        raise OSError(f"incomplete download of {url}: {escrito} of {esperado} bytes")


def _piper(ruta: str) -> Any:
    # Imported on first use: piper pulls in onnxruntime.
    from piper import PiperVoice

    return PiperVoice.load(ruta)


def _borrar(*rutas: Path) -> None:
    for ruta in rutas:
        with contextlib.suppress(OSError):
            ruta.unlink(missing_ok=True)


class Sintetizador:
    def __init__(
        self,
        voz: str = VOZ_POR_DEFECTO,
        carpeta: Path | None = None,
        descargar: Callable[[str, Path], None] | None = None,
        cargador: Callable[[str], Any] | None = None,
    ) -> None:
        self._voz = voz_valida(voz)
        self.carpeta = Path(carpeta) if carpeta is not None else carpeta_datos() / "modelos" / "piper"
        # Both replaceable in tests: download a URL to a file, load a voice file.
        self._descargar = descargar or _descargar
        self._cargador = cargador or _piper
        self._piper: Any = None
        self._cargada: str | None = None  # id of the loaded voice
        self._uso = threading.RLock()  # one load or synthesis at a time

    @property
    def voz(self) -> str:
        return self._voz

    @voz.setter
    def voz(self, voz: str) -> None:
        """Choose another voice; it is loaded on the next use."""
        with self._uso:
            self._voz = voz_valida(voz)

    def _archivo(self, extension: str, voz: str | None = None) -> Path:
        return self.carpeta / f"{voz or self._voz}{extension}"

    def necesita_descarga(self) -> bool:
        """Whether the voice files are missing (the first load downloads them)."""
        try:
            return not all(self._archivo(e).stat().st_size > 0 for e in EXTENSIONES)
        except OSError:
            return True

    def _descargar_voz(self, voz: str) -> None:
        """Download both files to temporary names, then rename them; a failure
        leaves nothing behind."""
        self.carpeta.mkdir(parents=True, exist_ok=True)
        temporales = [self._archivo(f"{e}.part", voz) for e in EXTENSIONES]
        finales = [self._archivo(e, voz) for e in EXTENSIONES]
        inicio = time.perf_counter()
        try:
            for extension, temporal in zip(EXTENSIONES, temporales, strict=True):
                self._descargar(VOCES[voz].url(extension), temporal)
            for temporal, final in zip(temporales, finales, strict=True):
                os.replace(temporal, final)
        except Exception:
            _borrar(*temporales, *finales)
            raise
        log.info("Piper voice %r downloaded in %.2f s", voz, time.perf_counter() - inicio)

    def cargar(self) -> None:
        """Load the chosen voice once, downloading it first if needed.

        Raises ``ErrorVoz`` with a message for the user; a later call retries.
        """
        with self._uso:
            voz = self._voz
            if self._piper is not None and self._cargada == voz:
                return
            if self.necesita_descarga():
                try:
                    self._descargar_voz(voz)
                except Exception as exc:
                    log.exception("Could not download the Piper voice %r", voz)
                    raise ErrorVoz(MENSAJE_SIN_DESCARGA) from exc
            inicio = time.perf_counter()
            try:
                self._piper = self._cargador(str(self._archivo(".onnx", voz)))
            except Exception as exc:
                log.exception("Could not load the Piper voice %r", voz)
                self._piper, self._cargada = None, None
                raise ErrorVoz(f"{PREFIJO_FALLO}{exc}") from exc
            self._cargada = voz
            log.info("Piper voice %r loaded in %.2f s", voz, time.perf_counter() - inicio)

    def sintetizar(self, texto: str) -> str:
        """Path of a new WAV in the temp folder with ``texto`` read aloud.

        Blocks for seconds: call it off the UI loop. Raises ``ErrorVoz``.
        """
        with self._uso:
            self.cargar()
            ruta = Path(tempfile.gettempdir()) / f"eva-habla-{uuid.uuid4().hex}.wav"
            inicio = time.perf_counter()
            try:
                archivo = wave.open(str(ruta), "wb")
                try:
                    self._piper.synthesize_wav(texto, archivo)
                except BaseException:
                    # Closing a WAV with no audio format raises its own error,
                    # which would hide the real one; the file is closed anyway.
                    with contextlib.suppress(Exception):
                        archivo.close()
                    raise
                archivo.close()
            except Exception as exc:
                log.exception("Could not synthesize %d characters", len(texto))
                _borrar(ruta)
                raise ErrorVoz(f"{PREFIJO_FALLO}{exc}") from exc
            log.info("Synthesized %d characters in %.2f s", len(texto), time.perf_counter() - inicio)
            return str(ruta)


# ----- text cleanup -----

_PENSAMIENTO = re.compile(r"<think>.*?(?:</think>|\Z)", re.DOTALL | re.IGNORECASE)
_BLOQUE_CODIGO = re.compile(r"^[ \t]*(```|~~~).*?(?:^[ \t]*\1[^\n]*$|\Z)", re.DOTALL | re.MULTILINE)
_IMAGEN_O_ENLACE = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")
_URL = re.compile(r"(?:https?://|www\.)\S+", re.IGNORECASE)
_CODIGO = re.compile(r"`+([^`]*)`+")
_SEPARADOR_TABLA = re.compile(r"^[ \t]*\|?[ \t]*:?-{3,}:?[ \t]*(?:\|[ \t]*:?-{3,}:?[ \t]*)*\|?[ \t]*$", re.MULTILINE)
_LINEA_HORIZONTAL = re.compile(r"^[ \t]*([-*_])(?:[ \t]*\1){2,}[ \t]*$", re.MULTILINE)
_ENCABEZADO = re.compile(r"^[ \t]*#{1,6}[ \t]*", re.MULTILINE)
_CITA = re.compile(r"^[ \t]*(?:>[ \t]?)+", re.MULTILINE)
_VINETA = re.compile(r"^[ \t]*[-*+][ \t]+", re.MULTILINE)
_MARCAS = re.compile(r"[*~]+|(?<!\w)_+|_+(?!\w)")
_GUION_BAJO = re.compile(r"[_|]+")  # inside words (snake_case) and table pipes
_ESPACIO_ANTES = re.compile(r"\s+([,.;:!?…)])")
_EMOJI = re.compile(
    "["
    "\U0001f000-\U0001faff"  # pictographs, emoticons, flags, skin tones
    "\u2600-\u27bf"  # symbols and dingbats
    "\u2b00-\u2bff"  # arrows and stars
    "\ufe0f\u200d\u20e3"  # variation selector, joiner, keycap
    "\U000e0020-\U000e007f"  # tag characters
    "]+"
)


def limpiar_para_voz(texto: str) -> str:
    """The part of an answer worth reading aloud ("" if nothing is left).

    Drops thinking blocks, code blocks, URLs, emojis and Markdown markers;
    links keep their text and inline code its content.
    """
    texto = _PENSAMIENTO.sub(" ", texto or "")
    texto = _BLOQUE_CODIGO.sub(" ", texto)
    texto = _IMAGEN_O_ENLACE.sub(r"\1", texto)
    texto = _URL.sub(" ", texto)
    texto = _CODIGO.sub(r"\1", texto)
    texto = _SEPARADOR_TABLA.sub(" ", texto)
    texto = _LINEA_HORIZONTAL.sub(" ", texto)
    texto = _ENCABEZADO.sub("", texto)
    texto = _CITA.sub("", texto)
    texto = _VINETA.sub("", texto)
    texto = _MARCAS.sub("", texto)
    texto = _GUION_BAJO.sub(" ", texto)
    texto = _EMOJI.sub(" ", texto)
    texto = re.sub(r"\s+", " ", texto)
    return _ESPACIO_ANTES.sub(r"\1", texto).strip()
