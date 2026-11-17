"""Tests for the encrypted ChatGPT account store (Fase 2D, S2).

An in-memory keyring and a temporary EVA_DATA_DIR are used, so neither the
real OS secret store nor the user's data folder is ever touched.
"""

import os
import re
from types import SimpleNamespace

import keyring.core
import pytest
from cryptography.fernet import Fernet

from app.cuentas_chatgpt import AlmacenCuentas, CuentaChatGPT, ErrorCuentas
from app.secretos import ErrorSecretos
from tests.keyring_falso import KeyringFalso, KeyringRoto

MENSAJE_ILEGIBLE = "No pude leer tus cuentas de ChatGPT guardadas."
PLAN = "chatgpt.tokens.use.direct"


@pytest.fixture
def datos(tmp_path, monkeypatch):
    carpeta = tmp_path / "datos"
    monkeypatch.setenv("EVA_DATA_DIR", str(carpeta))
    return carpeta


@pytest.fixture
def almacen_seguro(monkeypatch):
    backend = KeyringFalso()
    monkeypatch.setattr(keyring.core, "_keyring_backend", backend)
    return backend


@pytest.fixture
def roto(monkeypatch):
    backend = KeyringRoto()
    monkeypatch.setattr(keyring.core, "_keyring_backend", backend)
    return backend


def cuenta(
    subject="sub-1",
    client_id="cli-1",
    email="ana@example.com",
    access="acc-secreto-1",
    refresh="ref-secreto-1",
    id_token="idt-secreto-1",
    scopes=("openid", PLAN),
) -> CuentaChatGPT:
    return CuentaChatGPT(
        email=email,
        subject=subject,
        client_id=client_id,
        id_token=id_token,
        access_token=access,
        refresh_token=refresh,
        expira=1_700_000_000,
        scopes=list(scopes),
    )


def archivo(datos):
    return datos / "chatgpt.bin"


# --- host id -----------------------------------------------------------------


def test_host_id_is_a_urn_uuid_created_once_and_stable_across_instances(datos, almacen_seguro):
    primero = AlmacenCuentas().host_id()
    assert re.fullmatch(r"urn:uuid:[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}", primero)
    assert archivo(datos).exists()
    assert AlmacenCuentas().host_id() == primero
    assert AlmacenCuentas().host_id() == primero


def test_host_id_survives_saving_and_deleting_accounts(datos, almacen_seguro):
    almacen = AlmacenCuentas()
    host = almacen.host_id()
    c = cuenta()
    almacen.guardar(c)
    almacen.olvidar_tokens(c.id)
    almacen.borrar(c.id)
    assert AlmacenCuentas().host_id() == host


# --- ids -----------------------------------------------------------------------


def test_id_is_stable_for_same_subject_and_client_id():
    a = cuenta(access="x")
    b = cuenta(access="y", email="otro@example.com")
    assert a.id == b.id
    assert a.id and len(a.id) <= 32


def test_id_differs_when_subject_or_client_id_differs():
    base = cuenta()
    assert cuenta(subject="sub-2").id != base.id
    assert cuenta(client_id="cli-2").id != base.id


# --- CRUD ------------------------------------------------------------------------


def test_empty_store_lists_nothing(datos, almacen_seguro):
    almacen = AlmacenCuentas()
    assert almacen.listar() == []
    assert almacen.obtener("nada") is None


def test_guardar_then_listar_and_obtener_from_a_new_instance(datos, almacen_seguro):
    c = cuenta()
    AlmacenCuentas().guardar(c)
    nuevo = AlmacenCuentas()
    assert nuevo.listar() == [c]
    assert nuevo.obtener(c.id) == c


def test_guardar_replaces_by_id(datos, almacen_seguro):
    almacen = AlmacenCuentas()
    almacen.guardar(cuenta(access="viejo"))
    almacen.guardar(cuenta(access="nuevo"))
    listadas = AlmacenCuentas().listar()
    assert len(listadas) == 1
    assert listadas[0].access_token == "nuevo"


def test_same_email_twice_is_kept_as_two_accounts(datos, almacen_seguro):
    almacen = AlmacenCuentas()
    a = cuenta(subject="sub-a", client_id="cli-a")
    b = cuenta(subject="sub-b", client_id="cli-b")
    almacen.guardar(a)
    almacen.guardar(b)
    assert [x.id for x in AlmacenCuentas().listar()] == [a.id, b.id]


