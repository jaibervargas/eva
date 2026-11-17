"""Tests for the ChatGPT client (S3): Responses API streaming and model list.

A fake server built with ``httpx.MockTransport`` stands in for OpenAI, so the
tests never reach the network.
"""

import json

import httpx
import pytest

from app.llm.base import ClienteLLM, ErrorLLM
from app.llm.chatgpt_client import TIMEOUT_CHAT, TIMEOUT_MODELOS, ClienteChatGPT

TOKEN = "tok-secreto-abc123"
HOLA = [{"role": "user", "content": "hola"}]

SESION = "ChatGPT no aceptó tu sesión. Vuelve a iniciar sesión en Ajustes."
NO_ELEGIBLE = "Tu cuenta de ChatGPT no puede usar su plan en Eva (hace falta Plus o Pro)."
LIMITE = "Llegaste al límite de uso de tu plan de ChatGPT. Revísalo en https://chatgpt.com/settings/usage"
NO_DISPONIBLE = "ChatGPT no está disponible en este momento. Inténtalo más tarde."
SIN_CONEXION = "No pude conectarme con ChatGPT. Revisa tu conexión a internet."
TARDO = "ChatGPT tardó demasiado en responder. Inténtalo de nuevo."
PERDIDA = "Se perdió la conexión con ChatGPT mientras respondía. Inténtalo de nuevo."


# ----- SSE bodies -----


def _evento(datos) -> bytes:
    return f"event: {datos['type']}\ndata: {json.dumps(datos)}\n\n".encode()


def _creado() -> bytes:
    return _evento({"type": "response.created", "response": {"id": "r1", "status": "in_progress"}})


def _delta(texto) -> bytes:
    return _evento({"type": "response.output_text.delta", "item_id": "i1", "output_index": 0, "delta": texto})


def _completado() -> bytes:
    return _evento({"type": "response.completed", "response": {"id": "r1", "status": "completed"}})


def _fallido(codigo, mensaje="falló") -> bytes:
    return _evento(
        {"type": "response.failed", "response": {"id": "r1", "status": "failed", "error": {"code": codigo, "message": mensaje}}}
    )


def _incompleto() -> bytes:
    return _evento(
        {"type": "response.incomplete", "response": {"id": "r1", "status": "incomplete", "incomplete_details": {"reason": "x"}}}
    )


def _sse(textos) -> list[bytes]:
    return [_creado(), *[_delta(t) for t in textos], _completado()]


# ----- Fake server -----


class Servidor:
    """Answers ``/v1/models`` and ``/v1/responses`` like OpenAI would.

    ``estado``/``cuerpo`` make every request fail with that HTTP answer;
    ``partes`` replaces the SSE body; ``cortar_con`` raises that error after
    sending the body; ``falla_con`` raises it instead of answering.
    """

    def __init__(self, textos=(), modelos=(), estado=None, cuerpo=None, partes=None, cortar_con=None, falla_con=None):
        self.textos = list(textos)
        self.modelos = list(modelos)
        self.estado = estado
        self.cuerpo = cuerpo
        self.partes = partes
        self.cortar_con = cortar_con
        self.falla_con = falla_con
        self.requests = []

    def __call__(self, request):
        self.requests.append(request)
        if self.falla_con is not None:
            raise self.falla_con(request)
        if self.estado is not None:
            return httpx.Response(self.estado, json=self.cuerpo if self.cuerpo is not None else {})
        if request.url.path == "/v1/models":
            return httpx.Response(200, json={"models": self.modelos})
        partes = self.partes if self.partes is not None else _sse(self.textos)
        return httpx.Response(200, stream=_stream(partes, self.cortar_con), headers={"content-type": "text/event-stream"})


def _stream(partes, error):
    class Stream(httpx.SyncByteStream):
        def __iter__(self):
            yield from partes
            if error is not None:
                raise error

    return Stream()


class Tokens:
    """``obtener_token`` fake that counts its calls."""

    def __init__(self, token=TOKEN):
        self.token = token
        self.llamadas = 0

    def __call__(self):
        self.llamadas += 1
        return self.token


