"""Tests for the Engram long-term memory client (MCP over stdio).

Unit tests use a fake MCP session that mimics the real Engram v3.7.2 replies,
so they never need the binary. Integration tests at the end run the real
binary when one is found (``bin/`` or PATH) and are skipped otherwise; they
always point ``ENGRAM_DATA_DIR`` at a fresh temporary folder, never at the
developer's own ``~/.engram``.
"""

import asyncio
import json
import logging
import shutil
import subprocess
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from mcp import types

from app.memory import engram_client as ec
from app.memory.engram_client import ErrorMemoria, MemoriaEngram, Recuerdo


def _texto(datos, error=False) -> types.CallToolResult:
    texto = datos if isinstance(datos, str) else json.dumps(datos, ensure_ascii=False)
    return types.CallToolResult(content=[types.TextContent(type="text", text=texto)], isError=error)


class FakeEngram:
    """In-memory stand-in for `engram mcp` with the real v3.7.2 reply shapes."""

    def __init__(self, preview=300):
        self.memorias: dict[int, dict] = {}
        self.por_tema: dict[str, int] = {}
        self.llamadas: list[tuple[str, dict]] = []
        self.siguiente = 1
        self.preview = preview
        self.retraso = 0.0
        self.entradas = 0
        self.salidas = 0

    def conectar(self):
        @asynccontextmanager
        async def sesion():
            self.entradas += 1
            try:
                yield self
            finally:
                self.salidas += 1

        return sesion()

    async def call_tool(self, name, arguments=None):
        args = dict(arguments or {})
        self.llamadas.append((name, args))
        if self.retraso:
            await asyncio.sleep(self.retraso)
        return getattr(self, "_" + name)(args)

    def _mem_save(self, a):
        id_ = self.por_tema.get(a.get("topic_key"))
        if id_ is None:
            id_ = self.siguiente
            self.siguiente += 1
        self.memorias[id_] = {"title": a["title"], "content": a.get("content", ""), "project": "eva"}
        if a.get("topic_key"):
            self.por_tema[a["topic_key"]] = id_
        return _texto({"id": id_, "project": "eva", "result": f'Memory saved: "{a["title"]}" (preference)', "state": "active"})

    def _mem_search(self, a):
        palabras = a["query"].lower().split()
        encontrados = [
            (i, m) for i, m in sorted(self.memorias.items(), reverse=True)
            if any(p in (m["title"] + " " + m["content"]).lower() for p in palabras)
        ][: int(a.get("limit", 10))]
        if not encontrados:
            return _texto({"project": "eva", "result": f'No memories found for: "{a["query"]}"'})
        return _texto({
            "project": "eva",
            "result": f"Found {len(encontrados)} memories.",
            "results": [
                {
                    "id": i, "pinned": False, "preview": m["content"][: self.preview], "project": m["project"],
                    "scope": "project", "state": "active", "title": m["title"], "topic_key": "t",
                    "truncated": len(m["content"]) > self.preview, "type": "preference",
                }
                for i, m in encontrados
            ],
        })

    def _mem_get_observation(self, a):
        m = self.memorias.get(int(a["id"]))
        if m is None:
            return _texto(f"Observation #{a['id']} not found", error=True)
        return _texto({
            "project": "eva",
            "result": f"#{a['id']} [preference] {m['title']}\n{m['content']}\nSession: manual-save-eva\n"
                      "Project: eva\nScope: project\nTopic: t\nDuplicates: 1\nRevisions: 1\nCreated: 2026-11-17 08:10:09",
        })

    def _mem_delete(self, a):
        if a.get("expected_project") != "eva":
            return _texto("expected_project must be a valid non-empty project name", error=True)
        if self.memorias.pop(int(a["id"]), None) is None:
            return _texto("Failed to delete memory: observation not found", error=True)
        return _texto(f"Memory #{a['id']} permanently deleted")


@pytest.fixture
def fake():
    return FakeEngram()


@pytest.fixture
def memoria(fake, tmp_path):
    m = MemoriaEngram(carpeta=tmp_path / "engram", conectar=fake.conectar, timeout=2)
    yield m
    m.cerrar()


# --- binary lookup, isolated data folder, server parameters (S2) ---