def test_actualizar_tokens_replaces_access_refresh_expiry_and_scopes_together(datos, almacen_seguro):
    almacen = AlmacenCuentas()
    c = cuenta()
    almacen.guardar(c)
    tokens = SimpleNamespace(access_token="acc-2", refresh_token="ref-2", expira=1_800_000_000, scopes=["openid"])
    almacen.actualizar_tokens(c.id, tokens)
    nueva = AlmacenCuentas().obtener(c.id)
    assert (nueva.access_token, nueva.refresh_token, nueva.expira, nueva.scopes) == (
        "acc-2",
        "ref-2",
        1_800_000_000,
        ["openid"],
    )
    assert (nueva.email, nueva.subject, nueva.client_id, nueva.id_token) == (
        c.email,
        c.subject,
        c.client_id,
        c.id_token,
    )
    assert nueva.plan_habilitado is False


def test_actualizar_tokens_of_unknown_account_raises_and_writes_nothing(datos, almacen_seguro):
    almacen = AlmacenCuentas()
    almacen.guardar(cuenta())
    antes = archivo(datos).read_bytes()
    tokens = SimpleNamespace(access_token="a", refresh_token="r", expira=1, scopes=[])
    with pytest.raises(KeyError):
        almacen.actualizar_tokens("desconocida", tokens)
    assert archivo(datos).read_bytes() == antes


def test_olvidar_tokens_keeps_registration_and_host_but_disconnects(datos, almacen_seguro):
    almacen = AlmacenCuentas()
    host = almacen.host_id()
    c = cuenta()
    almacen.guardar(c)
    assert c.conectada is True
    almacen.olvidar_tokens(c.id)
    olvidada = AlmacenCuentas().obtener(c.id)
    assert (olvidada.id, olvidada.client_id, olvidada.email, olvidada.subject) == (
        c.id,
        c.client_id,
        c.email,
        c.subject,
    )
    assert (olvidada.id_token, olvidada.access_token, olvidada.refresh_token) == ("", "", "")
    assert olvidada.conectada is False
    assert AlmacenCuentas().host_id() == host


def test_borrar_removes_only_that_account(datos, almacen_seguro):
    almacen = AlmacenCuentas()
    a = cuenta(subject="sub-a")
    b = cuenta(subject="sub-b")
    almacen.guardar(a)
    almacen.guardar(b)
    almacen.borrar(a.id)
    assert AlmacenCuentas().listar() == [b]
    almacen.borrar("desconocida")  # not an error
    assert AlmacenCuentas().listar() == [b]


# --- properties -------------------------------------------------------------------


def test_plan_habilitado_depends_on_the_direct_use_scope():
    assert cuenta(scopes=("openid", PLAN)).plan_habilitado is True
    assert cuenta(scopes=("openid", "email")).plan_habilitado is False


def test_conectada_requires_access_and_refresh_tokens():
    assert cuenta().conectada is True
    assert cuenta(access="").conectada is False
    assert cuenta(refresh="").conectada is False


# --- encryption and keyring ----------------------------------------------------------


def test_file_on_disk_has_no_secret_email_or_client_id_in_clear(datos, almacen_seguro):
    almacen = AlmacenCuentas()
    host = almacen.host_id()
    almacen.guardar(cuenta())
    contenido = archivo(datos).read_bytes()
    for texto in ("acc-secreto-1", "ref-secreto-1", "idt-secreto-1", "ana@example.com", "cli-1", "sub-1", host):
        assert texto.encode() not in contenido
    assert sorted(p.name for p in datos.iterdir()) == ["chatgpt.bin"]


def test_fernet_key_lives_in_keyring_and_not_in_the_data_folder(datos, almacen_seguro):
    AlmacenCuentas().guardar(cuenta())
    assert list(almacen_seguro.datos) == [("Eva", "chatgpt-cifrado")]
    clave = almacen_seguro.datos[("Eva", "chatgpt-cifrado")]
    Fernet(clave.encode())  # a valid Fernet key
    for ruta in datos.rglob("*"):
        assert clave.encode() not in ruta.read_bytes()
    # The key decrypts the file.
    Fernet(clave.encode()).decrypt(archivo(datos).read_bytes())


def test_key_is_created_once_and_reused(datos, almacen_seguro):
    AlmacenCuentas().guardar(cuenta(subject="a"))
    clave = almacen_seguro.datos[("Eva", "chatgpt-cifrado")]
    AlmacenCuentas().guardar(cuenta(subject="b"))
    assert almacen_seguro.datos[("Eva", "chatgpt-cifrado")] == clave


@pytest.mark.parametrize(
    "operacion",
    [
        lambda a: a.host_id(),
        lambda a: a.guardar(cuenta()),
    ],
    ids=["host_id", "guardar"],
)
def test_broken_keyring_raises_error_secretos_and_writes_nothing(datos, roto, operacion):
    with pytest.raises(ErrorSecretos):
        operacion(AlmacenCuentas())
    assert not archivo(datos).exists()
    assert list(datos.iterdir()) == []


