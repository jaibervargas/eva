"""Use a saved ChatGPT account from the chat (Fase 2D, S4).

``proveedor_token`` hands ``ClienteChatGPT`` a valid access token, refreshing
it shortly before it expires (one refresh at a time per account) and saving
the rotated tokens; ``cerrar_sesion_cuenta`` signs out under the same lock.
``conectar_chatgpt`` builds the chat client and ``cliente_respaldo`` the
memory-extraction fallback. Every failure reaches the caller as an
``ErrorLLM`` with a message for the user, never with a token.
"""

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx

from app.auth_chatgpt import ErrorAuth, ErrorSesionVencida, Tokens, renovar
from app.config import resolver_modelo
from app.cuentas_chatgpt import ALCANCE_PLAN, AlmacenCuentas, ErrorCuentas
from app.llm.base import ErrorLLM
from app.llm.chatgpt_client import ClienteChatGPT
from app.secretos import ErrorSecretos

log = logging.getLogger(__name__)

__all__ = [
    "ErrorSesionVencida",
    "MENSAJE_INICIA_SESION",
    "MENSAJE_PLAN",
    "MENSAJE_SIN_MODELOS",
    "Tokens",
    "cerrar_sesion_cuenta",
    "cliente_respaldo",
    "conectar_chatgpt",
    "listar_modelos",
    "proveedor_token",
]

MENSAJE_INICIA_SESION = "Inicia sesión con ChatGPT en Ajustes."
MENSAJE_PLAN = "Tu cuenta inició sesión, pero no autorizó usar tu plan de ChatGPT en Eva."
MENSAJE_SIN_MODELOS = "Tu cuenta de ChatGPT no tiene modelos disponibles."

# Refresh when the access token expires within this many seconds.
MARGEN_RENOVACION = 120

# One lock per account id, shared by every token provider of that account:
# OpenAI rotates refresh tokens, so two refreshes at once would burn one.
_CANDADOS: dict[str, threading.Lock] = {}
_CANDADO_CANDADOS = threading.Lock()


def _candado(cuenta_id: str) -> threading.Lock:
    with _CANDADO_CANDADOS:
        return _CANDADOS.setdefault(cuenta_id, threading.Lock())


@dataclass
class _SinGuardar:
    """Rotated tokens the store could not save yet.

    ``previo`` is the refresh token the store still holds; when it changes
    (sign-out, a new sign-in) these tokens are stale and are dropped.
    """

    tokens: Tokens
    previo: str


# Keyed by (store file, account id); read and written with the account's lock held.
_SIN_GUARDAR: dict[tuple[str, str], _SinGuardar] = {}


def _clave(almacen: AlmacenCuentas, cuenta_id: str) -> tuple[str, str]:
    return str(almacen.ruta), cuenta_id


def _guardar_tokens(almacen: AlmacenCuentas, cuenta_id: str, tokens: Tokens, previo: str) -> None:
    """Save rotated tokens; when the store fails keep them in memory and retry later.

    The old refresh token is already spent, so losing the new ones would sign
    the user out. Raises ``ErrorLLM`` if the account was removed meanwhile.
    """
    clave = _clave(almacen, cuenta_id)
    try:
        almacen.actualizar_tokens(cuenta_id, tokens)
    except KeyError:  # the account was removed meanwhile
        _SIN_GUARDAR.pop(clave, None)
        raise ErrorLLM(MENSAJE_INICIA_SESION) from None
    except (ErrorCuentas, ErrorSecretos) as exc:
        log.warning("Could not save the refreshed ChatGPT tokens; keeping them in memory: %s", exc)
        _SIN_GUARDAR[clave] = _SinGuardar(tokens, previo)
    else:
        _SIN_GUARDAR.pop(clave, None)


def proveedor_token(
    almacen: AlmacenCuentas, cuenta_id: str, reloj: Callable[[], float] = time.time
) -> Callable[[], str]:
    """A function returning a valid access token of the saved account ``cuenta_id``.

    It raises ``ErrorLLM`` when the account is missing or signed out, has not
    allowed plan use, its session expired (the tokens are then forgotten) or
    the refresh, the store or the keyring fails.
    """

    def obtener_token() -> str:
        with _candado(cuenta_id):
            try:
                return _token_vigente(almacen, cuenta_id, reloj)
            except ErrorSesionVencida as exc:
                mensaje = str(exc)
                _SIN_GUARDAR.pop(_clave(almacen, cuenta_id), None)
                try:
                    almacen.olvidar_tokens(cuenta_id)
                except (ErrorCuentas, ErrorSecretos) as error:
                    raise ErrorLLM(str(error)) from None
                raise ErrorLLM(mensaje) from None
            except (ErrorAuth, ErrorCuentas, ErrorSecretos) as exc:
                # ``from None``: the cause must not carry tokens into logs.
                raise ErrorLLM(str(exc)) from None

    return obtener_token


