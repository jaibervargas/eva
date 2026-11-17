"""Tests for the cloud model client (S1): OpenAI, Claude, Gemini and OpenRouter.

Fake servers built with MockTransport stand in for each provider, so the tests
never reach the network. The OpenAI SDK takes an ``httpx`` client; the native
Anthropic SDK only takes an ``httpx2`` client, so each fake uses the module
its SDK expects.
"""

import json

import httpx
import httpx2
import pytest

from app.llm.base import ClienteLLM, ErrorLLM
from app.llm.cloud_client import (
    MAX_TOKENS_CLAUDE,
    PROVEEDORES_NUBE,
    conectar_nube,
    crear_cliente,
)

CLAVE = "sk-prueba-123"
NOMBRES = {"openai": "OpenAI", "anthropic": "Claude", "gemini": "Gemini", "openrouter": "OpenRouter"}
TODOS = list(NOMBRES)
COMPATIBLES = ["openai", "gemini", "openrouter"]
HOLA = [{"role": "user", "content": "hola"}]


def _modulo(proveedor):
    return httpx2 if proveedor == "anthropic" else httpx


def _ids(proveedor, *nombres):
    # Gemini only lists ids containing "gemini", with a "models/" prefix.
    if proveedor == "gemini":
        return [f"models/gemini-{n}" for n in nombres]
    return list(nombres)


def _id(proveedor, nombre):
    return f"gemini-{nombre}" if proveedor == "gemini" else nombre


# ----- SSE bodies -----


def _sse_openai(textos, reasoning=None) -> list[bytes]:
    def chunk(delta):
        return {
            "id": "x",
            "object": "chat.completion.chunk",
            "created": 0,
            "model": "m",
            "choices": [{"index": 0, "delta": delta, "finish_reason": None}],
        }

    deltas = [{"reasoning_content": reasoning}] if reasoning else []
    deltas += [{"content": t} for t in textos] + [{}]
    return [f"data: {json.dumps(chunk(d))}\n\n".encode() for d in deltas]


def _evento(nombre, datos) -> bytes:
    return f"event: {nombre}\ndata: {json.dumps(datos)}\n\n".encode()


def _sse_claude_inicio() -> list[bytes]:
    mensaje = {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "model": "m",
        "content": [],
        "stop_reason": None,
        "stop_sequence": None,
        "usage": {"input_tokens": 1, "output_tokens": 0},
    }
    return [
        _evento("message_start", {"type": "message_start", "message": mensaje}),
        _evento("content_block_start", {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}}),
    ]


def _delta_claude(texto) -> bytes:
    delta = {"type": "text_delta", "text": texto}
    return _evento("content_block_delta", {"type": "content_block_delta", "index": 0, "delta": delta})


def _sse_claude(textos) -> list[bytes]:
    return [
        *_sse_claude_inicio(),
        *[_delta_claude(t) for t in textos],
        _evento("content_block_stop", {"type": "content_block_stop", "index": 0}),
        _evento(
            "message_delta",
            {"type": "message_delta", "delta": {"stop_reason": "end_turn", "stop_sequence": None}, "usage": {"output_tokens": 3}},
        ),
        _evento("message_stop", {"type": "message_stop"}),
    ]


def _cuerpo_error(proveedor, mensaje):
    if proveedor == "anthropic":
        return {"type": "error", "error": {"type": "api_error", "message": mensaje}}
    return {"error": {"message": mensaje, "type": "api_error"}}


# ----- Fake server -----


