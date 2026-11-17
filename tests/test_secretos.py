"""Tests for API key storage in the OS keyring (S2).

An in-memory keyring backend replaces the real one, so the OS store is never used.
"""

import keyring.core
import pytest

from app import secretos
from app.secretos import ErrorSecretos, borrar_clave, guardar_clave, leer_clave
from tests.keyring_falso import KeyringFalso, KeyringRoto

CLAVE = "sk-secreta-123"


@pytest.fixture
def almacen(monkeypatch):
    backend = KeyringFalso()
    monkeypatch.setattr(keyring.core, "_keyring_backend", backend)
    return backend


@pytest.fixture
def roto(monkeypatch):
    backend = KeyringRoto()
    monkeypatch.setattr(keyring.core, "_keyring_backend", backend)
    return backend


def test_service_name_is_eva():
    assert secretos.SERVICIO == "Eva"


def test_guardar_then_leer_returns_the_key(almacen):
    guardar_clave("openai", CLAVE)
    assert leer_clave("openai") == CLAVE
    assert almacen.datos == {("Eva", "openai"): CLAVE}


def test_keys_are_kept_per_provider(almacen):
    guardar_clave("openai", "a")
    guardar_clave("anthropic", "b")
    assert (leer_clave("openai"), leer_clave("anthropic"), leer_clave("gemini")) == ("a", "b", None)


def test_guardar_replaces_the_previous_key(almacen):
    guardar_clave("gemini", "vieja")
    guardar_clave("gemini", "nueva")
    assert leer_clave("gemini") == "nueva"


def test_leer_missing_key_returns_none(almacen):
    assert leer_clave("openrouter") is None


def test_borrar_removes_only_that_key(almacen):
    guardar_clave("openai", "a")
    guardar_clave("gemini", "b")
    borrar_clave("openai")
    assert leer_clave("openai") is None
    assert almacen.datos == {("Eva", "gemini"): "b"}


def test_borrar_missing_key_is_not_an_error(almacen):
    borrar_clave("openai")
    assert almacen.datos == {}


@pytest.mark.parametrize(
    "operacion",
    [
        lambda: leer_clave("openai"),
        lambda: guardar_clave("openai", CLAVE),
        lambda: borrar_clave("openai"),
    ],
    ids=["leer", "guardar", "borrar"],
)
def test_failing_backend_raises_error_secretos_with_user_message(roto, operacion):
    with pytest.raises(ErrorSecretos) as exc:
        operacion()
    assert str(exc.value) == "No pude usar el almacén seguro del sistema: almacén bloqueado"


def test_error_message_never_contains_the_key(monkeypatch):
    # A backend whose error text echoes the secret must not leak it to the user.
    monkeypatch.setattr(keyring.core, "_keyring_backend", KeyringRoto(detalle=f"no acepto {CLAVE}"))
    with pytest.raises(ErrorSecretos) as exc:
        guardar_clave("openai", CLAVE)
    mensaje = str(exc.value)
    assert CLAVE not in mensaje
    assert mensaje.startswith("No pude usar el almacén seguro del sistema: ")
