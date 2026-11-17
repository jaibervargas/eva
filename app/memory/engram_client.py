"""Long-term memory with Engram (Gentleman Programming) over MCP.

Eva starts ``engram mcp --tools=agent,admin`` in the background and talks to
it with the ``mcp`` Python library. The async MCP session lives in ONE
background thread with its own asyncio loop; ``MemoriaEngram`` is a
synchronous, thread-safe facade over it, so the UI and worker threads can
call it directly.

Eva's memories live in ``carpeta_datos()/engram`` (``ENGRAM_DATA_DIR``) under
project ``eva`` (``ENGRAM_PROJECT``), never in the developer's ``~/.engram``.

Errors: every operation either returns its documented value or raises
``ErrorMemoria`` (Engram missing, failed to start, timed out, replied with an
error or with something unexpected, or the memory was closed). Each failure
is logged. ``cerrar()`` never raises.
"""

import asyncio
import json
import logging
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unicodedata
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import get_default_environment, stdio_client

from app.config import carpeta_datos

log = logging.getLogger(__name__)

PROYECTO = "eva"
NOMBRE_BINARIO = "engram.exe" if sys.platform == "win32" else "engram"
CARPETA_APP = Path(__file__).resolve().parents[2]
TIMEOUT = 10.0
LIMITE_BUSQUEDA = 20  # Engram's maximum for mem_search

_SIN_BINARIO = "No se encontró el programa de Engram (bin/engram)"
_SIN_RESPUESTA = "Engram no respondió a tiempo"
_CERRADA = "La memoria a largo plazo está cerrada"
# A fresh store has no "eva" project until the first save: Engram 3.7.2 then
# answers searches with error_code unknown_project and `export` exits with
# "unknown project: eva". Both mean "no memories yet", not a failure.
_PROYECTO_DESCONOCIDO = '"unknown_project"'


def _sin_proyecto(exc: subprocess.CalledProcessError) -> bool:
    salida = exc.stderr or b""
    if isinstance(salida, bytes):
        salida = salida.decode("utf-8", "replace")
    return "unknown project" in salida.lower()


class ErrorMemoria(Exception):
    """Long-term memory is unavailable or an operation on it failed."""


@dataclass(frozen=True)
class Recuerdo:
    id: int
    titulo: str
    contenido: str


class SesionMCP(Protocol):
    async def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> Any: ...


Conectar = Callable[[], AbstractAsyncContextManager[SesionMCP]]


def buscar_binario(carpeta_app: Path | None = None) -> Path | None:
    """``bin/engram(.exe)`` next to the app, else ``engram`` on PATH, else None."""
    local = (carpeta_app or CARPETA_APP) / "bin" / NOMBRE_BINARIO
    if local.is_file():
        return local
    en_path = shutil.which("engram")
    return Path(en_path) if en_path else None


def carpeta_memoria() -> Path:
    """Eva's own Engram data folder, created if missing."""
    carpeta = carpeta_datos() / "engram"
    carpeta.mkdir(parents=True, exist_ok=True)
    return carpeta


def _entorno(carpeta: Path) -> dict[str, str]:
    return {"ENGRAM_DATA_DIR": str(carpeta), "ENGRAM_PROJECT": PROYECTO}


def parametros_servidor(binario: Path, carpeta: Path) -> StdioServerParameters:
    # admin is needed for mem_delete. The mcp library adds only a minimal safe
    # environment (PATH, USERPROFILE, ...) to these variables.
    return StdioServerParameters(
        command=str(binario), args=["mcp", "--tools=agent,admin"], env=_entorno(carpeta)
    )


def clave_tema(titulo: str) -> str:
    """Stable ``topic_key`` so saving the same fact again updates it in place."""
    ascii_ = unicodedata.normalize("NFKD", titulo).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_.lower()).strip("-")
    return f"usuario/{slug or 'recuerdo'}"


def _conectar_stdio(binario: Path, carpeta: Path) -> Conectar:
    @asynccontextmanager
    async def conectar():
        # The server's stderr goes to a log file: a windowed app may have no stderr.
        with open(carpeta / "engram-mcp.log", "a", encoding="utf-8") as errlog:
            async with stdio_client(parametros_servidor(binario, carpeta), errlog=errlog) as (leer, escribir):
                async with ClientSession(leer, escribir) as sesion:
                    await sesion.initialize()
                    yield sesion

    return conectar


