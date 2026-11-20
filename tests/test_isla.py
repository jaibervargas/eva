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
    # With no known previous position, the window keeps the one it has instead
    # of being sent to an undefined spot.
    assert (page.window.left, page.window.top) == (300, 20)
    assert page.window.frameless is False
    assert page.window.always_on_top is False
    assert page.window.title_bar_hidden is False


def test_the_island_stays_hidden_until_it_has_its_shape():
    """Avoids one frame of a full-size island when Windows un-minimizes."""
    page = PageFalsa()
    completa = ft.Container(visible=True)
    isla = ft.Container(visible=False)
    controlador = IslaFlotante(page, completa, isla)

    page.window.minimized = True  # the taskbar icon minimized it first
    controlador.evento(SimpleNamespace(type=ft.WindowEventType.MINIMIZE))
    assert page.window.visible is False

    page.window.width, page.window.height = 1280.0, 720.0
    controlador.evento(SimpleNamespace(type=ft.WindowEventType.RESIZED))

    assert (page.window.width, page.window.height) == (300, 56)
    assert page.window.visible is True


def test_the_island_opened_from_the_app_button_is_never_hidden():
    page = PageFalsa()
    completa = ft.Container(visible=True)
    isla = ft.Container(visible=False)
    controlador = IslaFlotante(page, completa, isla)

    controlador.activar()  # the app's own minimize button, window not minimized

    assert page.window.visible is True


def test_windows_resizing_the_island_back_is_corrected():
    """Un-minimizing makes Windows restore the big size over the island's."""
    page = PageFalsa()
    completa = ft.Container(visible=True)
    isla = ft.Container(visible=False)
    controlador = IslaFlotante(page, completa, isla)

    controlador.evento(SimpleNamespace(type=ft.WindowEventType.MINIMIZE))
    page.window.width, page.window.height = 1280.0, 720.0
    page.window.left, page.window.top = 10.0, 10.0
    controlador.evento(SimpleNamespace(type=ft.WindowEventType.RESIZED))

    assert (page.window.width, page.window.height) == (300, 56)
    assert (page.window.left, page.window.top) == (300, 20)
    assert (completa.visible, isla.visible) == (False, True)


def test_the_geometry_reported_while_minimized_is_not_remembered():
    """Windows reports 160x28 at -32000,-32000 once the window is minimized."""
    page = PageFalsa()
    completa = ft.Container(visible=True)
    isla = ft.Container(visible=False)
    controlador = IslaFlotante(page, completa, isla)
    page.window.left, page.window.top = 120, 80
    controlador.evento(SimpleNamespace(type=ft.WindowEventType.RESIZED))

    page.window.width, page.window.height = 160.0, 28.0
    page.window.left, page.window.top = -32000.0, -32000.0
    controlador.evento(SimpleNamespace(type=ft.WindowEventType.MINIMIZE))
    page.window.minimized = True
    controlador.evento(SimpleNamespace(type=ft.WindowEventType.MINIMIZE))

    assert (page.window.width, page.window.height) == (900, 700)
    assert (page.window.left, page.window.top) == (120, 80)


def test_the_restore_echo_of_activating_does_not_undo_the_island():
    """Showing the island un-minimizes the window, and Windows echoes RESTORE."""
    page = PageFalsa()
    completa = ft.Container(visible=True)
    isla = ft.Container(visible=False)
    controlador = IslaFlotante(page, completa, isla)

    controlador.evento(SimpleNamespace(type=ft.WindowEventType.MINIMIZE))
    controlador.evento(SimpleNamespace(type=ft.WindowEventType.RESTORE))

    assert (completa.visible, isla.visible) == (False, True)
    assert (page.window.width, page.window.height) == (300, 56)


def test_taskbar_icon_restores_the_chat_from_the_island():
    """Clicking Eva's taskbar icon minimizes again: from the island it must restore."""
    page = PageFalsa()
    completa = ft.Container(visible=True)
    isla = ft.Container(visible=False)
    controlador = IslaFlotante(page, completa, isla)

    controlador.evento(SimpleNamespace(type=ft.WindowEventType.MINIMIZE))
    assert (completa.visible, isla.visible) == (False, True)

    page.window.minimized = True  # Windows minimizes before reporting the event
    controlador.evento(SimpleNamespace(type=ft.WindowEventType.MINIMIZE))

    assert (completa.visible, isla.visible) == (True, False)
    assert (page.window.width, page.window.height) == (900, 700)
    assert page.window.minimized is False


def test_restore_from_a_plain_minimize_returns_the_chat():
    page = PageFalsa()
    completa = ft.Container(visible=True)
    isla = ft.Container(visible=False)
    controlador = IslaFlotante(page, completa, isla)

    controlador.activar()
    controlador.restaurar()  # "Volver al chat" closed the island
    page.window.minimized = True

    controlador.evento(SimpleNamespace(type=ft.WindowEventType.RESTORE))

    assert (completa.visible, isla.visible) == (True, False)
    assert page.window.minimized is False
