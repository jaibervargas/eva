"""Tests for the local OpenAI-compatible client (LM Studio / Ollama).

A fake server built with httpx.MockTransport stands in for LM Studio, so the
tests run without any model server.
"""

import json

import httpx
import pytest

from app.llm.base import ClienteLLM, ErrorLLM
from app.llm.local_client import LMSTUDIO_URL, OLLAMA_URL, ClienteLocal, conectar


def _sse(chunks: list[dict]) -> bytes:
    lines = [f"data: {json.dumps(c)}\n\n" for c in chunks]
    lines.append("data: [DONE]\n\n")
    return "".join(lines).encode()


def _chunk(content=None, reasoning=None) -> dict:
    delta = {}
    if content is not None:
        delta["content"] = content
    if reasoning is not None:
        delta["reasoning_content"] = reasoning
    return {
        "id": "x",
        "object": "chat.completion.chunk",
        "created": 0,
        "model": "m",
        "choices": [{"index": 0, "delta": delta, "finish_reason": None}],
    }


class FakeServer:
    def __init__(self, chunks=None, models=None):
        self.chunks = chunks or []
        self.models = models or []
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.path.endswith("/models"):
            data = [{"id": m, "object": "model", "owned_by": "x"} for m in self.models]
            return httpx.Response(200, json={"object": "list", "data": data})
        return httpx.Response(
            200,
            content=_sse(self.chunks),
            headers={"content-type": "text/event-stream"},
        )


def _client(server, modelo="qwen/qwen3-1.7b", base_url=LMSTUDIO_URL):
    http = httpx.Client(transport=httpx.MockTransport(server))
    return ClienteLocal(modelo=modelo, base_url=base_url, http_client=http)


def test_is_a_cliente_llm():
    assert isinstance(_client(FakeServer()), ClienteLLM)


def test_default_urls_match_the_plan():
    assert LMSTUDIO_URL == "http://localhost:1234/v1"
    assert OLLAMA_URL == "http://localhost:11434/v1"


def test_responder_streams_only_answer_content_in_order():
    server = FakeServer(
        chunks=[
            _chunk(reasoning="pensando..."),
            _chunk(content=""),
            _chunk(content="Hola"),
            _chunk(content=", soy"),
            _chunk(content=" Eva"),
            _chunk(),
        ]
    )
    partes = list(_client(server).responder([{"role": "user", "content": "hola"}]))
    assert partes == ["Hola", ", soy", " Eva"]


def test_responder_sends_model_messages_and_stream_to_server_url():
    server = FakeServer(chunks=[_chunk(content="ok")])
    mensajes = [
        {"role": "system", "content": "Eres Eva"},
        {"role": "user", "content": "hola"},
    ]
    list(_client(server, modelo="mi-modelo", base_url=OLLAMA_URL).responder(mensajes))

    (req,) = server.requests
    assert str(req.url) == "http://localhost:11434/v1/chat/completions"
    body = json.loads(req.content)
    assert body["model"] == "mi-modelo"
    assert body["messages"] == mensajes
    assert body["stream"] is True


def test_responder_raises_error_llm_when_server_is_down():
    def down(request):
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(ErrorLLM) as exc:
        list(_client(down).responder([{"role": "user", "content": "hola"}]))
    assert str(exc.value) == (
        "No pude conectarme con el modelo local en http://localhost:1234/v1. "
        "Revisa que LM Studio u Ollama esté abierto con su servidor activo."
    )


def test_listar_modelos_raises_error_llm_on_http_error():
    def failing(request):
        return httpx.Response(500, json={"error": {"message": "boom"}})

    with pytest.raises(ErrorLLM, match="El modelo local devolvió un error"):
        _client(failing).listar_modelos()


def _http(server):
    return httpx.Client(transport=httpx.MockTransport(server))


def test_conectar_uses_saved_model_when_installed():
    server = FakeServer(models=["a", "b"])
    cliente = conectar(OLLAMA_URL, "b", http_client=_http(server))
    assert (cliente.modelo, cliente.base_url) == ("b", OLLAMA_URL)


def test_conectar_falls_back_to_first_chat_model():
    server = FakeServer(models=["text-embedding-x", "a", "b"])
    assert conectar(LMSTUDIO_URL, "borrado", http_client=_http(server)).modelo == "a"


def test_conectar_without_chat_models_raises_error_llm():
    server = FakeServer(models=["text-embedding-x"])
    with pytest.raises(ErrorLLM) as exc:
        conectar(LMSTUDIO_URL, "", http_client=_http(server))
    assert str(exc.value) == "El servidor local no tiene modelos de chat. Descarga uno en LM Studio u Ollama."


def test_listar_modelos_skips_embedding_models():
    server = FakeServer(
        models=["qwen/qwen3-1.7b", "text-embedding-nomic-embed-text-v1.5", "google/gemma"]
    )
    assert _client(server).listar_modelos() == ["qwen/qwen3-1.7b", "google/gemma"]


# ----- S1: transport failures while streaming -----

PERDIDA = (
    "Se perdió la conexión con el modelo local mientras respondía. "
    "Revisa que LM Studio u Ollama siga abierto e inténtalo de nuevo."
)


class _StreamCortado(httpx.SyncByteStream):
    """Sends some SSE chunks, then the connection breaks with ``error``."""

    def __init__(self, chunks, error):
        self.chunks = chunks
        self.error = error

    def __iter__(self):
        for c in self.chunks:
            yield f"data: {json.dumps(c)}\n\n".encode()
        raise self.error


@pytest.mark.parametrize(
    "error",
    [httpx.RemoteProtocolError("peer closed connection"), httpx.ReadTimeout("timed out")],
)
def test_stream_broken_mid_answer_raises_error_llm_after_delivering_fragments(error):
    def server(request):
        return httpx.Response(
            200,
            stream=_StreamCortado([_chunk(content="Hola"), _chunk(content=", soy")], error),
            headers={"content-type": "text/event-stream"},
        )

    recibidas = []
    with pytest.raises(ErrorLLM) as exc:
        for parte in _client(server).responder([{"role": "user", "content": "hola"}]):
            recibidas.append(parte)
    assert recibidas == ["Hola", ", soy"]
    assert str(exc.value) == PERDIDA


# ----- S5: short timeout for listing models -----


def test_listing_models_uses_a_short_timeout_but_chat_keeps_the_long_one():
    from app.llm.local_client import TIMEOUT_MODELOS

    server = FakeServer(chunks=[_chunk(content="ok")], models=["a"])
    cliente = conectar(LMSTUDIO_URL, "", http_client=_http(server))
    list(cliente.responder([{"role": "user", "content": "hola"}]))

    modelos, chat = server.requests
    assert TIMEOUT_MODELOS == 5.0
    assert modelos.url.path.endswith("/models")
    assert modelos.extensions["timeout"]["read"] == 5.0
    assert chat.extensions["timeout"]["read"] == 120.0
