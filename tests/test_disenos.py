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
    menu = pantalla._crear_menu(210)

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
    menu = pantalla._crear_menu(210)

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
    menu = pantalla._crear_menu(210)

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
    menu = pantalla._crear_menu(210)

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

    menu = pantalla._crear_menu(210)
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


def test_switching_from_marea_to_brisa_keeps_a_usable_shell(tmp_path):
    """Marea's shell is a Column and Brisa's a Row: the container must change."""
    import flet as ft

    pantalla = _marea(tmp_path)
    shell = pantalla.vista()

    pantalla.aplicar_diseno("brisa")

    piezas = _descendientes(shell)
    assert pantalla.campo in piezas
    assert pantalla.boton in piezas
    assert pantalla.microfono.boton in piezas
    # The rail and the chat sit side by side again.
    fila = [c for c in piezas if isinstance(c, ft.Row) and pantalla.lateral in getattr(c, "controls", [])]
    assert fila


def _brisa(tmp_path):
    pantalla = PantallaChat(
        type("Page", (), {"update": lambda self: None, "width": 1200})(),
        Historial(tmp_path / "eva.db"),
        Config(extras={"eva": {"ui": {"design": "brisa"}}}),
    )
    pantalla.vista()
    return pantalla


def test_brisa_menu_labels_are_readable_on_the_light_rail(tmp_path):
    """Flet's dark theme paints them pale blue over the aqua rail."""
    import flet as ft

    pantalla = _brisa(tmp_path)
    menu = pantalla._crear_menu(230)

    botones = [c for c in _descendientes(menu) if isinstance(c, (ft.TextButton, ft.FilledButton))]
    assert botones
    assert all(b.style is not None and b.style.color == DISENOS["brisa"].texto for b in botones)


def test_brisa_conversation_rows_use_the_design_colors(tmp_path):
    pantalla = _brisa(tmp_path)
    pantalla.historial.crear_conversacion("una charla")
    pantalla._refrescar_lista()

    fila = pantalla.lista.controls[0]
    assert fila.title.color == DISENOS["brisa"].texto
    assert fila.selected_tile_color == DISENOS["brisa"].superficie_mensaje


def test_brisa_avatar_has_no_white_box(tmp_path):
    """The seed PNG carries a white background over the aqua rail."""
    import flet as ft

    pantalla = _brisa(tmp_path)
    menu = pantalla._crear_menu(230)

    assert not [c for c in _descendientes(menu) if isinstance(c, ft.Image)]


def test_every_design_shares_the_marea_menu_distribution(tmp_path):
    """One rail layout: Nuevo chat, options, and Ajustes resting at the foot."""
    import flet as ft

    for clave, hacer in (("brisa", _brisa), ("marea", _marea)):
        pantalla = hacer(tmp_path / clave if False else tmp_path)
        pantalla.aplicar_diseno(clave)
        menu = pantalla._crear_menu(220)
        columna = menu.content
        botones = [c for c in _descendientes(menu) if isinstance(c, ft.TextButton)]

        ultimo = columna.controls[-1]
        assert isinstance(ultimo, ft.TextButton) and ultimo.content == "Ajustes", clave
        assert "Nuevo chat" in [b.content for b in botones], clave
        assert all(b.style.alignment == ft.Alignment.CENTER_LEFT for b in botones), clave
        assert all(b.style.color == DISENOS[clave].texto for b in botones), clave
        assert all(ft.ControlState.HOVERED in b.style.side for b in botones), clave


def test_brisa_chat_header_is_readable(tmp_path):
    pantalla = _brisa(tmp_path)
    cabecera = pantalla._crear_cabecera_chat()

    assert pantalla.titulo_conversacion.color == DISENOS["brisa"].texto
    assert cabecera.bgcolor == DISENOS["brisa"].fondo_chat


def test_every_design_holds_the_composer_in_one_box(tmp_path):
    """The references draw field, microphone and send inside one box."""
    for clave, hacer in (("brisa", _brisa), ("marea", _marea)):
        pantalla = hacer(tmp_path)
        pantalla.aplicar_diseno(clave)
        composer = pantalla._crear_composer()

        dentro = _descendientes(composer)
        assert pantalla.campo in dentro, clave
        assert pantalla.microfono.boton in dentro, clave
        assert pantalla.boton in dentro, clave
        assert composer.border is not None, clave
        assert pantalla.boton.bgcolor == DISENOS[clave].acento, clave


