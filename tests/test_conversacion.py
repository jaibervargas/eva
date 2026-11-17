"""Tests for Eva's system prompt and the in-memory conversation."""

import pytest

from app.llm.base import ClienteLLM, ErrorLLM
from app.llm.conversacion import Conversacion
from app.llm.prompts import PROMPT_SISTEMA


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


def test_system_prompt_presents_eva_from_ideva_in_spanish():
    assert "Eres Eva" in PROMPT_SISTEMA
    assert "IDEVA" in PROMPT_SISTEMA
    assert "español" in PROMPT_SISTEMA


def test_enviar_streams_fragments_and_sends_system_prompt_first():
    llm = FakeLLM(partes=["Hola", ", soy Eva"])
    conv = Conversacion(llm)

    assert list(conv.enviar("hola")) == ["Hola", ", soy Eva"]
    assert llm.recibidos[0] == [
        {"role": "system", "content": PROMPT_SISTEMA},
        {"role": "user", "content": "hola"},
    ]


def test_enviar_keeps_history_between_turns():
    llm = FakeLLM(partes=["Hola Ana"])
    conv = Conversacion(llm)
    list(conv.enviar("Soy Ana"))
    list(conv.enviar("¿Cómo me llamo?"))

    assert llm.recibidos[1] == [
        {"role": "system", "content": PROMPT_SISTEMA},
        {"role": "user", "content": "Soy Ana"},
        {"role": "assistant", "content": "Hola Ana"},
        {"role": "user", "content": "¿Cómo me llamo?"},
    ]
    assert conv.historial[-1] == {"role": "assistant", "content": "Hola Ana"}


def test_failed_turn_raises_and_leaves_history_unchanged():
    conv = Conversacion(FakeLLM(partes=["Ho"], error=ErrorLLM("sin conexión")))

    with pytest.raises(ErrorLLM, match="sin conexión"):
        list(conv.enviar("hola"))
    assert conv.historial == []
