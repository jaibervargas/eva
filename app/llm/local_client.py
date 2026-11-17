"""Client for local model servers with an OpenAI-compatible API.

LM Studio and Ollama expose the same API as OpenAI, so one client serves both:
only the server address changes.
"""

from collections.abc import Iterator

import httpx
import openai

from app.config import resolver_modelo
from app.llm.base import ClienteLLM, ErrorLLM, Mensaje

LMSTUDIO_URL = "http://localhost:1234/v1"
OLLAMA_URL = "http://localhost:11434/v1"

# Local servers ignore the key, but the OpenAI SDK requires one.
_API_KEY_LOCAL = "local"

# Listing models must answer fast (the UI waits for it); chat answers may take long.
TIMEOUT_MODELOS = 5.0
TIMEOUT_CHAT = 120.0

MENSAJE_CONEXION_PERDIDA = (
    "Se perdió la conexión con el modelo local mientras respondía. "
    "Revisa que LM Studio u Ollama siga abierto e inténtalo de nuevo."
)


class ClienteLocal(ClienteLLM):
    def __init__(
        self,
        modelo: str,
        base_url: str = LMSTUDIO_URL,
        http_client: httpx.Client | None = None,
        timeout: float = TIMEOUT_CHAT,
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
        except (openai.APIConnectionError, httpx.HTTPError) as exc:
            raise ErrorLLM(self._mensaje_sin_conexion()) from exc
        except openai.APIError as exc:
            raise ErrorLLM(f"El modelo local devolvió un error: {exc}") from exc

        # A failure here means the answer had already started (e.g. the server
        # closed or unloaded the model mid-answer); fragments sent stay sent.
        try:
            with stream:
                for chunk in stream:
                    if not chunk.choices:
                        continue
                    # Reasoning models (e.g. qwen3) stream their thinking in a
                    # separate field; only the final answer goes to the user.
                    texto = chunk.choices[0].delta.content
                    if texto:
                        yield texto
        except (openai.APIConnectionError, httpx.HTTPError) as exc:
            raise ErrorLLM(MENSAJE_CONEXION_PERDIDA) from exc
        except openai.APIError as exc:
            raise ErrorLLM(f"El modelo local devolvió un error: {exc}") from exc

    def listar_modelos(self) -> list[str]:
        """Chat models available on the server (embedding models are skipped)."""
        try:
            modelos = self._cliente.with_options(timeout=TIMEOUT_MODELOS).models.list()
        except (openai.APIConnectionError, httpx.HTTPError) as exc:
            raise ErrorLLM(self._mensaje_sin_conexion()) from exc
        except openai.APIError as exc:
            raise ErrorLLM(f"El modelo local devolvió un error: {exc}") from exc
        return [m.id for m in modelos if "embed" not in m.id.lower()]

    def _mensaje_sin_conexion(self) -> str:
        return (
            f"No pude conectarme con el modelo local en {self.base_url}. "
            "Revisa que LM Studio u Ollama esté abierto con su servidor activo."
        )


def conectar(base_url: str, modelo_guardado: str, http_client: httpx.Client | None = None) -> ClienteLocal:
    """Client for the server, using the saved model if installed, else the first one.

    Raises ``ErrorLLM`` when the server is unreachable or has no chat models.
    """
    cliente = ClienteLocal(modelo="", base_url=base_url, http_client=http_client)
    modelos = cliente.listar_modelos()
    if not modelos:
        raise ErrorLLM("El servidor local no tiene modelos de chat. Descarga uno en LM Studio u Ollama.")
    cliente.modelo = resolver_modelo(modelo_guardado, modelos)
    return cliente
