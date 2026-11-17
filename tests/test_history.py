"""Tests for the SQLite chat history."""

import sqlite3
import stat
import sys

import pytest

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


# ----- S2: a turn is saved in one transaction -----


def test_guardar_turno_creates_the_conversation_with_its_messages(tmp_path):
    h = Historial(tmp_path / "eva.db")
    turno = [{"role": "user", "content": "hola"}, {"role": "assistant", "content": "Hola"}]

    cid = h.guardar_turno(None, "hola", turno)
    assert [(c.id, c.titulo) for c in h.listar_conversaciones()] == [(cid, "hola")]
    assert h.mensajes(cid) == turno

    assert h.guardar_turno(cid, "ignorado", [{"role": "user", "content": "otra"}]) == cid
    assert [c.titulo for c in h.listar_conversaciones()] == ["hola"]
    assert h.mensajes(cid)[-1] == {"role": "user", "content": "otra"}


def test_guardar_turno_failing_on_messages_leaves_no_conversation(tmp_path):
    h = Historial(tmp_path / "eva.db")

    with pytest.raises(sqlite3.IntegrityError):
        h.guardar_turno(None, "hola", [{"role": "user", "content": "hola"}, {"role": "robot", "content": "x"}])
    assert h.listar_conversaciones() == []
    with sqlite3.connect(tmp_path / "eva.db") as db:
        assert db.execute("SELECT COUNT(*) FROM mensajes").fetchone() == (0,)


# ----- S7: private data folder outside Windows -----


def test_data_folder_is_made_private_outside_windows(tmp_path, monkeypatch):
    llamadas = []
    monkeypatch.setenv("EVA_DATA_DIR", str(tmp_path / "datos"))
    monkeypatch.setattr("sys.platform", "linux")
    monkeypatch.setattr("app.config.os.chmod", lambda ruta, modo: llamadas.append((ruta, modo)))

    carpeta = carpeta_datos()
    assert llamadas == [(carpeta, 0o700)]


def test_existing_user_chosen_folder_keeps_its_permissions(tmp_path, monkeypatch):
    llamadas = []
    (tmp_path / "compartida").mkdir()
    monkeypatch.setenv("EVA_DATA_DIR", str(tmp_path / "compartida"))
    monkeypatch.setattr("sys.platform", "linux")
    monkeypatch.setattr("app.config.os.chmod", lambda ruta, modo: llamadas.append((ruta, modo)))

    assert carpeta_datos() == tmp_path / "compartida"
    assert llamadas == []


def test_chmod_failure_does_not_block_startup(tmp_path, monkeypatch):
    def rechazar(ruta, modo):
        raise PermissionError("vfat")

    monkeypatch.setenv("EVA_DATA_DIR", str(tmp_path / "usb"))
    monkeypatch.setattr("sys.platform", "linux")
    monkeypatch.setattr("app.config.os.chmod", rechazar)

    assert carpeta_datos() == tmp_path / "usb"


def test_data_folder_permissions_are_unchanged_on_windows(tmp_path, monkeypatch):
    llamadas = []
    monkeypatch.setenv("EVA_DATA_DIR", str(tmp_path / "datos"))
    monkeypatch.setattr("sys.platform", "win32")
    monkeypatch.setattr("app.config.os.chmod", lambda ruta, modo: llamadas.append((ruta, modo)))

    carpeta_datos()
    assert llamadas == []


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permissions")
def test_data_folder_mode_is_0o700(tmp_path, monkeypatch):
    destino = tmp_path / "datos"
    destino.mkdir(mode=0o755)
    monkeypatch.setenv("EVA_DATA_DIR", str(destino))
    assert stat.S_IMODE(carpeta_datos().stat().st_mode) == 0o700
