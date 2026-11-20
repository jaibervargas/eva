"""Prepare Eva's local services and speech models.

Run with ``python -m app.setup_inicial`` when installing Eva from source. The
functions accept injectable clients and models so setup can be tested without
network access or downloading hundreds of megabytes.
"""

import argparse
import logging
from collections.abc import Callable
from typing import Any

import httpx

from app.config import PROVEEDORES, cargar_config
from app.voice.stt import Transcriptor
from app.voice.tts import Sintetizador, VOZ_POR_DEFECTO

log = logging.getLogger(__name__)


def servidor_disponible(url: str, cliente: Any | None = None) -> bool:
    """Return whether a local OpenAI-compatible server answers ``/models``."""
    propio = cliente is None
    cliente = cliente or httpx.Client(timeout=3.0, follow_redirects=True)
    try:
        respuesta = cliente.get(url.rstrip("/") + "/models")
        respuesta.raise_for_status()
        return True
    except (OSError, httpx.HTTPError):
        return False
    finally:
        if propio:
            cliente.close()


def preparar_modelos(
    sintetizador: Any,
    transcriptor: Any,
    progreso: Callable[[str], None] | None = None,
) -> dict[str, bool]:
    """Download missing speech models and return which downloads occurred."""
    progreso = progreso or (lambda mensaje: None)
    resultados = {"voz": False, "transcripcion": False}
    if sintetizador.necesita_descarga():
        progreso("Descargando la voz de Eva…")
        sintetizador.cargar()
        resultados["voz"] = True
    if transcriptor.necesita_descarga():
        progreso("Descargando el modelo de voz…")
        transcriptor.cargar()
        resultados["transcripcion"] = True
    return resultados


def preparar(
    proveedor: str,
    sintetizador: Any | None = None,
    transcriptor: Any | None = None,
    progreso: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Check the configured local server and prepare both speech models."""
    config = cargar_config()
    datos = PROVEEDORES.get(proveedor, PROVEEDORES[config.proveedor])
    disponible = servidor_disponible(datos.url) if datos.url else None
    sintetizador = sintetizador or Sintetizador(VOZ_POR_DEFECTO)
    transcriptor = transcriptor or Transcriptor()
    modelos = preparar_modelos(sintetizador, transcriptor, progreso)
    return {"servidor": disponible, **modelos}


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepara Eva para su primer uso.")
    parser.add_argument("--proveedor", choices=tuple(PROVEEDORES), default=None)
    args = parser.parse_args()
    config = cargar_config()
    proveedor = args.proveedor or config.proveedor
    print("Preparando Eva…")
    resultado = preparar(proveedor, progreso=lambda mensaje: print(mensaje))
    if resultado["servidor"] is False:
        print("Aviso: el servidor local no está disponible; puedes abrirlo después desde Ajustes.")
    print("Eva está lista.")
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    raise SystemExit(main())
