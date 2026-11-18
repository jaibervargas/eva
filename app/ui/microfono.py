"""Microphone button: one click starts recording, the next stops it; the audio
is transcribed off the UI loop and sent to the chat like a typed question."""

import asyncio
import contextlib
import logging
import tempfile
import uuid
from pathlib import Path
from typing import Any

import flet as ft

from app.voice.stt import ErrorVoz, Transcriptor

ROJO = "#F87171"  # same red as the chat's error bubbles
HABLAR = "Hablar"
DETENER = "Detener"
SIN_PERMISO = "Eva no tiene permiso para usar el micrófono. Actívalo en la configuración de privacidad de Windows."
SIN_MICROFONO = "No encontré ningún micrófono conectado."
FALLO_GRABADOR = "No pude grabar el audio: "
NO_TE_ENTENDI = "No te entendí. Intenta hablar de nuevo."
TRANSCRIBIENDO = "Transcribiendo…"
DESCARGANDO = "Descargando el modelo de voz (unos 460 MB). Solo pasa la primera vez…"

log = logging.getLogger(__name__)


def crear_grabador(page: ft.Page) -> Any:
    """The app's audio recorder (a page service) recording WAV with the
    recorder's default sample rate and channels."""
    from flet_audio_recorder import AudioEncoder, AudioRecorder, AudioRecorderConfiguration

    grabador = AudioRecorder(configuration=AudioRecorderConfiguration(encoder=AudioEncoder.WAV))
    page.services.append(grabador)
    return grabador


def _borrar(*rutas: str | None) -> None:
    for ruta in rutas:
        if ruta:
            try:
                Path(ruta).unlink(missing_ok=True)
            except OSError:
                log.warning("Could not delete the temporary audio %s", ruta, exc_info=True)


class Microfono:
    """The microphone button of a ``PantallaChat``.

    While recording or transcribing it keeps the chat busy, so nothing else is
    sent. The button is disabled while a click is handled, while transcribing,
    and while Eva connects or answers: a second ``start_recording`` while one is
    active crashes the Windows recorder.
    """

    def __init__(self, pantalla, transcriptor: Transcriptor | None = None, grabador: Any = None) -> None:
        self.pantalla = pantalla
        # Both are created on first use: the recorder needs the live page and
        # the transcriber the data folder.
        self._transcriptor = transcriptor
        self._grabador = grabador
        self.grabando = False
        self._procesando = False  # handling a click or transcribing
        self._ruta: str | None = None  # where the current recording goes
        self.boton = ft.IconButton(icon=ft.Icons.MIC, tooltip=HABLAR, on_click=self._clic)

    @property
    def transcriptor(self) -> Transcriptor:
        if self._transcriptor is None:
            self._transcriptor = Transcriptor()
        return self._transcriptor

    @property
    def grabador(self) -> Any:
        if self._grabador is None:
            self._grabador = crear_grabador(self.pantalla.page)
        return self._grabador

    def actualizar(self) -> None:
        """Enable the button only when a click can be handled safely."""
        # While recording the chat is busy because of the microphone itself.
        self.boton.disabled = self._procesando or (self.pantalla.ocupado and not self.grabando)

    def _mostrar_detener(self, detener: bool) -> None:
        self.boton.icon = ft.Icons.STOP if detener else ft.Icons.MIC
        self.boton.icon_color = ROJO if detener else None
        self.boton.tooltip = DETENER if detener else HABLAR

    async def _clic(self, e) -> None:
        if self._procesando:
            return
        if self.grabando:
            await self._detener()
        elif not self.pantalla.ocupado:
            await self._empezar()

    def _ocupar(self) -> None:
        self._procesando = True
        self.pantalla._ocupar(True)  # also refreshes this button
        self.pantalla.page.update()

    def _terminar(self, error: str | None = None) -> None:
        """Back to idle, releasing the chat; show ``error`` if given."""
        self.grabando = False
        self._procesando = False
        self._ruta = None
        self._mostrar_detener(False)
        self.pantalla._ocupar(False)
        if error is not None:
            self.pantalla.mostrar_error(error)
        self.pantalla.page.update()

    async def _empezar(self) -> None:
        # The stop icon shows at once, locked until recording really starts.
        self._mostrar_detener(True)
        self._ocupar()
        ruta = str(Path(tempfile.gettempdir()) / f"eva-voz-{uuid.uuid4().hex}.wav")
        error = None
        try:
            grabador = self.grabador
            if not await grabador.has_permission():
                error = SIN_PERMISO
            elif not await grabador.get_input_devices():
                error = SIN_MICROFONO
            elif not await grabador.start_recording(output_path=ruta):
                error = f"{FALLO_GRABADOR}la grabación no empezó."
        except Exception as exc:
            log.exception("Could not start recording")
            await self._cancelar()
            error = f"{FALLO_GRABADOR}{exc}"
        if error is not None:
            _borrar(ruta)
            self._terminar(error)
            return
        self._ruta = ruta
        self.grabando = True
        self._procesando = False
        self.actualizar()
        self.pantalla.page.update()

    async def _cancelar(self) -> None:
        with contextlib.suppress(Exception):
            await self.grabador.cancel_recording()

    async def _detener(self) -> None:
        self._procesando = True
        self.grabando = False
        self._mostrar_detener(False)
        self.actualizar()
        self.pantalla.page.update()
        ruta = self._ruta
        try:
            guardada = await self.grabador.stop_recording()
        except Exception as exc:
            log.exception("Could not stop recording")
            await self._cancelar()
            _borrar(ruta)
            self._terminar(f"{FALLO_GRABADOR}{exc}")
            return
        audio = guardada if isinstance(guardada, str) and guardada else ruta
        try:
            texto, error = await self._transcribir(audio)
        finally:
            _borrar(ruta, audio)
        if error is None and not texto:
            error = NO_TE_ENTENDI
        self._terminar(error)
        if error is None:
            await self.pantalla.enviar_texto(texto)

    async def _transcribir(self, audio: str) -> tuple[str, str | None]:
        """(text, None) or ("", error message); the model runs off the UI loop."""
        transcriptor = self.transcriptor
        descargar = transcriptor.necesita_descarga()
        aviso = self.pantalla.mostrar_aviso(DESCARGANDO if descargar else TRANSCRIBIENDO)
        self.pantalla.page.update()  # show the bubble before the slow work starts
        try:
            if descargar:
                await asyncio.to_thread(transcriptor.cargar)
                aviso.value = TRANSCRIBIENDO
                self.pantalla.page.update()
            texto = await asyncio.to_thread(transcriptor.transcribir, audio)
            return texto.strip(), None
        except ErrorVoz as exc:
            return "", str(exc)
        except Exception as exc:  # transcribir only raises ErrorVoz; last guard
            log.exception("Unexpected error transcribing")
            return "", f"No pude transcribir el audio: {exc}"
        finally:
            self.pantalla.quitar_aviso(aviso)
