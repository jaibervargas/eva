"""Eva's voice and when she speaks, in the settings dialog (Fase 4, S4 and S5)."""

import json

import pytest

from app.config import Config, cargar_config
from app.ui.ajustes import DialogoAjustes
from app.ui.chat import GRIS_TEXTO, ROJO_ERROR
from app.voice.stt import ErrorVoz
from tests.test_reproductor import PaginaVoz
from tests.test_ui import Servidor, _usar_servidor

PRUEBA = "Hola, soy Eva. Así suena mi voz."
DESCARGANDO = "Descargando la voz de Eva (unos 60–115 MB). Solo pasa la primera vez…"
SIN_DESCARGA = "No pude descargar la voz de Eva. Revisa tu conexión a internet."


@pytest.fixture(autouse=True)
def datos(tmp_path, monkeypatch):
    monkeypatch.setenv("EVA_DATA_DIR", str(tmp_path / "datos"))
    _usar_servidor(monkeypatch, Servidor(models=["a"]))
    return tmp_path / "datos"


class VozFalsa:
    """Stands in for ``VozEva``: records what it is asked to say."""

    def __init__(self, descarga=False, error=None):
        self.descarga = descarga
        self.error = error
        self.dichos: list[tuple[str, str]] = []
        self.pagina = None
        self.en_hilo: list[bool] = []
        self.vistos: list = []  # dialog state when the slow work starts

    def hablar(self, texto, voz, al_descargar=None):
        self.en_hilo.append(self.pagina is not None and self.pagina.en_hilo > 0)
        if self.descarga and al_descargar is not None:
            al_descargar()
        self.vistos.append(self.estado())
        if self.error is not None:
            raise self.error
        self.dichos.append((texto, voz))
        return True


def _dialogo(config=None, voz=None):
    page = PaginaVoz()
    if voz is not None:
        voz.pagina = page
    aplicadas: list[Config] = []
    dialogo = DialogoAjustes(page, config or Config(), aplicadas.append, voz=voz)
    if voz is not None:
        voz.estado = lambda: (dialogo.estado_voz.value, dialogo.estado_voz.color, dialogo.boton_probar.disabled)
    dialogo.abrir()
    return dialogo, page, aplicadas


def _en_disco(datos):
    return json.loads((datos / "config.json").read_text(encoding="utf-8"))


# ----- the controls -----


def test_the_voice_list_offers_the_catalog_with_daniela_by_default():
    dialogo, _, _ = _dialogo()

    assert dialogo.voz_eva.label == "Voz de Eva"
    assert [(o.key, o.text) for o in dialogo.voz_eva.options] == [
        ("es_AR-daniela-high", "Daniela (Argentina)"),
        ("es_MX-claude-high", "Claude (México)"),
        ("es_MX-ald-medium", "Ald (México)"),
        ("es_ES-davefx-medium", "Davefx (España)"),
    ]
    assert dialogo.voz_eva.value == "es_AR-daniela-high"


def test_the_answer_mode_offers_three_options_with_voice_only_by_default():
    dialogo, _, _ = _dialogo()

    assert dialogo.responder.label == "Responder en voz alta"
    assert [(o.key, o.text) for o in dialogo.responder.options] == [
        ("voz", "Solo cuando le hablas por voz"),
        ("siempre", "Siempre"),
        ("nunca", "Nunca"),
    ]
    assert dialogo.responder.value == "voz"


def test_saved_choices_are_preselected():
    dialogo, _, _ = _dialogo(Config(extras={"voz_eva": "es_MX-claude-high", "responder_en_voz": "nunca"}))

    assert (dialogo.voz_eva.value, dialogo.responder.value) == ("es_MX-claude-high", "nunca")


def test_unknown_saved_choices_fall_back_to_the_defaults():
    dialogo, _, _ = _dialogo(Config(extras={"voz_eva": "nadie", "responder_en_voz": 3}))

    assert (dialogo.voz_eva.value, dialogo.responder.value) == ("es_AR-daniela-high", "voz")


# ----- saving -----


