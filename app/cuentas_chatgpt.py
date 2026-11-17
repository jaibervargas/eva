"""Saved ChatGPT accounts, kept in one Fernet-encrypted file (Fase 2D, S2).

The file ``carpeta_datos()/chatgpt.bin`` holds every account (registration and
tokens) plus Eva's host id. Its encryption key lives in the OS keyring (service
"Eva", user name ``chatgpt-cifrado``), never in the data folder. A keyring
failure raises ``ErrorSecretos`` (fail closed); a file that cannot be decrypted
or parsed raises ``ErrorCuentas`` and is never overwritten or deleted.
"""

import contextlib
import dataclasses
import hashlib
import json
import os
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from cryptography.fernet import Fernet, InvalidToken

from app import secretos
from app.config import carpeta_datos

USUARIO_CLAVE = "chatgpt-cifrado"
NOMBRE_ARCHIVO = "chatgpt.bin"
ALCANCE_PLAN = "chatgpt.tokens.use.direct"
MENSAJE_ILEGIBLE = "No pude leer tus cuentas de ChatGPT guardadas."

# One lock for every store instance: they all share the same file and key.
_CANDADO = threading.RLock()


class ErrorCuentas(Exception):
    """The saved accounts file cannot be read, with a message for the user."""


class TokensNuevos(Protocol):
    """What ``actualizar_tokens`` needs (``auth_chatgpt.Tokens`` fits)."""

    access_token: str
    refresh_token: str
    expira: float
    scopes: list[str]


def id_cuenta(subject: str, client_id: str) -> str:
    """Stable short id for an account: same subject and client id, same id."""
    return hashlib.sha256(f"{subject}\0{client_id}".encode()).hexdigest()[:16]


@dataclass
class CuentaChatGPT:
    email: str
    subject: str
    client_id: str
    id_token: str = ""
    access_token: str = ""
    refresh_token: str = ""
    expira: float = 0
    scopes: list[str] = field(default_factory=list)
    # When the account was last saved (epoch seconds); set by ``guardar``.
    guardada: float = field(default=0.0, compare=False)
    id: str = ""

    def __post_init__(self) -> None:
        if not self.id:
            self.id = id_cuenta(self.subject, self.client_id)

    @property
    def plan_habilitado(self) -> bool:
        return ALCANCE_PLAN in self.scopes

    @property
    def conectada(self) -> bool:
        return bool(self.access_token and self.refresh_token)


_CAMPOS = tuple(f.name for f in dataclasses.fields(CuentaChatGPT))


