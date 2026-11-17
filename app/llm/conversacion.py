"""A conversation with Eva, persisted in the SQLite history, plus the
long-term memory it uses (facts about the user kept in Engram).

Long-term memories are context only: they go to the model in one extra system
message and are never stored in the chat history.
"""

import json
import logging
import re
import threading
from collections.abc import Iterator
from typing import Protocol

from app.llm.base import ClienteLLM, ErrorLLM
from app.llm.prompts import PROMPT_EXTRACCION, PROMPT_SISTEMA
from app.memory.engram_client import ErrorMemoria, Recuerdo
from app.memory.history import Historial

log = logging.getLogger(__name__)

# How many saved messages go to the model as context.
LIMITE_CONTEXTO = 20
LARGO_TITULO = 40
# How many long-term memories go to the model before each answer.
LIMITE_RECUERDOS = 8
ENCABEZADO_RECUERDOS = "Recuerdos sobre el usuario (de conversaciones anteriores):"

_PENSAMIENTO = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_CERCO = re.compile(r"```[a-zA-Z]*")


def extraer_hechos(salida: str) -> list[tuple[str, str]]:
    """``(titulo, contenido)`` pairs from the extraction model's answer.

    Tolerates thinking blocks, code fences and text around the JSON list;
    anything that is not a JSON list gives no facts, and items without a
    non-empty text ``titulo`` and ``contenido`` are skipped.
    """
    texto = _PENSAMIENTO.sub("", salida or "")
    if "<think>" in texto.lower():
        return []  # the model never finished thinking: no final answer
    texto = _CERCO.sub("", texto)
    inicio, fin = texto.find("["), texto.rfind("]")
    if inicio < 0 or fin < inicio:
        return []
    try:
        datos = json.loads(texto[inicio : fin + 1])
    except ValueError:
        return []
    if not isinstance(datos, list):
        return []
    hechos = []
    for item in datos:
        if not isinstance(item, dict):
            continue
        titulo, contenido = item.get("titulo"), item.get("contenido")
        if isinstance(titulo, str) and isinstance(contenido, str) and titulo.strip() and contenido.strip():
            hechos.append((titulo.strip(), contenido.strip()))
    return hechos


class Memoria(Protocol):
    """What the chat needs from long-term memory (``MemoriaEngram``)."""

    @property
    def disponible(self) -> bool: ...
    @property
    def error(self) -> str | None: ...
    def esperar(self, timeout: float | None = None) -> bool: ...
    def buscar(self, texto: str, limite: int = 5) -> list[Recuerdo]: ...
    def guardar(self, titulo: str, contenido: str) -> int | None: ...
    def listar(self) -> list[Recuerdo]: ...
    def borrar(self, id_: int) -> None: ...
    def cerrar(self) -> None: ...


