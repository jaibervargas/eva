import flet as ft

from app.ui.grabacion import GRABANDO, IndicadorGrabacion


class PageFalsa:
    def __init__(self):
        self.actualizaciones = 0

    def update(self):
        self.actualizaciones += 1


def _indicador():
    return IndicadorGrabacion(PageFalsa(), acento="#36D5CE", texto="#E8F4F7")


def test_the_indicator_is_hidden_until_recording_starts():
    indicador = _indicador()

    assert indicador.control.visible is False


def test_starting_shows_the_recording_label_and_resets_the_clock():
    indicador = _indicador()

    indicador.iniciar(ahora=100.0)

    assert indicador.control.visible is True
    assert indicador.etiqueta.value == GRABANDO
    assert indicador.tiempo.value == "0:00"


def test_the_clock_counts_the_elapsed_seconds():
    indicador = _indicador()
    indicador.iniciar(ahora=100.0)

    indicador.refrescar(ahora=165.0)

    assert indicador.tiempo.value == "1:05"


def test_each_refresh_moves_the_bars():
    indicador = _indicador()
    indicador.iniciar(ahora=0.0)
    antes = [b.height for b in indicador.barras]

    indicador.refrescar(ahora=0.2)

    assert [b.height for b in indicador.barras] != antes
    assert all(6 <= b.height <= 24 for b in indicador.barras)


def test_stopping_hides_the_indicator_and_releases_the_timer():
    indicador = _indicador()
    indicador.iniciar(ahora=0.0)

    indicador.detener()

    assert indicador.control.visible is False
    assert indicador._temporizador is None


def test_the_bars_use_the_design_accent():
    indicador = _indicador()

    assert all(b.bgcolor == "#36D5CE" for b in indicador.barras)
    assert isinstance(indicador.control, ft.Control)