def _exportar_cli(binario: Path, carpeta: Path, timeout: float) -> Callable[[], dict]:
    """Engram has no MCP tool to list every memory, so use ``engram export``.

    The CLI reads the same database (SQLite in WAL mode, safe while the MCP
    server runs) and writes JSON with full contents and ``deleted_at``.
    """

    def exportar() -> dict:
        with tempfile.TemporaryDirectory() as tmp:
            archivo = Path(tmp) / "export.json"
            subprocess.run(
                [str(binario), "export", str(archivo), "--project", PROYECTO],
                env={**get_default_environment(), **_entorno(carpeta)},
                capture_output=True,
                timeout=timeout,
                check=True,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            return json.loads(archivo.read_text(encoding="utf-8"))

    return exportar


class MemoriaEngram:
    """Synchronous, thread-safe access to Eva's Engram memory.

    The connection starts in the background as soon as the object is built;
    calls wait for it (within ``timeout``). ``conectar``/``exportar`` can be
    injected (tests); by default the binary is looked up with
    ``buscar_binario`` and the data folder is ``carpeta_memoria()``.
    """

    def __init__(
        self,
        binario: Path | None = None,
        carpeta: Path | None = None,
        *,
        timeout: float = TIMEOUT,
        conectar: Conectar | None = None,
        exportar: Callable[[], dict] | None = None,
    ) -> None:
        self._timeout = timeout
        self._carpeta = carpeta or carpeta_memoria()
        self._carpeta.mkdir(parents=True, exist_ok=True)
        self._binario = binario or (None if conectar else buscar_binario())
        self._error: str | None = None
        self._cerrada = False
        self._candado = threading.Lock()
        self._sesion: SesionMCP | None = None
        self._listo = threading.Event()  # set once connected or failed
        self._fin = asyncio.Event()
        self._tarea: asyncio.Task | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._hilo: threading.Thread | None = None

        if conectar is None and self._binario is not None:
            conectar = _conectar_stdio(self._binario, self._carpeta)
        if exportar is None and self._binario is not None:
            exportar = _exportar_cli(self._binario, self._carpeta, timeout)
        self._exportar = exportar
        if conectar is None:
            self._fallar(_SIN_BINARIO)
            self._listo.set()
            return
        self._conectar = conectar
        self._loop = asyncio.new_event_loop()
        self._hilo = threading.Thread(target=self._ejecutar, name="engram-mcp", daemon=True)
        self._hilo.start()

    # --- public API ---

    @property
    def disponible(self) -> bool:
        """True while connected to Engram."""
        return self._sesion is not None and not self._cerrada

    @property
    def error(self) -> str | None:
        """Why memory is unavailable (startup failure), else None."""
        return self._error

    def esperar(self, timeout: float | None = None) -> bool:
        """Wait (at most ``timeout``, default the client timeout) until Engram
        is connected or has failed; return ``disponible``."""
        self._listo.wait(self._timeout if timeout is None else timeout)
        return self.disponible

    def buscar(self, texto: str, limite: int = 5) -> list[Recuerdo]:
        """Memories related to ``texto`` (any word may match), best first."""
        consulta = texto.strip()
        if not consulta or limite <= 0:
            return []
        datos = self._json(self._llamar("mem_search", {
            "query": consulta,
            "limit": min(limite, LIMITE_BUSQUEDA),
            "match_mode": "any",
            "response_format": "compact",
        }, si_no_hay_proyecto="{}"))
        recuerdos = []
        for r in datos.get("results") or []:
            if r.get("project", PROYECTO) != PROYECTO:
                continue
            id_ = int(r["id"])
            contenido = r.get("preview", "")
            # Previews are cut at 300 characters; an older engram (1.x on PATH)
            # ignores response_format=compact and sends no preview at all.
            if r.get("truncated") or "preview" not in r:
                contenido = self._contenido_completo(id_, contenido)
            recuerdos.append(Recuerdo(id_, r.get("title", ""), contenido))
        return recuerdos

    def guardar(self, titulo: str, contenido: str) -> int | None:
        """Save (or update, same title) a fact about the user; return its id."""
        if not titulo.strip() or not contenido.strip():
            return None
        datos = self._json(self._llamar("mem_save", {
            "title": titulo,
            "content": contenido,
            "type": "preference",
            "topic_key": clave_tema(titulo),
            "capture_prompt": False,
        }))
        id_ = datos.get("id")
        return int(id_) if isinstance(id_, int) else None

    def listar(self) -> list[Recuerdo]:
        """Every live memory of Eva's project, newest first."""
        self._comprobar()
        if self._exportar is None:
            raise self._fallo(_SIN_BINARIO)
        try:
            datos = self._exportar()
        except subprocess.CalledProcessError as exc:
            if _sin_proyecto(exc):
                return []
            raise self._fallo(f"No se pudieron listar los recuerdos: {exc}") from exc
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            raise self._fallo(f"No se pudieron listar los recuerdos: {exc}") from exc
        observaciones = (datos or {}).get("observations") or []
        vivas = [
            o for o in observaciones
            if o.get("project") == PROYECTO and not o.get("deleted_at")
        ]
        vivas.sort(key=lambda o: int(o["id"]), reverse=True)
        return [Recuerdo(int(o["id"]), o.get("title", ""), o.get("content", "")) for o in vivas]

    def borrar(self, id_: int) -> None:
        """Delete a memory permanently."""
        self._llamar("mem_delete", {"id": id_, "hard_delete": True, "expected_project": PROYECTO})

    def cerrar(self) -> None:
        """Close the session and stop the Engram process. Idempotent, never raises."""
        with self._candado:
            if self._cerrada:
                return
            self._cerrada = True
        if self._hilo is None or self._loop is None:
            return
        try:
            self._loop.call_soon_threadsafe(self._detener)
        except RuntimeError:  # loop already finished
            pass
        # stdio_client waits up to 2 s for engram to exit before terminating it.
        self._hilo.join(timeout=max(self._timeout, 5.0))
        if self._hilo.is_alive():
            log.warning("Engram did not stop in time")

    # --- background thread ---

    def _ejecutar(self) -> None:
        loop = self._loop
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._principal())
        finally:
            loop.close()

    async def _principal(self) -> None:
        self._tarea = asyncio.current_task()
        if self._fin.is_set():  # closed before the thread started
            self._listo.set()
            return
        try:
            async with self._conectar() as sesion:
                self._sesion = sesion
                self._listo.set()
                await self._fin.wait()
        except asyncio.CancelledError:
            pass
        except Exception as exc:  # any startup/transport failure
            while isinstance(exc, BaseExceptionGroup) and exc.exceptions:
                exc = exc.exceptions[0]  # anyio task groups wrap the real cause
            if not self._cerrada:
                if self._sesion is None:
                    self._fallar(f"No se pudo iniciar Engram: {exc}")
                else:
                    self._fallar(f"Engram se detuvo: {exc}")
        finally:
            self._sesion = None
            self._listo.set()

    def _detener(self) -> None:
        self._fin.set()
        if self._sesion is None and self._tarea is not None:
            self._tarea.cancel()  # still starting: abort the connection attempt

    # --- helpers ---

    def _fallar(self, mensaje: str) -> None:
        self._error = mensaje
        log.warning("Long-term memory unavailable: %s", mensaje)

    def _fallo(self, mensaje: str) -> ErrorMemoria:
        log.warning("Engram: %s", mensaje)
        return ErrorMemoria(mensaje)

    def _comprobar(self) -> None:
        if self._cerrada:
            raise ErrorMemoria(_CERRADA)

    def _llamar(
        self, herramienta: str, argumentos: dict[str, Any], si_no_hay_proyecto: str | None = None
    ) -> str:
        """Call an MCP tool from any thread; return its text or raise ErrorMemoria.

        With ``si_no_hay_proyecto``, an "unknown project" error (no memories
        saved yet) returns that text instead of raising.
        """
        self._comprobar()
        limite = time.monotonic() + self._timeout
        if not self._listo.wait(self._timeout):
            raise self._fallo(_SIN_RESPUESTA)
        sesion = self._sesion
        if sesion is None or self._loop is None:
            self._comprobar()
            raise ErrorMemoria(self._error or _SIN_BINARIO)
        try:
            futuro = asyncio.run_coroutine_threadsafe(sesion.call_tool(herramienta, argumentos), self._loop)
        except RuntimeError as exc:  # loop closed meanwhile
            raise self._fallo(f"Error al hablar con Engram: {exc}") from exc
        try:
            resultado = futuro.result(timeout=max(0.0, limite - time.monotonic()))
        except TimeoutError:
            futuro.cancel()
            raise self._fallo(_SIN_RESPUESTA) from None
        except Exception as exc:
            raise self._fallo(f"Error al hablar con Engram: {exc}") from exc
        texto = "".join(
            c.text for c in getattr(resultado, "content", []) if getattr(c, "type", None) == "text"
        )
        if getattr(resultado, "isError", False):
            if si_no_hay_proyecto is not None and _PROYECTO_DESCONOCIDO in texto:
                return si_no_hay_proyecto
            raise self._fallo(f"Engram devolvió un error: {texto}")
        return texto

    def _json(self, texto: str) -> dict:
        try:
            datos = json.loads(texto)
        except ValueError:
            datos = None
        if not isinstance(datos, dict):
            raise self._fallo(f"Respuesta inesperada de Engram: {texto[:200]}")
        return datos

    def _contenido_completo(self, id_: int, respaldo: str) -> str:
        """Full content from mem_get_observation's text: header line, content, trailer."""
        try:
            texto = self._json(self._llamar("mem_get_observation", {"id": id_})).get("result", "")
        except ErrorMemoria:
            return respaldo
        _, _, resto = texto.partition("\n")
        fin = resto.rfind("\nSession: ")
        return resto[:fin] if fin >= 0 else respaldo