class Servidor:
    """Answers model listing and streamed chat like ``proveedor`` would.

    ``estado`` makes every request fail with that HTTP status; ``partes``
    replaces the SSE body (raw byte pieces); ``cortar_con`` breaks the
    connection with that error after sending the body.
    """

    def __init__(self, proveedor, textos=(), modelos=(), estado=None, error="boom", partes=None, cortar_con=None):
        self.proveedor = proveedor
        self.h = _modulo(proveedor)
        self.textos = list(textos)
        self.modelos = list(modelos)
        self.estado = estado
        self.error = error
        self.partes = partes
        self.cortar_con = cortar_con
        self.requests = []

    def __call__(self, request):
        self.requests.append(request)
        if self.estado is not None:
            return self.h.Response(self.estado, json=_cuerpo_error(self.proveedor, self.error))
        if request.url.path.endswith("/models"):
            return self.h.Response(200, json=self._lista())
        if self.partes is not None:
            partes = self.partes
        elif self.proveedor == "anthropic":
            partes = _sse_claude(self.textos)
        else:
            partes = [*_sse_openai(self.textos), b"data: [DONE]\n\n"]
        return self.h.Response(
            200,
            stream=_stream(self.h, partes, self.cortar_con),
            headers={"content-type": "text/event-stream"},
        )

    def _lista(self):
        if self.proveedor == "anthropic":
            data = [
                {"id": m, "type": "model", "display_name": m, "created_at": "2025-01-01T00:00:00Z"}
                for m in self.modelos
            ]
            return {
                "data": data,
                "has_more": False,
                "first_id": self.modelos[0] if self.modelos else None,
                "last_id": self.modelos[-1] if self.modelos else None,
            }
        data = [{"id": m, "object": "model", "created": 0, "owned_by": "x"} for m in self.modelos]
        return {"object": "list", "data": data}


def _stream(h, partes, error):
    class Stream(h.SyncByteStream):
        def __iter__(self):
            yield from partes
            if error is not None:
                raise error

    return Stream()


def _http(servidor):
    h = servidor.h
    return h.Client(transport=h.MockTransport(servidor))


def _cliente(servidor, modelo="m", clave=CLAVE):
    return crear_cliente(servidor.proveedor, modelo, clave, http_client=_http(servidor))


def _caido(proveedor):
    h = _modulo(proveedor)

    def servidor(request):
        raise h.ConnectError("refused", request=request)

    servidor.proveedor = proveedor
    servidor.h = h
    return servidor


# ----- Provider table -----


def test_provider_table_has_names_and_official_urls():
    assert {p: v.nombre for p, v in PROVEEDORES_NUBE.items()} == NOMBRES
    assert PROVEEDORES_NUBE["openai"].url == "https://api.openai.com/v1"
    assert PROVEEDORES_NUBE["gemini"].url == "https://generativelanguage.googleapis.com/v1beta/openai/"
    assert PROVEEDORES_NUBE["openrouter"].url == "https://openrouter.ai/api/v1"


@pytest.mark.parametrize("proveedor", TODOS)
def test_is_a_cliente_llm(proveedor):
    cliente = _cliente(Servidor(proveedor), modelo="x")
    assert isinstance(cliente, ClienteLLM)
    assert (cliente.proveedor, cliente.modelo) == (proveedor, "x")


def test_unknown_provider_is_rejected():
    with pytest.raises(ValueError):
        crear_cliente("desconocido", "m", CLAVE)


# ----- Streaming answers -----


@pytest.mark.parametrize("proveedor", TODOS)
def test_responder_streams_text_fragments_in_order(proveedor):
    servidor = Servidor(proveedor, textos=["Hola", ", soy", " Eva"])
    assert list(_cliente(servidor).responder(HOLA)) == ["Hola", ", soy", " Eva"]


def test_openai_compatible_stream_skips_reasoning_and_empty_fragments():
    partes = [*_sse_openai(["", "Hola"], reasoning="pensando..."), b"data: [DONE]\n\n"]
    servidor = Servidor("openrouter", partes=partes)
    assert list(_cliente(servidor).responder(HOLA)) == ["Hola"]


def test_claude_stream_skips_thinking_fragments():
    pensamiento = _evento(
        "content_block_delta",
        {"type": "content_block_delta", "index": 0, "delta": {"type": "thinking_delta", "thinking": "mmm"}},
    )
    partes = [*_sse_claude_inicio(), pensamiento, _delta_claude("Hola"), _evento("message_stop", {"type": "message_stop"})]
    servidor = Servidor("anthropic", partes=partes)
    assert list(_cliente(servidor).responder(HOLA)) == ["Hola"]


