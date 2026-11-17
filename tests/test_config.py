"""Tests for config.json (provider and model settings)."""

import json

import pytest

from app.config import PROVEEDORES, Config, cargar_config, guardar_config, resolver_modelo


def test_missing_file_gives_lm_studio_defaults(tmp_path):
    cfg = cargar_config(tmp_path / "config.json")
    assert cfg.proveedor == "lmstudio"
    assert cfg.modelo == ""
    assert cfg.servidor == "http://localhost:1234/v1"


def test_providers_point_to_the_plan_ports():
    assert PROVEEDORES["lmstudio"].url == "http://localhost:1234/v1"
    assert PROVEEDORES["ollama"].url == "http://localhost:11434/v1"


def test_saved_settings_are_loaded_back(tmp_path):
    ruta = tmp_path / "config.json"
    guardar_config(Config(proveedor="ollama", modelo="llama3.2"), ruta)

    cfg = cargar_config(ruta)
    assert (cfg.proveedor, cfg.modelo, cfg.servidor) == ("ollama", "llama3.2", "http://localhost:11434/v1")
    assert json.loads(ruta.read_text(encoding="utf-8")) == {"proveedor": "ollama", "modelo": "llama3.2"}


def test_corrupt_file_falls_back_to_defaults_and_is_left_untouched(tmp_path):
    ruta = tmp_path / "config.json"
    ruta.write_text("{esto no es json", encoding="utf-8")

    assert cargar_config(ruta) == Config()
    assert ruta.read_text(encoding="utf-8") == "{esto no es json"


def test_unknown_provider_falls_back_to_lm_studio_and_keeps_the_model(tmp_path):
    ruta = tmp_path / "config.json"
    ruta.write_text(json.dumps({"proveedor": "marciano", "modelo": "m"}), encoding="utf-8")

    cfg = cargar_config(ruta)
    assert (cfg.proveedor, cfg.modelo) == ("lmstudio", "m")


@pytest.mark.parametrize("contenido", ['{"proveedor": []}', '{"proveedor": {"a": 1}}', '{"proveedor": 3}'])
def test_invalid_provider_type_gives_defaults(tmp_path, contenido):
    ruta = tmp_path / "config.json"
    ruta.write_text(contenido, encoding="utf-8")
    assert cargar_config(ruta) == Config()


def test_unknown_keys_are_preserved_on_save(tmp_path):
    ruta = tmp_path / "config.json"
    ruta.write_text(json.dumps({"proveedor": "lmstudio", "voz": "es_MX"}), encoding="utf-8")

    cfg = cargar_config(ruta)
    cfg.modelo = "qwen"
    guardar_config(cfg, ruta)
    assert json.loads(ruta.read_text(encoding="utf-8")) == {"proveedor": "lmstudio", "modelo": "qwen", "voz": "es_MX"}


def test_resolver_modelo_keeps_the_saved_model_if_installed():
    assert resolver_modelo("b", ["a", "b"]) == "b"


def test_resolver_modelo_falls_back_to_the_first_installed_model():
    assert resolver_modelo("borrado", ["a", "b"]) == "a"
    assert resolver_modelo("", ["a", "b"]) == "a"
    assert resolver_modelo("x", []) == ""


# ----- S4: a failed save cleans up and keeps the previous settings -----


def test_failed_replace_removes_the_temp_file_and_keeps_the_old_config(tmp_path, monkeypatch):
    ruta = tmp_path / "config.json"
    guardar_config(Config(proveedor="ollama", modelo="viejo"), ruta)
    anterior = ruta.read_text(encoding="utf-8")

    def bloqueado(origen, destino):
        raise PermissionError(13, "Acceso denegado")

    monkeypatch.setattr("app.config.os.replace", bloqueado)
    with pytest.raises(PermissionError):
        guardar_config(Config(proveedor="lmstudio", modelo="nuevo"), ruta)

    assert ruta.read_text(encoding="utf-8") == anterior
    assert sorted(p.name for p in tmp_path.iterdir()) == ["config.json"]


def test_failed_write_raises_oserror_and_leaves_no_temp_file(tmp_path):
    ruta = tmp_path / "no-existe" / "config.json"
    with pytest.raises(OSError):
        guardar_config(Config(), ruta)
    assert not ruta.with_suffix(".json.tmp").exists()
