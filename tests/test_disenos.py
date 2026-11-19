from app.config import Config, cargar_config, guardar_config
from app.memory.history import Historial
from app.ui.chat import PantallaChat
from app.ui.disenos import (
    DISENOS,
    leer_diseno,
    guardar_diseno,
    diseno_valido,
)


def test_catalog_contains_three_structurally_distinct_designs():
    assert set(DISENOS) == {"brisa", "marea", "horizonte"}
    assert DISENOS["brisa"].layout != DISENOS["marea"].layout
    assert DISENOS["marea"].layout != DISENOS["horizonte"].layout
    assert DISENOS["brisa"].fondo_chat != DISENOS["horizonte"].fondo_chat


def test_missing_or_invalid_design_requires_the_chooser():
    assert leer_diseno(Config()) is None
    assert leer_diseno(Config(extras={"eva": {"ui": {"design": "otro"}}})) is None
    assert diseno_valido("marea") == "marea"
    assert diseno_valido("otro") is None


def test_design_is_saved_nested_without_losing_existing_preferences():
    config = Config(extras={"voz_eva": "es_AR-daniela-high", "eva": {"ui": {"zoom": 1.1}}})

    guardar_diseno(config, "horizonte")

    assert leer_diseno(config) == "horizonte"
    assert config.extras["voz_eva"] == "es_AR-daniela-high"
    assert config.extras["eva"]["ui"]["zoom"] == 1.1


def test_design_round_trips_through_config_json(tmp_path):
    ruta = tmp_path / "config.json"
    config = Config(extras={"voz_eva": "es_AR-daniela-high"})
    guardar_diseno(config, "marea")
    guardar_config(config, ruta)

    cargada = cargar_config(ruta)

    assert cargada.extras["eva"]["ui"]["design"] == "marea"
    assert cargada.extras["voz_eva"] == "es_AR-daniela-high"


def test_applying_a_design_keeps_the_draft_and_live_messages(tmp_path):
    pantalla = PantallaChat(type("Page", (), {"update": lambda self: None, "width": 1200})(), Historial(tmp_path / "eva.db"), Config(extras={"eva": {"ui": {"design": "brisa"}}}))
    pantalla.vista()
    pantalla.campo.value = "borrador sin enviar"
    pantalla._agregar(__import__("flet").Text("respuesta en curso"), de_usuario=False)

    pantalla.aplicar_diseno("horizonte")

    assert pantalla.campo.value == "borrador sin enviar"
    assert pantalla.mensajes.controls[0].content.controls[0].content.value == "respuesta en curso"
    assert pantalla.diseno == "horizonte"


def test_malformed_nested_preferences_are_repaired_on_save():
    config = Config(extras={"eva": "old-value"})

    guardar_diseno(config, "brisa")

    assert config.extras["eva"] == {"ui": {"design": "brisa"}}