def test_binary_in_app_bin_folder_wins_over_path(tmp_path, monkeypatch):
    (tmp_path / "bin").mkdir()
    binario = tmp_path / "bin" / ec.NOMBRE_BINARIO
    binario.write_bytes(b"x")
    monkeypatch.setattr(ec.shutil, "which", lambda nombre: "C:/otro/engram.exe")
    assert ec.buscar_binario(tmp_path) == binario


def test_binary_falls_back_to_path(tmp_path, monkeypatch):
    otro = tmp_path / "otro"
    otro.mkdir()
    (otro / ec.NOMBRE_BINARIO).write_bytes(b"engram")
    monkeypatch.setenv("PATH", str(otro))
    assert ec.buscar_binario(tmp_path) == otro / ec.NOMBRE_BINARIO


def test_no_binary_found(tmp_path, monkeypatch):
    vacia = tmp_path / "vacia"
    vacia.mkdir()
    monkeypatch.setenv("PATH", str(vacia))
    assert ec.buscar_binario(tmp_path) is None


def test_default_app_folder_is_the_repo_root():
    assert ec.CARPETA_APP == Path(__file__).resolve().parents[1]
    assert ec.NOMBRE_BINARIO in ("engram", "engram.exe")


def test_server_runs_mcp_with_agent_and_admin_tools_in_eva_data_folder(tmp_path):
    parametros = ec.parametros_servidor(Path("C:/app/bin/engram.exe"), tmp_path / "engram")
    assert parametros.command == str(Path("C:/app/bin/engram.exe"))
    assert parametros.args == ["mcp", "--tools=agent,admin"]
    assert parametros.env == {"ENGRAM_DATA_DIR": str(tmp_path / "engram"), "ENGRAM_PROJECT": "eva"}


def test_default_data_folder_is_inside_eva_data_never_home_engram(tmp_path, monkeypatch):
    monkeypatch.setenv("EVA_DATA_DIR", str(tmp_path / "eva"))
    carpeta = ec.carpeta_memoria()
    assert carpeta == tmp_path / "eva" / "engram"
    assert carpeta.is_dir()
    assert carpeta != Path.home() / ".engram"


def test_topic_key_is_stable_slug_of_title():
    assert ec.clave_tema("Nombre del usuario") == "usuario/nombre-del-usuario"
    assert ec.clave_tema("  Café  FAVORITO! ") == "usuario/cafe-favorito"
    assert ec.clave_tema("¿¡!?") == "usuario/recuerdo"


# --- facade operations (S2) ---

def test_guardar_sends_preference_with_topic_key_and_returns_id(memoria, fake):
    assert memoria.guardar("Nombre del usuario", "Se llama Ana") == 1
    assert fake.llamadas == [(
        "mem_save",
        {
            "title": "Nombre del usuario",
            "content": "Se llama Ana",
            "type": "preference",
            "topic_key": "usuario/nombre-del-usuario",
            "capture_prompt": False,
        },
    )]


def test_guardar_same_title_updates_instead_of_duplicating(memoria, fake):
    primero = memoria.guardar("Nombre del usuario", "Se llama Ana")
    segundo = memoria.guardar("Nombre del usuario", "Se llama Ana María")
    assert primero == segundo == 1
    assert fake.memorias == {1: {"title": "Nombre del usuario", "content": "Se llama Ana María", "project": "eva"}}


def test_guardar_blank_saves_nothing(memoria, fake):
    assert memoria.guardar("  ", "algo") is None
    assert memoria.guardar("Titulo", "   ") is None
    assert fake.llamadas == []


def test_guardar_reply_without_id_returns_none(memoria, fake):
    fake._mem_save = lambda a: _texto({"result": "Memory saved"})
    assert memoria.guardar("Gusto", "Le gusta el café") is None


def test_buscar_returns_recuerdos_and_uses_any_match(memoria, fake):
    memoria.guardar("Nombre del usuario", "Se llama Ana")
    memoria.guardar("Bebida favorita", "Le gusta el café")
    fake.llamadas.clear()

    assert memoria.buscar("café ana", limite=5) == [
        Recuerdo(id=2, titulo="Bebida favorita", contenido="Le gusta el café"),
        Recuerdo(id=1, titulo="Nombre del usuario", contenido="Se llama Ana"),
    ]
    assert fake.llamadas == [(
        "mem_search",
        {"query": "café ana", "limit": 5, "match_mode": "any", "response_format": "compact"},
    )]


def test_buscar_without_results_is_empty(memoria):
    assert memoria.buscar("nada") == []


