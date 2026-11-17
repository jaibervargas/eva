"""Tests for scripts/descargar_engram.py (pinned Engram release into bin/).

A fake ``obtener(url) -> bytes`` stands in for GitHub, so no network is used.
"""

import hashlib
import importlib.util
import io
import os
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest

_RUTA = Path(__file__).resolve().parents[1] / "scripts" / "descargar_engram.py"
_spec = importlib.util.spec_from_file_location("descargar_engram", _RUTA)
de = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(de)

BASE = "https://github.com/Gentleman-Programming/engram/releases/download/v3.7.2/"


def _zip(miembros: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for nombre, datos in miembros.items():
            z.writestr(nombre, datos)
    return buf.getvalue()


def _tgz(miembros: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as t:
        for nombre, datos in miembros.items():
            info = tarfile.TarInfo(nombre)
            info.size = len(datos)
            info.mode = 0o755
            t.addfile(info, io.BytesIO(datos))
    return buf.getvalue()


class FakeGitHub:
    def __init__(self, asset: str, datos: bytes, suma: str | None = None):
        suma = suma or hashlib.sha256(datos).hexdigest()
        otra = "0" * 64
        self.archivos = {
            BASE + "checksums.txt": (
                f"{otra}  engram_3.7.2_otro.zip\n{suma}  {asset}\n"
            ).encode(),
            BASE + asset: datos,
        }
        self.urls: list[str] = []

    def __call__(self, url: str) -> bytes:
        self.urls.append(url)
        if url not in self.archivos:
            raise de.ErrorDescarga(f"404 {url}")
        return self.archivos[url]


@pytest.mark.parametrize(
    ("sistema", "arq", "esperado"),
    [
        ("windows", "amd64", "engram_3.7.2_windows_amd64.zip"),
        ("windows", "arm64", "engram_3.7.2_windows_arm64.zip"),
        ("linux", "amd64", "engram_3.7.2_linux_amd64.tar.gz"),
        ("linux", "arm64", "engram_3.7.2_linux_arm64.tar.gz"),
        ("darwin", "amd64", "engram_3.7.2_darwin_amd64.tar.gz"),
        ("darwin", "arm64", "engram_3.7.2_darwin_arm64.tar.gz"),
    ],
)
def test_asset_name_per_platform(sistema, arq, esperado):
    assert de.nombre_asset(sistema, arq) == esperado


def test_unknown_platform_is_rejected():
    with pytest.raises(de.ErrorDescarga, match="Plataforma no soportada: solaris-sparc"):
        de.nombre_asset("solaris", "sparc")


@pytest.mark.parametrize(
    ("plataforma", "maquina", "esperado"),
    [
        ("win32", "AMD64", ("windows", "amd64")),
        ("win32", "ARM64", ("windows", "arm64")),
        ("linux", "x86_64", ("linux", "amd64")),
        ("linux", "aarch64", ("linux", "arm64")),
        ("darwin", "arm64", ("darwin", "arm64")),
        ("darwin", "x86_64", ("darwin", "amd64")),
    ],
)
def test_current_platform_detection(monkeypatch, plataforma, maquina, esperado):
    monkeypatch.setattr(de.sys, "platform", plataforma)
    monkeypatch.setattr(de.platform, "machine", lambda: maquina)
    assert de.plataforma_actual() == esperado


def test_downloads_verifies_and_extracts_only_the_windows_binary(tmp_path):
    asset = "engram_3.7.2_windows_amd64.zip"
    datos = _zip({"tools/cloud-sync-projects.ps1": b"script", "engram.exe": b"MZ-binario"})
    github = FakeGitHub(asset, datos)

    ruta = de.descargar(("windows", "amd64"), destino=tmp_path, obtener=github)

    assert ruta == tmp_path / "engram.exe"
    assert ruta.read_bytes() == b"MZ-binario"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["engram.exe"]
    assert github.urls == [BASE + "checksums.txt", BASE + asset]


def test_downloads_and_extracts_the_unix_binary_executable(tmp_path):
    asset = "engram_3.7.2_linux_amd64.tar.gz"
    datos = _tgz({"tools/cloud-sync-projects.sh": b"script", "engram": b"ELF-binario"})

    ruta = de.descargar(("linux", "amd64"), destino=tmp_path, obtener=FakeGitHub(asset, datos))

    assert ruta == tmp_path / "engram"
    assert ruta.read_bytes() == b"ELF-binario"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["engram"]
    if sys.platform != "win32":
        assert os.stat(ruta).st_mode & 0o111


def test_checksum_mismatch_aborts_and_writes_nothing(tmp_path):
    asset = "engram_3.7.2_windows_amd64.zip"
    datos = _zip({"engram.exe": b"MZ-binario"})
    github = FakeGitHub(asset, datos, suma="f" * 64)

    with pytest.raises(de.ErrorDescarga, match=r"La suma SHA-256 de engram_3\.7\.2_windows_amd64\.zip no coincide con checksums\.txt"):
        de.descargar(("windows", "amd64"), destino=tmp_path, obtener=github)

    assert list(tmp_path.iterdir()) == []


def test_asset_missing_from_checksums_aborts(tmp_path):
    asset = "engram_3.7.2_windows_amd64.zip"
    github = FakeGitHub(asset, _zip({"engram.exe": b"x"}))
    github.archivos[BASE + "checksums.txt"] = b"abc  otra-cosa.zip\n"

    with pytest.raises(de.ErrorDescarga, match="checksums.txt no incluye engram_3.7.2_windows_amd64.zip"):
        de.descargar(("windows", "amd64"), destino=tmp_path, obtener=github)
    assert list(tmp_path.iterdir()) == []


def test_archive_without_binary_aborts(tmp_path):
    asset = "engram_3.7.2_windows_amd64.zip"
    github = FakeGitHub(asset, _zip({"LEEME.txt": b"x"}))

    with pytest.raises(de.ErrorDescarga, match="El archivo engram_3.7.2_windows_amd64.zip no contiene engram.exe"):
        de.descargar(("windows", "amd64"), destino=tmp_path, obtener=github)
    assert list(tmp_path.iterdir()) == []


def test_existing_binary_is_replaced(tmp_path):
    (tmp_path / "engram.exe").write_bytes(b"viejo")
    asset = "engram_3.7.2_windows_amd64.zip"
    de.descargar(("windows", "amd64"), destino=tmp_path, obtener=FakeGitHub(asset, _zip({"engram.exe": b"nuevo"})))
    assert (tmp_path / "engram.exe").read_bytes() == b"nuevo"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["engram.exe"]


def test_main_success_message_and_platform_option(tmp_path, capsys):
    asset = "engram_3.7.2_darwin_arm64.tar.gz"
    github = FakeGitHub(asset, _tgz({"engram": b"bin"}))

    codigo = de.main(["--plataforma", "darwin-arm64", "--destino", str(tmp_path)], obtener=github)

    salida = capsys.readouterr()
    assert codigo == 0
    assert salida.out == f"Engram 3.7.2 (darwin-arm64) guardado en {tmp_path / 'engram'}\n"
    assert salida.err == ""


def test_main_failure_message(tmp_path, capsys):
    asset = "engram_3.7.2_windows_amd64.zip"
    github = FakeGitHub(asset, _zip({"engram.exe": b"x"}), suma="0" * 64)

    codigo = de.main(["--plataforma", "windows-amd64", "--destino", str(tmp_path)], obtener=github)

    salida = capsys.readouterr()
    assert codigo == 1
    assert salida.out == ""
    assert salida.err == (
        "No se pudo descargar Engram: La suma SHA-256 de engram_3.7.2_windows_amd64.zip "
        "no coincide con checksums.txt\n"
    )
    assert list(tmp_path.iterdir()) == []


def test_main_rejects_malformed_platform(tmp_path, capsys):
    codigo = de.main(["--plataforma", "windows", "--destino", str(tmp_path)], obtener=lambda url: b"")
    salida = capsys.readouterr()
    assert codigo == 1
    assert salida.err == "No se pudo descargar Engram: Plataforma no soportada: windows (usa sistema-arquitectura, p. ej. windows-amd64)\n"


def test_network_errors_become_descarga_errors(monkeypatch):
    def falla(*a, **k):
        raise de.urllib.error.URLError("sin red")

    monkeypatch.setattr(de.urllib.request, "urlopen", falla)
    with pytest.raises(de.ErrorDescarga, match="No se pudo descargar https://example.invalid/x"):
        de.obtener_url("https://example.invalid/x")
