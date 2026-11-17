"""Clients for cloud models used with the user's API key.

OpenAI, Gemini and OpenRouter share OpenAI's API (Gemini and OpenRouter through
their official OpenAI-compatible endpoints), so one client serves the three.
Claude uses Anthropic's native SDK, whose OpenAI-compatible layer is not meant
for production use.
"""

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import anthropic
import httpx
import httpx2
import openai

from app.config import resolver_modelo
from app.llm.base import ClienteLLM, ErrorLLM, Mensaje


@dataclass(frozen=True)
class ProveedorNube:
    nombre: str  # shown to the user
    url: str


PROVEEDORES_NUBE = {
    "openai": ProveedorNube("OpenAI", "https://api.openai.com/v1"),
    "anthropic": ProveedorNube("Claude", "https://api.anthropic.com"),
    "gemini": ProveedorNube("Gemini", "https://generativelanguage.googleapis.com/v1beta/openai/"),
    "openrouter": ProveedorNube("OpenRouter", "https://openrouter.ai/api/v1"),
}

# Listing models must answer fast (the UI waits for it); chat answers may take long.
TIMEOUT_MODELOS = 10.0
TIMEOUT_CHAT = 120.0

# Anthropic's API requires an explicit answer length limit.
MAX_TOKENS_CLAUDE = 4096

# Model ids containing any of these are not chat models (OpenAI and Gemini lists).
_NO_CHAT = (
    "embedding", "tts", "whisper", "dall-e", "image", "audio",
    "realtime", "transcribe", "moderation", "search",
)

_ERRORES_DE_RED = (openai.APIConnectionError, anthropic.APIConnectionError, httpx.HTTPError, httpx2.HTTPError)
_ERRORES_DE_API = (openai.APIError, anthropic.APIError)


class ClienteNube(ClienteLLM):
    """A cloud model; ``proveedor`` is a key of ``PROVEEDORES_NUBE``."""

    def __init__(self, proveedor: str, modelo: str, clave: str) -> None:
        self.proveedor = proveedor
        self.modelo = modelo
        self._clave = clave

    @property
    def nombre(self) -> str:
        return PROVEEDORES_NUBE[self.proveedor].nombre

    def responder(self, mensajes: list[Mensaje]) -> Iterator[str]:
        try:
            stream = self._abrir_stream(mensajes)
        except (*_ERRORES_DE_RED, *_ERRORES_DE_API) as exc:
            raise self._error(exc) from exc

        # A failure here means the answer had already started; fragments sent stay sent.
        try:
            with stream:
                yield from self._textos(stream)
        except (*_ERRORES_DE_RED, *_ERRORES_DE_API) as exc:
            raise self._error(exc, respondiendo=True) from exc

    def listar_modelos(self) -> list[str]:
        """Chat models the key can use, in the order the provider lists them."""
        try:
            return self._listar()
        except (*_ERRORES_DE_RED, *_ERRORES_DE_API) as exc:
            raise self._error(exc) from exc

    def _abrir_stream(self, mensajes: list[Mensaje]) -> Any:
        raise NotImplementedError

    def _textos(self, stream: Any) -> Iterator[str]:
        raise NotImplementedError

    def _listar(self) -> list[str]:
        raise NotImplementedError

    def _error(self, exc: Exception, respondiendo: bool = False) -> ErrorLLM:
        estado = getattr(exc, "status_code", None)
        if estado in (401, 403):
            return ErrorLLM(f"La clave de {self.nombre} no es válida. Revísala en Ajustes.")
        if estado in (402, 429):  # OpenRouter answers 402 when the account has no credit
            return ErrorLLM(
                f"{self.nombre} rechazó el pedido por límite de uso o falta de saldo. Inténtalo más tarde."
            )
        if isinstance(exc, _ERRORES_DE_RED):
            if respondiendo:
                return ErrorLLM(f"Se perdió la conexión con {self.nombre} mientras respondía. Inténtalo de nuevo.")
            return ErrorLLM(f"No pude conectarme con {self.nombre}. Revisa tu conexión a internet.")
        detalle = _detalle(exc)
        if self._clave:
            detalle = detalle.replace(self._clave, "***")
        return ErrorLLM(f"{self.nombre} devolvió un error: {detalle}")