class MemoriaUsuario:
    """Long-term memory as the chat uses it.

    Engram's search matches whole words only ("¿Cómo me llamo?" does not find
    "El usuario se llama Ana"), so the context is the search hits plus the
    user's other known facts from a cached list. The list is loaded once
    (``cargar``, in the background at startup) and kept up to date after each
    save or delete, so no turn has to run ``engram export``.

    Every method may be called from any thread. Only ``listar`` and ``borrar``
    (used by the memories dialog) raise ``ErrorMemoria``; the rest log memory
    failures and carry on, so the chat never depends on Engram.
    """

    def __init__(self, memoria: Memoria) -> None:
        self.memoria = memoria
        self._cache: list[Recuerdo] = []  # newest first
        self._candado = threading.Lock()

    @property
    def disponible(self) -> bool:
        return bool(self.memoria.disponible)

    def cargar(self) -> bool:
        """Wait for Engram and load the known facts; return ``disponible``."""
        try:
            if not self.memoria.esperar():
                log.warning("Long-term memory unavailable: %s", self.memoria.error)
                return False
            self._fijar(self.memoria.listar())
        except ErrorMemoria as exc:
            log.warning("Could not load the memories: %s", exc)
        except Exception:
            log.exception("Unexpected error loading the memories")
        return self.disponible

    def recuerdos(self) -> list[Recuerdo]:
        """The cached facts, newest first."""
        with self._candado:
            return list(self._cache)

    def contexto(self, pregunta: str) -> list[Recuerdo]:
        """Memories for answering ``pregunta``: search hits first, then the
        other cached facts, without duplicates, at most ``LIMITE_RECUERDOS``."""
        encontrados: list[Recuerdo] = []
        if self.disponible:
            try:
                encontrados = self.memoria.buscar(pregunta, LIMITE_RECUERDOS)
            except ErrorMemoria as exc:
                log.warning("Memory search failed: %s", exc)
        vistos: set[int] = set()
        elegidos: list[Recuerdo] = []
        for recuerdo in [*encontrados, *self.recuerdos()]:
            if recuerdo.id in vistos:
                continue
            vistos.add(recuerdo.id)
            elegidos.append(recuerdo)
            if len(elegidos) == LIMITE_RECUERDOS:
                break
        return elegidos

    def aprender(self, cliente: ClienteLLM, texto: str) -> list[Recuerdo]:
        """Ask the model for durable facts in the user's message ``texto`` and
        save them. Returns what was saved; never raises."""
        if not self.disponible:
            return []
        mensajes = [
            {"role": "system", "content": PROMPT_EXTRACCION},
            {"role": "user", "content": texto},
        ]
        try:
            salida = "".join(cliente.responder(mensajes))
        except ErrorLLM as exc:
            log.warning("Could not extract facts to remember: %s", exc)
            return []
        except Exception:
            log.exception("Unexpected error extracting facts to remember")
            return []
        guardados = []
        for titulo, contenido in extraer_hechos(salida):
            try:
                id_ = self.memoria.guardar(titulo, contenido)
            except ErrorMemoria as exc:
                log.warning("Could not save the memory %r: %s", titulo, exc)
                continue
            except Exception:
                log.exception("Unexpected error saving the memory %r", titulo)
                continue
            if id_ is not None:
                guardados.append(Recuerdo(id_, titulo, contenido))
        if guardados:
            with self._candado:
                ids = {r.id for r in guardados}
                # Same title => same id in Engram: replace the old version.
                self._cache = [*reversed(guardados), *(r for r in self._cache if r.id not in ids)]
        return guardados

    def listar(self) -> list[Recuerdo]:
        """Every memory, fresh from Engram (refreshes the cache). Raises ErrorMemoria."""
        recuerdos = self.memoria.listar()
        self._fijar(recuerdos)
        return recuerdos

    def borrar(self, id_: int) -> None:
        """Delete a memory permanently. Raises ErrorMemoria."""
        self.memoria.borrar(id_)
        with self._candado:
            self._cache = [r for r in self._cache if r.id != id_]

    def cerrar(self) -> None:
        self.memoria.cerrar()

    def _fijar(self, recuerdos: list[Recuerdo]) -> None:
        with self._candado:
            self._cache = list(recuerdos)


def _titulo(texto: str) -> str:
    texto = " ".join(texto.split())
    if len(texto) <= LARGO_TITULO:
        return texto
    return texto[:LARGO_TITULO].rstrip(" ,.;:") + "…"


class Conversacion:
    def __init__(
        self,
        cliente: ClienteLLM,
        historial: Historial,
        conversacion_id: int | None = None,
        prompt_sistema: str = PROMPT_SISTEMA,
        memoria: MemoriaUsuario | None = None,
    ) -> None:
        self.cliente = cliente
        self.historial = historial
        # None until the first answer completes, so empty chats are never saved.
        self.conversacion_id = conversacion_id
        self.prompt_sistema = prompt_sistema
        self.memoria = memoria

    def _recuerdos(self, texto: str) -> list[dict[str, str]]:
        """The memories system message for ``texto``, if there is any memory."""
        if self.memoria is None:
            return []
        try:
            recuerdos = self.memoria.contexto(texto)
        except Exception:
            log.exception("Unexpected error reading long-term memory")
            return []
        if not recuerdos:
            return []
        lineas = [ENCABEZADO_RECUERDOS, *(f"- {r.titulo}: {r.contenido}" for r in recuerdos)]
        return [{"role": "system", "content": "\n".join(lineas)}]

    def enviar(self, texto: str) -> Iterator[str]:
        """Stream Eva's answer to ``texto``.

        The question and answer are saved only when the answer completes, so a
        failed turn (``ErrorLLM``) saves nothing.
        """
        contexto = []
        if self.conversacion_id is not None:
            contexto = self.historial.mensajes(self.conversacion_id, limite=LIMITE_CONTEXTO)
        pregunta = {"role": "user", "content": texto}
        mensajes = [
            {"role": "system", "content": self.prompt_sistema},
            *self._recuerdos(texto),  # context only: never saved below
            *contexto,
            pregunta,
        ]

        partes: list[str] = []
        for parte in self.cliente.responder(mensajes):
            partes.append(parte)
            yield parte

        if not partes:
            return  # nothing to remember from an empty answer
        # One transaction: a failed save never leaves an empty conversation.
        self.conversacion_id = self.historial.guardar_turno(
            self.conversacion_id,
            _titulo(texto),
            [pregunta, {"role": "assistant", "content": "".join(partes)}],
        )
