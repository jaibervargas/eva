"""Recording indicator shown inside the composer while the microphone is on.

The bars report that Eva is listening and the clock reports how long. They are
not a waveform: the Flet recorder does not report the input level, so nothing
here claims to show the user's actual volume.
"""

import contextlib
import logging
import threading
import time
from typing import Any

import flet as ft

GRABANDO = "Grabando…"
BARRAS = 5
ALTO_MINIMO = 6
ALTO_MAXIMO = 24
INTERVALO = 0.18  # seconds between beats

log = logging.getLogger(__name__)


class IndicadorGrabacion:
    """Replaces the composer field while a recording is in progress."""

    def __init__(self, page: Any, acento: str, texto: str) -> None:
        self.page = page
        self.etiqueta = ft.Text(GRABANDO, size=14, weight=ft.FontWeight.W_500, color=texto)
        self.tiempo = ft.Text("0:00", size=13, color=texto)
        self.barras = [
            ft.Container(
                width=3,
                height=ALTO_MINIMO,
                bgcolor=acento,
                border_radius=2,
                animate=ft.Animation(150, ft.AnimationCurve.EASE_IN_OUT),
            )
            for _ in range(BARRAS)
        ]
        self.control = ft.Container(
            visible=False,
            expand=True,
            padding=ft.Padding.symmetric(horizontal=4, vertical=12),
            content=ft.Row(
                [
                    ft.Container(width=9, height=9, bgcolor="#F87171", border_radius=5),
                    self.etiqueta,
                    ft.Row(self.barras, spacing=3, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                    ft.Container(expand=True),
                    self.tiempo,
                ],
                spacing=10,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
        )
        self._inicio = 0.0
        self._paso = 0
        self._temporizador: threading.Timer | None = None

    def iniciar(self, ahora: float | None = None) -> None:
        self._inicio = time.monotonic() if ahora is None else ahora
        self._paso = 0
        self.etiqueta.value = GRABANDO
        self.tiempo.value = "0:00"
        self.control.visible = True
        self._programar()

    def detener(self) -> None:
        self.control.visible = False
        self._cancelar()

    def refrescar(self, ahora: float | None = None) -> None:
        """One beat: move the bars and advance the clock."""
        instante = time.monotonic() if ahora is None else ahora
        self._paso += 1
        for indice, barra in enumerate(self.barras):
            # A fixed pattern per step, so every beat visibly differs.
            altura = ALTO_MINIMO + (ALTO_MAXIMO - ALTO_MINIMO) * _onda(self._paso + indice)
            barra.height = round(altura)
        segundos = max(int(instante - self._inicio), 0)
        self.tiempo.value = f"{segundos // 60}:{segundos % 60:02d}"

    # ----- internals -----

    def _cancelar(self) -> None:
        if self._temporizador is not None:
            self._temporizador.cancel()
            self._temporizador = None

    def _programar(self) -> None:
        self._cancelar()
        self._temporizador = threading.Timer(INTERVALO, self._latir)
        self._temporizador.daemon = True
        self._temporizador.start()

    def _latir(self) -> None:
        if not self.control.visible:
            return
        self.refrescar()
        with contextlib.suppress(Exception):
            self.page.update()
        self._programar()


def _onda(paso: int) -> float:
    """A bounded 0..1 pattern; no input level is available to show."""
    patron = (0.2, 0.6, 1.0, 0.75, 0.35, 0.5)
    return patron[paso % len(patron)]
