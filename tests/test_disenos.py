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


def test_marea_title_bar_spans_the_whole_window(tmp_path):
    """In the reference the bar crosses the top; menu and history sit below."""
    import flet as ft

    pantalla = _marea(tmp_path)
    shell = pantalla._construir_layout()

    assert isinstance(shell, ft.Column)
    barra, cuerpo = shell.controls
    assert barra.height == 44
    assert isinstance(cuerpo, ft.Row)
    assert pantalla.lateral in cuerpo.controls


def test_marea_menu_labels_use_the_design_text_color(tmp_path):
    import flet as ft

    pantalla = _marea(tmp_path)
    menu = pantalla._crear_menu(210, incluir_historial=False)

    botones = [c for c in _descendientes(menu) if isinstance(c, ft.TextButton)]
    assert botones
    assert all(b.style is not None and b.style.color == DISENOS["marea"].texto for b in botones)


def test_marea_send_button_is_a_rounded_square(tmp_path):
    pantalla = _marea(tmp_path)
    pantalla._crear_composer()

    assert pantalla.boton.style is not None
    assert isinstance(pantalla.boton.style.shape, __import__("flet").RoundedRectangleBorder)


def test_marea_title_bar_is_separated_from_the_chat(tmp_path):
    """The reference bar has its own tone and a line along its bottom."""
    pantalla = _marea(tmp_path)
    barra = pantalla._crear_barra()

    assert barra.bgcolor != DISENOS["marea"].fondo_chat
    assert barra.border is not None and barra.border.bottom is not None


def test_marea_menu_options_are_left_aligned(tmp_path):
    import flet as ft

    pantalla = _marea(tmp_path)
    menu = pantalla._crear_menu(210, incluir_historial=False)

    botones = [c for c in _descendientes(menu) if isinstance(c, ft.TextButton)]
    assert botones
    assert all(b.style.alignment == ft.Alignment.CENTER_LEFT for b in botones)


def test_marea_composer_field_has_no_tone_of_its_own(tmp_path):
    """A filled field paints a cut-looking box inside the composer."""
    pantalla = _marea(tmp_path)
    pantalla._crear_composer()

    assert pantalla.campo.bgcolor == "#00000000"


def _botones_menu(pantalla):
    import flet as ft

    menu = pantalla._crear_menu(210, incluir_historial=False)
    return menu, [c for c in _descendientes(menu) if isinstance(c, ft.TextButton)]


def test_marea_settings_sits_at_the_foot_of_the_menu(tmp_path):
    import flet as ft

    pantalla = _marea(tmp_path)
    menu, _ = _botones_menu(pantalla)
    columna = menu.content

    ultimo = columna.controls[-1]
    assert isinstance(ultimo, ft.TextButton) and ultimo.content == "Ajustes"
    assert any(getattr(c, "expand", False) for c in columna.controls[:-1])


def test_marea_new_chat_looks_like_the_other_options(tmp_path):
    pantalla = _marea(tmp_path)
    _, botones = _botones_menu(pantalla)

    estilos = {id(b.style) for b in botones}
    assert len(estilos) == 1


def test_marea_menu_options_show_a_border_on_hover(tmp_path):
    import flet as ft

    pantalla = _marea(tmp_path)
    _, botones = _botones_menu(pantalla)

    lado = botones[0].style.side
    assert isinstance(lado, dict) and ft.ControlState.HOVERED in lado


def test_marea_menu_options_are_transparent_when_idle(tmp_path):
    """Giving only a hover color leaves the theme's dark fill at rest."""
    import flet as ft

    pantalla = _marea(tmp_path)
    _, botones = _botones_menu(pantalla)

    fondo = botones[0].style.bgcolor
    assert fondo[ft.ControlState.DEFAULT] == "#00000000"


def test_removing_a_status_bubble_works_with_an_avatar(tmp_path):
    """Eva's bubbles carry an avatar first, which has no content attribute."""
    pantalla = _marea(tmp_path)
    aviso = pantalla.mostrar_aviso("Grabando…")
    assert len(pantalla.mensajes.controls) == 1

    pantalla.quitar_aviso(aviso)

    assert pantalla.mensajes.controls == []


def test_status_bubble_with_a_real_avatar_can_be_removed(tmp_path):
    """The live app puts an Image first, which has no content attribute."""
    import flet as ft

    from app.ui.chat import _burbuja

    pantalla = _marea(tmp_path)
    aviso = ft.Text("Grabando…")
    pantalla.mensajes.controls.append(
        _burbuja(aviso, de_usuario=False, avatar=ft.Image(src="x.png", width=34, height=34))
    )

    pantalla.quitar_aviso(aviso)

    assert pantalla.mensajes.controls == []


def test_listen_button_is_attached_next_to_an_answer_with_an_avatar(tmp_path):
    import flet as ft

    from app.ui.chat import _burbuja

    pantalla = _marea(tmp_path)
    respuesta = ft.Text("respuesta")
    pantalla.mensajes.controls.append(
        _burbuja(respuesta, de_usuario=False, avatar=ft.Image(src="x.png", width=34, height=34))
    )
    pantalla._boton_escuchar = lambda r: ft.IconButton(icon=ft.Icons.VOLUME_UP)

    pantalla._agregar_escuchar(respuesta)

    fila = pantalla.mensajes.controls[0].content
    assert isinstance(fila.controls[-1], ft.IconButton)


def test_marea_island_follows_the_reference_capsule(tmp_path):
    """Drop, divider, the real state and a square button to come back."""
    import flet as ft

    pantalla = _marea(tmp_path)
    isla = pantalla.vista_isla()

    piezas = _descendientes(isla)
    iconos = [c.icon for c in piezas if isinstance(c, ft.Icon)]
    assert ft.Icons.WATER_DROP in iconos
    assert ft.Icons.AUTO_AWESOME not in iconos
    # The capsule shows the state alone, not Eva's name repeated.
    assert [t.value for t in piezas if isinstance(t, ft.Text)] == [pantalla.estado_isla.value]


def test_marea_island_text_uses_the_capsule_typography(tmp_path):
    """The reference state text is light and readable, not a small gray note."""
    import flet as ft

    pantalla = _marea(tmp_path)
    pantalla.vista_isla()

    assert pantalla.estado_isla.size == 14
    assert pantalla.estado_isla.color == DISENOS["marea"].texto
    assert pantalla.estado_isla.weight == ft.FontWeight.W_500


def test_idle_island_text_keeps_the_design_color(tmp_path):
    """Going back to the idle label must not leave the gray status color."""
    from app.ui.chat import LISTO

    pantalla = _marea(tmp_path)
    pantalla.vista_isla()

    pantalla._poner_estado("Pensando…", "#A8C0CC")
    pantalla._poner_estado(LISTO, "#A8C0CC")

    assert pantalla.estado_isla.value == "Eva"
    assert pantalla.estado_isla.color == DISENOS["marea"].texto


def test_the_composer_swaps_the_field_for_the_recording_indicator(tmp_path):
    pantalla = _marea(tmp_path)
    pantalla._crear_composer()

    pantalla.mostrar_grabacion(True)
    assert pantalla.campo.visible is False
    assert pantalla.indicador_grabacion.control.visible is True

    pantalla.mostrar_grabacion(False)
    assert pantalla.campo.visible is True
    assert pantalla.indicador_grabacion.control.visible is False


def test_the_recording_indicator_lives_inside_the_composer(tmp_path):
    pantalla = _marea(tmp_path)
    composer = pantalla._crear_composer()

    assert pantalla.indicador_grabacion.control in _descendientes(composer)