def test_broken_keyring_on_read_of_existing_file_raises_error_secretos(datos, monkeypatch):
    monkeypatch.setattr(keyring.core, "_keyring_backend", KeyringFalso())
    AlmacenCuentas().guardar(cuenta())
    antes = archivo(datos).read_bytes()
    monkeypatch.setattr(keyring.core, "_keyring_backend", KeyringRoto())
    with pytest.raises(ErrorSecretos):
        AlmacenCuentas().listar()
    assert archivo(datos).read_bytes() == antes


# --- unreadable file -------------------------------------------------------------------


OPERACIONES = [
    lambda a: a.host_id(),
    lambda a: a.listar(),
    lambda a: a.obtener("x"),
    lambda a: a.guardar(cuenta()),
    lambda a: a.actualizar_tokens("x", SimpleNamespace(access_token="a", refresh_token="r", expira=1, scopes=[])),
    lambda a: a.olvidar_tokens("x"),
    lambda a: a.borrar("x"),
]
IDS = ["host_id", "listar", "obtener", "guardar", "actualizar_tokens", "olvidar_tokens", "borrar"]


@pytest.mark.parametrize("operacion", OPERACIONES, ids=IDS)
def test_corrupted_file_raises_error_cuentas_and_is_left_unchanged(datos, almacen_seguro, operacion):
    AlmacenCuentas().guardar(cuenta())
    archivo(datos).write_bytes(b"esto no es un archivo cifrado")
    with pytest.raises(ErrorCuentas) as exc:
        operacion(AlmacenCuentas())
    assert str(exc.value) == MENSAJE_ILEGIBLE
    assert archivo(datos).read_bytes() == b"esto no es un archivo cifrado"


@pytest.mark.parametrize("operacion", OPERACIONES, ids=IDS)
def test_wrong_key_raises_error_cuentas_and_file_is_left_unchanged(datos, almacen_seguro, operacion):
    AlmacenCuentas().guardar(cuenta())
    antes = archivo(datos).read_bytes()
    almacen_seguro.datos[("Eva", "chatgpt-cifrado")] = Fernet.generate_key().decode()
    with pytest.raises(ErrorCuentas) as exc:
        operacion(AlmacenCuentas())
    assert str(exc.value) == MENSAJE_ILEGIBLE
    assert archivo(datos).read_bytes() == antes


def test_missing_key_with_existing_file_raises_error_cuentas_and_keeps_file(datos, almacen_seguro):
    AlmacenCuentas().guardar(cuenta())
    antes = archivo(datos).read_bytes()
    del almacen_seguro.datos[("Eva", "chatgpt-cifrado")]
    with pytest.raises(ErrorCuentas) as exc:
        AlmacenCuentas().guardar(cuenta(subject="otra"))
    assert str(exc.value) == MENSAJE_ILEGIBLE
    assert archivo(datos).read_bytes() == antes
    assert ("Eva", "chatgpt-cifrado") not in almacen_seguro.datos


def test_valid_encryption_with_bad_content_raises_error_cuentas(datos, almacen_seguro):
    AlmacenCuentas().guardar(cuenta())
    clave = almacen_seguro.datos[("Eva", "chatgpt-cifrado")].encode()
    basura = Fernet(clave).encrypt(b"{no es json")
    archivo(datos).write_bytes(basura)
    with pytest.raises(ErrorCuentas) as exc:
        AlmacenCuentas().listar()
    assert str(exc.value) == MENSAJE_ILEGIBLE
    assert archivo(datos).read_bytes() == basura


# --- atomic write -----------------------------------------------------------------------


def test_failing_replace_leaves_previous_file_intact(datos, almacen_seguro, monkeypatch):
    almacen = AlmacenCuentas()
    c = cuenta()
    almacen.guardar(c)
    antes = archivo(datos).read_bytes()

    def falla(*args, **kwargs):
        raise OSError("disco ocupado")

    with monkeypatch.context() as parche:
        parche.setattr(os, "replace", falla)
        with pytest.raises(OSError):
            almacen.guardar(cuenta(subject="otra"))
    assert archivo(datos).read_bytes() == antes
    assert sorted(p.name for p in datos.iterdir()) == ["chatgpt.bin"]
    assert AlmacenCuentas().listar() == [c]


def test_explicit_path_is_used(tmp_path, almacen_seguro):
    ruta = tmp_path / "otro" / "cuentas.bin"
    ruta.parent.mkdir()
    AlmacenCuentas(ruta).guardar(cuenta())
    assert ruta.exists()
    assert AlmacenCuentas(ruta).listar() == [cuenta()]


@pytest.mark.skipif(os.name == "nt", reason="POSIX permissions")
def test_file_is_private_on_posix(datos, almacen_seguro):
    AlmacenCuentas().guardar(cuenta())
    assert archivo(datos).stat().st_mode & 0o777 == 0o600