def _cliente(servidor, modelo="gpt-5", tokens=None):
    http = httpx.Client(transport=httpx.MockTransport(servidor))
    return ClienteChatGPT(modelo, tokens or Tokens(), http_client=http)


def _error_responder(servidor):
    with pytest.raises(ErrorLLM) as exc:
        list(_cliente(servidor).responder(HOLA))
    return str(exc.value)


def _error_listar(servidor):
    with pytest.raises(ErrorLLM) as exc:
        _cliente(servidor).listar_modelos()
    return str(exc.value)


def _ambos(servidor_factory):
    return _error_responder(servidor_factory()), _error_listar(servidor_factory())


def _cuerpo(codigo=None, mensaje="algo"):
    error = {"message": mensaje, "type": "invalid_request_error"}
    if codigo:
        error["code"] = codigo
    return {"error": error}


# ----- Identity -----


def test_is_a_cliente_llm_with_name_and_provider():
    cliente = _cliente(Servidor(), modelo="gpt-5")
    assert isinstance(cliente, ClienteLLM)
    assert (cliente.nombre, cliente.proveedor, cliente.modelo) == ("ChatGPT", "chatgpt", "gpt-5")


def test_timeouts_are_120s_chat_and_10s_listing():
    assert (TIMEOUT_CHAT, TIMEOUT_MODELOS) == (120.0, 10.0)


# ----- Request -----


def test_request_body_uses_instructions_and_input_without_system_items():
    servidor = Servidor(textos=["ok"])
    mensajes = [
        {"role": "system", "content": "Eres Eva"},
        {"role": "user", "content": "hola"},
        {"role": "system", "content": "El usuario se llama Ana"},
        {"role": "assistant", "content": "¡Hola!"},
        {"role": "user", "content": "¿cómo estás?"},
    ]
    list(_cliente(servidor, modelo="gpt-5").responder(mensajes))

    (req,) = servidor.requests
    assert req.method == "POST"
    assert str(req.url) == "https://api.openai.com/v1/responses"
    assert req.headers["authorization"] == f"Bearer {TOKEN}"
    body = json.loads(req.content)
    assert body == {
        "model": "gpt-5",
        "stream": True,
        "store": False,
        "instructions": "Eres Eva\n\nEl usuario se llama Ana",
        "input": [
            {"role": "user", "content": "hola"},
            {"role": "assistant", "content": "¡Hola!"},
            {"role": "user", "content": "¿cómo estás?"},
        ],
    }


def test_request_without_system_messages_sends_no_instructions():
    servidor = Servidor(textos=["ok"])
    list(_cliente(servidor).responder(HOLA))
    body = json.loads(servidor.requests[0].content)
    assert body == {"model": "gpt-5", "stream": True, "store": False, "input": HOLA}


def test_token_is_requested_for_every_request():
    tokens = Tokens()
    servidor = Servidor(textos=["ok"], modelos=[{"slug": "a", "display_name": "A", "visibility": "list"}])
    cliente = _cliente(servidor, tokens=tokens)
    list(cliente.responder(HOLA))
    tokens.token = "tok-nuevo"
    list(cliente.responder(HOLA))
    cliente.listar_modelos()
    assert tokens.llamadas == 3
    auth = [r.headers["authorization"] for r in servidor.requests]
    assert auth == [f"Bearer {TOKEN}", "Bearer tok-nuevo", "Bearer tok-nuevo"]


def test_token_provider_error_passes_through_without_contacting_anyone():
    def obtener_token():
        raise ErrorLLM("Tu sesión de ChatGPT venció. Vuelve a iniciar sesión en Ajustes.")

    servidor = Servidor(textos=["ok"])
    http = httpx.Client(transport=httpx.MockTransport(servidor))
    cliente = ClienteChatGPT("gpt-5", obtener_token, http_client=http)
    with pytest.raises(ErrorLLM) as exc:
        list(cliente.responder(HOLA))
    assert str(exc.value) == "Tu sesión de ChatGPT venció. Vuelve a iniciar sesión en Ajustes."
    with pytest.raises(ErrorLLM) as exc:
        cliente.listar_modelos()
    assert str(exc.value) == "Tu sesión de ChatGPT venció. Vuelve a iniciar sesión en Ajustes."
    assert servidor.requests == []


