"""Client for the user's ChatGPT plan ("Sign in with ChatGPT").

Uses OpenAI's Responses API with an OAuth access token instead of an API key.
The plan only accepts a subset of the API: streamed, unstored answers whose
instructions travel apart from the conversation, so the requests are built by
hand with ``httpx`` and the Server-Sent Events are parsed here.
"""

import json
from collections.abc import Callable, Iterator
from typing import Any

import httpx
import httpx2

from app.llm.base import ClienteLLM, ErrorLLM, Mensaje

URL_RESPUESTAS = "https://api.openai.com/v1/responses"
URL_MODELOS = "https://api.openai.com/v1/models"

# Listing models must answer fast (the UI waits for it); chat answers may take long.
TIMEOUT_MODELOS = 10.0
TIMEOUT_CHAT = 120.0
# Reaching OpenAI must be quick even when the answer itself may take long.
TIMEOUT_CONEXION = 10.0

NOMBRE = "ChatGPT"

_SESION = "ChatGPT no aceptó tu sesión. Vuelve a iniciar sesión en Ajustes."
_NO_ELEGIBLE = "Tu cuenta de ChatGPT no puede usar su plan en Eva (hace falta Plus o Pro)."
_LIMITE = "Llegaste al límite de uso de tu plan de ChatGPT. Revísalo en https://chatgpt.com/settings/usage"
_NO_DISPONIBLE = "ChatGPT no está disponible en este momento. Inténtalo más tarde."
_SIN_CONEXION = "No pude conectarme con ChatGPT. Revisa tu conexión a internet."
_TARDO = "ChatGPT tardó demasiado en responder. Inténtalo de nuevo."
_PERDIDA = "Se perdió la conexión con ChatGPT mientras respondía. Inténtalo de nuevo."
_ERROR = "ChatGPT devolvió un error: {}"
_INCOMPLETA = "ChatGPT no terminó la respuesta{}. Inténtalo de nuevo."
_VACIA = "ChatGPT no devolvió ninguna respuesta. Inténtalo de nuevo."

# Error codes of the plan ("subscription sharing") and the message each one shows.
_POR_CODIGO = {
    "subscription_sharing_invalid_user": _SESION,
    "subscription_sharing_user_not_eligible": _NO_ELEGIBLE,
    "subscription_sharing_usage_limit_exceeded": _LIMITE,
    "subscription_sharing_usage_unavailable": _NO_DISPONIBLE,
    "subscription_sharing_user_unavailable": _NO_DISPONIBLE,
}

_ERRORES_DE_RED = (httpx.HTTPError, httpx2.HTTPError)
_TIMEOUTS = (httpx.TimeoutException, httpx2.TimeoutException)
_TIMEOUTS_DE_CONEXION = (httpx.ConnectTimeout, httpx2.ConnectTimeout)


class ClienteChatGPT(ClienteLLM):
    """A model of the user's ChatGPT plan.

    ``obtener_token`` returns a valid access token; it is called once per
    request (it may refresh the token) and an ``ErrorLLM`` it raises reaches
    the caller unchanged. ``http_client`` (an ``httpx.Client``) is for tests.
    Requests are never retried.
    """

    nombre = NOMBRE
    proveedor = "chatgpt"

    def __init__(self, modelo: str, obtener_token: Callable[[], str], http_client: Any = None) -> None:
        self.modelo = modelo
        self._obtener_token = obtener_token
        self._http = http_client if http_client is not None else httpx.Client()

    def responder(self, mensajes: list[Mensaje]) -> Iterator[str]:
        token = self._obtener_token()
        cuerpo: dict[str, Any] = {"model": self.modelo, "stream": True, "store": False}
        # The plan rejects system items: the instructions go in their own field.
        instrucciones = "\n\n".join(m["content"] for m in mensajes if m["role"] == "system")
        if instrucciones:
            cuerpo["instructions"] = instrucciones
        cuerpo["input"] = [{"role": m["role"], "content": m["content"]} for m in mensajes if m["role"] != "system"]

        abierto = False  # the server accepted the request and started the stream
        respondio = False  # at least one text fragment reached the user
        try:
            with self._http.stream(
                "POST",
                URL_RESPUESTAS,
                json=cuerpo,
                headers=_cabeceras(token),
                timeout=httpx.Timeout(TIMEOUT_CHAT, connect=TIMEOUT_CONEXION),
            ) as respuesta:
                if respuesta.status_code >= 400:
                    respuesta.read()
                    raise _error_http(respuesta, token)
                abierto = True
                for evento in _eventos(respuesta.iter_lines()):
                    tipo = evento.get("type")
                    if tipo == "response.output_text.delta":
                        texto = evento.get("delta")
                        if isinstance(texto, str) and texto:
                            respondio = True
                            yield texto
                    elif tipo == "response.completed":
                        if not respondio:
                            raise ErrorLLM(_VACIA)
                        return
                    elif tipo == "response.incomplete":
                        raise _error_incompleta(evento, token)
                    elif tipo in ("response.failed", "error"):
                        raise _error_evento(evento, token)
        except _ERRORES_DE_RED as exc:
            if respondio or (abierto and not _tardo(exc)):
                # Fragments already sent stay sent.
                raise ErrorLLM(_PERDIDA) from exc
            raise _error_red(exc) from exc
        # The stream ended without ``response.completed``: the answer is not whole.
        raise ErrorLLM(_PERDIDA)

    def listar_modelos(self) -> list[tuple[str, str]]:
        """``(slug, display_name)`` of the models the plan lists, in server order."""
        token = self._obtener_token()
        try:
            respuesta = self._http.get(URL_MODELOS, headers=_cabeceras(token), timeout=TIMEOUT_MODELOS)
        except _ERRORES_DE_RED as exc:
            raise _error_red(exc) from exc
        if respuesta.status_code >= 400:
            raise _error_http(respuesta, token)
        try:
            modelos = respuesta.json()["models"]
            if not isinstance(modelos, list):
                raise TypeError
        except (ValueError, KeyError, TypeError) as exc:
            raise ErrorLLM(_ERROR.format("respuesta inesperada al listar los modelos")) from exc
        return [
            (m["slug"], m.get("display_name") or m["slug"])
            for m in modelos
            if isinstance(m, dict) and m.get("visibility") == "list" and isinstance(m.get("slug"), str)
        ]


