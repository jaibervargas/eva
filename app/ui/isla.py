"""Compact floating-window adapter for desktop Flet."""

import contextlib
from typing import Any

import flet as ft

ANCHO = 300
ALTO = 56


class IslaFlotante:
    """Switch a full chat view to a small rounded always-on-top island.

    Window support differs by Flet platform, so all native changes are kept in
    this adapter and the views remain ordinary controls that can be tested.
    """

    def __init__(self, page: ft.Page, completa: ft.Control, isla: ft.Control) -> None:
        self.page = page
        self.completa = completa
        self.isla = isla
        self._ancho = getattr(page.window, "width", None)
        self._alto = getattr(page.window, "height", None)
        self._left = getattr(page.window, "left", None)
        self._top = getattr(page.window, "top", None)
        self._instalado = False

    def instalar(self) -> None:
        if self._instalado:
            return
        self._instalado = True
        self.page.window.on_event = self.evento

    def evento(self, e: Any) -> None:
        tipo = getattr(e, "type", None)
        if tipo == ft.WindowEventType.MINIMIZE:
            self.activar()
        elif tipo == ft.WindowEventType.RESTORE:
            self.restaurar()

    def activar(self) -> None:
        ventana = self.page.window
        self._ancho = getattr(ventana, "width", None) or self._ancho or 900
        self._alto = getattr(ventana, "height", None) or self._alto or 700
        self._left = getattr(ventana, "left", None)
        self._top = getattr(ventana, "top", None)
        ventana.minimized = False
        ventana.width = ANCHO
        ventana.height = ALTO
        ventana.left = (self._left if self._left is not None else 0) + max((self._ancho - ANCHO) // 2, 0)
        ventana.top = max(self._top if self._top is not None else 0, 20)
        ventana.frameless = True
        ventana.title_bar_hidden = True
        ventana.always_on_top = True
        ventana.skip_task_bar = True
        self.completa.visible = False
        self.isla.visible = True
        with contextlib.suppress(RuntimeError):
            ventana.update()
        self.page.update()

    def restaurar(self) -> None:
        ventana = self.page.window
        ventana.width = self._ancho or 900
        ventana.height = self._alto or 700
        ventana.left = self._left
        ventana.top = self._top
        ventana.frameless = False
        ventana.title_bar_hidden = False
        ventana.always_on_top = False
        ventana.skip_task_bar = False
        self.completa.visible = True
        self.isla.visible = False
        with contextlib.suppress(RuntimeError):
            ventana.update()
        self.page.update()
