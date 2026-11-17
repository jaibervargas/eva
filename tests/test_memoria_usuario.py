"""Tests for long-term memory in the chat: context (S3), fact extraction (S4),
failures (S6) and remembering across conversations (S7).

``MemoriaFalsa`` stands in for Engram; the S7 end-to-end test also runs the
real ``MemoriaEngram`` client over the fake MCP server of the client tests, and
once more against the real binary when one is found (always in a temporary
data folder, never the developer's ``~/.engram``).
"""

import json
import logging
from pathlib import Path

import pytest

from app.llm.base import ClienteLLM, ErrorLLM
from app.llm.conversacion import (
    ENCABEZADO_RECUERDOS,
    LIMITE_RECUERDOS,
    Conversacion,
    MemoriaUsuario,
    extraer_hechos,
)
from app.llm.prompts import PROMPT_EXTRACCION, PROMPT_SISTEMA
from app.memory import engram_client as ec
from app.memory.engram_client import MemoriaEngram, Recuerdo
from app.memory.history import Historial
from tests.memoria_falsa import MemoriaFalsa
from tests.test_engram_client import FakeEngram

ANA = "Me llamo Ana y me gusta el café"
HECHOS_ANA = json.dumps(
    [
        {"titulo": "Nombre", "contenido": "El usuario se llama Ana"},
        {"titulo": "Bebida favorita", "contenido": "Le gusta el café"},
    ],
    ensure_ascii=False,
)


class LLMGuion(ClienteLLM):
    """Answers chat turns with ``respuesta`` and extraction requests with ``extraccion``."""

    modelo = "m"

    def __init__(self, respuesta="ok", extraccion="[]", error_extraccion=None):
        self.respuesta = respuesta
        self.extraccion = extraccion
        self.error_extraccion = error_extraccion
        self.recibidos: list[list[dict]] = []

    def responder(self, mensajes):
        self.recibidos.append([dict(m) for m in mensajes])
        if mensajes[0]["content"] == PROMPT_EXTRACCION:
            if self.error_extraccion:
                raise self.error_extraccion
            yield self.extraccion
        else:
            yield self.respuesta

    def chats(self):
        return [m for m in self.recibidos if m[0]["content"] != PROMPT_EXTRACCION]


@pytest.fixture
def historial(tmp_path):
    return Historial(tmp_path / "eva.db")


def _usuario(*recuerdos, **kw):
    falsa = MemoriaFalsa(recuerdos, **kw)
    memoria = MemoriaUsuario(falsa)
    memoria.cargar()
    return memoria, falsa


def _bloque(*lineas):
    return {"role": "system", "content": "\n".join([ENCABEZADO_RECUERDOS, *lineas])}


# ----- S3: memories as context -----


def test_memories_go_in_one_system_message_right_after_the_prompt(historial):
    memoria, _ = _usuario(Recuerdo(1, "Nombre", "El usuario se llama Ana"), Recuerdo(2, "Bebida favorita", "Le gusta el café"))
    llm = LLMGuion()

    list(Conversacion(llm, historial, memoria=memoria).enviar("¿Qué tomo con el café?"))

    assert ENCABEZADO_RECUERDOS == "Recuerdos sobre el usuario (de conversaciones anteriores):"
    assert llm.recibidos[0] == [
        {"role": "system", "content": PROMPT_SISTEMA},
        _bloque("- Bebida favorita: Le gusta el café", "- Nombre: El usuario se llama Ana"),
        {"role": "user", "content": "¿Qué tomo con el café?"},
    ]


def test_search_hits_come_first_then_cached_facts_without_duplicates_max_8(historial):
    recuerdos = [Recuerdo(i, f"Dato {i}", f"valor {i}") for i in range(1, 11)]
    recuerdos[2] = Recuerdo(3, "Ciudad", "Vive en Medellín")
    memoria, _ = _usuario(*recuerdos)
    llm = LLMGuion()

    list(Conversacion(llm, historial, memoria=memoria).enviar("¿Hace frío en Medellín?"))

    assert LIMITE_RECUERDOS == 8
    lineas = llm.recibidos[0][1]["content"].split("\n")[1:]
    assert lineas == ["- Ciudad: Vive en Medellín"] + [f"- Dato {i}: valor {i}" for i in (10, 9, 8, 7, 6, 5, 4)]


