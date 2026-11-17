"""In-memory conversation: keeps the history and talks to the model.

Phase 2 replaces the in-memory history with SQLite.
"""

from collections.abc import Iterator

from app.llm.base import ClienteLLM, Mensaje
from app.llm.prompts import PROMPT_SISTEMA


class Conversacion:
    def __init__(self, cliente: ClienteLLM, prompt_sistema: str = PROMPT_SISTEMA) -> None:
        self.cliente = cliente
        self.prompt_sistema = prompt_sistema
        self.historial: list[Mensaje] = []

    def enviar(self, texto: str) -> Iterator[str]:
        """Stream Eva's answer to ``texto``.

        The turn is added to the history only when the answer completes, so a
        failed turn (``ErrorLLM``) leaves the history unchanged.
        """
        pregunta = {"role": "user", "content": texto}
        mensajes = [{"role": "system", "content": self.prompt_sistema}, *self.historial, pregunta]
        partes: list[str] = []
        for parte in self.cliente.responder(mensajes):
            partes.append(parte)
            yield parte
        self.historial += [pregunta, {"role": "assistant", "content": "".join(partes)}]
