"""Latency and resilience of long-term memory (review 2B fixes, S1-S5).

Real-binary tests run Engram with a fresh temporary ``ENGRAM_DATA_DIR`` and
only ever terminate the engram process started by the test itself, by PID.
"""

import asyncio
import json
import logging
import os
import signal
import subprocess
import sys
import threading
import time
from contextlib import asynccontextmanager

import anyio
import pytest

from app.llm.base import ClienteLLM
from app.llm.conversacion import Conversacion, MemoriaUsuario
from app.llm.prompts import PROMPT_EXTRACCION
from app.memory import engram_client as ec
from app.memory.engram_client import ErrorMemoria, MemoriaEngram, Recuerdo
from app.memory.history import Historial
from tests.memoria_falsa import MemoriaFalsa
from tests.test_engram_client import FakeEngram, _texto

# ----- S1: one deadline for the search and every full-content fetch -----


class EngramLento(FakeEngram):
    """Searches answer at once; every mem_get_observation stalls."""

    def __init__(self, preview=10):
        super().__init__(preview=preview)
        self.fetch_retraso = 30.0

    async def call_tool(self, name, arguments=None):
        if name == "mem_get_observation":
            self.llamadas.append((name, dict(arguments or {})))
            await asyncio.sleep(self.fetch_retraso)
        return await super().call_tool(name, arguments)


def test_buscar_with_stalled_fetches_returns_previews_within_one_timeout(tmp_path):
    fake = EngramLento(preview=10)
    timeout = 0.4
    m = MemoriaEngram(carpeta=tmp_path, conectar=fake.conectar, timeout=timeout)
    try:
        for i in range(3):
            m.guardar(f"Dato {i}", f"café número {i} " + "x" * 40)
        inicio = time.monotonic()
        recuerdos = m.buscar("café")
        transcurrido = time.monotonic() - inicio
        assert transcurrido < 1.5 * timeout
        assert [r.titulo for r in recuerdos] == ["Dato 2", "Dato 1", "Dato 0"]
        assert [r.contenido for r in recuerdos] == ["café númer"] * 3  # the 10-char previews
    finally:
        m.cerrar()


def test_buscar_drops_hits_without_preview_when_the_deadline_is_spent(tmp_path):
    fake = EngramLento()
    timeout = 0.4
    m = MemoriaEngram(carpeta=tmp_path, conectar=fake.conectar, timeout=timeout)
    try:
        m.guardar("Nombre", "Se llama Ana")
        m.guardar("Ciudad", "Ana vive en Medellín")
        original = fake._mem_search

        def sin_preview(a):
            datos = json.loads(original(a).content[0].text)
            for r in datos["results"]:
                del r["preview"], r["truncated"]
            return _texto(datos)

        fake._mem_search = sin_preview
        inicio = time.monotonic()
        assert m.buscar("ana") == []
        assert time.monotonic() - inicio < 1.5 * timeout
    finally:
        m.cerrar()


def test_buscar_fast_fetches_still_bring_the_full_content(tmp_path):
    fake = EngramLento(preview=10)
    fake.fetch_retraso = 0
    m = MemoriaEngram(carpeta=tmp_path, conectar=fake.conectar, timeout=2)
    try:
        largo = "Le gusta el café " + "y" * 40
        m.guardar("Bebida", largo)
        assert m.buscar("café") == [Recuerdo(1, "Bebida", largo)]
    finally:
        m.cerrar()


# ----- S4: malformed search results -----


def _resultados(*items):
    return lambda a: _texto({"project": "eva", "result": "Found", "results": list(items)})


def test_malformed_search_items_are_skipped_and_logged(tmp_path, caplog):
    fake = FakeEngram()
    m = MemoriaEngram(carpeta=tmp_path, conectar=fake.conectar, timeout=2)
    try:
        bueno = {"id": 3, "title": "Nombre", "preview": "Se llama Ana", "project": "eva", "truncated": False}
        fake._mem_search = _resultados(
            {"title": "sin id", "preview": "x", "project": "eva"},
            {"id": "abc", "title": "id raro", "preview": "x", "project": "eva"},
            {"id": None, "title": "id nulo", "preview": "x", "project": "eva"},
            "no soy un objeto",
            bueno,
        )
        with caplog.at_level(logging.WARNING, logger="app.memory.engram_client"):
            assert m.buscar("ana") == [Recuerdo(3, "Nombre", "Se llama Ana")]
        assert "id raro" in caplog.text or "abc" in caplog.text
    finally:
        m.cerrar()