def test_both_choices_are_saved_keeping_the_other_extras(datos):
    extras = {"voz": "es_MX", "chatgpt_cuenta": "c1"}
    dialogo, page, aplicadas = _dialogo(Config(extras=extras))
    dialogo.voz_eva.value = "es_ES-davefx-medium"
    dialogo.responder.value = "siempre"
    dialogo._guardar(None)

    esperado = {
        "proveedor": "lmstudio",
        "modelo": "a",
        "voz": "es_MX",
        "chatgpt_cuenta": "c1",
        "voz_eva": "es_ES-davefx-medium",
        "responder_en_voz": "siempre",
    }
    assert _en_disco(datos) == esperado
    assert aplicadas[0].extras == {k: v for k, v in esperado.items() if k not in ("proveedor", "modelo")}
    assert page.dialogos == []
    leida = cargar_config(datos / "config.json")
    assert (leida.extras["voz_eva"], leida.extras["responder_en_voz"]) == ("es_ES-davefx-medium", "siempre")


def test_saved_choices_can_go_back_to_the_defaults(datos):
    dialogo, _, _ = _dialogo(Config(extras={"voz_eva": "es_MX-claude-high", "responder_en_voz": "nunca"}))
    dialogo.voz_eva.value = "es_AR-daniela-high"
    dialogo.responder.value = "voz"
    dialogo._guardar(None)

    assert _en_disco(datos) == {
        "proveedor": "lmstudio",
        "modelo": "a",
        "voz_eva": "es_AR-daniela-high",
        "responder_en_voz": "voz",
    }


def test_untouched_defaults_add_nothing_to_the_config(datos):
    dialogo, _, _ = _dialogo()
    dialogo._guardar(None)

    assert _en_disco(datos) == {"proveedor": "lmstudio", "modelo": "a"}


def test_cancel_saves_nothing(datos):
    dialogo, page, aplicadas = _dialogo()
    dialogo.responder.value = "nunca"
    dialogo._cancelar(None)

    assert aplicadas == [] and page.dialogos == []
    assert not (datos / "config.json").exists()


# ----- "Probar voz" -----


def test_try_voice_speaks_the_sample_with_the_selected_voice_off_the_ui_loop():
    voz = VozFalsa()
    dialogo, _, _ = _dialogo(voz=voz)
    assert dialogo.boton_probar.content == "Probar voz"
    assert dialogo.boton_probar.disabled is False
    dialogo.voz_eva.value = "es_MX-ald-medium"  # not saved yet
    dialogo.boton_probar.on_click(None)

    assert voz.dichos == [(PRUEBA, "es_MX-ald-medium")]
    assert voz.en_hilo == [True]
    assert voz.vistos == [("", GRIS_TEXTO, True)]  # locked while it works
    assert (dialogo.estado_voz.value, dialogo.boton_probar.disabled) == ("", False)


def test_try_voice_shows_the_download_first_when_needed():
    voz = VozFalsa(descarga=True)
    dialogo, _, _ = _dialogo(voz=voz)
    dialogo.boton_probar.on_click(None)

    assert voz.vistos == [(DESCARGANDO, GRIS_TEXTO, True)]
    assert dialogo.estado_voz.value == ""


def test_try_voice_failures_are_shown_in_the_dialog():
    voz = VozFalsa(error=ErrorVoz(SIN_DESCARGA))
    dialogo, page, aplicadas = _dialogo(voz=voz)
    dialogo.boton_probar.on_click(None)

    assert (dialogo.estado_voz.value, dialogo.estado_voz.color) == (SIN_DESCARGA, ROJO_ERROR)
    assert dialogo.boton_probar.disabled is False
    assert page.dialogos == [dialogo.dialogo] and aplicadas == []


def test_try_voice_unexpected_failures_are_shown_too(caplog):
    voz = VozFalsa(error=RuntimeError("roto"))
    dialogo, _, _ = _dialogo(voz=voz)
    dialogo.boton_probar.on_click(None)

    assert (dialogo.estado_voz.value, dialogo.estado_voz.color) == ("No pude generar la voz: roto", ROJO_ERROR)
    assert "roto" in caplog.text


def test_try_voice_is_disabled_without_a_voice():
    dialogo, _, _ = _dialogo()

    assert dialogo.boton_probar.disabled is True