def test_conversation_rows_carry_the_speech_bubble_icon(tmp_path):
    """The reference lists each conversation with a bubble icon."""
    import flet as ft

    pantalla = _brisa(tmp_path)
    cid = pantalla.historial.crear_conversacion("una charla")
    pantalla.conversacion_id = cid
    pantalla._refrescar_lista()

    fila = pantalla.lista.controls[0]
    assert isinstance(fila.leading, ft.Icon)
    assert fila.leading.icon == ft.Icons.CHAT_BUBBLE_OUTLINE
    # The open conversation stands out in the accent.
    assert fila.leading.color == DISENOS["brisa"].acento
    assert fila.title.color == DISENOS["brisa"].acento


def test_the_rail_no_longer_lists_conversations(tmp_path):
    for clave, hacer in (("brisa", _brisa), ("marea", _marea)):
        pantalla = hacer(tmp_path)
        pantalla.aplicar_diseno(clave)
        menu = pantalla._crear_menu(220)

        assert pantalla.lista not in _descendientes(menu), clave


def test_every_design_uses_the_history_column(tmp_path):
    for clave, hacer in (("brisa", _brisa), ("marea", _marea)):
        pantalla = hacer(tmp_path)
        pantalla.aplicar_diseno(clave)
        shell = pantalla.vista()

        piezas = _descendientes(shell)
        assert pantalla.historial_marea in piezas, clave
        assert pantalla.lista_historial in piezas, clave
        assert pantalla.historial_marea.bgcolor == DISENOS[clave].fondo_historial, clave


def test_the_history_column_toggles_in_every_design(tmp_path):
    pantalla = _brisa(tmp_path)
    visible = pantalla.historial_marea.visible

    pantalla._alternar_historial()

    assert pantalla.historial_marea.visible is not visible


def test_the_rail_has_the_same_width_in_every_design(tmp_path):
    from app.ui.chat import ANCHO_LATERAL

    for clave, hacer in (("brisa", _brisa), ("marea", _marea)):
        pantalla = hacer(tmp_path)
        pantalla.aplicar_diseno(clave)
        pantalla.vista()

        assert pantalla.lateral.width == ANCHO_LATERAL, clave


def test_history_rows_carry_the_speech_bubble_icon(tmp_path):
    import flet as ft

    pantalla = _marea(tmp_path)
    cid = pantalla.historial.crear_conversacion("una charla")
    pantalla.conversacion_id = cid
    filas = pantalla._filas_historial()

    iconos = [c for c in _descendientes(filas[0]) if isinstance(c, ft.Icon)]
    assert iconos and iconos[0].icon == ft.Icons.CHAT_BUBBLE_OUTLINE
    assert iconos[0].color == DISENOS["marea"].acento


def test_narrow_windows_collapse_the_panels_in_every_design(tmp_path):
    from app.ui.chat import ANCHO_LATERAL

    for clave, hacer in (("brisa", _brisa), ("marea", _marea)):
        pantalla = hacer(tmp_path)
        pantalla.aplicar_diseno(clave)
        pantalla.vista()

        pantalla.page.width = 1200
        pantalla._ajustar_layout(None)
        assert pantalla.lateral.width == ANCHO_LATERAL, clave
        assert pantalla.historial_marea.visible is True, clave

        pantalla.page.width = 700
        pantalla._ajustar_layout(None)
        assert pantalla.lateral.width < ANCHO_LATERAL, clave
        assert pantalla.historial_marea.visible is False, clave


class PaginaCerrada:
    """A Flet page whose session died while Eva was still answering."""

    width = 1200

    def __init__(self, fallar_despues=0):
        self.restantes = fallar_despues
        self.intentos = 0

    def update(self):
        self.intentos += 1
        if self.restantes <= 0:
            raise RuntimeError("An attempt to fetch destroyed session.")
        self.restantes -= 1


def test_refreshing_a_closed_window_is_not_an_error(tmp_path):
    pantalla = PantallaChat(PaginaCerrada(), Historial(tmp_path / "eva.db"), Config())

    assert pantalla._actualizar_pagina() is False


def test_other_runtime_errors_still_surface(tmp_path):
    import pytest

    class PaginaRota(PaginaCerrada):
        def update(self):
            raise RuntimeError("something else broke")

    pantalla = PantallaChat(PaginaRota(), Historial(tmp_path / "eva.db"), Config())

    with pytest.raises(RuntimeError):
        pantalla._actualizar_pagina()