def test_chat_uses_120s_timeout_and_listing_10s():
    servidor = Servidor(textos=["ok"], modelos=[])
    cliente = _cliente(servidor)
    cliente.listar_modelos()
    list(cliente.responder(HOLA))
    modelos, chat = servidor.requests
    assert modelos.extensions["timeout"]["read"] == 10.0
    assert chat.extensions["timeout"]["read"] == 120.0


def test_chat_connects_within_10s_and_reads_up_to_120s():
    servidor = Servidor(textos=["ok"])
    list(_cliente(servidor).responder(HOLA))
    (chat,) = servidor.requests
    assert chat.extensions["timeout"]["connect"] == 10.0
    assert chat.extensions["timeout"]["read"] == 120.0


@pytest.mark.parametrize("estado", [500, 503, 429, 401])
def test_failed_requests_are_not_retried(estado):
    servidor = Servidor(estado=estado, cuerpo=_cuerpo())
    with pytest.raises(ErrorLLM):
        list(_cliente(servidor).responder(HOLA))
    assert len(servidor.requests) == 1
    servidor = Servidor(estado=estado, cuerpo=_cuerpo())
    with pytest.raises(ErrorLLM):
        _cliente(servidor).listar_modelos()
    assert len(servidor.requests) == 1


# ----- Streaming -----


def test_streams_text_deltas_in_order():
    servidor = Servidor(textos=["Hola", ", soy", " Eva"])
    assert list(_cliente(servidor).responder(HOLA)) == ["Hola", ", soy", " Eva"]


def test_ignores_other_events_and_empty_deltas():
    otros = [
        _evento({"type": "response.output_item.added", "output_index": 0, "item": {"type": "reasoning"}}),
        _evento({"type": "response.reasoning_summary_text.delta", "delta": "pensando"}),
        _delta(""),
        b": comentario\n\n",
    ]
    servidor = Servidor(partes=[_creado(), *otros, _delta("Hola"), _completado()])
    assert list(_cliente(servidor).responder(HOLA)) == ["Hola"]


def test_data_split_across_chunks_is_reassembled():
    completo = b"".join(_sse(["Hola", " Eva"]))
    partes = [completo[i : i + 7] for i in range(0, len(completo), 7)]
    assert list(_cliente(Servidor(partes=partes)).responder(HOLA)) == ["Hola", " Eva"]


def test_events_after_completed_are_ignored():
    servidor = Servidor(partes=[_delta("Hola"), _completado(), _delta("extra")])
    assert list(_cliente(servidor).responder(HOLA)) == ["Hola"]


def _recibir(servidor):
    recibidas = []
    with pytest.raises(ErrorLLM) as exc:
        for parte in _cliente(servidor).responder(HOLA):
            recibidas.append(parte)
    return recibidas, str(exc.value)


def test_end_without_completed_is_a_lost_connection():
    assert _recibir(Servidor(partes=[_creado(), _delta("Hola")])) == (["Hola"], PERDIDA)


def test_empty_stream_is_a_lost_connection():
    assert _recibir(Servidor(partes=[])) == ([], PERDIDA)


def test_incomplete_response_says_why():
    esperado = "ChatGPT no terminó la respuesta (x). Inténtalo de nuevo."
    assert _recibir(Servidor(partes=[_delta("Hola"), _incompleto()])) == (["Hola"], esperado)


@pytest.mark.parametrize(
    "respuesta",
    [
        {"status": "incomplete"},
        {"status": "incomplete", "incomplete_details": None},
        {"status": "incomplete", "incomplete_details": {"reason": ["raro"]}},
    ],
    ids=["sin-detalles", "detalles-nulos", "razon-no-texto"],
)
def test_incomplete_response_without_reason(respuesta):
    evento = _evento({"type": "response.incomplete", "response": respuesta})
    esperado = "ChatGPT no terminó la respuesta. Inténtalo de nuevo."
    assert _recibir(Servidor(partes=[_delta("Hola"), evento])) == (["Hola"], esperado)