def test_buscar_blank_query_does_not_call_engram(memoria, fake):
    assert memoria.buscar("   ") == []
    assert memoria.buscar("algo", limite=0) == []
    assert fake.llamadas == []


def test_buscar_limit_is_capped_at_engram_maximum(memoria, fake):
    memoria.buscar("algo", limite=50)
    assert fake.llamadas[0][1]["limit"] == 20


def test_buscar_fetches_full_content_of_truncated_previews(fake, tmp_path):
    fake.preview = 10
    m = MemoriaEngram(carpeta=tmp_path, conectar=fake.conectar, timeout=2)
    try:
        largo = "Le gusta el café\ncon leche y Session: sin azúcar " + "x" * 50
        m.guardar("Bebida", largo)
        assert m.buscar("café") == [Recuerdo(id=1, titulo="Bebida", contenido=largo)]
        assert fake.llamadas[-1] == ("mem_get_observation", {"id": 1})
    finally:
        m.cerrar()


def test_buscar_fetches_content_when_reply_has_no_preview(memoria, fake):
    # Engram 1.19 (an older engram on PATH) ignores response_format=compact.
    memoria.guardar("Nombre", "Se llama Ana")
    original = fake._mem_search

    def sin_preview(a):
        datos = json.loads(original(a).content[0].text)
        for r in datos["results"]:
            del r["preview"], r["truncated"]
        return _texto(datos)

    fake._mem_search = sin_preview
    assert memoria.buscar("ana") == [Recuerdo(1, "Nombre", "Se llama Ana")]


def test_buscar_ignores_results_from_other_projects(memoria, fake):
    memoria.guardar("Nombre", "Ana")
    fake.memorias[1]["project"] = "otro"
    assert memoria.buscar("ana") == []


def test_borrar_is_permanent_and_scoped_to_eva(memoria, fake):
    memoria.guardar("Nombre", "Ana")
    fake.llamadas.clear()
    assert memoria.borrar(1) is None
    assert fake.llamadas == [("mem_delete", {"id": 1, "hard_delete": True, "expected_project": "eva"})]
    assert fake.memorias == {}


def test_borrar_unknown_id_raises(memoria):
    with pytest.raises(ErrorMemoria, match="Engram devolvió un error: Failed to delete memory: observation not found"):
        memoria.borrar(99)


def test_listar_uses_export_and_keeps_only_live_eva_memories(fake, tmp_path):
    exportado = {
        "observations": [
            {"id": 1, "title": "Nombre", "content": "Ana", "project": "eva"},
            {"id": 3, "title": "Ciudad", "content": "Bogotá", "project": "eva", "deleted_at": "2026-11-17"},
            {"id": 4, "title": "Otro", "content": "x", "project": "otro"},
            {"id": 5, "title": "Bebida", "content": "Café", "project": "eva", "deleted_at": None},
        ]
    }
    m = MemoriaEngram(carpeta=tmp_path, conectar=fake.conectar, exportar=lambda: exportado, timeout=2)
    try:
        assert m.listar() == [Recuerdo(5, "Bebida", "Café"), Recuerdo(1, "Nombre", "Ana")]
    finally:
        m.cerrar()


def test_listar_with_empty_export(fake, tmp_path):
    m = MemoriaEngram(carpeta=tmp_path, conectar=fake.conectar, exportar=lambda: {"observations": None}, timeout=2)
    try:
        assert m.listar() == []
    finally:
        m.cerrar()


def test_new_store_without_eva_memories_lists_and_finds_nothing(fake, tmp_path, caplog):
    # A fresh Engram store has no "eva" project until the first save (seen live with 3.7.2).
    def exportar():
        raise subprocess.CalledProcessError(1, ["engram", "export"], stderr=b"engram: unknown project: eva\n")

    fake._mem_search = lambda a: _texto(
        '{"available_projects":null,"error_code":"unknown_project",'
        '"message":"Project \\"eva\\" not found in store"}',
        error=True,
    )
    m = MemoriaEngram(carpeta=tmp_path, conectar=fake.conectar, exportar=exportar, timeout=2)
    try:
        with caplog.at_level(logging.WARNING):
            assert m.listar() == []
            assert m.buscar("hola") == []
        assert caplog.records == []
    finally:
        m.cerrar()


