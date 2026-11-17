"""Common interface for every language model Eva can use.

Each provider (local server or cloud) implements ``ClienteLLM`` so the rest of
the app can switch providers from the settings without other code changes.
"""

from abc import ABC, abstractmethod
from collections.abc import Iterator

# A chat message in OpenAI format: {"role": "system" | "user" | "assistant", "content": "..."}
Mensaje = dict[str, str]


class ErrorLLM(Exception):
    """A model failure, with a message meant to be shown to the user."""


class ClienteLLM(ABC):
    @abstractmethod
    def responder(self, mensajes: list[Mensaje]) -> Iterator[str]:
        """Stream the answer to ``mensajes`` as text fragments, in order.

        Raises ``ErrorLLM`` when the model cannot be reached or fails.
        """
