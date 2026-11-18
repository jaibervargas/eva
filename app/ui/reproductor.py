"""Eva's voice in the app: synthesize an answer (off the UI loop) and play it
with Flet's audio player, one answer at a time."""

import contextlib
import logging
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from app.voice.tts import Sintetizador, limpiar_para_voz, voz_valida

NO_PUDE_LEER = "No pude leer la respuesta en voz alta."
PRUEBA = "Hola, soy Eva. Así suena mi voz."
ESCUCHAR = "Escuchar"
# extras["responder_en_voz"]: when Eva reads her answers aloud on her own.
MODOS = {"voz": "Solo cuando le hablas por voz", "siempre": "Siempre", "nunca": "Nunca"}
MODO_POR_DEFECTO = "voz"

log = logging.getLogger(__name__)

# WAVs handed to a player and not deleted yet; whatever is left goes when the
# app closes (see borrar_audios).
_audios: set[str] = set()
_audios_lock = threading.Lock()


def modo_valido(modo: Any) -> str:
    return modo if isinstance(modo, str) and modo in MODOS else MODO_POR_DEFECTO


def _borrar(ruta: str) -> None:
    try:
        Path(ruta).unlink(missing_ok=True)
    except OSError:
        # Still open somewhere (e.g. the player has not let it go): retried on close.
        log.warning("Could not delete the temporary audio %s", ruta, exc_info=True)
        return
    with _audios_lock:
        _audios.discard(ruta)


def borrar_audios() -> None:
    """Delete every WAV still kept for playback (called when the app closes)."""
    with _audios_lock:
        rutas = list(_audios)
    for ruta in rutas:
        _borrar(ruta)


def crear_audio(ruta: str) -> Any:
    """A new Flet audio player that starts playing ``ruta`` once on the page.

    A new player per playback: changing ``src`` on an existing one did not play
    (Fase 4 spike).
    """
    from flet_audio import Audio

    return Audio(src=ruta, autoplay=True)


class Reproductor:
    """Plays one WAV at a time; a new playback stops and releases the previous
    one and deletes its file. Its coroutines run on the page's event loop."""

    def __init__(self, page, crear_audio: Callable[[str], Any] | None = None) -> None:
        self.page = page
        self._crear_audio = crear_audio or globals()["crear_audio"]
        self._actual: tuple[Any, str] | None = None  # (player, its WAV)

    async def reproducir(self, ruta: str) -> None:
        await self.detener()
        with _audios_lock:
            _audios.add(ruta)
        audio = self._crear_audio(ruta)
        # Another playback may have started while the previous one was released.
        anterior, self._actual = self._actual, (audio, ruta)
        # The page keeps a reference so Flet does not drop the service while it plays.
        self.page.services.append(audio)
        self.page.update()
        if anterior is not None:
            await self._soltar(*anterior)

    async def detener(self) -> None:
        anterior, self._actual = self._actual, None
        if anterior is not None:
            await self._soltar(*anterior)

    async def _soltar(self, audio: Any, ruta: str) -> None:
        try:
            await audio.release()
        except Exception:
            log.warning("Could not release the audio player", exc_info=True)
        # By identity: Flet controls compare equal by value.
        self.page.services = [s for s in self.page.services if s is not audio]
        with contextlib.suppress(Exception):
            self.page.update()
        _borrar(ruta)


class VozEva:
    """Reads text aloud: cleans it, synthesizes it with the chosen voice and
    plays it. Only the latest request plays; ``callar`` stops everything."""

    def __init__(self, page, sintetizador: Sintetizador | None = None, reproductor: Reproductor | None = None) -> None:
        self.page = page
        # Created on first use: the synthesizer needs the data folder.
        self._sintetizador = sintetizador
        self.reproductor = reproductor or Reproductor(page)
        self._uso = threading.Lock()  # one voice change + synthesis at a time
        self._turno = 0
        self._turno_lock = threading.Lock()

    @property
    def sintetizador(self) -> Sintetizador:
        if self._sintetizador is None:
            self._sintetizador = Sintetizador()
        return self._sintetizador

    def _nuevo_turno(self) -> int:
        with self._turno_lock:
            self._turno += 1
            return self._turno

    def _vigente(self, turno: int) -> bool:
        with self._turno_lock:
            return turno == self._turno

    def hablar(self, texto: str, voz: str, al_descargar: Callable[[], None] | None = None) -> bool:
        """Read ``texto`` aloud with ``voz``; whether something will play.

        Blocks for seconds (download, synthesis): call it off the UI loop.
        ``al_descargar`` runs before a missing voice is downloaded. Raises
        ``ErrorVoz`` (or anything unexpected) for the caller to report.
        """
        turno = self._nuevo_turno()
        limpio = limpiar_para_voz(texto)
        if not limpio:
            return False
        with self._uso:
            sintetizador = self.sintetizador
            sintetizador.voz = voz_valida(voz)
            if sintetizador.necesita_descarga():
                if al_descargar is not None:
                    al_descargar()
                sintetizador.cargar()
            ruta = sintetizador.sintetizar(limpio)
        if not self._vigente(turno):
            _borrar(ruta)  # a newer request or the microphone came first
            return False
        with _audios_lock:
            _audios.add(ruta)
        self.page.run_task(self._reproducir, ruta, turno)
        return True

    async def _reproducir(self, ruta: str, turno: int) -> None:
        if self._vigente(turno):
            await self.reproductor.reproducir(ruta)
        else:
            _borrar(ruta)

    async def callar(self) -> None:
        """Stop what is playing and drop any answer still being synthesized."""
        self._nuevo_turno()
        await self.reproductor.detener()
