"""User data folder and app settings."""

import os
import sys
from pathlib import Path

NOMBRE_APP = "Eva"


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
