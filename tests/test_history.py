"""Tests for the SQLite chat history."""

import sqlite3

from app.config import carpeta_datos
from app.memory.history import Historial


def test_creates_conversaciones_and_mensajes_tables(tmp_path):
    ruta = tmp_path / "eva.db"
    Historial(ruta)
    with sqlite3.connect(ruta) as db:
        tablas = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"conversaciones", "mensajes"} <= tablas


def test_messages_survive_reopening_the_database(tmp_path):
    ruta = tmp_path / "eva.db"
    h = Historial(ruta)
    cid = h.crear_conversacion("Hola")
    h.agregar_mensajes(cid, [{"role": "user", "content": "Soy Ana"}, {"role": "assistant", "content": "Hola Ana"}])

    assert Historial(ruta).mensajes(cid) == [
        {"role": "user", "content": "Soy Ana"},
        {"role": "assistant", "content": "Hola Ana"},
    ]


def test_mensajes_with_limit_returns_the_last_ones_in_order(tmp_path):
    h = Historial(tmp_path / "eva.db")
    cid = h.crear_conversacion("x")
    h.agregar_mensajes(cid, [{"role": "user", "content": str(i)} for i in range(25)])

    ultimos = h.mensajes(cid, limite=20)
    assert [m["content"] for m in ultimos] == [str(i) for i in range(5, 25)]


def test_conversations_are_listed_most_recently_used_first(tmp_path):
    h = Historial(tmp_path / "eva.db")
    a = h.crear_conversacion("A")
    b = h.crear_conversacion("B")
    h.agregar_mensajes(b, [{"role": "user", "content": "1"}])
    h.agregar_mensajes(a, [{"role": "user", "content": "2"}])

    assert [(c.id, c.titulo) for c in h.listar_conversaciones()] == [(a, "A"), (b, "B")]


def test_messages_are_kept_per_conversation(tmp_path):
    h = Historial(tmp_path / "eva.db")
    a = h.crear_conversacion("A")
    b = h.crear_conversacion("B")
    h.agregar_mensajes(a, [{"role": "user", "content": "de A"}])

    assert h.mensajes(b) == []


def test_data_folder_honors_eva_data_dir(tmp_path, monkeypatch):
    destino = tmp_path / "datos"
    monkeypatch.setenv("EVA_DATA_DIR", str(destino))
    assert carpeta_datos() == destino
    assert destino.is_dir()


def test_default_data_folder_on_windows_is_appdata_eva(tmp_path, monkeypatch):
    monkeypatch.delenv("EVA_DATA_DIR", raising=False)
    monkeypatch.setattr("sys.platform", "win32")
    monkeypatch.setenv("APPDATA", str(tmp_path))
    assert carpeta_datos() == tmp_path / "Eva"