def test_completed_without_text_is_an_empty_answer():
    esperado = "ChatGPT no devolvió ninguna respuesta. Inténtalo de nuevo."
    assert _recibir(Servidor(partes=[_creado(), _completado()])) == ([], esperado)
    assert _recibir(Servidor(partes=[_creado(), _delta(""), _completado()])) == ([], esperado)


def test_stream_cut_keeps_delivered_fragments():
    servidor = Servidor(partes=[_delta("Hola"), _delta(", soy")], cortar_con=httpx.RemoteProtocolError("peer closed"))
    assert _recibir(servidor) == (["Hola", ", soy"], PERDIDA)


def test_timeout_mid_answer_is_a_lost_connection():
    servidor = Servidor(partes=[_delta("Hola")], cortar_con=httpx.ReadTimeout("timed out"))
    assert _recibir(servidor) == (["Hola"], PERDIDA)


def test_timeout_before_any_text_says_it_took_too_long():
    servidor = Servidor(partes=[_creado()], cortar_con=httpx.ReadTimeout("timed out"))
    assert _recibir(servidor) == ([], TARDO)


def test_usage_limit_mid_stream():
    servidor = Servidor(partes=[_delta("Hola"), _fallido("subscription_sharing_usage_limit_exceeded")])
    assert _recibir(servidor) == (["Hola"], LIMITE)


def test_other_failed_response_shows_its_code():
    servidor = Servidor(partes=[_delta("Hola"), _fallido("server_error", f"oops {TOKEN}")])
    assert _recibir(servidor) == (["Hola"], "ChatGPT devolvió un error: server_error")


def test_failed_response_without_code():
    servidor = Servidor(partes=[_evento({"type": "response.failed", "response": {"status": "failed"}})])
    recibidas, mensaje = _recibir(servidor)
    assert recibidas == []
    assert mensaje.startswith("ChatGPT devolvió un error: ")
    assert TOKEN not in mensaje


@pytest.mark.parametrize("codigo", [["subscription_sharing_usage_limit_exceeded"], {"a": 1}], ids=["lista", "objeto"])
def test_failed_response_with_a_non_text_code_uses_its_message(codigo):
    servidor = Servidor(partes=[_delta("Hola"), _fallido(codigo, "se cayó")])
    assert _recibir(servidor) == (["Hola"], "ChatGPT devolvió un error: se cayó")


def test_error_event_mid_stream_uses_its_code():
    error = _evento({"type": "error", "code": "subscription_sharing_usage_limit_exceeded", "message": "límite"})
    assert _recibir(Servidor(partes=[_delta("Hola"), error])) == (["Hola"], LIMITE)


# ----- Errors before the stream (and when listing) -----


@pytest.mark.parametrize(
    ("estado", "cuerpo", "esperado"),
    [
        (401, _cuerpo(), SESION),
        (401, {"detail": "Unauthorized"}, SESION),
        (403, _cuerpo("subscription_sharing_invalid_user"), SESION),
        (401, _cuerpo("subscription_sharing_invalid_user"), SESION),
        (403, _cuerpo("subscription_sharing_user_not_eligible"), NO_ELEGIBLE),
        (429, _cuerpo("subscription_sharing_usage_limit_exceeded"), LIMITE),
        (503, _cuerpo(), NO_DISPONIBLE),
        (503, {}, NO_DISPONIBLE),
        (500, _cuerpo("subscription_sharing_usage_unavailable"), NO_DISPONIBLE),
        (403, _cuerpo("subscription_sharing_user_unavailable"), NO_DISPONIBLE),
    ],
)
def test_http_error_messages(estado, cuerpo, esperado):
    assert _ambos(lambda: Servidor(estado=estado, cuerpo=cuerpo)) == (esperado, esperado)


