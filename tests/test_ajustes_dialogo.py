"""Visual structure and active-design styling for Eva's settings dialog."""

import flet as ft

from app.config import Config
from app.ui.ajustes import DialogoAjustes
from app.ui.disenos import DISENOS
from tests.test_ui import FakePage, Servidor, _usar_servidor


def _descendientes(control):
    yield control
    controls = getattr(control, "controls", None) or []
    for child in controls:
        yield from _descendientes(child)
    content = getattr(control, "content", None)
    if isinstance(content, ft.Control):
        yield from _descendientes(content)


def _dialogo(monkeypatch, config):
    _usar_servidor(monkeypatch, Servidor(models=["modelo-a"]))
    dialogo = DialogoAjustes(FakePage(), config, lambda config: None)
    dialogo.abrir()
    return dialogo


def test_settings_dialog_has_title_close_and_one_header_per_section(monkeypatch):
    dialogo = _dialogo(monkeypatch, Config(extras={"eva": {"ui": {"design": "marea"}}}))
    controls = list(_descendientes(dialogo.dialogo))
    texts = [control.value for control in controls if isinstance(control, ft.Text)]
    texts += [control.value for control in _descendientes(dialogo.dialogo.title) if isinstance(control, ft.Text)]
    icons = [control.icon for control in controls if isinstance(control, ft.Icon)]

    assert "Ajustes" in texts
    assert any(isinstance(control, ft.IconButton) and control.icon == ft.Icons.CLOSE for control in _descendientes(dialogo.dialogo.title))
    assert all(name in texts for name in ("Modelo de IA", "Voz", "Apariencia"))
    assert all(icon in icons for icon in (ft.Icons.MEMORY, ft.Icons.GRAPHIC_EQ, ft.Icons.PALETTE))


def test_dropdowns_have_external_field_labels(monkeypatch):
    dialogo = _dialogo(monkeypatch, Config())
    controls = list(_descendientes(dialogo.dialogo))
    labels = [control.value for control in controls if isinstance(control, ft.Text)]

    assert dialogo.proveedor.label is None
    assert dialogo.modelo.label is None
    assert "Proveedor" in labels
    assert "Modelo" in labels


def test_appearance_row_shows_current_design_and_opens_chooser(monkeypatch):
    dialogo = _dialogo(monkeypatch, Config(extras={"eva": {"ui": {"design": "brisa"}}}))
    controls = list(_descendientes(dialogo.boton_apariencia))
    texts = [control.value for control in controls if isinstance(control, ft.Text)]

    assert "Brisa" in texts
    assert dialogo.boton_apariencia.on_click is not None


def test_save_button_uses_active_design_accent(monkeypatch):
    dialogo = _dialogo(monkeypatch, Config(extras={"eva": {"ui": {"design": "brisa"}}}))
    estilo = dialogo.boton_guardar.style

    assert estilo.bgcolor[ft.ControlState.DEFAULT] == DISENOS["brisa"].acento


def test_dialog_uses_brisa_tokens_when_brisa_is_active(monkeypatch):
    dialogo = _dialogo(monkeypatch, Config(extras={"eva": {"ui": {"design": "brisa"}}}))
    diseno = DISENOS["brisa"]

    assert dialogo.dialogo.bgcolor == diseno.fondo_lateral
    assert dialogo.boton_guardar.style.bgcolor[ft.ControlState.DEFAULT] == diseno.acento
    assert dialogo.boton_guardar.style.color[ft.ControlState.DEFAULT] == diseno.texto
    assert dialogo.dialogo.bgcolor != DISENOS["marea"].fondo_lateral


def test_the_appearance_row_is_clickable(monkeypatch):
    """expand inside a Column grows vertically and left the row unclickable."""
    dialogo = _dialogo(monkeypatch, Config(extras={"eva": {"ui": {"design": "marea"}}}))

    fila = [
        c
        for c in dialogo.dialogo.content.controls
        if isinstance(c, ft.Row) and dialogo.boton_apariencia in c.controls
    ]
    assert fila, "the appearance row must sit inside a Row to expand sideways"
    assert dialogo.boton_apariencia.expand is True