def _horizonte(tmp_path):
    pantalla = PantallaChat(
        type("Page", (), {"update": lambda self: None, "width": 1200})(),
        Historial(tmp_path / "eva.db"),
        Config(extras={"eva": {"ui": {"design": "horizonte"}}}),
    )
    pantalla.vista()
    return pantalla


def test_every_design_declares_a_text_color_for_its_rail():
    """Horizonte pairs a dark rail with a light chat: one color cannot serve both."""
    for diseno in DISENOS.values():
        assert diseno.texto_lateral
    assert DISENOS["horizonte"].texto_lateral != DISENOS["horizonte"].texto


def test_horizonte_rail_and_history_use_the_rail_text_color(tmp_path):
    import flet as ft

    pantalla = _horizonte(tmp_path)
    menu = pantalla._crear_menu(280)
    botones = [c for c in _descendientes(menu) if isinstance(c, ft.TextButton)]
    pantalla.historial.crear_conversacion("una charla")
    filas = pantalla._filas_historial()

    assert all(b.style.color == DISENOS["horizonte"].texto_lateral for b in botones)
    titulos = [t for t in _descendientes(filas[0]) if isinstance(t, ft.Text) and t.value == "una charla"]
    assert titulos and titulos[0].color == DISENOS["horizonte"].texto_lateral


def test_the_rail_highlight_belongs_to_the_rail_surface():
    """Horizonte's rail is dark: the chat surface would hide its light text."""
    for diseno in DISENOS.values():
        assert diseno.superficie_lateral
    assert DISENOS["horizonte"].superficie_lateral != DISENOS["horizonte"].superficie_mensaje


def test_horizonte_hover_and_open_conversation_stay_readable(tmp_path):
    import flet as ft

    pantalla = _horizonte(tmp_path)
    menu = pantalla._crear_menu(280)
    botones = [c for c in _descendientes(menu) if isinstance(c, ft.TextButton)]
    cid = pantalla.historial.crear_conversacion("una charla")
    pantalla.conversacion_id = cid
    filas = pantalla._filas_historial()

    assert botones[0].style.bgcolor[ft.ControlState.HOVERED] == DISENOS["horizonte"].superficie_lateral
    assert filas[0].bgcolor == DISENOS["horizonte"].superficie_lateral


def test_long_answers_wrap_inside_the_bubble(tmp_path):
    """A row without expand lets the balloon grow past the chat width."""
    import flet as ft

    from app.ui.chat import _burbuja

    burbuja = _burbuja(ft.Text("una respuesta muy larga " * 20), de_usuario=False, avatar=ft.Icon(ft.Icons.WATER_DROP))

    fila = burbuja.content
    assert fila.expand is True
    globo = [c for c in fila.controls if isinstance(c, ft.Container)][0]
    assert globo.expand_loose is True


def test_horizonte_has_no_conversation_tabs(tmp_path):
    """Conversations live in the history column, not in a top strip."""
    import flet as ft

    pantalla = _horizonte(tmp_path)
    pantalla.historial.crear_conversacion("una charla")
    pantalla._refrescar_lista()
    shell = pantalla.vista()

    piezas = _descendientes(shell)
    assert not hasattr(pantalla, "pestanas") or pantalla.pestanas not in piezas
    assert pantalla.historial_marea in piezas


def test_every_design_draws_its_own_title_bar(tmp_path):
    """The window controls live in Eva's bar, not in the native Windows one."""
    import flet as ft

    for clave, hacer in (("brisa", _brisa), ("marea", _marea), ("horizonte", _horizonte)):
        pantalla = hacer(tmp_path)
        pantalla.aplicar_diseno(clave)
        shell = pantalla.vista()
        barra = pantalla._crear_barra()

        assert barra.height == 44, clave
        assert barra.border is not None and barra.border.bottom is not None, clave
        assert [t for t in _descendientes(barra) if isinstance(t, ft.Text)] == [], clave
        # The bar crosses the top; rail, history and chat sit below it.
        contenido = shell.content
        assert isinstance(contenido, ft.Column), clave
        assert contenido.controls[0].height == 44, clave


def test_the_conversation_title_moves_into_the_chat(tmp_path):
    """Removing it from the bar must not lose the conversation title."""
    pantalla = _brisa(tmp_path)
    shell = pantalla.vista()

    assert pantalla.titulo_conversacion in _descendientes(shell)