def _token_vigente(almacen: AlmacenCuentas, cuenta_id: str, reloj: Callable[[], float]) -> str:
    """Called with the account's lock held, so a waiting caller sees the new tokens."""
    cuenta = almacen.obtener(cuenta_id)
    en_disco = cuenta.refresh_token if cuenta is not None else ""
    clave = _clave(almacen, cuenta_id)
    pendiente = _SIN_GUARDAR.get(clave)
    if pendiente is not None:
        if cuenta is None or not en_disco or en_disco != pendiente.previo:
            # Signed out or signed in again since: the unsaved tokens are stale.
            _SIN_GUARDAR.pop(clave, None)
        else:
            _guardar_tokens(almacen, cuenta_id, pendiente.tokens, pendiente.previo)
            cuenta.access_token = pendiente.tokens.access_token
            cuenta.refresh_token = pendiente.tokens.refresh_token
            cuenta.expira = pendiente.tokens.expira
            cuenta.scopes = list(pendiente.tokens.scopes)
    if cuenta is None or not cuenta.conectada:
        raise ErrorLLM(MENSAJE_INICIA_SESION)
    if not cuenta.plan_habilitado:
        raise ErrorLLM(MENSAJE_PLAN)
    if cuenta.expira - reloj() > MARGEN_RENOVACION:
        return cuenta.access_token
    tokens = renovar(cuenta.client_id, cuenta.refresh_token)
    _guardar_tokens(almacen, cuenta_id, tokens, en_disco)
    if ALCANCE_PLAN not in tokens.scopes:
        raise ErrorLLM(MENSAJE_PLAN)
    return tokens.access_token


def cerrar_sesion_cuenta(
    almacen: AlmacenCuentas, cuenta_id: str, revocar: Callable[[str, str], bool]
) -> bool:
    """Sign the account out: revoke its newest refresh token, then forget the tokens.

    Holds the account's refresh lock, so a refresh in flight finishes first
    and none can write tokens back afterwards. ``revocar`` is
    ``auth_chatgpt.cerrar_sesion``; if it raises, the revocation counts as
    unconfirmed. The local tokens are always forgotten. Returns ``True`` only
    when OpenAI confirmed the revocation (or there was nothing to revoke).
    Raises ``ErrorCuentas``/``ErrorSecretos`` if the store fails.
    """
    with _candado(cuenta_id):
        pendiente = _SIN_GUARDAR.pop(_clave(almacen, cuenta_id), None)
        confirmado = True
        try:
            cuenta = almacen.obtener(cuenta_id)
            refresh = cuenta.refresh_token if cuenta is not None else ""
            if pendiente is not None and refresh and refresh == pendiente.previo:
                refresh = pendiente.tokens.refresh_token  # newer than the saved one
            if cuenta is not None and refresh:
                try:
                    confirmado = bool(revocar(cuenta.client_id, refresh))
                except Exception as exc:
                    # Only the type: the error could quote what was sent.
                    log.warning("Could not revoke the ChatGPT session: %s", type(exc).__name__)
                    confirmado = False
        finally:
            almacen.olvidar_tokens(cuenta_id)
        return confirmado


def _cuenta_conectada(almacen: AlmacenCuentas, cuenta_id: Any) -> bool:
    if not (isinstance(cuenta_id, str) and cuenta_id):
        return False
    try:
        cuenta = almacen.obtener(cuenta_id)
    except (ErrorCuentas, ErrorSecretos) as exc:
        raise ErrorLLM(str(exc)) from None
    return cuenta is not None and cuenta.conectada


def listar_modelos(
    cuenta_id: str, almacen: AlmacenCuentas | None = None, http_client: Any = None
) -> list[tuple[str, str]]:
    """``(slug, display_name)`` of the models the account's plan offers. Raises ``ErrorLLM``."""
    almacen = almacen or AlmacenCuentas()
    if http_client is not None:
        return ClienteChatGPT("", proveedor_token(almacen, cuenta_id), http_client=http_client).listar_modelos()
    with httpx.Client() as cliente_http:
        return ClienteChatGPT("", proveedor_token(almacen, cuenta_id), http_client=cliente_http).listar_modelos()


def conectar_chatgpt(
    cuenta_id: str | None,
    modelo_guardado: str,
    almacen: AlmacenCuentas | None = None,
    http_client: Any = None,
) -> ClienteChatGPT:
    """Chat client for the saved account, on the saved model or else the first one.

    The client also gets ``nombre_modelo``, the model's display name. Raises
    ``ErrorLLM`` when there is no usable account or the plan offers no models.
    """
    almacen = almacen or AlmacenCuentas()
    if not (isinstance(cuenta_id, str) and cuenta_id):
        raise ErrorLLM(MENSAJE_INICIA_SESION)
    cliente = ClienteChatGPT("", proveedor_token(almacen, cuenta_id), http_client=http_client)
    modelos = cliente.listar_modelos()
    if not modelos:
        raise ErrorLLM(MENSAJE_SIN_MODELOS)
    nombres = dict(modelos)
    cliente.modelo = resolver_modelo(modelo_guardado, list(nombres))
    cliente.nombre_modelo = nombres[cliente.modelo]
    return cliente


def cliente_respaldo(
    cuenta_id: Any, modelo: Any, almacen: AlmacenCuentas | None = None
) -> ClienteChatGPT | None:
    """Client for memory extraction with the saved account, or ``None`` when the
    account is missing or signed out or no model was saved. Raises ``ErrorLLM``
    if the saved accounts cannot be read."""
    almacen = almacen or AlmacenCuentas()
    if not (isinstance(modelo, str) and modelo) or not _cuenta_conectada(almacen, cuenta_id):
        return None
    return ClienteChatGPT(modelo, proveedor_token(almacen, cuenta_id))
