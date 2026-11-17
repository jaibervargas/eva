"""Tests for the app entry point: Engram is created, wired and closed on exit (S6)."""

import logging

import pytest

from app import main as app_main
from app.llm.conversacion import MemoriaUsuario
from tests.memoria_falsa import MemoriaFalsa


@pytest.fixture(autouse=True)
def datos(tmp_path, monkeypatch):
    monkeypatch.setenv("EVA_DATA_DIR", str(tmp_path / "datos"))


def test_engram_is_closed_when_the_app_window_closes(monkeypatch):
    falsa = MemoriaFalsa()
    monkeypatch.setattr(app_main, "MemoriaEngram", lambda: falsa)
    abiertas = []

    def run(main):
        abiertas.append(falsa.cerrada)  # still open while the window runs

    monkeypatch.setattr(app_main.ft, "run", run)
    app_main.ejecutar()

    assert abiertas == [False]
    assert falsa.cerrada is True


def test_engram_is_closed_even_if_the_app_crashes(monkeypatch):
    falsa = MemoriaFalsa()
    monkeypatch.setattr(app_main, "MemoriaEngram", lambda: falsa)

    def run(main):
        raise RuntimeError("ventana rota")

    monkeypatch.setattr(app_main.ft, "run", run)
    with pytest.raises(RuntimeError, match="ventana rota"):
        app_main.ejecutar()
    assert falsa.cerrada is True


def test_engram_that_cannot_be_created_leaves_the_app_without_memory(monkeypatch, caplog):
    def rota():
        raise OSError("sin permiso")

    monkeypatch.setattr(app_main, "MemoriaEngram", rota)
    with caplog.at_level(logging.ERROR):
        assert app_main.crear_memoria() is None
    assert "sin permiso" in caplog.text


def test_app_without_memory_still_runs(monkeypatch):
    monkeypatch.setattr(app_main, "crear_memoria", lambda: None)
    corridas = []
    monkeypatch.setattr(app_main.ft, "run", corridas.append)

    app_main.ejecutar()

    assert len(corridas) == 1


class _Pagina:
    def add(self, *controles):
        self.controles = controles


def test_main_gives_the_chat_screen_the_long_term_memory(monkeypatch):
    creadas = []

    class Pantalla:
        def __init__(self, page, historial, config, memoria=None):
            self.memoria = memoria
            creadas.append(self)

        def vista(self):
            return None

        def iniciar(self):
            self.iniciada = True

    monkeypatch.setattr(app_main, "PantallaChat", Pantalla)
    falsa = MemoriaFalsa()
    app_main.crear_main(falsa)(_Pagina())
    app_main.crear_main(None)(_Pagina())

    con, sin = creadas
    assert isinstance(con.memoria, MemoriaUsuario) and con.memoria.memoria is falsa
    assert con.iniciada is True
    assert sin.memoria is None