def test_memories_are_context_only_never_saved_in_the_history(historial):
    memoria, _ = _usuario(Recuerdo(1, "Nombre", "El usuario se llama Ana"))
    conv = Conversacion(LLMGuion(respuesta="Hola Ana"), historial, memoria=memoria)

    list(conv.enviar("hola"))

    assert historial.mensajes(conv.conversacion_id) == [
        {"role": "user", "content": "hola"},
        {"role": "assistant", "content": "Hola Ana"},
    ]


def test_previous_turns_follow_the_memories(historial):
    memoria, _ = _usuario(Recuerdo(1, "Nombre", "El usuario se llama Ana"))
    llm = LLMGuion(respuesta="Hola")
    conv = Conversacion(llm, historial, memoria=memoria)
    list(conv.enviar("hola"))
    list(conv.enviar("otra"))

    assert [m["role"] for m in llm.recibidos[1]] == ["system", "system", "user", "assistant", "user"]


def test_without_memories_nothing_is_added(historial):
    memoria, _ = _usuario()
    llm = LLMGuion()

    list(Conversacion(llm, historial, memoria=memoria).enviar("hola"))

    assert llm.recibidos[0] == [{"role": "system", "content": PROMPT_SISTEMA}, {"role": "user", "content": "hola"}]


def test_the_full_list_is_loaded_once_and_reused(historial):
    memoria, falsa = _usuario(Recuerdo(1, "Nombre", "El usuario se llama Ana"))
    for texto in ("uno", "dos", "tres"):
        list(Conversacion(LLMGuion(), historial, memoria=memoria).enviar(texto))

    assert falsa.llamadas.count("listar") == 1
    assert falsa.llamadas.count("buscar") == 3


def test_memory_errors_do_not_stop_the_answer_and_are_logged(historial, caplog):
    memoria, falsa = _usuario(Recuerdo(1, "Nombre", "Ana"))
    falsa.fallar = "Engram no respondió a tiempo"
    llm = LLMGuion(respuesta="Hola")

    with caplog.at_level(logging.WARNING):
        partes = list(Conversacion(llm, historial, memoria=memoria).enviar("hola"))

    assert partes == ["Hola"]
    # The cached facts are still used when only the search fails.
    assert llm.recibidos[0][1] == _bloque("- Nombre: Ana")
    assert "Engram no respondió a tiempo" in caplog.text


def test_unexpected_memory_failure_is_logged_and_the_chat_continues(historial, caplog):
    class Rota:
        def contexto(self, texto):
            raise RuntimeError("se rompió")

    llm = LLMGuion(respuesta="Hola")
    with caplog.at_level(logging.ERROR):
        partes = list(Conversacion(llm, historial, memoria=Rota()).enviar("hola"))

    assert partes == ["Hola"]
    assert len(llm.recibidos[0]) == 2
    assert "se rompió" in caplog.text


def test_unavailable_memory_adds_nothing(historial):
    memoria, _ = _usuario(Recuerdo(1, "Nombre", "Ana"), disponible=False)
    llm = LLMGuion()

    list(Conversacion(llm, historial, memoria=memoria).enviar("hola Ana"))

    assert memoria.disponible is False
    assert len(llm.recibidos[0]) == 2


# ----- S4: learning facts after a turn -----


@pytest.mark.parametrize(
    "salida",
    [
        HECHOS_ANA,
        "<think>El usuario dice su nombre [y algo].</think>\n" + HECHOS_ANA,
        "```json\n" + HECHOS_ANA + "\n```",
        "Aquí tienes los datos:\n" + HECHOS_ANA + "\nEso es todo.",
        "<think>\nhmm\n</think>\n```\n" + HECHOS_ANA + "\n```",
    ],
)
def test_extraer_hechos_reads_json_wrapped_in_thinking_fences_or_text(salida):
    assert extraer_hechos(salida) == [
        ("Nombre", "El usuario se llama Ana"),
        ("Bebida favorita", "Le gusta el café"),
    ]


