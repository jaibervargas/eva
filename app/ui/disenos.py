"""Visual design catalog and preference helpers for Eva."""

from dataclasses import dataclass
from typing import Any

import flet as ft

from app.config import Config, guardar_config


@dataclass(frozen=True)
class Diseno:
    id: str
    nombre: str
    layout: str
    fondo_chat: str
    fondo_lateral: str
    fondo_historial: str
    texto: str
    acento: str
    superficie_mensaje: str
    radio: int


DISENOS = {
    "brisa": Diseno(
        id="brisa",
        nombre="Brisa",
        layout="rail",
        fondo_chat="#F6F9FC",
        fondo_lateral="#D2F3F4",
        fondo_historial="#D2F3F4",
        texto="#0D2948",
        acento="#007F8D",
        superficie_mensaje="#DDF3FA",
        radio=16,
    ),
    "marea": Diseno(
        id="marea",
        nombre="Marea",
        layout="double-rail",
        fondo_chat="#061E29",
        fondo_lateral="#092732",
        fondo_historial="#0B2C38",
        texto="#E8F4F7",
        acento="#36D5CE",
        superficie_mensaje="#102F3A",
        radio=12,
    ),
    "horizonte": Diseno(
        id="horizonte",
        nombre="Horizonte",
        layout="tabs",
        fondo_chat="#F1F6FA",
        fondo_lateral="#063346",
        fondo_historial="#063346",
        texto="#0B2841",
        acento="#00A3B0",
        superficie_mensaje="#DDF1F5",
        radio=16,
    ),
}


def diseno_valido(valor: Any) -> str | None:
    return valor if isinstance(valor, str) and valor in DISENOS else None


def leer_diseno(config: Config) -> str | None:
    """Read the nested preference without disturbing legacy extras."""
    eva = config.extras.get("eva")
    if not isinstance(eva, dict):
        return None
    ui = eva.get("ui")
    return diseno_valido(ui.get("design")) if isinstance(ui, dict) else None


@dataclass
class EstadoInterfaz:
    """Single UI state shared by layout variants."""

    diseno: str | None = None
    conversacion_id: int | None = None
    borrador: str = ""
    operacion: str = "reposo"
    respuesta_en_curso: bool = False


def guardar_diseno(config: Config, diseno: str) -> None:
    """Store a valid design while preserving unrelated nested preferences."""
    valido = diseno_valido(diseno)
    if valido is None:
        raise ValueError(f"Unknown Eva design: {diseno}")
    eva = config.extras.get("eva")
    if not isinstance(eva, dict):
        eva = {}
        config.extras["eva"] = eva
    ui = eva.get("ui")
    if not isinstance(ui, dict):
        ui = {}
        eva["ui"] = ui
    ui["design"] = valido


class SelectorDiseno:
    """First-run design chooser; layout previews use the real catalog tokens."""

    def __init__(
        self,
        page: ft.Page,
        config: Config,
        al_aplicar,
        al_preview=None,
        permite_cancelar: bool = False,
    ) -> None:
        self.page = page
        self.config = config
        self.al_aplicar = al_aplicar
        self.al_preview = al_preview or (lambda clave: None)
        self._original = leer_diseno(config)
        self.seleccion: str | None = self._original
        self.tarjetas: dict[str, ft.Container] = {}
        acciones = [ft.TextButton("Cancelar", on_click=self._cancelar)] if permite_cancelar else []
        acciones.append(ft.FilledButton("Usar este diseño", on_click=self._aplicar, disabled=self.seleccion is None))
        self.dialogo = ft.AlertDialog(
            modal=True,
            title=ft.Text("Apariencia" if permite_cancelar else "Elige el diseño de Eva"),
            content=ft.Column(self._crear_tarjetas(), tight=True, width=620, scroll=ft.ScrollMode.AUTO),
            actions=acciones,
        )
        self.boton_aplicar = acciones[-1]

    def _crear_tarjetas(self) -> list[ft.Control]:
        tarjetas = []
        for clave, diseno in DISENOS.items():
            tarjeta = ft.Container(
                content=ft.Row(
                    [
                        ft.Container(
                            width=110,
                            height=58,
                            bgcolor=diseno.fondo_chat,
                            border=ft.Border.all(2, diseno.acento),
                            border_radius=diseno.radio,
                            content=ft.Icon(ft.Icons.AUTO_AWESOME, color=diseno.acento, size=28),
                            alignment=ft.Alignment.CENTER,
                        ),
                        ft.Column(
                            [ft.Text(diseno.nombre, size=16, weight=ft.FontWeight.W_600), ft.Text(self._descripcion(clave), size=12)],
                            spacing=3,
                            expand=True,
                        ),
                    ],
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                padding=12,
                border_radius=diseno.radio,
                border=ft.Border.all(1, diseno.acento),
                on_click=lambda e, clave=clave: self._seleccionar(clave),
            )
            self.tarjetas[clave] = tarjeta
            tarjetas.append(tarjeta)
        return tarjetas

    @staticmethod
    def _descripcion(clave: str) -> str:
        return {
            "brisa": "Claro, suave y centrado en el historial.",
            "marea": "Oscuro, profundo y con doble panel.",
            "horizonte": "Claro, organizado por pestañas y tareas.",
        }[clave]

    def _seleccionar(self, clave: str) -> None:
        self.seleccion = clave
        self.boton_aplicar.disabled = False
        self.al_preview(clave)
        for nombre, tarjeta in self.tarjetas.items():
            tarjeta.bgcolor = DISENOS[nombre].superficie_mensaje if nombre == clave else None
        self.page.update()

    def _cancelar(self, e) -> None:
        self.page.pop_dialog()
        if self._original is not None:
            self.al_preview(self._original)

    def _aplicar(self, e) -> None:
        if self.seleccion is None:
            return
        guardar_diseno(self.config, self.seleccion)
        guardar_config(self.config)
        self.page.pop_dialog()
        self.al_aplicar(self.seleccion)

    def abrir(self) -> None:
        self.page.show_dialog(self.dialogo)