@pytest.mark.parametrize(
    ("proveedor", "url"),
    [
        ("openai", "https://api.openai.com/v1/chat/completions"),
        ("gemini", "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"),
        ("openrouter", "https://openrouter.ai/api/v1/chat/completions"),
    ],
)
def test_openai_compatible_request_goes_to_official_url_with_key(proveedor, url):
    servidor = Servidor(proveedor, textos=["ok"])
    mensajes = [{"role": "system", "content": "Eres Eva"}, *HOLA]
    list(_cliente(servidor, modelo="mi-modelo").responder(mensajes))

    (req,) = servidor.requests
    assert str(req.url) == url
    assert req.headers["authorization"] == f"Bearer {CLAVE}"
    body = json.loads(req.content)
    assert (body["model"], body["messages"], body["stream"]) == ("mi-modelo", mensajes, True)


def test_claude_hoists_all_system_messages_and_sets_max_tokens():
    servidor = Servidor("anthropic", textos=["ok"])
    mensajes = [
        {"role": "system", "content": "Eres Eva"},
        {"role": "system", "content": "El usuario se llama Ana"},
        {"role": "user", "content": "hola"},
        {"role": "assistant", "content": "¡Hola!"},
        {"role": "user", "content": "¿cómo estás?"},
    ]
    list(_cliente(servidor, modelo="claude-x").responder(mensajes))

    (req,) = servidor.requests
    assert str(req.url) == "https://api.anthropic.com/v1/messages"
    # Anthropic SDK 2.x sends the key as a bearer token (1.x used x-api-key).
    assert CLAVE in (req.headers.get("x-api-key"), req.headers.get("authorization", "").removeprefix("Bearer "))
    body = json.loads(req.content)
    assert body["system"] == "Eres Eva\nEl usuario se llama Ana"
    assert body["messages"] == mensajes[2:]
    assert (body["model"], body["max_tokens"], body["stream"]) == ("claude-x", MAX_TOKENS_CLAUDE, True)
    assert MAX_TOKENS_CLAUDE > 0


def test_claude_without_system_messages_sends_no_system():
    servidor = Servidor("anthropic", textos=["ok"])
    list(_cliente(servidor).responder(HOLA))
    assert "system" not in json.loads(servidor.requests[0].content)


# ----- Model lists -----


def test_openai_model_list_keeps_only_chat_models():
    modelos = [
        "gpt-4o", "text-embedding-3-small", "tts-1", "whisper-1", "dall-e-3", "gpt-image-1",
        "gpt-4o-audio-preview", "gpt-4o-realtime-preview", "gpt-4o-transcribe",
        "omni-moderation-latest", "gpt-4o-search-preview", "o3",
    ]
    assert _cliente(Servidor("openai", modelos=modelos)).listar_modelos() == ["gpt-4o", "o3"]


def test_gemini_model_list_keeps_gemini_chat_models_without_prefix():
    modelos = [
        "models/gemini-2.5-flash", "models/embedding-001", "models/gemini-embedding-001",
        "models/imagen-3.0-generate-002", "models/gemini-2.5-pro",
    ]
    assert _cliente(Servidor("gemini", modelos=modelos)).listar_modelos() == ["gemini-2.5-flash", "gemini-2.5-pro"]


@pytest.mark.parametrize("proveedor", ["anthropic", "openrouter"])
def test_claude_and_openrouter_keep_every_model(proveedor):
    modelos = ["claude-sonnet-4-5", "openai/gpt-4o", "google/gemini-2.5-pro"]
    assert _cliente(Servidor(proveedor, modelos=modelos)).listar_modelos() == modelos


@pytest.mark.parametrize("proveedor", TODOS)
def test_listing_uses_10s_timeout_and_chat_120s(proveedor):
    servidor = Servidor(proveedor, textos=["ok"], modelos=_ids(proveedor, "a"))
    cliente = conectar_nube(proveedor, "", CLAVE, http_client=_http(servidor))
    list(cliente.responder(HOLA))

    modelos, chat = servidor.requests
    assert modelos.url.path.endswith("/models")
    assert modelos.extensions["timeout"]["read"] == 10.0
    assert chat.extensions["timeout"]["read"] == 120.0