@pytest.mark.parametrize("respuesta", [
    {"project": "eva", "results": "no es una lista"},
    {"project": "eva", "results": {"id": 1}},
])
def test_search_reply_of_the_wrong_shape_raises_only_memory_error(tmp_path, respuesta):
    fake = FakeEngram()
    m = MemoriaEngram(carpeta=tmp_path, conectar=fake.conectar, timeout=2)
    try:
        fake._mem_search = lambda a: _texto(respuesta)
        try:
            assert m.buscar("ana") == []
        except ErrorMemoria:
            pass
    finally:
        m.cerrar()


class BusquedaRota(MemoriaFalsa):
    def __init__(self, recuerdos, error):
        super().__init__(recuerdos)
        self.error_busqueda = error

    def buscar(self, texto, limite=5):
        raise self.error_busqueda


@pytest.mark.parametrize("error", [KeyError("id"), TypeError("x"), ValueError("y"), ErrorMemoria("z")])
def test_contexto_keeps_the_cached_facts_whatever_the_search_failure(error, tmp_path, caplog):
    usuario = MemoriaUsuario(BusquedaRota([Recuerdo(1, "Nombre", "El usuario se llama Ana")], error))
    usuario.cargar()
    with caplog.at_level(logging.WARNING):
        assert usuario.contexto("¿Cómo me llamo?") == [Recuerdo(1, "Nombre", "El usuario se llama Ana")]

    class LLM(ClienteLLM):
        modelo = "m"

        def __init__(self):
            self.recibidos = []

        def responder(self, mensajes):
            self.recibidos.append(mensajes)
            yield "Hola"

    llm = LLM()
    list(Conversacion(llm, Historial(tmp_path / "eva.db"), memoria=usuario).enviar("¿Cómo me llamo?"))
    assert "- Nombre: El usuario se llama Ana" in llm.recibidos[0][1]["content"]


# ----- S2: the cache load is retried lazily -----


class MemoriaTardia(MemoriaFalsa):
    """Connects late and/or fails its first listings."""

    def __init__(self, recuerdos=(), disponible=True, fallos_listar=0):
        super().__init__(recuerdos, disponible=disponible)
        self.fallos_listar = fallos_listar

    def listar(self):
        self._operar("listar")
        if self.fallos_listar:
            self.fallos_listar -= 1
            raise ErrorMemoria("export lento")
        return self.listar_sin_registro()


ANA = Recuerdo(1, "Nombre", "El usuario se llama Ana")


def test_late_connection_loads_the_cache_on_the_next_turn():
    falsa = MemoriaTardia([ANA], disponible=False)
    usuario = MemoriaUsuario(falsa)
    assert usuario.cargar() is False

    falsa._disponible = True  # Engram finished starting after cargar gave up
    assert usuario.contexto("¿Cómo me llamo?") == [ANA]


def test_failed_startup_listing_returns_false_and_is_retried_next_turn():
    falsa = MemoriaTardia([ANA], fallos_listar=2)
    usuario = MemoriaUsuario(falsa)
    assert usuario.cargar() is False
    assert falsa.llamadas.count("listar") == 1

    assert usuario.contexto("¿Cómo me llamo?") == []  # retried once, failed again
    assert falsa.llamadas.count("listar") == 2
    assert usuario.contexto("¿Cómo me llamo?") == [ANA]  # next turn: loaded
    assert falsa.llamadas.count("listar") == 3

    usuario.contexto("otra pregunta")
    assert falsa.llamadas.count("listar") == 3  # loaded: never listed again


class LLMHechos(ClienteLLM):
    modelo = "m"

    def responder(self, mensajes):
        yield '[{"titulo": "Ciudad", "contenido": "Vive en Medellín"}]'