def _cabeceras(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _eventos(lineas: Iterator[str]) -> Iterator[dict[str, Any]]:
    """JSON payloads of a Server-Sent Events stream, in order."""
    datos: list[str] = []
    for linea in lineas:
        if linea == "":
            yield from _payload(datos)
            datos = []
        elif linea.startswith("data:"):
            datos.append(linea[5:].removeprefix(" "))
        # ``event:``, ``id:`` and comment lines are not needed: the payload has its type.
    yield from _payload(datos)


def _payload(datos: list[str]) -> Iterator[dict[str, Any]]:
    if not datos:
        return
    texto = "\n".join(datos)
    if texto == "[DONE]":
        return
    try:
        evento = json.loads(texto)
    except ValueError:
        return
    if isinstance(evento, dict):
        yield evento


def _tardo(exc: Exception) -> bool:
    """A read/write/pool timeout: OpenAI was reached but answered too slowly."""
    return isinstance(exc, _TIMEOUTS) and not isinstance(exc, _TIMEOUTS_DE_CONEXION)


def _error_red(exc: Exception) -> ErrorLLM:
    return ErrorLLM(_TARDO if _tardo(exc) else _SIN_CONEXION)


def _error_http(respuesta: Any, token: str) -> ErrorLLM:
    try:
        cuerpo = respuesta.json()
    except ValueError:
        cuerpo = None
    error = cuerpo.get("error") if isinstance(cuerpo, dict) else None
    error = error if isinstance(error, dict) else {}
    codigo = _codigo(error)
    if codigo in _POR_CODIGO:
        return ErrorLLM(_POR_CODIGO[codigo])
    if respuesta.status_code == 401:
        return ErrorLLM(_SESION)
    if respuesta.status_code == 503:
        return ErrorLLM(_NO_DISPONIBLE)
    detalle = error.get("message")
    if not (isinstance(detalle, str) and detalle) and isinstance(cuerpo, dict):
        detalle = cuerpo.get("detail")
    if not (isinstance(detalle, str) and detalle):
        detalle = f"HTTP {respuesta.status_code}"
    return ErrorLLM(_ERROR.format(_enmascarar(detalle, token)))


def _error_evento(evento: dict[str, Any], token: str) -> ErrorLLM:
    """Error of a ``response.failed`` or ``error`` event."""
    respuesta = evento.get("response")
    error = respuesta.get("error") if isinstance(respuesta, dict) else None
    if not isinstance(error, dict):
        error = evento.get("error") if isinstance(evento.get("error"), dict) else evento
    codigo = _codigo(error)
    if codigo in _POR_CODIGO:
        return ErrorLLM(_POR_CODIGO[codigo])
    if codigo:
        return ErrorLLM(_ERROR.format(_enmascarar(codigo, token)))
    mensaje = error.get("message")
    detalle = mensaje if isinstance(mensaje, str) and mensaje else "la respuesta falló"
    return ErrorLLM(_ERROR.format(_enmascarar(detalle, token)))


def _codigo(error: dict[str, Any]) -> str:
    """The error code; anything but text (a list, an object) counts as unknown."""
    codigo = error.get("code")
    return codigo if isinstance(codigo, str) else ""


def _error_incompleta(evento: dict[str, Any], token: str) -> ErrorLLM:
    """Error of a ``response.incomplete`` event, with its reason when it has one."""
    respuesta = evento.get("response")
    detalles = respuesta.get("incomplete_details") if isinstance(respuesta, dict) else None
    razon = detalles.get("reason") if isinstance(detalles, dict) else None
    sufijo = f" ({_enmascarar(razon, token)})" if isinstance(razon, str) and razon else ""
    return ErrorLLM(_INCOMPLETA.format(sufijo))


def _enmascarar(texto: str, token: str) -> str:
    return texto.replace(token, "***") if token else texto