@pytest.mark.parametrize("proveedor", TODOS)
def test_failed_requests_are_not_retried(proveedor):
    servidor = Servidor(proveedor, estado=500)
    with pytest.raises(ErrorLLM):
        list(_cliente(servidor).responder(HOLA))
    assert len(servidor.requests) == 1


# ----- Error messages -----


@pytest.mark.parametrize("estado", [401, 403])
@pytest.mark.parametrize("proveedor", TODOS)
def test_invalid_key_message(proveedor, estado):
    nombre = NOMBRES[proveedor]
    esperado = f"La clave de {nombre} no es válida. Revísala en Ajustes."
    with pytest.raises(ErrorLLM) as exc:
        list(_cliente(Servidor(proveedor, estado=estado)).responder(HOLA))
    assert str(exc.value) == esperado
    with pytest.raises(ErrorLLM) as exc:
        _cliente(Servidor(proveedor, estado=estado)).listar_modelos()
    assert str(exc.value) == esperado


@pytest.mark.parametrize("proveedor", TODOS)
def test_no_connection_message(proveedor):
    esperado = f"No pude conectarme con {NOMBRES[proveedor]}. Revisa tu conexión a internet."
    with pytest.raises(ErrorLLM) as exc:
        list(_cliente(_caido(proveedor)).responder(HOLA))
    assert str(exc.value) == esperado
    with pytest.raises(ErrorLLM) as exc:
        _cliente(_caido(proveedor)).listar_modelos()
    assert str(exc.value) == esperado


@pytest.mark.parametrize("estado", [429, 402])
@pytest.mark.parametrize("proveedor", TODOS)
def test_rate_limit_or_no_credit_message(proveedor, estado):
    esperado = (
        f"{NOMBRES[proveedor]} rechazó el pedido por límite de uso o falta de saldo. Inténtalo más tarde."
    )
    with pytest.raises(ErrorLLM) as exc:
        list(_cliente(Servidor(proveedor, estado=estado)).responder(HOLA))
    assert str(exc.value) == esperado


@pytest.mark.parametrize("proveedor", TODOS)
def test_other_api_error_message_includes_detail(proveedor):
    with pytest.raises(ErrorLLM) as exc:
        list(_cliente(Servidor(proveedor, estado=400, error="modelo inexistente")).responder(HOLA))
    mensaje = str(exc.value)
    assert mensaje.startswith(f"{NOMBRES[proveedor]} devolvió un error: ")
    assert "modelo inexistente" in mensaje


@pytest.mark.parametrize("proveedor", TODOS)
def test_listing_api_error_message_includes_detail(proveedor):
    with pytest.raises(ErrorLLM) as exc:
        _cliente(Servidor(proveedor, estado=500, error="caído")).listar_modelos()
    assert str(exc.value).startswith(f"{NOMBRES[proveedor]} devolvió un error: ")
    assert "caído" in str(exc.value)


@pytest.mark.parametrize("proveedor", TODOS)
def test_error_detail_never_contains_the_key(proveedor):
    servidor = Servidor(proveedor, estado=400, error=f"clave rara {CLAVE}")
    with pytest.raises(ErrorLLM) as exc:
        list(_cliente(servidor).responder(HOLA))
    assert CLAVE not in str(exc.value)


@pytest.mark.parametrize("proveedor", TODOS)
def test_connection_lost_mid_answer_keeps_delivered_fragments(proveedor):
    h = _modulo(proveedor)
    if proveedor == "anthropic":
        partes = [*_sse_claude_inicio(), _delta_claude("Hola"), _delta_claude(", soy")]
    else:
        partes = _sse_openai(["Hola", ", soy"])[:2]
    servidor = Servidor(proveedor, partes=partes, cortar_con=h.RemoteProtocolError("peer closed connection"))

    recibidas = []
    with pytest.raises(ErrorLLM) as exc:
        for parte in _cliente(servidor).responder(HOLA):
            recibidas.append(parte)
    assert recibidas == ["Hola", ", soy"]
    assert str(exc.value) == (
        f"Se perdió la conexión con {NOMBRES[proveedor]} mientras respondía. Inténtalo de nuevo."
    )


