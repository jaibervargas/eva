"""Tests for the local OpenAI-compatible client (LM Studio / Ollama).

A fake server built with httpx.MockTransport stands in for LM Studio, so the
tests run without any model server.
"""

import json

import httpx
import pytest

from app.llm.base import ClienteLLM, ErrorLLM
from app.llm.local_client import LMSTUDIO_URL, OLLAMA_URL, ClienteLocal


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


def test_listar_modelos_skips_embedding_models():
    server = FakeServer(
        models=["qwen/qwen3-1.7b", "text-embedding-nomic-embed-text-v1.5", "google/gemma"]
    )
    assert _client(server).listar_modelos() == ["qwen/qwen3-1.7b", "google/gemma"]
