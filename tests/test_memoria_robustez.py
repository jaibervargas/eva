"""Regression tests for the block 2B verification advisories."""

import os

from app.llm.base import ClienteLLM
from app.llm.conversacion import MemoriaUsuario
from app.memory import engram_client
from app.memory.engram_client import NOMBRE_BINARIO, Recuerdo, buscar_binario


# ----- the binary is never taken from the current folder -----


def test_engram_in_the_current_folder_is_ignored(tmp_path, monkeypatch):
    app = tmp_path / "app"  # no bin/ here
    app.mkdir()
    lanzamiento = tmp_path / "descargas"
    lanzamiento.mkdir()
    (lanzamiento / NOMBRE_BINARIO).write_bytes(b"malicioso")
    monkeypatch.chdir(lanzamiento)
    vacia = tmp_path / "vacia"
    vacia.mkdir()
    monkeypatch.setenv("PATH", str(vacia))  # a real PATH with no engram

    assert buscar_binario(app) is None


def test_engram_on_an_absolute_path_entry_is_found(tmp_path, monkeypatch):
    app = tmp_path / "app"
    app.mkdir()
    instalado = tmp_path / "instalado"
    instalado.mkdir()
    (instalado / NOMBRE_BINARIO).write_bytes(b"engram")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PATH", os.pathsep.join([".", "", "relativa", str(instalado)]))

    assert buscar_binario(app) == instalado / NOMBRE_BINARIO


# ----- a fact learned while the list is loading is not lost -----


class LLMHechos(ClienteLLM):
    def responder(self, mensajes):
        yield '[{"titulo": "Nombre", "contenido": "El usuario se llama Ana"}]'


class MemoriaLenta:
    """Fake memory whose listing overlaps a save or a delete."""

    disponible = True
    error = None

    def __init__(self):
        self.guardados: dict[int, Recuerdo] = {7: Recuerdo(7, "Viejo", "dato viejo")}
        self.durante_listar = None

    def esperar(self, timeout=None):
        return True

    def buscar(self, texto, limite=5):
        return []

    def guardar(self, titulo, contenido):
        self.guardados[1] = Recuerdo(1, titulo, contenido)
        return 1

    def borrar(self, id_):
        self.guardados.pop(id_, None)

    def listar(self):
        foto = list(self.guardados.values())  # snapshot taken first...
        if self.durante_listar:
            self.durante_listar()  # ...then another thread saves/deletes
        return foto

    def cerrar(self):
        pass


def test_fact_saved_while_listing_stays_in_the_cache():
    memoria = MemoriaLenta()
    usuario = MemoriaUsuario(memoria)
    memoria.durante_listar = lambda: usuario.aprender(LLMHechos(), "Me llamo Ana")

    usuario.listar()

    assert {r.id for r in usuario.recuerdos()} == {1, 7}
    assert [r.titulo for r in usuario.contexto("¿Cómo me llamo?")][0] == "Nombre"


def test_fact_deleted_while_listing_does_not_come_back():
    memoria = MemoriaLenta()
    usuario = MemoriaUsuario(memoria)
    memoria.durante_listar = lambda: usuario.borrar(7)

    usuario.listar()

    assert usuario.recuerdos() == []