@pytest.mark.parametrize(
    "salida",
    [
        "",
        "[]",
        "No hay datos para guardar.",
        '[{"titulo": "Nombre", "contenido": ',
        '{"titulo": "Nombre", "contenido": "Ana"}',
        "<think>" + HECHOS_ANA,  # still thinking: nothing final
        '["Nombre", "Ana"]',
    ],
)
def test_invalid_or_empty_extraction_gives_no_facts(salida):
    assert extraer_hechos(salida) == []


def test_extraer_hechos_skips_incomplete_items_and_trims():
    salida = json.dumps([
        {"titulo": "  Nombre ", "contenido": " El usuario se llama Ana "},
        {"titulo": "", "contenido": "x"},
        {"titulo": "Edad"},
        {"titulo": "Edad", "contenido": 30},
        "suelto",
    ])
    assert extraer_hechos(salida) == [("Nombre", "El usuario se llama Ana")]


def test_aprender_asks_the_same_model_and_saves_each_fact(historial):
    memoria, falsa = _usuario()
    llm = LLMGuion(extraccion=HECHOS_ANA)

    guardados = memoria.aprender(llm, ANA)

    assert llm.recibidos == [[{"role": "system", "content": PROMPT_EXTRACCION}, {"role": "user", "content": ANA}]]
    assert guardados == [Recuerdo(1, "Nombre", "El usuario se llama Ana"), Recuerdo(2, "Bebida favorita", "Le gusta el café")]
    assert falsa.listar_sin_registro() == list(reversed(guardados))


def test_extraction_prompt_asks_for_json_facts_about_the_user():
    for palabra in ("JSON", '"titulo"', '"contenido"', "[]", "nombre", "gustos", "decisiones"):
        assert palabra in PROMPT_EXTRACCION


def test_learned_facts_update_the_cache_without_listing_again(historial):
    memoria, falsa = _usuario(Recuerdo(1, "Nombre", "El usuario se llama Ana"))
    memoria.aprender(LLMGuion(extraccion='[{"titulo": "Nombre", "contenido": "El usuario se llama Ana María"}]'), "Soy Ana María")
    llm = LLMGuion()

    list(Conversacion(llm, historial, memoria=memoria).enviar("hola"))

    assert llm.recibidos[0][1] == _bloque("- Nombre: El usuario se llama Ana María")
    assert falsa.llamadas.count("listar") == 1
    assert memoria.recuerdos() == [Recuerdo(1, "Nombre", "El usuario se llama Ana María")]


def test_invalid_extraction_saves_nothing(historial):
    memoria, falsa = _usuario()

    assert memoria.aprender(LLMGuion(extraccion="Claro, Ana es su nombre."), ANA) == []
    assert "guardar" not in falsa.llamadas
    assert memoria.recuerdos() == []


def test_extraction_model_error_saves_nothing_and_is_logged(caplog):
    memoria, falsa = _usuario()

    with caplog.at_level(logging.WARNING):
        guardados = memoria.aprender(LLMGuion(error_extraccion=ErrorLLM("sin conexión")), ANA)

    assert guardados == []
    assert "guardar" not in falsa.llamadas
    assert "sin conexión" in caplog.text


def test_save_failure_is_logged_and_never_raises(caplog):
    memoria, falsa = _usuario()
    falsa.fallar = "disco lleno"

    with caplog.at_level(logging.WARNING):
        assert memoria.aprender(LLMGuion(extraccion=HECHOS_ANA), ANA) == []
    assert "disco lleno" in caplog.text


def test_unavailable_memory_does_not_even_ask_the_model():
    memoria, _ = _usuario(disponible=False)
    llm = LLMGuion(extraccion=HECHOS_ANA)

    assert memoria.aprender(llm, ANA) == []
    assert llm.recibidos == []


def test_cargar_reports_whether_memory_works(caplog):
    assert MemoriaUsuario(MemoriaFalsa()).cargar() is True
    with caplog.at_level(logging.WARNING):
        assert MemoriaUsuario(MemoriaFalsa(disponible=False)).cargar() is False
        rota = MemoriaFalsa(fallar="export roto")
        assert MemoriaUsuario(rota).cargar() is False  # the listing failed (retried later)
    assert "export roto" in caplog.text


