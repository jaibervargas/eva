"""Client for local model servers with an OpenAI-compatible API.

LM Studio and Ollama expose the same API as OpenAI, so one client serves both:
only the server address changes.
"""

from collections.abc import Iterator

import httpx
import openai

from app.llm.base import ClienteLLM, ErrorLLM, Mensaje

LMSTUDIO_URL = "http://localhost:1234/v1"
OLLAMA_URL = "http://localhost:11434/v1"

# Local servers ignore the key, but the OpenAI SDK requires one.
_API_KEY_LOCAL = "local"


class ClienteLocal(ClienteLLM):
    def __init__(
        self,
        modelo: str,
        base_url: str = LMSTUDIO_URL,
        http_client: httpx.Client | None = None,
        timeout: float = 120.0,
    ) -> None:
        self.modelo = modelo
        self.base_url = base_url
        self._cliente = openai.OpenAI(
            base_url=base_url,
            api_key=_API_KEY_LOCAL,
            http_client=http_client,
            timeout=timeout,
            max_retries=0,
        )

    def responder(self, mensajes: list[Mensaje]) -> Iterator[str]:
        try:
            stream = self._cliente.chat.completions.create(
                model=self.modelo, messages=mensajes, stream=True
            )
            for chunk in stream:
                if not chunk.choices:
                    continue
                # Reasoning models (e.g. qwen3) stream their thinking in a
                # separate field; only the final answer goes to the user.
                texto = chunk.choices[0].delta.content
                if texto:
                    yield texto
        except openai.APIConnectionError as exc:
            raise ErrorLLM(self._mensaje_sin_conexion()) from exc
        except openai.APIError as exc:
            raise ErrorLLM(f"El modelo local devolvió un error: {exc}") from exc

    def listar_modelos(self) -> list[str]:
        """Chat models available on the server (embedding models are skipped)."""
        try:
            modelos = self._cliente.models.list()
        except openai.APIConnectionError as exc:
            raise ErrorLLM(self._mensaje_sin_conexion()) from exc
        except openai.APIError as exc:
            raise ErrorLLM(f"El modelo local devolvió un error: {exc}") from exc
        return [m.id for m in modelos if "embed" not in m.id.lower()]

    def _mensaje_sin_conexion(self) -> str:
        return (
            f"No pude conectarme con el modelo local en {self.base_url}. "
            "Revisa que LM Studio u Ollama esté abierto con su servidor activo."
        )
