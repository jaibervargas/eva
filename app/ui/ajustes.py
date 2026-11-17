"""Settings dialog: choose the local provider and one of its installed models."""

import logging
from collections.abc import Callable

import flet as ft

from app.config import PROVEEDORES, Config, guardar_config, resolver_modelo
from app.llm.base import ErrorLLM
from app.llm.local_client import ClienteLocal

log = logging.getLogger(__name__)

ROJO_ERROR = "#F87171"
GRIS_TEXTO = "#9CA3AF"
CONECTANDO = "Conectando…"


class DialogoAjustes:
    def __init__(self, page: ft.Page, config: Config, al_guardar: Callable[[Config], None]) -> None:
        self.page = page
        self.config = config
        self.al_guardar = al_guardar

        self.proveedor = ft.Dropdown(
            label="Proveedor",
            value=config.proveedor,
            options=[ft.DropdownOption(key=k, text=p.nombre) for k, p in PROVEEDORES.items()],
            on_select=self._cambio_proveedor,
            expand=True,
        )
        self.modelo = ft.Dropdown(label="Modelo", options=[], expand=True)
        self.estado = ft.Text("", size=12, color=GRIS_TEXTO)
        self.boton_guardar = ft.FilledButton("Guardar", on_click=self._guardar)
        self.dialogo = ft.AlertDialog(
            modal=True,
            title=ft.Text("Ajustes"),
            content=ft.Column(
                [
                    ft.Row([self.proveedor]),
                    ft.Row(
                        [
                            self.modelo,
                            ft.IconButton(
                                icon=ft.Icons.REFRESH,
                                tooltip="Volver a buscar modelos",
                                on_click=lambda e: self._cargar_modelos(),
                            ),
                        ]
                    ),
                    self.estado,
                ],
                tight=True,
                width=420,
            ),
            actions=[ft.TextButton("Cancelar", on_click=lambda e: self.page.pop_dialog()), self.boton_guardar],
        )

    def abrir(self) -> None:
        self.page.show_dialog(self.dialogo)
        self._cargar_modelos()

    def _cambio_proveedor(self, e) -> None:
        self._cargar_modelos()

    def _cargar_modelos(self) -> None:
        """Fill the model list from the selected provider's server, off the UI loop."""
        proveedor = self.proveedor.value
        self._carga = getattr(self, "_carga", 0) + 1
        self.estado.value, self.estado.color = CONECTANDO, GRIS_TEXTO
        self.boton_guardar.disabled = True
        self.page.update()
        self.page.run_thread(self._buscar_modelos, proveedor, self._carga)

    def _buscar_modelos(self, proveedor: str, carga: int = 0) -> None:
        url = PROVEEDORES[proveedor].url
        try:
            modelos = ClienteLocal(modelo="", base_url=url).listar_modelos()
        except ErrorLLM as exc:
            modelos = []
            error = str(exc)
        except Exception as exc:  # never leave the dialog stuck on "Conectando…"
            log.exception("Unexpected error listing models")
            modelos = []
            error = f"Ocurrió un error inesperado: {exc}"
        else:
            error = None
        if carga != getattr(self, "_carga", carga) or self.proveedor.value != proveedor:
            return  # a newer load (or another provider) started meanwhile; it wins
        if error is not None:
            self.estado.value, self.estado.color = error, ROJO_ERROR
        else:
            if modelos:
                self.estado.value = f"{len(modelos)} modelos instalados en {url}"
                self.estado.color = GRIS_TEXTO
            else:
                self.estado.value = "El servidor no tiene modelos de chat instalados."
                self.estado.color = ROJO_ERROR
        self.modelo.options = [ft.DropdownOption(key=m, text=m) for m in modelos]
        self.modelo.value = resolver_modelo(self.config.modelo, modelos) or None
        self.boton_guardar.disabled = not modelos
        self.page.update()

    def _guardar(self, e) -> None:
        nueva = Config(proveedor=self.proveedor.value, modelo=self.modelo.value or "", extras=self.config.extras)
        try:
            guardar_config(nueva)
        except OSError as exc:
            self.estado.value = f"No pude guardar los ajustes: {exc}"
            self.estado.color = ROJO_ERROR
            self.page.update()
            return
        self.page.pop_dialog()
        self.al_guardar(nueva)
