"""User data folder and app settings."""

import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

NOMBRE_APP = "Eva"


@dataclass(frozen=True)
class Proveedor:
    nombre: str
    url: str


# Local model servers with an OpenAI-compatible API (cloud providers: Phase 2C).
PROVEEDORES = {
    "lmstudio": Proveedor("LM Studio", "http://localhost:1234/v1"),
    "ollama": Proveedor("Ollama", "http://localhost:11434/v1"),
}
PROVEEDOR_POR_DEFECTO = "lmstudio"


@dataclass
class Config:
    proveedor: str = PROVEEDOR_POR_DEFECTO
    modelo: str = ""  # empty: use the first model the server lists
    # Keys this version does not know, kept so saving never loses them.
    extras: dict[str, Any] = field(default_factory=dict)

    @property
    def servidor(self) -> str:
        return PROVEEDORES[self.proveedor].url


def ruta_config() -> Path:
    return carpeta_datos() / "config.json"


def cargar_config(ruta: Path | None = None) -> Config:
    """Read config.json; a missing or unreadable file gives the defaults."""
    ruta = ruta or ruta_config()
    try:
        datos = json.loads(ruta.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return Config()
    if not isinstance(datos, dict):
        return Config()
    proveedor = datos.pop("proveedor", PROVEEDOR_POR_DEFECTO)
    modelo = datos.pop("modelo", "")
    return Config(
        proveedor=proveedor if proveedor in PROVEEDORES else PROVEEDOR_POR_DEFECTO,
        modelo=modelo if isinstance(modelo, str) else "",
        extras=datos,
    )


def guardar_config(config: Config, ruta: Path | None = None) -> None:
    """Write config.json atomically (a crash never leaves a half-written file)."""
    ruta = ruta or ruta_config()
    datos = {"proveedor": config.proveedor, "modelo": config.modelo, **config.extras}
    temporal = ruta.with_suffix(".json.tmp")
    temporal.write_text(json.dumps(datos, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporal, ruta)


def resolver_modelo(guardado: str, disponibles: list[str]) -> str:
    """The saved model if it is still installed, else the first one available."""
    if guardado in disponibles:
        return guardado
    return disponibles[0] if disponibles else ""


def carpeta_datos() -> Path:
    """Per-user folder for Eva's data (history, settings), created if missing.

    ``EVA_DATA_DIR`` overrides the location (useful for tests and Docker).
    """
    if destino := os.environ.get("EVA_DATA_DIR"):
        carpeta = Path(destino)
    elif sys.platform == "win32":
        carpeta = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming") / NOMBRE_APP
    elif sys.platform == "darwin":
        carpeta = Path.home() / "Library" / "Application Support" / NOMBRE_APP
    else:
        base = os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share"
        carpeta = Path(base) / NOMBRE_APP.lower()
    carpeta.mkdir(parents=True, exist_ok=True)
    return carpeta