def test_listar_export_failure_raises(fake, tmp_path):
    def falla():
        raise OSError("disco")

    m = MemoriaEngram(carpeta=tmp_path, conectar=fake.conectar, exportar=falla, timeout=2)
    try:
        with pytest.raises(ErrorMemoria, match="No se pudieron listar los recuerdos: disco"):
            m.listar()
    finally:
        m.cerrar()


def test_calls_from_many_threads_share_one_session(memoria, fake):
    errores = []

    def trabajo(n):
        try:
            memoria.guardar(f"Dato {n}", f"valor {n}")
        except Exception as exc:  # pragma: no cover - reported below
            errores.append(exc)

    hilos = [threading.Thread(target=trabajo, args=(n,)) for n in range(10)]
    for h in hilos:
        h.start()
    for h in hilos:
        h.join()
    assert errores == []
    assert len(fake.memorias) == 10
    assert fake.entradas == 1


# --- robustness (S6) ---

def test_missing_binary_makes_memory_unavailable(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(ec, "buscar_binario", lambda carpeta=None: None)
    with caplog.at_level(logging.WARNING, logger="app.memory.engram_client"):
        m = MemoriaEngram(carpeta=tmp_path)
    assert m.disponible is False
    assert m.error == "No se encontró el programa de Engram (bin/engram)"
    for llamada in (lambda: m.buscar("x"), lambda: m.guardar("a", "b"), m.listar, lambda: m.borrar(1)):
        with pytest.raises(ErrorMemoria, match=r"No se encontró el programa de Engram \(bin/engram\)"):
            llamada()
    assert "No se encontró el programa de Engram" in caplog.text
    m.cerrar()


def test_startup_failure_makes_memory_unavailable(tmp_path, caplog):
    @asynccontextmanager
    async def roto():
        raise OSError("acceso denegado")
        yield

    with caplog.at_level(logging.WARNING, logger="app.memory.engram_client"):
        m = MemoriaEngram(carpeta=tmp_path, conectar=roto, timeout=2)
        try:
            with pytest.raises(ErrorMemoria, match="No se pudo iniciar Engram: acceso denegado"):
                m.buscar("x")
            assert m.disponible is False
            assert m.error == "No se pudo iniciar Engram: acceso denegado"
        finally:
            m.cerrar()
    assert "No se pudo iniciar Engram: acceso denegado" in caplog.text


def test_call_timeout_raises_quickly_and_memory_keeps_working(fake, tmp_path, caplog):
    fake.retraso = 30
    m = MemoriaEngram(carpeta=tmp_path, conectar=fake.conectar, timeout=0.3)
    try:
        inicio = time.monotonic()
        with caplog.at_level(logging.WARNING, logger="app.memory.engram_client"):
            with pytest.raises(ErrorMemoria, match="Engram no respondió a tiempo"):
                m.buscar("algo")
        assert time.monotonic() - inicio < 2
        assert "Engram no respondió a tiempo" in caplog.text
        fake.retraso = 0
        assert m.guardar("Nombre", "Ana") == 1
    finally:
        m.cerrar()


def test_startup_that_hangs_times_out_and_closes_promptly(tmp_path):
    @asynccontextmanager
    async def colgado():
        await asyncio.sleep(60)
        yield

    m = MemoriaEngram(carpeta=tmp_path, conectar=colgado, timeout=0.3)
    with pytest.raises(ErrorMemoria, match="Engram no respondió a tiempo"):
        m.buscar("x")
    inicio = time.monotonic()
    m.cerrar()
    assert time.monotonic() - inicio < 2
    assert m.disponible is False


def test_error_reply_raises_memory_error(memoria, fake):
    fake._mem_search = lambda a: _texto('Search error: fts5: syntax error near "" (1).', error=True)
    with pytest.raises(ErrorMemoria, match="Engram devolvió un error: Search error"):
        memoria.buscar("x")


def test_unexpected_reply_raises_memory_error(memoria, fake):
    fake._mem_save = lambda a: _texto("esto no es json")
    with pytest.raises(ErrorMemoria, match="Respuesta inesperada de Engram"):
        memoria.guardar("a", "b")


def test_session_exception_raises_memory_error(memoria, fake):
    def explota(a):
        raise RuntimeError("tubería rota")

    fake._mem_search = explota
    with pytest.raises(ErrorMemoria, match="Error al hablar con Engram: tubería rota"):
        memoria.buscar("x")


def test_connected_memory_is_available(memoria, fake):
    memoria.buscar("x")
    assert memoria.disponible is True
    assert memoria.error is None


def test_esperar_waits_for_the_connection(memoria, fake):
    assert memoria.esperar() is True
    assert fake.entradas == 1


def test_esperar_reports_unavailable_memory_without_waiting(tmp_path, monkeypatch):
    monkeypatch.setattr(ec, "buscar_binario", lambda carpeta=None: None)
    m = MemoriaEngram(carpeta=tmp_path, timeout=5)
    inicio = time.monotonic()
    assert m.esperar() is False
    assert time.monotonic() - inicio < 1


def test_esperar_gives_up_after_the_timeout(tmp_path):
    @asynccontextmanager
    async def colgado():
        await asyncio.sleep(60)
        yield

    m = MemoriaEngram(carpeta=tmp_path, conectar=colgado, timeout=0.3)
    try:
        assert m.esperar() is False
    finally:
        m.cerrar()


def test_cerrar_ends_session_is_idempotent_and_blocks_later_calls(fake, tmp_path):
    m = MemoriaEngram(carpeta=tmp_path, conectar=fake.conectar, timeout=2)
    m.buscar("x")
    m.cerrar()
    m.cerrar()
    assert (fake.entradas, fake.salidas) == (1, 1)
    assert m.disponible is False
    with pytest.raises(ErrorMemoria, match="La memoria a largo plazo está cerrada"):
        m.buscar("x")


def test_cerrar_before_connecting(fake, tmp_path):
    m = MemoriaEngram(carpeta=tmp_path, conectar=fake.conectar, timeout=2)
    m.cerrar()
    assert fake.salidas == fake.entradas


# --- integration with the real binary (skipped when none is found) ---

_BINARIO = ec.buscar_binario()
real = pytest.mark.skipif(_BINARIO is None, reason="Engram binary not found (run scripts/descargar_engram.py)")


@pytest.fixture
def real_memoria(tmp_path):
    carpeta = tmp_path / "engram"
    carpeta.mkdir()
    assert carpeta.resolve() != (Path.home() / ".engram").resolve()
    m = MemoriaEngram(binario=_BINARIO, carpeta=carpeta, timeout=20)
    yield m, carpeta
    m.cerrar()


@real
def test_real_engram_save_search_list_delete(real_memoria):
    m, carpeta = real_memoria
    id_ = m.guardar("Nombre del usuario", "El usuario se llama Ana")
    assert isinstance(id_, int)
    assert m.guardar("Nombre del usuario", "El usuario se llama Ana María") == id_
    largo = "Le gusta el café" + " muy cargado" * 40  # longer than the 300-char search preview
    id_cafe = m.guardar("Bebida favorita", largo)

    assert m.buscar("llama") == [Recuerdo(id_, "Nombre del usuario", "El usuario se llama Ana María")]
    assert m.buscar("café") == [Recuerdo(id_cafe, "Bebida favorita", largo)]
    assert m.buscar("inexistente") == []
    assert m.listar() == [
        Recuerdo(id_cafe, "Bebida favorita", largo),
        Recuerdo(id_, "Nombre del usuario", "El usuario se llama Ana María"),
    ]
    assert m.disponible is True

    m.borrar(id_)
    assert m.buscar("llama") == []
    assert [r.id for r in m.listar()] == [id_cafe]
    with pytest.raises(ErrorMemoria):
        m.borrar(id_)
    assert (carpeta / "engram.db").exists()


@real
def test_real_new_store_is_empty_not_an_error(real_memoria):
    m, _ = real_memoria
    assert m.listar() == []
    assert m.buscar("hola") == []


@real
def test_real_engram_process_is_released_on_close(real_memoria):
    m, carpeta = real_memoria
    m.guardar("Dato", "valor")
    m.cerrar()
    shutil.rmtree(carpeta)  # Windows refuses while the engram process holds the database
    assert not carpeta.exists()


def test_real_stdio_unstartable_binary_is_reported(tmp_path):
    falso = tmp_path / "no-existe" / ec.NOMBRE_BINARIO
    m = MemoriaEngram(binario=falso, carpeta=tmp_path / "engram", timeout=5)
    try:
        with pytest.raises(ErrorMemoria, match="No se pudo iniciar Engram"):
            m.buscar("x")
        assert m.disponible is False
    finally:
        m.cerrar()