@pytest.mark.parametrize(
    ("estado", "cuerpo", "detalle"),
    [
        (400, _cuerpo(mensaje="modelo inexistente"), "modelo inexistente"),
        (500, {"detail": "caído"}, "caído"),
        (429, _cuerpo(mensaje="demasiadas"), "demasiadas"),
        (403, _cuerpo(mensaje="prohibido"), "prohibido"),
    ],
)
def test_other_http_error_shows_detail(estado, cuerpo, detalle):
    esperado = f"ChatGPT devolvió un error: {detalle}"
    assert _ambos(lambda: Servidor(estado=estado, cuerpo=cuerpo)) == (esperado, esperado)


@pytest.mark.parametrize("codigo", [["subscription_sharing_user_not_eligible"], {"a": 1}], ids=["lista", "objeto"])
@pytest.mark.parametrize(
    ("estado", "esperado"),
    [(400, "ChatGPT devolvió un error: algo"), (401, SESION), (503, NO_DISPONIBLE)],
)
def test_http_error_with_a_non_text_code_is_treated_as_unknown(codigo, estado, esperado):
    cuerpo = {"error": {"code": codigo, "message": "algo"}}
    assert _ambos(lambda: Servidor(estado=estado, cuerpo=cuerpo)) == (esperado, esperado)


def test_other_http_error_without_detail_mentions_status():
    def sin_cuerpo(request):
        return httpx.Response(502, content=b"<html>bad gateway</html>")

    with pytest.raises(ErrorLLM) as exc:
        list(_cliente(sin_cuerpo).responder(HOLA))
    assert str(exc.value).startswith("ChatGPT devolvió un error: ")
    assert "502" in str(exc.value)


def test_error_detail_masks_the_token():
    esperado = "ChatGPT devolvió un error: token raro ***"
    assert _ambos(lambda: Servidor(estado=400, cuerpo=_cuerpo(mensaje=f"token raro {TOKEN}"))) == (esperado, esperado)
    esperado = "ChatGPT devolvió un error: Bearer ***"
    assert _ambos(lambda: Servidor(estado=400, cuerpo={"detail": f"Bearer {TOKEN}"})) == (esperado, esperado)


def _falla(error):
    def crear(request):
        return error("falló", request=request)

    return crear


def test_connect_error_message():
    assert _ambos(lambda: Servidor(falla_con=_falla(httpx.ConnectError))) == (SIN_CONEXION, SIN_CONEXION)


def test_connect_timeout_keeps_the_no_connection_message():
    assert _ambos(lambda: Servidor(falla_con=_falla(httpx.ConnectTimeout))) == (SIN_CONEXION, SIN_CONEXION)


@pytest.mark.parametrize("error", [httpx.ReadTimeout, httpx.WriteTimeout, httpx.PoolTimeout])
def test_timeout_before_the_answer_says_it_took_too_long(error):
    assert _ambos(lambda: Servidor(falla_con=_falla(error))) == (TARDO, TARDO)


# ----- Model list -----


def test_listar_modelos_keeps_listed_models_in_server_order():
    modelos = [
        {"slug": "gpt-5.5", "display_name": "GPT-5.5", "visibility": "list"},
        {"slug": "oculto", "display_name": "Oculto", "visibility": "hide"},
        {"slug": "gpt-5", "display_name": "GPT-5", "visibility": "list", "extra": 1},
        {"slug": "sin-visibilidad", "display_name": "Sin"},
        {"slug": "gpt-4.1", "display_name": "GPT-4.1", "visibility": "list"},
    ]
    servidor = Servidor(modelos=modelos)
    assert _cliente(servidor).listar_modelos() == [("gpt-5.5", "GPT-5.5"), ("gpt-5", "GPT-5"), ("gpt-4.1", "GPT-4.1")]
    (req,) = servidor.requests
    assert req.method == "GET"
    assert str(req.url) == "https://api.openai.com/v1/models"
    assert req.headers["authorization"] == f"Bearer {TOKEN}"


def test_listar_modelos_empty_list():
    assert _cliente(Servidor(modelos=[])).listar_modelos() == []


def test_listar_modelos_unexpected_body_is_an_error():
    def raro(request):
        return httpx.Response(200, content=b"no es json")

    with pytest.raises(ErrorLLM) as exc:
        _cliente(raro).listar_modelos()
    assert str(exc.value).startswith("ChatGPT devolvió un error: ")
