"""Speech to text with faster-whisper, run locally on the CPU.

The model is downloaded automatically the first time it is used, into
``carpeta_datos()/modelos/whisper``, and loaded once (lazily, thread-safe).
"""

import logging
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from app.config import carpeta_datos

MENSAJE_SIN_DESCARGA = "No pude descargar el modelo de voz. Revisa tu conexión a internet."
PREFIJO_FALLO = "No pude transcribir el audio: "

log = logging.getLogger(__name__)


class ErrorVoz(Exception):
    """A speech-to-text failure; its text is meant for the user."""


def _whisper(nombre: str, **opciones) -> Any:
    # Imported on first use: faster-whisper pulls in heavy libraries.
    from faster_whisper import WhisperModel

    return WhisperModel(nombre, **opciones)


class Transcriptor:
    def __init__(
        self,
        modelo: str = "small",
        carpeta: Path | None = None,
        fabrica: Callable[..., Any] | None = None,
    ) -> None:
        self.modelo = modelo
        self.carpeta = Path(carpeta) if carpeta is not None else carpeta_datos() / "modelos" / "whisper"
        # Builds the model (faster_whisper.WhisperModel); replaceable in tests.
        self._fabrica = fabrica or _whisper
        self._whisper: Any = None
        self._cargando = threading.Lock()

    def necesita_descarga(self) -> bool:
        """Whether the model files are missing (the first load will download them)."""
        repo = self.modelo if "/" in self.modelo else f"Systran/faster-whisper-{self.modelo}"
        snapshots = self.carpeta / f"models--{repo.replace('/', '--')}" / "snapshots"
        try:
            return not any(
                (s / "model.bin").is_file() and (s / "config.json").is_file() for s in snapshots.iterdir()
            )
        except OSError:
            return True

    def cargar(self) -> None:
        """Load the model once, downloading it first if needed.

        Raises ``ErrorVoz`` with a message for the user; a later call retries.
        """
        with self._cargando:
            if self._whisper is not None:
                return
            descargar = self.necesita_descarga()
            inicio = time.perf_counter()
            try:
                self._whisper = self._fabrica(
                    self.modelo,
                    device="cpu",
                    compute_type="int8",
                    download_root=str(self.carpeta),
                    # A model already on disk loads without asking the internet.
                    local_files_only=not descargar,
                )
            except Exception as exc:
                log.exception("Could not load the Whisper model %r", self.modelo)
                if descargar:
                    raise ErrorVoz(MENSAJE_SIN_DESCARGA) from exc
                raise ErrorVoz(f"{PREFIJO_FALLO}{exc}") from exc
            log.info("Whisper model %r loaded in %.2f s", self.modelo, time.perf_counter() - inicio)

    def transcribir(self, ruta: str | Path) -> str:
        """Spanish text spoken in the audio file ("" if nothing was understood).

        Blocks for seconds: call it off the UI loop. Raises ``ErrorVoz``.
        """
        self.cargar()
        inicio = time.perf_counter()
        try:
            segmentos, info = self._whisper.transcribe(str(ruta), language="es", vad_filter=True)
            # Segments are produced lazily: the real work happens here.
            texto = " ".join(t for s in segmentos if (t := s.text.strip()))
        except Exception as exc:
            log.exception("Could not transcribe %s", ruta)
            raise ErrorVoz(f"{PREFIJO_FALLO}{exc}") from exc
        log.info(
            "Transcribed %.1f s of audio in %.2f s",
            getattr(info, "duration", 0.0) or 0.0,
            time.perf_counter() - inicio,
        )
        return texto