class _ClienteCompatible(ClienteNube):
    """OpenAI, Gemini and OpenRouter through the OpenAI SDK."""

    def __init__(self, proveedor: str, modelo: str, clave: str, http_client: Any = None) -> None:
        super().__init__(proveedor, modelo, clave)
        self._cliente = openai.OpenAI(
            base_url=PROVEEDORES_NUBE[proveedor].url,
            api_key=clave,
            http_client=http_client,
            timeout=TIMEOUT_CHAT,
            max_retries=0,
        )

    def _abrir_stream(self, mensajes: list[Mensaje]) -> Any:
        return self._cliente.chat.completions.create(model=self.modelo, messages=mensajes, stream=True)

    def _textos(self, stream: Any) -> Iterator[str]:
        for chunk in stream:
            if not chunk.choices:
                continue
            # Reasoning models stream their thinking in a separate field; only
            # the final answer goes to the user.
            texto = chunk.choices[0].delta.content
            if texto:
                yield texto

    def _listar(self) -> list[str]:
        modelos = self._cliente.with_options(timeout=TIMEOUT_MODELOS).models.list()
        ids = [m.id for m in modelos]
        if self.proveedor == "openrouter":
            return ids
        if self.proveedor == "gemini":
            ids = [i.removeprefix("models/") for i in ids]
            ids = [i for i in ids if "gemini" in i.lower()]
        return [i for i in ids if not any(palabra in i.lower() for palabra in _NO_CHAT)]


class _ClienteClaude(ClienteNube):
    """Claude through Anthropic's native SDK."""

    def __init__(self, modelo: str, clave: str, http_client: Any = None) -> None:
        super().__init__("anthropic", modelo, clave)
        self._cliente = anthropic.Anthropic(
            base_url=PROVEEDORES_NUBE["anthropic"].url,
            api_key=clave,
            http_client=http_client,
            timeout=TIMEOUT_CHAT,
            max_retries=0,
        )

    def _abrir_stream(self, mensajes: list[Mensaje]) -> Any:
        # Anthropic takes the instructions apart from the conversation.
        sistema = "\n".join(m["content"] for m in mensajes if m["role"] == "system")
        conversacion = [{"role": m["role"], "content": m["content"]} for m in mensajes if m["role"] != "system"]
        extra: dict[str, Any] = {"system": sistema} if sistema else {}
        return self._cliente.messages.create(
            model=self.modelo,
            max_tokens=MAX_TOKENS_CLAUDE,
            messages=conversacion,  # type: ignore[arg-type]
            stream=True,
            **extra,
        )

    def _textos(self, stream: Any) -> Iterator[str]:
        for evento in stream:
            # Only answer text reaches the user (thinking deltas are skipped).
            if evento.type == "content_block_delta" and evento.delta.type == "text_delta" and evento.delta.text:
                yield evento.delta.text

    def _listar(self) -> list[str]:
        modelos = self._cliente.with_options(timeout=TIMEOUT_MODELOS).models.list(limit=1000)
        return [m.id for m in modelos]


def crear_cliente(proveedor: str, modelo: str, clave: str, http_client: Any = None) -> ClienteNube:
    """Client for ``proveedor`` without contacting it.

    ``http_client`` is for tests: an ``httpx.Client`` or ``httpx2.Client`` for
    the OpenAI-compatible providers, an ``httpx2.Client`` for Claude.
    Raises ``ValueError`` for an unknown provider.
    """
    if proveedor not in PROVEEDORES_NUBE:
        raise ValueError(f"Unknown cloud provider: {proveedor!r}")
    if proveedor == "anthropic":
        return _ClienteClaude(modelo, clave, http_client=http_client)
    return _ClienteCompatible(proveedor, modelo, clave, http_client=http_client)


def conectar_nube(proveedor: str, modelo_guardado: str, clave: str | None, http_client: Any = None) -> ClienteNube:
    """Client using the saved model if the key can use it, else the first one.

    Raises ``ErrorLLM`` when the key is missing or invalid, the provider is
    unreachable, or it offers no chat models.
    """
    if proveedor not in PROVEEDORES_NUBE:
        raise ValueError(f"Unknown cloud provider: {proveedor!r}")
    nombre = PROVEEDORES_NUBE[proveedor].nombre
    if not clave:
        raise ErrorLLM(f"Falta la clave de {nombre}. Agrégala en Ajustes.")
    cliente = crear_cliente(proveedor, "", clave, http_client=http_client)
    modelos = cliente.listar_modelos()
    if not modelos:
        raise ErrorLLM(f"No encontré modelos de chat en tu cuenta de {nombre}.")
    cliente.modelo = resolver_modelo(modelo_guardado, modelos)
    return cliente


def _detalle(exc: Exception) -> str:
    """The provider's own error text when it sent one, else the exception text."""
    cuerpo = getattr(exc, "body", None)
    if isinstance(cuerpo, dict):
        error = cuerpo.get("error", cuerpo)
        if isinstance(error, dict) and isinstance(error.get("message"), str) and error["message"]:
            return error["message"]
    return str(exc) or type(exc).__name__