def test_listar_and_borrar_refresh_the_cache():
    memoria, falsa = _usuario(Recuerdo(1, "Nombre", "Ana"))
    falsa.guardar("Ciudad", "Medellín")

    assert memoria.listar() == [Recuerdo(2, "Ciudad", "Medellín"), Recuerdo(1, "Nombre", "Ana")]
    memoria.borrar(1)

    assert memoria.recuerdos() == [Recuerdo(2, "Ciudad", "Medellín")]
    assert falsa.listar_sin_registro() == [Recuerdo(2, "Ciudad", "Medellín")]


def test_cerrar_closes_engram():
    memoria, falsa = _usuario()
    memoria.cerrar()
    assert falsa.cerrada is True


# ----- S7: remembering across different conversations -----


def _eva_con_engram_falso(fake: FakeEngram, carpeta: Path) -> MemoriaEngram:
    def exportar():
        return {"observations": [
            {"id": i, "title": m["title"], "content": m["content"], "project": m["project"]}
            for i, m in fake.memorias.items()
        ]}

    return MemoriaEngram(carpeta=carpeta, conectar=fake.conectar, exportar=exportar, timeout=5)


def _recordar_entre_conversaciones(engram: MemoriaEngram, historial: Historial) -> LLMGuion:
    memoria = MemoriaUsuario(engram)
    assert memoria.cargar() is True
    llm = LLMGuion(respuesta="¡Hola, Ana!", extraccion=HECHOS_ANA)

    primera = Conversacion(llm, historial, memoria=memoria)
    list(primera.enviar(ANA))
    memoria.aprender(llm, ANA)  # the chat screen runs this in the background

    segunda = Conversacion(llm, historial, memoria=memoria)  # a NEW conversation
    list(segunda.enviar("¿Cómo me llamo?"))

    assert segunda.conversacion_id != primera.conversacion_id
    ultimo = llm.chats()[-1]
    assert ultimo[0] == {"role": "system", "content": PROMPT_SISTEMA}
    assert ultimo[1]["content"].startswith(ENCABEZADO_RECUERDOS + "\n")
    assert "- Nombre: El usuario se llama Ana" in ultimo[1]["content"].split("\n")
    assert "- Bebida favorita: Le gusta el café" in ultimo[1]["content"].split("\n")
    # It is memory, not chat history: the first conversation is not in the context.
    assert {"role": "user", "content": ANA} not in ultimo
    assert ultimo[-1] == {"role": "user", "content": "¿Cómo me llamo?"}
    return llm


def test_eva_remembers_the_user_in_a_new_conversation(tmp_path, historial):
    fake = FakeEngram()
    engram = _eva_con_engram_falso(fake, tmp_path / "engram")
    try:
        _recordar_entre_conversaciones(engram, historial)
    finally:
        engram.cerrar()
    assert sorted(m["title"] for m in fake.memorias.values()) == ["Bebida favorita", "Nombre"]


_BINARIO = ec.buscar_binario()


@pytest.mark.skipif(_BINARIO is None, reason="Engram binary not found (run scripts/descargar_engram.py)")
def test_real_engram_remembers_the_user_after_restarting(tmp_path, monkeypatch, historial):
    monkeypatch.setenv("EVA_DATA_DIR", str(tmp_path / "datos"))
    carpeta = ec.carpeta_memoria()
    assert carpeta == tmp_path / "datos" / "engram"
    assert carpeta.resolve() != (Path.home() / ".engram").resolve()

    engram = MemoriaEngram(binario=_BINARIO, timeout=20)
    try:
        _recordar_entre_conversaciones(engram, historial)
    finally:
        engram.cerrar()

    # The app opens again: a fresh client loads the saved facts from disk.
    engram = MemoriaEngram(binario=_BINARIO, timeout=20)
    try:
        memoria = MemoriaUsuario(engram)
        assert memoria.cargar() is True
        contexto = [(r.titulo, r.contenido) for r in memoria.contexto("¿Cómo me llamo?")]
        assert ("Nombre", "El usuario se llama Ana") in contexto
    finally:
        engram.cerrar()
