"""In-memory stand-in for ``MemoriaEngram`` (same public API) for chat and UI tests."""

import re

from app.memory.engram_client import ErrorMemoria, Recuerdo


def _palabras(texto: str) -> set[str]:
    # Like Engram's full-text search: whole words only ("llama" != "llamo").
    return set(re.findall(r"\w+", texto.lower()))


class MemoriaFalsa:
    def __init__(self, recuerdos=(), disponible=True, fallar: str | None = None):
        self.datos: dict[int, Recuerdo] = {r.id: r for r in recuerdos}
        self.siguiente = max(self.datos, default=0) + 1
        self._disponible = disponible
        self.fallar = fallar  # every operation raises ErrorMemoria(fallar)
        self.llamadas: list[str] = []
        self.cerrada = False

    @property
    def disponible(self) -> bool:
        return self._disponible and not self.cerrada

    @property
    def error(self) -> str | None:
        return None if self._disponible else "Engram no está"

    def esperar(self, timeout=None) -> bool:
        return self.disponible

    def _operar(self, nombre: str) -> None:
        self.llamadas.append(nombre)
        if not self.disponible:
            raise ErrorMemoria(self.error or "cerrada")
        if self.fallar:
            raise ErrorMemoria(self.fallar)

    def buscar(self, texto, limite=5):
        self._operar("buscar")
        consulta = _palabras(texto)
        return [
            r for r in self.listar_sin_registro()
            if consulta & _palabras(r.titulo + " " + r.contenido)
        ][:limite]

    def guardar(self, titulo, contenido):
        self._operar("guardar")
        existente = next((r.id for r in self.datos.values() if r.titulo == titulo), None)
        id_ = existente or self.siguiente
        if existente is None:
            self.siguiente += 1
        self.datos[id_] = Recuerdo(id_, titulo, contenido)
        return id_

    def listar_sin_registro(self):
        return sorted(self.datos.values(), key=lambda r: r.id, reverse=True)

    def listar(self):
        self._operar("listar")
        return self.listar_sin_registro()

    def borrar(self, id_):
        self._operar("borrar")
        if self.datos.pop(id_, None) is None:
            raise ErrorMemoria(f"Engram devolvió un error: Observation #{id_} not found")

    def cerrar(self):
        self.cerrada = True
