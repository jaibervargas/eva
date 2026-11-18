from types import SimpleNamespace

import flet as ft

from app.ui.isla import IslaFlotante


class PageFalsa:
    def __init__(self):
        self.window = ft.Window(width=900, height=700)
        self.actualizaciones = 0

    def update(self):
        self.actualizaciones += 1


def test_minimize_turns_the_window_into_a_compact_floating_island():
    page = PageFalsa()
    completa = ft.Container(visible=True)
    isla = ft.Container(visible=False)
    controlador = IslaFlotante(page, completa, isla)

    controlador.activar()

    assert (completa.visible, isla.visible) == (False, True)
    assert (page.window.width, page.window.height) == (300, 56)
    assert page.window.left == 300
    assert page.window.top == 20
    assert page.window.frameless is True
    assert page.window.always_on_top is True
    assert page.window.title_bar_hidden is True


def test_restore_returns_the_previous_window_shape():
    page = PageFalsa()
    completa = ft.Container(visible=True)
    isla = ft.Container(visible=False)
    controlador = IslaFlotante(page, completa, isla)
    controlador.activar()

    controlador.restaurar()

    assert (completa.visible, isla.visible) == (True, False)
    assert (page.window.width, page.window.height) == (900, 700)
    assert (page.window.left, page.window.top) == (None, None)
    assert page.window.frameless is False
    assert page.window.always_on_top is False
    assert page.window.title_bar_hidden is False


def test_window_events_toggle_the_island():
    page = PageFalsa()
    completa = ft.Container(visible=True)
    isla = ft.Container(visible=False)
    controlador = IslaFlotante(page, completa, isla)

    controlador.evento(SimpleNamespace(type=ft.WindowEventType.MINIMIZE))
    assert isla.visible is True

    controlador.evento(SimpleNamespace(type=ft.WindowEventType.RESTORE))
    assert completa.visible is True
