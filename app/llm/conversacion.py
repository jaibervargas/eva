"""A conversation with Eva, persisted in the SQLite history."""

from collections.abc import Iterator

from app.llm.base import ClienteLLM
from app.llm.prompts import PROMPT_SISTEMA
from app.memory.history import Historial

# How many saved messages go to the model as context.
LIMITE_CONTEXTO = 20
LARGO_TITULO = 40


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
    ) -> None:
        self.cliente = cliente
        self.historial = historial
        # None until the first answer completes, so empty chats are never saved.
        self.conversacion_id = conversacion_id
        self.prompt_sistema = prompt_sistema

    def enviar(self, texto: str) -> Iterator[str]:
        """Stream Eva's answer to ``texto``.

        The question and answer are saved only when the answer completes, so a
        failed turn (``ErrorLLM``) saves nothing.
        """
        contexto = []
        if self.conversacion_id is not None:
            contexto = self.historial.mensajes(self.conversacion_id, limite=LIMITE_CONTEXTO)
        pregunta = {"role": "user", "content": texto}
        mensajes = [{"role": "system", "content": self.prompt_sistema}, *contexto, pregunta]

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
