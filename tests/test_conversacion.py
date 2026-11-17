"""Tests for Eva's system prompt and the persisted conversation."""

import sqlite3

import pytest

from app.llm.base import ClienteLLM, ErrorLLM
from app.llm.conversacion import LIMITE_CONTEXTO, Conversacion
from app.llm.prompts import PROMPT_SISTEMA
from app.memory.history import Historial


class FakeLLM(ClienteLLM):
    def __init__(self, partes=None, error=None):
        self.partes = partes or []
        self.error = error
        self.recibidos = []

    def responder(self, mensajes):
        self.recibidos.append([dict(m) for m in mensajes])
        yield from self.partes
        if self.error:
            raise self.error


@pytest.fixture
def historial(tmp_path):
    return Historial(tmp_path / "eva.db")


def test_system_prompt_presents_eva_from_ideva_in_spanish():
    assert "Eres Eva" in PROMPT_SISTEMA
    assert "IDEVA" in PROMPT_SISTEMA
    assert "español" in PROMPT_SISTEMA


def test_enviar_streams_fragments_and_sends_system_prompt_first(historial):
    llm = FakeLLM(partes=["Hola", ", soy Eva"])
    conv = Conversacion(llm, historial)

    assert list(conv.enviar("hola")) == ["Hola", ", soy Eva"]
    assert llm.recibidos[0] == [
        {"role": "system", "content": PROMPT_SISTEMA},
        {"role": "user", "content": "hola"},
    ]


def test_turns_are_saved_and_sent_as_context(historial):
    llm = FakeLLM(partes=["Hola Ana"])
    conv = Conversacion(llm, historial)
    list(conv.enviar("Soy Ana"))
    list(conv.enviar("¿Cómo me llamo?"))

    assert llm.recibidos[1] == [
        {"role": "system", "content": PROMPT_SISTEMA},
        {"role": "user", "content": "Soy Ana"},
        {"role": "assistant", "content": "Hola Ana"},
        {"role": "user", "content": "¿Cómo me llamo?"},
    ]
    assert historial.mensajes(conv.conversacion_id)[-1] == {"role": "assistant", "content": "Hola Ana"}


def test_only_the_last_20_messages_are_sent_as_context(historial):
    cid = historial.crear_conversacion("larga")
    historial.agregar_mensajes(cid, [{"role": "user", "content": str(i)} for i in range(30)])
    llm = FakeLLM(partes=["ok"])

    list(Conversacion(llm, historial, cid).enviar("nueva"))

    enviados = llm.recibidos[0]
    assert LIMITE_CONTEXTO == 20
    assert enviados[0]["role"] == "system"
    assert [m["content"] for m in enviados[1:-1]] == [str(i) for i in range(10, 30)]
    assert enviados[-1] == {"role": "user", "content": "nueva"}


def test_conversation_is_created_on_first_answer_titled_by_the_question(historial):
    conv = Conversacion(FakeLLM(partes=["ok"]), historial)
    assert conv.conversacion_id is None
    assert historial.listar_conversaciones() == []

    list(conv.enviar("¿Qué tiempo hace hoy en Bogotá, Colombia, por la tarde?"))

    (resumen,) = historial.listar_conversaciones()
    assert resumen.id == conv.conversacion_id
    assert resumen.titulo == "¿Qué tiempo hace hoy en Bogotá, Colombia…"


def test_existing_conversation_continues_after_reopening(tmp_path):
    ruta = tmp_path / "eva.db"
    primera = Conversacion(FakeLLM(partes=["Hola Ana"]), Historial(ruta))
    list(primera.enviar("Soy Ana"))

    llm = FakeLLM(partes=["Ana"])
    list(Conversacion(llm, Historial(ruta), primera.conversacion_id).enviar("¿Cómo me llamo?"))

    assert {"role": "user", "content": "Soy Ana"} in llm.recibidos[0]


def test_empty_answer_is_not_saved(historial):
    conv = Conversacion(FakeLLM(partes=[]), historial)

    assert list(conv.enviar("hola")) == []
    assert conv.conversacion_id is None
    assert historial.listar_conversaciones() == []


def test_failed_turn_raises_and_saves_nothing(historial):
    conv = Conversacion(FakeLLM(partes=["Ho"], error=ErrorLLM("sin conexión")), historial)

    with pytest.raises(ErrorLLM, match="sin conexión"):
        list(conv.enviar("hola"))
    assert historial.listar_conversaciones() == []


def test_failed_save_leaves_no_empty_conversation(historial):
    # The database refuses new messages (e.g. disk full) after the answer streamed.
    with sqlite3.connect(historial.ruta) as db:
        db.execute("CREATE TRIGGER falla BEFORE INSERT ON mensajes BEGIN SELECT RAISE(ABORT, 'disco lleno'); END")
    conv = Conversacion(FakeLLM(partes=["Hola"]), historial)

    with pytest.raises(sqlite3.IntegrityError, match="disco lleno"):
        list(conv.enviar("hola"))
    assert conv.conversacion_id is None
    assert historial.listar_conversaciones() == []
