"""API keys and account tokens kept in the OS secure store (via ``keyring``).

Keys never go to ``config.json``, logs or error messages. Each key is stored
under the service "Eva" with the provider id as the user name.
"""

import keyring

SERVICIO = "Eva"


class ErrorSecretos(Exception):
    """The OS secure store failed, with a message meant to be shown to the user."""


def leer_clave(proveedor: str) -> str | None:
    """The saved key for ``proveedor``, or ``None`` if there is none.

    Raises ``ErrorSecretos`` if the secure store fails (no other source is tried).
    """
    try:
        clave = keyring.get_password(SERVICIO, proveedor)
    except Exception as exc:
        raise _error(exc) from None
    return clave or None


def guardar_clave(proveedor: str, clave: str) -> None:
    """Save (or replace) the key for ``proveedor``. Raises ``ErrorSecretos``."""
    try:
        keyring.set_password(SERVICIO, proveedor, clave)
    except Exception as exc:
        raise _error(exc, clave) from None


def borrar_clave(proveedor: str) -> None:
    """Delete the key for ``proveedor``; a missing key is not an error.

    Raises ``ErrorSecretos`` if the secure store fails.
    """
    # Backends report "not found" and real failures with the same exception,
    # so check first to tell them apart.
    if leer_clave(proveedor) is None:
        return
    try:
        keyring.delete_password(SERVICIO, proveedor)
    except Exception as exc:
        raise _error(exc) from None


def _error(exc: Exception, clave: str | None = None) -> ErrorSecretos:
    # ``from None`` at the call sites keeps the original exception (which may
    # quote the key) out of tracebacks and logs.
    detalle = str(exc) or type(exc).__name__
    if clave:
        detalle = detalle.replace(clave, "***")
    return ErrorSecretos(f"No pude usar el almacén seguro del sistema: {detalle}")