@pytest.mark.parametrize("proveedor", TODOS)
def test_error_event_mid_answer_is_an_api_error(proveedor):
    if proveedor == "anthropic":
        error = {"type": "error", "error": {"type": "overloaded_error", "message": "sobrecargado"}}
        partes = [*_sse_claude_inicio(), _delta_claude("Hola"), _evento("error", error)]
    else:
        error = {"error": {"message": "sobrecargado", "type": "server_error"}}
        partes = [_sse_openai(["Hola"])[0], f"data: {json.dumps(error)}\n\n".encode()]
    servidor = Servidor(proveedor, partes=partes)

    recibidas = []
    with pytest.raises(ErrorLLM) as exc:
        for parte in _cliente(servidor).responder(HOLA):
            recibidas.append(parte)
    assert recibidas == ["Hola"]
    assert str(exc.value).startswith(f"{NOMBRES[proveedor]} devolvió un error: ")
    assert "sobrecargado" in str(exc.value)


# ----- conectar_nube -----


@pytest.mark.parametrize("proveedor", TODOS)
def test_conectar_nube_uses_saved_model_when_available(proveedor):
    servidor = Servidor(proveedor, modelos=_ids(proveedor, "a", "b"))
    cliente = conectar_nube(proveedor, _id(proveedor, "b"), CLAVE, http_client=_http(servidor))
    assert (cliente.proveedor, cliente.modelo) == (proveedor, _id(proveedor, "b"))


@pytest.mark.parametrize("proveedor", TODOS)
def test_conectar_nube_falls_back_to_first_model(proveedor):
    servidor = Servidor(proveedor, modelos=_ids(proveedor, "a", "b"))
    assert conectar_nube(proveedor, "borrado", CLAVE, http_client=_http(servidor)).modelo == _id(proveedor, "a")


def test_conectar_nube_gemini_matches_saved_model_without_prefix():
    servidor = Servidor("gemini", modelos=["models/gemini-2.5-flash", "models/gemini-2.5-pro"])
    assert conectar_nube("gemini", "gemini-2.5-pro", CLAVE, http_client=_http(servidor)).modelo == "gemini-2.5-pro"


@pytest.mark.parametrize("clave", [None, ""])
@pytest.mark.parametrize("proveedor", TODOS)
def test_conectar_nube_without_key_asks_for_it_and_contacts_nobody(proveedor, clave):
    servidor = Servidor(proveedor, modelos=["a"])
    with pytest.raises(ErrorLLM) as exc:
        conectar_nube(proveedor, "a", clave, http_client=_http(servidor))
    assert str(exc.value) == f"Falta la clave de {NOMBRES[proveedor]}. Agrégala en Ajustes."
    assert servidor.requests == []


@pytest.mark.parametrize("proveedor", TODOS)
def test_conectar_nube_without_chat_models_raises_error_llm(proveedor):
    servidor = Servidor(proveedor, modelos=[])
    with pytest.raises(ErrorLLM) as exc:
        conectar_nube(proveedor, "", CLAVE, http_client=_http(servidor))
    assert str(exc.value) == f"No encontré modelos de chat en tu cuenta de {NOMBRES[proveedor]}."


@pytest.mark.parametrize("proveedor", TODOS)
def test_conectar_nube_propagates_invalid_key(proveedor):
    with pytest.raises(ErrorLLM) as exc:
        conectar_nube(proveedor, "", CLAVE, http_client=_http(Servidor(proveedor, estado=401)))
    assert str(exc.value) == f"La clave de {NOMBRES[proveedor]} no es válida. Revísala en Ajustes."