def test_the_island_uses_the_rail_surface_in_every_design(tmp_path):
    """Horizonte's chat is light but its island is dark, like the reference."""
    for clave, hacer in (("brisa", _brisa), ("marea", _marea), ("horizonte", _horizonte)):
        pantalla = hacer(tmp_path)
        pantalla.aplicar_diseno(clave)
        pantalla.vista_isla()

        assert pantalla._isla_surface.bgcolor == DISENOS[clave].fondo_lateral, clave
        assert pantalla.estado_isla.color == DISENOS[clave].texto_lateral, clave


def test_applying_a_design_recolors_an_open_island(tmp_path):
    pantalla = _horizonte(tmp_path)
    pantalla.vista_isla()

    pantalla.aplicar_diseno("marea")

    assert pantalla._isla_surface.bgcolor == DISENOS["marea"].fondo_lateral


def test_bubble_margins_follow_the_window_width(tmp_path):
    """A fixed 60 px gutter eats the usable width on a narrow window."""
    import flet as ft

    pantalla = _marea(tmp_path)
    pantalla.vista()
    pantalla._agregar(ft.Text("una respuesta"), de_usuario=False)

    pantalla.page.width = 1600
    pantalla._ajustar_layout(None)
    ancha = pantalla.mensajes.controls[0].padding.right

    pantalla.page.width = 640
    pantalla._ajustar_layout(None)
    angosta = pantalla.mensajes.controls[0].padding.right

    assert ancha > angosta
    assert angosta >= 8  # never flush against the edge


def test_message_text_shrinks_on_narrow_windows(tmp_path):
    import flet as ft

    pantalla = _marea(tmp_path)
    pantalla.vista()
    pantalla._agregar(ft.Text("una respuesta"), de_usuario=False)

    pantalla.page.width = 1600
    pantalla._ajustar_layout(None)
    grande = pantalla.mensajes.controls[0].content.controls[-1].content.size

    pantalla.page.width = 640
    pantalla._ajustar_layout(None)
    chico = pantalla.mensajes.controls[0].content.controls[-1].content.size

    assert grande > chico


class _VentanaAsincrona:
    """Flet 1.1.2 window: close and start_dragging are coroutines."""

    def __init__(self):
        self.llamadas = []

    async def close(self):
        self.llamadas.append("close")

    async def start_dragging(self):
        self.llamadas.append("start_dragging")


def _ejecutar_manejador(manejador):
    """Run a handler the way Flet does: await it only if it is async."""
    import asyncio

    if asyncio.iscoroutinefunction(manejador):
        asyncio.run(manejador(None))
    else:
        manejador(None)


def test_close_button_awaits_the_window_close(tmp_path):
    """A lambda returning Window.close() left the coroutine never awaited."""
    import flet as ft

    pantalla = _marea(tmp_path)
    pantalla.page.window = _VentanaAsincrona()
    barra = pantalla._crear_barra()

    cerrar = next(c for c in _descendientes(barra) if isinstance(c, ft.IconButton) and c.tooltip == "Cerrar Eva")
    _ejecutar_manejador(cerrar.on_click)

    assert pantalla.page.window.llamadas == ["close"]


def test_title_bar_and_island_drag_the_window(tmp_path):
    """Dragging must start natively: a Python round trip after pan start
    arrives too late, and an uncolored container receives no hits."""
    import flet as ft

    pantalla = _marea(tmp_path)
    barra = pantalla._crear_barra()
    isla = pantalla.vista_isla()

    areas_barra = [c for c in _descendientes(barra) if isinstance(c, ft.WindowDragArea)]
    areas_isla = [c for c in _descendientes(isla) if isinstance(c, ft.WindowDragArea)]
    assert len(areas_barra) == 1
    assert areas_barra[0].content.bgcolor == ft.Colors.TRANSPARENT
    assert len(areas_isla) == 1
    assert areas_isla[0].maximizable is False
    assert not any(isinstance(c, ft.GestureDetector) and c.on_pan_start for c in _descendientes(barra))


def test_long_messages_wrap_inside_the_chat(tmp_path):
    """expand_loose alone does nothing in Flet: without expand the bubble is
    not flexible and a long answer runs past the window edge."""
    import flet as ft

    from app.ui.chat import _burbuja

    for de_usuario in (True, False):
        texto = ft.Text("palabra " * 80)
        fila = _burbuja(texto, de_usuario, avatar=ft.Icon(ft.Icons.PERSON)).content
        globo = next(c for c in fila.controls if isinstance(c, ft.Container) and c.content is texto)
        assert globo.expand
        assert globo.expand_loose is True