def test_retry_happens_at_most_once_per_turn_and_aprender_retries_next():
    falsa = MemoriaTardia([ANA], fallos_listar=3)
    usuario = MemoriaUsuario(falsa)
    usuario.cargar()  # 1st failure

    usuario.contexto("hola")  # 2nd failure: this turn's retry
    usuario.aprender(LLMHechos(), "Vivo en Medellín")  # same turn: no second retry
    assert falsa.llamadas.count("listar") == 2

    usuario.contexto("hola")  # 3rd failure
    assert falsa.llamadas.count("listar") == 3
    falsa.fallos_listar = 0
    usuario.aprender(LLMHechos(), "Vivo en Medellín")  # same turn as the last failure
    assert falsa.llamadas.count("listar") == 3


def test_aprender_retries_a_failed_load_when_no_turn_has_retried():
    falsa = MemoriaTardia([ANA], fallos_listar=1)
    usuario = MemoriaUsuario(falsa)
    usuario.cargar()
    usuario.aprender(LLMHechos(), "Vivo en Medellín")
    assert falsa.llamadas.count("listar") == 2
    assert {r.titulo for r in usuario.recuerdos()} == {"Nombre", "Ciudad"}


def test_successful_cargar_returns_true_and_never_retries():
    falsa = MemoriaTardia([ANA])
    usuario = MemoriaUsuario(falsa)
    assert usuario.cargar() is True
    usuario.contexto("hola")
    usuario.aprender(LLMHechos(), "Vivo en Medellín")
    assert falsa.llamadas.count("listar") == 1


# ----- S3: a dead Engram makes memory unavailable -----


class EngramMortal(FakeEngram):
    """Fake connection that ends like a dead process when ``matar`` is called."""

    def __init__(self):
        super().__init__()
        self._muerte: anyio.Event | None = None
        self._loop = None

    def conectar(self):
        @asynccontextmanager
        async def sesion():
            self._loop = asyncio.get_running_loop()
            self._muerte = anyio.Event()
            async with anyio.create_task_group() as tg:
                async def vigilar():
                    await self._muerte.wait()
                    raise anyio.EndOfStream()

                tg.start_soon(vigilar)
                yield self
                tg.cancel_scope.cancel()

        return sesion()

    def matar(self):
        self._loop.call_soon_threadsafe(self._muerte.set)


def _esperar(condicion, limite=3.0):
    fin = time.monotonic() + limite
    while time.monotonic() < fin:
        if condicion():
            return True
        time.sleep(0.02)
    return condicion()


def test_connection_that_ends_marks_memory_unavailable_and_fails_fast(tmp_path):
    fake = EngramMortal()
    m = MemoriaEngram(carpeta=tmp_path, conectar=fake.conectar, timeout=5)
    try:
        assert m.esperar() is True
        fake.matar()
        assert _esperar(lambda: not m.disponible)
        assert m.error and "Engram se detuvo" in m.error
        inicio = time.monotonic()
        with pytest.raises(ErrorMemoria):
            m.buscar("x")
        assert time.monotonic() - inicio < 0.5
    finally:
        m.cerrar()


def test_call_in_flight_when_the_connection_ends_fails_fast(tmp_path):
    fake = EngramMortal()
    fake.retraso = 30
    m = MemoriaEngram(carpeta=tmp_path, conectar=fake.conectar, timeout=5)
    try:
        assert m.esperar() is True
        threading.Timer(0.2, fake.matar).start()
        inicio = time.monotonic()
        with pytest.raises(ErrorMemoria):
            m.buscar("x")
        assert time.monotonic() - inicio < 2
        assert m.disponible is False
    finally:
        m.cerrar()