class AlmacenCuentas:
    """Encrypted store of ChatGPT accounts and Eva's host id."""

    def __init__(self, ruta: Path | None = None) -> None:
        self._ruta = ruta

    @property
    def ruta(self) -> Path:
        return self._ruta or carpeta_datos() / NOMBRE_ARCHIVO

    # --- public API ---------------------------------------------------------

    def host_id(self) -> str:
        """Eva's ``urn:uuid:`` host id, created once and persisted."""
        with _CANDADO:
            datos = self._leer()
            if not datos["host_id"]:
                datos["host_id"] = f"urn:uuid:{uuid.uuid4()}"
                self._escribir(datos)
            return datos["host_id"]

    def listar(self) -> list[CuentaChatGPT]:
        with _CANDADO:
            return list(self._leer()["cuentas"])

    def obtener(self, id: str) -> CuentaChatGPT | None:
        with _CANDADO:
            return next((c for c in self._leer()["cuentas"] if c.id == id), None)

    def guardar(self, cuenta: CuentaChatGPT) -> None:
        """Insert the account, or replace the saved one with the same id."""
        with _CANDADO:
            datos = self._leer()
            nueva = dataclasses.replace(cuenta, scopes=list(cuenta.scopes), guardada=time.time())
            cuentas = datos["cuentas"]
            for i, existente in enumerate(cuentas):
                if existente.id == nueva.id:
                    cuentas[i] = nueva
                    break
            else:
                cuentas.append(nueva)
            self._escribir(datos)

    def actualizar_tokens(self, id: str, tokens: TokensNuevos) -> None:
        """Replace access, refresh, expiry and scopes together.

        Raises ``KeyError`` if there is no account with ``id``.
        """
        with _CANDADO:
            datos = self._leer()
            cuenta = self._buscar(datos, id)
            if cuenta is None:
                raise KeyError(id)
            cuenta.access_token = tokens.access_token
            cuenta.refresh_token = tokens.refresh_token
            cuenta.expira = tokens.expira
            cuenta.scopes = list(tokens.scopes)
            self._escribir(datos)

    def olvidar_tokens(self, id: str) -> None:
        """Sign out: drop the tokens, keep the registration and the host id.

        An unknown id is not an error.
        """
        with _CANDADO:
            datos = self._leer()
            cuenta = self._buscar(datos, id)
            if cuenta is None:
                return
            cuenta.id_token = cuenta.access_token = cuenta.refresh_token = ""
            cuenta.expira = 0
            cuenta.scopes = []
            self._escribir(datos)

    def borrar(self, id: str) -> None:
        """Remove the account; an unknown id is not an error."""
        with _CANDADO:
            datos = self._leer()
            restantes = [c for c in datos["cuentas"] if c.id != id]
            if len(restantes) == len(datos["cuentas"]):
                return
            datos["cuentas"] = restantes
            self._escribir(datos)

    # --- file and key -------------------------------------------------------

    @staticmethod
    def _buscar(datos: dict[str, Any], id: str) -> CuentaChatGPT | None:
        return next((c for c in datos["cuentas"] if c.id == id), None)

    def _leer(self) -> dict[str, Any]:
        """The decrypted contents; an empty store when there is no file yet."""
        ruta = self.ruta
        try:
            cifrado = ruta.read_bytes()
        except FileNotFoundError:
            return {"host_id": "", "cuentas": []}
        except OSError:
            raise ErrorCuentas(MENSAJE_ILEGIBLE) from None
        clave = secretos.leer_clave(USUARIO_CLAVE)
        if clave is None:
            raise ErrorCuentas(MENSAJE_ILEGIBLE)
        try:
            crudo = json.loads(Fernet(clave.encode()).decrypt(cifrado))
            return {
                "host_id": _texto(crudo.get("host_id", "")),
                "cuentas": [_cuenta_desde(c) for c in crudo["cuentas"]],
            }
        except (InvalidToken, ValueError, TypeError, KeyError, AttributeError):
            # ``from None``: the parse error could quote decrypted secrets.
            raise ErrorCuentas(MENSAJE_ILEGIBLE) from None

    def _escribir(self, datos: dict[str, Any]) -> None:
        """Encrypt and write atomically; the previous file survives any failure."""
        clave = secretos.leer_clave(USUARIO_CLAVE)
        if clave is None:
            clave = Fernet.generate_key().decode()
            secretos.guardar_clave(USUARIO_CLAVE, clave)
        try:
            fernet = Fernet(clave.encode())
        except (ValueError, TypeError):
            raise ErrorCuentas(MENSAJE_ILEGIBLE) from None
        contenido = {
            "version": 1,
            "host_id": datos["host_id"],
            "cuentas": [{n: getattr(c, n) for n in _CAMPOS} for c in datos["cuentas"]],
        }
        cifrado = fernet.encrypt(json.dumps(contenido, ensure_ascii=False).encode("utf-8"))
        ruta = self.ruta
        temporal = ruta.with_name(ruta.name + ".tmp")
        try:
            descriptor = os.open(temporal, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_BINARY", 0), 0o600)
            with os.fdopen(descriptor, "wb") as salida:
                salida.write(cifrado)
            if sys.platform != "win32":
                os.chmod(temporal, 0o600)
            os.replace(temporal, ruta)
        except OSError:
            with contextlib.suppress(OSError):
                temporal.unlink(missing_ok=True)
            raise


def _texto(valor: Any) -> str:
    if not isinstance(valor, str):
        raise TypeError("expected text")
    return valor


def _cuenta_desde(crudo: dict[str, Any]) -> CuentaChatGPT:
    cuenta = CuentaChatGPT(**{n: crudo[n] for n in _CAMPOS if n in crudo})
    if not isinstance(cuenta.scopes, list):
        raise TypeError("expected a list of scopes")
    return cuenta
