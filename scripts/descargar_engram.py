"""Download the pinned Engram release into ``bin/``.

Usage::

    python scripts/descargar_engram.py                      # this computer
    python scripts/descargar_engram.py --plataforma linux-arm64

The archive's SHA-256 is checked against the release's ``checksums.txt``
before anything is written; only the ``engram``/``engram.exe`` binary is
extracted. ``bin/`` is git-ignored.
"""

import argparse
import hashlib
import io
import os
import platform
import sys
import tarfile
import urllib.error
import urllib.request
import zipfile
from collections.abc import Callable
from pathlib import Path

VERSION = "3.7.2"
URL_BASE = f"https://github.com/Gentleman-Programming/engram/releases/download/v{VERSION}/"
BIN = Path(__file__).resolve().parents[1] / "bin"

SISTEMAS = ("windows", "linux", "darwin")
ARQUITECTURAS = ("amd64", "arm64")
_ARQ_MAQUINA = {"amd64": "amd64", "x86_64": "amd64", "x64": "amd64", "arm64": "arm64", "aarch64": "arm64"}


class ErrorDescarga(Exception):
    """The binary could not be downloaded, verified or extracted."""


def nombre_asset(sistema: str, arquitectura: str) -> str:
    if sistema not in SISTEMAS or arquitectura not in ARQUITECTURAS:
        raise ErrorDescarga(f"Plataforma no soportada: {sistema}-{arquitectura}")
    extension = "zip" if sistema == "windows" else "tar.gz"
    return f"engram_{VERSION}_{sistema}_{arquitectura}.{extension}"


def plataforma_actual() -> tuple[str, str]:
    if sys.platform == "win32":
        sistema = "windows"
    elif sys.platform == "darwin":
        sistema = "darwin"
    elif sys.platform.startswith("linux"):
        sistema = "linux"
    else:
        sistema = sys.platform
    maquina = platform.machine().lower()
    return sistema, _ARQ_MAQUINA.get(maquina, maquina)


def obtener_url(url: str) -> bytes:
    try:
        with urllib.request.urlopen(url, timeout=60) as respuesta:
            return respuesta.read()
    except (urllib.error.URLError, OSError) as exc:
        raise ErrorDescarga(f"No se pudo descargar {url}: {exc}") from exc


def _suma_esperada(checksums: str, asset: str) -> str:
    for linea in checksums.splitlines():
        partes = linea.split()
        if len(partes) == 2 and partes[1].lstrip("*") == asset:
            return partes[0].lower()
    raise ErrorDescarga(f"checksums.txt no incluye {asset}")


def _extraer(datos: bytes, asset: str, nombre: str) -> bytes:
    """Return the bytes of the top-level ``nombre`` member of the archive."""
    try:
        if asset.endswith(".zip"):
            with zipfile.ZipFile(io.BytesIO(datos)) as archivo:
                if nombre in archivo.namelist():
                    return archivo.read(nombre)
        else:
            with tarfile.open(fileobj=io.BytesIO(datos), mode="r:gz") as archivo:
                for miembro in archivo.getmembers():
                    if miembro.name in (nombre, f"./{nombre}") and miembro.isfile():
                        return archivo.extractfile(miembro).read()
    except (zipfile.BadZipFile, tarfile.TarError, OSError) as exc:
        raise ErrorDescarga(f"El archivo {asset} está dañado: {exc}") from exc
    raise ErrorDescarga(f"El archivo {asset} no contiene {nombre}")


def descargar(
    plataforma: tuple[str, str] | None = None,
    destino: Path = BIN,
    obtener: Callable[[str], bytes] = obtener_url,
) -> Path:
    """Download, verify and extract the binary; return its path in ``destino``."""
    sistema, arquitectura = plataforma or plataforma_actual()
    asset = nombre_asset(sistema, arquitectura)
    esperada = _suma_esperada(obtener(URL_BASE + "checksums.txt").decode("utf-8", "replace"), asset)
    datos = obtener(URL_BASE + asset)
    if hashlib.sha256(datos).hexdigest() != esperada:
        raise ErrorDescarga(f"La suma SHA-256 de {asset} no coincide con checksums.txt")
    nombre = "engram.exe" if sistema == "windows" else "engram"
    binario = _extraer(datos, asset, nombre)

    destino.mkdir(parents=True, exist_ok=True)
    ruta = destino / nombre
    temporal = destino / f"{nombre}.descarga"
    try:
        temporal.write_bytes(binario)
        if sistema != "windows":
            os.chmod(temporal, 0o755)
        os.replace(temporal, ruta)
    except OSError as exc:
        temporal.unlink(missing_ok=True)
        raise ErrorDescarga(f"No se pudo guardar {ruta}: {exc}") from exc
    return ruta


def _leer_plataforma(texto: str) -> tuple[str, str]:
    sistema, separador, arquitectura = texto.partition("-")
    if not separador:
        raise ErrorDescarga(
            f"Plataforma no soportada: {texto} (usa sistema-arquitectura, p. ej. windows-amd64)"
        )
    nombre_asset(sistema, arquitectura)  # validates
    return sistema, arquitectura


def main(argv: list[str] | None = None, obtener: Callable[[str], bytes] = obtener_url) -> int:
    parser = argparse.ArgumentParser(description=f"Descarga Engram {VERSION} en bin/.")
    parser.add_argument(
        "--plataforma",
        help="sistema-arquitectura de otro equipo, p. ej. linux-arm64 (por defecto: este equipo)",
    )
    parser.add_argument("--destino", type=Path, default=BIN, help="carpeta de destino (por defecto: bin/)")
    args = parser.parse_args(argv)
    try:
        plataforma = _leer_plataforma(args.plataforma) if args.plataforma else plataforma_actual()
        ruta = descargar(plataforma, args.destino, obtener)
    except ErrorDescarga as exc:
        print(f"No se pudo descargar Engram: {exc}", file=sys.stderr)
        return 1
    print(f"Engram {VERSION} ({plataforma[0]}-{plataforma[1]}) guardado en {ruta}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
