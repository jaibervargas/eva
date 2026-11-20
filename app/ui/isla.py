"""Compact floating-window adapter for desktop Flet."""

import contextlib
import logging
import threading
from typing import Any

import flet as ft

log = logging.getLogger(__name__)

ANCHO = 300
ALTO = 56
# A minimized Windows window reports a tiny size at -32000,-32000. Those values
# must never be remembered as the chat geometry to restore.
ANCHO_MINIMO_REAL = 400
FUERA_DE_PANTALLA = -30000
# If the system never reports the resize, show the island anyway.
RESPALDO_SEGUNDOS = 0.4


class IslaFlotante:
    """Switch a full chat view to a small rounded always-on-top island.

    Window support differs by Flet platform, so all native changes are kept in
    this adapter and the views remain ordinary controls that can be tested.
    """

    def __init__(self, page: ft.Page, completa: ft.Control, isla: ft.Control, custom_chrome: bool = False) -> None:
        self.page = page
        self.completa = completa
        self.isla = isla
        self.custom_chrome = custom_chrome
        self._ancho = getattr(page.window, "width", None)
        self._alto = getattr(page.window, "height", None)
        self._left = getattr(page.window, "left", None)
        self._top = getattr(page.window, "top", None)
        self._fondo_original = getattr(page, "bgcolor", None)
        self._instalado = False
        self.activa = False
        self._isla_left: float | None = None
        self._isla_top: float | None = None
        self._esperando_forma = False
        self._respaldo: threading.Timer | None = None

    def instalar(self) -> None:
        if self._instalado:
            return
        self._instalado = True
        self.page.window.on_event = self.evento

    def evento(self, e: Any) -> None:
        tipo = getattr(e, "type", None)
        log.debug("window event %s (island active=%s)", tipo, self.activa)
        if not self.activa and tipo in (ft.WindowEventType.RESIZED, ft.WindowEventType.MOVED):
            self._recordar_geometria()
        elif self.activa and tipo in (ft.WindowEventType.RESIZED, ft.WindowEventType.MOVED):
            self._reafirmar_isla()
        if tipo == ft.WindowEventType.MINIMIZE:
            # Clicking Eva's taskbar icon always minimizes the window, so from
            # the island that same click has to bring the chat back.
            self.restaurar() if self.activa else self.activar()
        elif tipo == ft.WindowEventType.RESTORE and not self.activa:
            # While the island is up, RESTORE is only the echo of the
            # un-minimize it performs itself; honoring it would undo the island.
            self.restaurar()

    def _recordar_geometria(self) -> None:
        """Keep the last chat geometry, ignoring what a minimized window reports."""
        ventana = self.page.window
        ancho = getattr(ventana, "width", None)
        alto = getattr(ventana, "height", None)
        if ancho and alto and ancho >= ANCHO_MINIMO_REAL:
            self._ancho, self._alto = ancho, alto
        left = getattr(ventana, "left", None)
        top = getattr(ventana, "top", None)
        if left is not None and top is not None and min(left, top) > FUERA_DE_PANTALLA:
            self._left, self._top = left, top

    def _reafirmar_isla(self) -> None:
        """Re-apply the island shape after Windows restores the previous size.

        Un-minimizing the window makes Windows impose the size it had before,
        which would paint the small capsule inside a full-size window.
        """
        ventana = self.page.window
        if (ventana.width, ventana.height) != (ANCHO, ALTO):
            log.debug("island reshaped by the system to w=%s h=%s", ventana.width, ventana.height)
            ventana.width = ANCHO
            ventana.height = ALTO
            if self._isla_left is not None and self._isla_top is not None:
                ventana.left = self._isla_left
                ventana.top = self._isla_top
        if self._esperando_forma:
            self._esperando_forma = False
            self._cancelar_respaldo()
            ventana.visible = True
        with contextlib.suppress(RuntimeError):
            ventana.update()
        with contextlib.suppress(RuntimeError):
            self.page.update()

    def _cancelar_respaldo(self) -> None:
        if self._respaldo is not None:
            self._respaldo.cancel()
            self._respaldo = None

    def _mostrar_aunque_falte_el_evento(self) -> None:
        if self._esperando_forma:
            self._reafirmar_isla()

    def activar(self) -> None:
        self.activa = True
        ventana = self.page.window
        # MINIMIZE arrives once Windows already minimized the window, so the
        # geometry it reports here is unusable; trust what was recorded before.
        self._recordar_geometria()
        self._ancho = self._ancho or 900
        self._alto = self._alto or 700
        # Coming from the taskbar icon the window is minimized, and showing it
        # again paints one frame at the old size before the island shape is
        # applied. Keep it hidden until the shape is settled.
        self._esperando_forma = bool(getattr(ventana, "minimized", False))
        if self._esperando_forma:
            ventana.visible = False
            self._cancelar_respaldo()
            self._respaldo = threading.Timer(RESPALDO_SEGUNDOS, self._mostrar_aunque_falte_el_evento)
            self._respaldo.daemon = True
            self._respaldo.start()
        ventana.minimized = False
        ventana.width = ANCHO
        ventana.height = ALTO
        ventana.left = (self._left if self._left is not None else 0) + max((self._ancho - ANCHO) // 2, 0)
        ventana.top = max(self._top if self._top is not None else 0, 20)
        self._isla_left, self._isla_top = ventana.left, ventana.top
        ventana.frameless = True
        ventana.title_bar_hidden = True
        ventana.always_on_top = True
        # Keep Eva represented in the taskbar so clicking its icon can
        # restore the chat from the island instead of leaving a hidden window.
        ventana.skip_task_bar = False
        ventana.bgcolor = "#00000000"
        # The native window paints the Page behind the rounded control. Make
        # that layer transparent too, otherwise a rectangular blue backdrop
        # remains visible behind the island.
        self.page.bgcolor = "#00000000"
        self.completa.visible = False
        self.isla.visible = True
        log.debug(
            "island shown w=%s h=%s left=%s top=%s minimized=%s",
            ventana.width, ventana.height, ventana.left, ventana.top, ventana.minimized,
        )
        with contextlib.suppress(RuntimeError):
            ventana.update()
        self.page.update()

    def restaurar(self) -> None:
        self.activa = False
        self._esperando_forma = False
        self._cancelar_respaldo()
        ventana = self.page.window
        ventana.visible = True
        # The taskbar click that brings the chat back leaves the window
        # minimized, so it has to be shown again explicitly.
        ventana.minimized = False
        ventana.width = self._ancho or 900
        ventana.height = self._alto or 700
        if self._left is not None and self._top is not None:
            ventana.left = self._left
            ventana.top = self._top
        ventana.frameless = False
        ventana.title_bar_hidden = self.custom_chrome
        ventana.title_bar_buttons_hidden = self.custom_chrome
        ventana.always_on_top = False
        ventana.skip_task_bar = False
        ventana.bgcolor = self._fondo_original
        self.page.bgcolor = self._fondo_original
        self.completa.visible = True
        self.isla.visible = False
        log.debug(
            "restored window w=%s h=%s left=%s top=%s bg=%s page_bg=%s frameless=%s visible=%s minimized=%s",
            ventana.width, ventana.height, ventana.left, ventana.top,
            ventana.bgcolor, self.page.bgcolor, ventana.frameless,
            getattr(ventana, "visible", None), ventana.minimized,
        )
        with contextlib.suppress(RuntimeError):
            ventana.update()
        self.page.update()
