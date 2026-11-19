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


def _marea(tmp_path):
    pantalla = PantallaChat(
        type("Page", (), {"update": lambda self: None, "width": 1200})(),
        Historial(tmp_path / "eva.db"),
        Config(extras={"eva": {"ui": {"design": "marea"}}}),
    )
    pantalla.vista()
    return pantalla


def _descendientes(control):
    import flet as ft

    vistos = []
    pila = [control]
    while pila:
        actual = pila.pop()
        vistos.append(actual)
        hijo = getattr(actual, "content", None)
        if isinstance(hijo, ft.Control):
            pila.append(hijo)
        pila.extend(c for c in getattr(actual, "controls", []) or [] if isinstance(c, ft.Control))
    return vistos


def test_marea_title_bar_only_holds_the_window_controls(tmp_path):
    """The reference shows an empty title bar with the three controls right."""
    import flet as ft

    pantalla = _marea(tmp_path)
    barra = pantalla._crear_barra()

    textos = [c.value for c in _descendientes(barra) if isinstance(c, ft.Text)]
    assert textos == []
    assert barra.height == 44


def test_marea_menu_lists_settings_as_an_option(tmp_path):
    """The reference menu is Nuevo chat, Historial, Recuerdos and Ajustes."""
    import flet as ft

    pantalla = _marea(tmp_path)
    menu = pantalla._crear_menu(210, incluir_historial=False)

    etiquetas = [c.content for c in _descendientes(menu) if isinstance(c, (ft.TextButton, ft.FilledButton))]
    assert "Ajustes" in etiquetas
    assert "Nuevo chat" in etiquetas


def test_marea_composer_keeps_microphone_and_send_in_one_box(tmp_path):
    """The reference shows one rounded box holding field, mic and send."""
    pantalla = _marea(tmp_path)
    composer = pantalla._crear_composer()

    dentro = _descendientes(composer)
    assert pantalla.campo in dentro
    assert pantalla.microfono.boton in dentro
    assert pantalla.boton in dentro
    assert pantalla.boton.bgcolor == DISENOS["marea"].acento


def test_marea_shows_the_history_column_from_the_start(tmp_path):
    """The reference always shows the history column beside the menu."""
    pantalla = _marea(tmp_path)

    assert pantalla.historial_marea.visible is True


def test_marea_avatar_is_not_the_white_backed_image(tmp_path):
    """The seed PNG has a white background that shows as a box on the dark rail."""
    import flet as ft

    pantalla = _marea(tmp_path)
    menu = pantalla._crear_menu(210, incluir_historial=False)

    assert not [c for c in _descendientes(menu) if isinstance(c, ft.Image)]