@pytest.mark.parametrize("error", [
    anyio.ClosedResourceError(), anyio.BrokenResourceError(), BrokenPipeError("pipe"), ConnectionResetError("reset"),
])
def test_call_failing_because_the_server_is_gone_marks_memory_unavailable(tmp_path, error):
    fake = FakeEngram()
    m = MemoriaEngram(carpeta=tmp_path, conectar=fake.conectar, timeout=5)
    try:
        def roto(a):
            raise error

        fake._mem_search = roto
        with pytest.raises(ErrorMemoria):
            m.buscar("x")
        assert m.disponible is False
        assert m.error
        inicio = time.monotonic()
        with pytest.raises(ErrorMemoria):
            m.guardar("Nombre", "Ana")
        assert time.monotonic() - inicio < 0.5
    finally:
        m.cerrar()


def test_ordinary_tool_errors_keep_memory_available(tmp_path):
    fake = FakeEngram()
    m = MemoriaEngram(carpeta=tmp_path, conectar=fake.conectar, timeout=5)
    try:
        def roto(a):
            raise RuntimeError("fallo puntual")

        fake._mem_search = roto
        with pytest.raises(ErrorMemoria):
            m.buscar("x")
        assert m.disponible is True
        assert m.guardar("Nombre", "Ana") == 1
    finally:
        m.cerrar()


_BINARIO = ec.buscar_binario()


def _hijos_engram() -> set[int]:
    """PIDs of engram processes started by THIS test process."""
    if sys.platform == "win32":
        salida = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             f"Get-CimInstance Win32_Process -Filter \"ParentProcessId={os.getpid()}\" | "
             f"Where-Object {{ $_.Name -eq '{ec.NOMBRE_BINARIO}' }} | ForEach-Object {{ $_.ProcessId }}"],
            capture_output=True, text=True, timeout=30,
        ).stdout
    else:
        salida = subprocess.run(
            ["ps", "-o", "pid=,comm=", "--ppid", str(os.getpid())], capture_output=True, text=True, timeout=30
        ).stdout
        salida = "\n".join(l.split()[0] for l in salida.splitlines() if "engram" in l)
    return {int(x) for x in salida.split() if x.strip().isdigit()}


@pytest.mark.skipif(_BINARIO is None, reason="Engram binary not found (run scripts/descargar_engram.py)")
def test_real_engram_killed_mid_session_makes_memory_unavailable(tmp_path):
    carpeta = tmp_path / "engram"
    carpeta.mkdir()
    antes = _hijos_engram()
    m = MemoriaEngram(binario=_BINARIO, carpeta=carpeta, timeout=10)
    try:
        assert m.esperar() is True
        m.guardar("Dato", "valor")
        nuestros = _hijos_engram() - antes
        assert len(nuestros) == 1, nuestros  # only the process this client started
        (pid,) = nuestros
        os.kill(pid, signal.SIGTERM)  # by PID: never other engram processes

        assert _esperar(lambda: not m.disponible, limite=5)
        assert m.error and "Engram se detuvo" in m.error
        inicio = time.monotonic()
        with pytest.raises(ErrorMemoria):
            m.buscar("valor")
        assert time.monotonic() - inicio < 1
    finally:
        m.cerrar()


# ----- S5: one extraction at a time, never started while answering -----

from app.config import Config  # noqa: E402
from app.ui import chat  # noqa: E402
from app.ui.chat import PantallaChat  # noqa: E402


class LLMVigilado(ClienteLLM):
    """Slow chat and extraction calls that record how they overlap."""

    modelo = "m"

    def __init__(self, espera_chat=0.05, espera_extraccion=0.5):
        self.espera_chat = espera_chat
        self.espera_extraccion = espera_extraccion
        self.candado = threading.Lock()
        self.chats_activos = 0
        self.extracciones_activas = 0
        self.max_extracciones = 0
        self.extraccion_durante_chat = False
        self.entradas_extraccion: list[str] = []

    def responder(self, mensajes):
        if mensajes[0]["content"] == PROMPT_EXTRACCION:
            with self.candado:
                self.extracciones_activas += 1
                self.max_extracciones = max(self.max_extracciones, self.extracciones_activas)
                if self.chats_activos:
                    self.extraccion_durante_chat = True
                self.entradas_extraccion.append(mensajes[-1]["content"])
            try:
                time.sleep(self.espera_extraccion)
                yield "[]"
            finally:
                with self.candado:
                    self.extracciones_activas -= 1
            return
        with self.candado:
            self.chats_activos += 1
        try:
            for parte in ("Ho", "la"):
                time.sleep(self.espera_chat)
                yield parte
        finally:
            with self.candado:
                self.chats_activos -= 1


class PaginaHilos:
    """Fake page whose run_thread really runs in another thread, like Flet's."""

    def __init__(self):
        self.hilos: list[threading.Thread] = []

    def update(self, *controles):
        pass

    def run_thread(self, funcion, *args):
        hilo = threading.Thread(target=funcion, args=args, daemon=True)
        self.hilos.append(hilo)
        hilo.start()


class PaginaManual:
    """Fake page that keeps run_thread work pending until the test runs it."""

    def __init__(self):
        self.pendientes: list = []

    def update(self, *controles):
        pass

    def run_thread(self, funcion, *args):
        self.pendientes.append((funcion, args))


def _pantalla(monkeypatch, tmp_path, llm, page):
    monkeypatch.setenv("EVA_DATA_DIR", str(tmp_path / "datos"))
    monkeypatch.setattr(chat, "conectar", lambda url, modelo: llm)
    pantalla = PantallaChat(page, Historial(tmp_path / "eva.db"), Config(), MemoriaUsuario(MemoriaFalsa()))

    async def sin_foco():
        pass

    pantalla.campo.focus = sin_foco
    return pantalla


def _enviar(pantalla, texto):
    pantalla.campo.value = texto
    asyncio.run(pantalla._enviar(None))


TURNOS = ["Me llamo Ana", "Vivo en Medellín", "Me gusta el café"]


def test_quick_turns_run_one_extraction_at_a_time_covering_every_message(monkeypatch, tmp_path):
    llm = LLMVigilado()
    page = PaginaHilos()
    pantalla = _pantalla(monkeypatch, tmp_path, llm, page)
    pantalla.iniciar()
    for texto in TURNOS:
        assert _esperar(lambda: not pantalla.ocupado)
        _enviar(pantalla, texto)
    assert _esperar(lambda: not pantalla.ocupado)
    assert _esperar(lambda: all(not h.is_alive() for h in page.hilos), limite=5)

    assert llm.max_extracciones == 1
    assert llm.extraccion_durante_chat is False
    cubiertos = "\n".join(llm.entradas_extraccion)
    assert all(texto in cubiertos for texto in TURNOS)
    # Turns 2 and 3 arrived while the first extraction ran: coalesced into one.
    assert len(llm.entradas_extraccion) == 2
    assert llm.entradas_extraccion[0] == TURNOS[0]


def test_extraction_waits_while_an_answer_is_streaming(monkeypatch, tmp_path):
    llm = LLMVigilado(espera_extraccion=0)
    page = PaginaManual()
    pantalla = _pantalla(monkeypatch, tmp_path, llm, page)
    pantalla.iniciar()
    funcion, args = page.pendientes.pop(0)  # connect
    funcion(*args)
    funcion, args = page.pendientes.pop(0)  # load memory
    funcion(*args)

    _enviar(pantalla, TURNOS[0])
    funcion, args = page.pendientes.pop(0)  # answer 1
    funcion(*args)
    _enviar(pantalla, TURNOS[1])  # the screen is busy with answer 2 from now on
    (trabajador, args_trabajador), (respuesta2, args_respuesta2) = page.pendientes
    page.pendientes.clear()
    hilo = threading.Thread(target=trabajador, args=args_trabajador, daemon=True)
    hilo.start()
    time.sleep(0.3)
    assert llm.entradas_extraccion == []  # waiting for the screen to be free

    respuesta2(*args_respuesta2)
    hilo.join(timeout=3)
    assert not hilo.is_alive()
    # Turn 2 joined the running extractor's queue, or started the next single one.
    assert len(page.pendientes) <= 1
    for funcion, args in page.pendientes:
        funcion(*args)
    assert llm.extraccion_durante_chat is False
    assert llm.max_extracciones == 1
    cubiertos = "\n".join(llm.entradas_extraccion)
    assert TURNOS[0] in cubiertos and TURNOS[1] in cubiertos
