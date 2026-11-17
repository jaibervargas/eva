"""Settings dialog: choose a local or cloud provider and one of its models.

Cloud providers need an API key (or, for OpenRouter, a sign-in); keys go to
the OS keyring through ``app.secretos``, never to ``config.json``.
"""

import logging
import threading
from collections.abc import Callable

import flet as ft

from app.auth import ErrorAuth, iniciar_sesion_openrouter
from app.config import PROVEEDORES, Config, guardar_config, resolver_modelo
from app.llm.base import ErrorLLM
from app.llm.cloud_client import crear_cliente
from app.llm.local_client import ClienteLocal
from app.secretos import ErrorSecretos, borrar_clave, guardar_clave, leer_clave

log = logging.getLogger(__name__)

ROJO_ERROR = "#F87171"
GRIS_TEXTO = "#9CA3AF"
CONECTANDO = "Conectando…"
CLAVE_GUARDADA = "Clave guardada"
INICIAR_OPENROUTER = "Iniciar sesión con OpenRouter"
CANCELAR_OPENROUTER = "Cancelar inicio de sesión"
ESPERANDO_NAVEGADOR = "Esperando el inicio de sesión en el navegador…"


class DialogoAjustes:
    def __init__(self, page: ft.Page, config: Config, al_guardar: Callable[[Config], None]) -> None:
        self.page = page
        self.config = config
        self.al_guardar = al_guardar
        self._inicio_sesion: threading.Event | None = None  # set while signing in

        self.proveedor = ft.Dropdown(
            label="Proveedor",
            value=config.proveedor,
            options=[ft.DropdownOption(key=k, text=p.nombre) for k, p in PROVEEDORES.items()],
            on_select=self._cambio_proveedor,
            expand=True,
        )
        self.clave = ft.TextField(
            label="Clave de API",
            password=True,
            can_reveal_password=True,
            on_submit=lambda e: self._cargar_modelos(),
            expand=True,
        )
        self.boton_borrar = ft.TextButton("Borrar clave", on_click=self._borrar_clave)
        self.boton_openrouter = ft.OutlinedButton(INICIAR_OPENROUTER, on_click=self._iniciar_sesion)
        self.fila_nube = ft.Column(
            [ft.Row([self.clave, self.boton_borrar]), self.boton_openrouter],
            tight=True,
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
                    self.fila_nube,
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
            actions=[ft.TextButton("Cancelar", on_click=self._cancelar), self.boton_guardar],
        )
        self._mostrar_campos()

    def abrir(self) -> None:
        self.page.show_dialog(self.dialogo)
        self._cargar_modelos()

    def _es_nube(self, proveedor: str | None = None) -> bool:
        return PROVEEDORES[proveedor or self.proveedor.value].nube

    def _mostrar_campos(self) -> None:
        self.fila_nube.visible = self._es_nube()
        self.boton_openrouter.visible = self.proveedor.value == "openrouter"

    def _cambio_proveedor(self, e) -> None:
        self._cancelar_inicio_sesion()
        self.clave.value = ""
        self.clave.hint_text = None
        self._mostrar_campos()
        self._cargar_modelos()

    def _cancelar(self, e) -> None:
        self._cancelar_inicio_sesion()
        self.page.pop_dialog()

    # ----- models -----

    def _cargar_modelos(self) -> None:
        """Fill the model list from the selected provider, off the UI loop."""
        proveedor = self.proveedor.value
        self._carga = getattr(self, "_carga", 0) + 1
        self.estado.value, self.estado.color = CONECTANDO, GRIS_TEXTO
        self.boton_guardar.disabled = True
        self.page.update()
        self.page.run_thread(self._buscar_modelos, proveedor, self._carga, (self.clave.value or "").strip())

    def _buscar_modelos(self, proveedor: str, carga: int = 0, clave_escrita: str = "") -> None:
        datos = PROVEEDORES[proveedor]
        aviso = None  # a gray hint instead of a red error
        guardada = None
        try:
            if datos.nube:
                guardada = leer_clave(proveedor)
                clave = clave_escrita or guardada
                if clave:
                    modelos = crear_cliente(proveedor, "", clave).listar_modelos()
                else:
                    modelos = []
                    aviso = self._pedir_clave(proveedor)
            else:
                modelos = ClienteLocal(modelo="", base_url=datos.url).listar_modelos()
        except (ErrorLLM, ErrorSecretos) as exc:
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
        if datos.nube:
            self.clave.hint_text = CLAVE_GUARDADA if guardada else None
        if error is not None:
            self.estado.value, self.estado.color = error, ROJO_ERROR
        elif aviso is not None:
            self.estado.value, self.estado.color = aviso, GRIS_TEXTO
        elif modelos:
            donde = f"disponibles en {datos.nombre}" if datos.nube else f"instalados en {datos.url}"
            self.estado.value, self.estado.color = f"{len(modelos)} modelos {donde}", GRIS_TEXTO
        else:
            vacio = (
                f"Tu cuenta de {datos.nombre} no tiene modelos de chat."
                if datos.nube
                else "El servidor no tiene modelos de chat instalados."
            )
            self.estado.value, self.estado.color = vacio, ROJO_ERROR
        self.modelo.options = [ft.DropdownOption(key=m, text=m) for m in modelos]
        self.modelo.value = resolver_modelo(self.config.modelo, modelos) or None
        self.boton_guardar.disabled = not modelos
        self.page.update()

    @staticmethod
    def _pedir_clave(proveedor: str) -> str:
        nombre = PROVEEDORES[proveedor].nombre
        if proveedor == "openrouter":
            return f"Escribe tu clave de API de {nombre} o inicia sesión para ver sus modelos."
        return f"Escribe tu clave de API de {nombre} para ver sus modelos."

    # ----- keys and sign-in -----

    def _borrar_clave(self, e) -> None:
        proveedor = self.proveedor.value
        try:
            borrar_clave(proveedor)
        except ErrorSecretos as exc:
            self.estado.value, self.estado.color = str(exc), ROJO_ERROR
            self.page.update()
            return
        self.clave.value = ""
        self.clave.hint_text = None
        self.modelo.options, self.modelo.value = [], None
        self.boton_guardar.disabled = True
        self.estado.value = f"Clave de {PROVEEDORES[proveedor].nombre} borrada."
        self.estado.color = GRIS_TEXTO
        self.page.update()

    def _iniciar_sesion(self, e) -> None:
        """Start the OpenRouter sign-in; a second click cancels it."""
        if self._inicio_sesion is not None:
            self._cancelar_inicio_sesion()
            return
        cancelado = threading.Event()
        self._inicio_sesion = cancelado
        self.boton_openrouter.content = CANCELAR_OPENROUTER
        self.estado.value, self.estado.color = ESPERANDO_NAVEGADOR, GRIS_TEXTO
        self.page.update()
        self.page.run_thread(self._esperar_inicio_sesion, cancelado)

    def _cancelar_inicio_sesion(self) -> None:
        if self._inicio_sesion is not None:
            self._inicio_sesion.set()

    def _esperar_inicio_sesion(self, cancelado: threading.Event) -> None:
        try:
            clave = iniciar_sesion_openrouter(cancelado=cancelado)
            guardar_clave("openrouter", clave)
        except (ErrorAuth, ErrorSecretos) as exc:
            error = str(exc)
        except Exception as exc:
            log.exception("Unexpected error signing in with OpenRouter")
            error = f"Ocurrió un error inesperado: {exc}"
        else:
            error = None
        if self._inicio_sesion is cancelado:
            self._inicio_sesion = None
            self.boton_openrouter.content = INICIAR_OPENROUTER
        if error is not None:
            self.estado.value, self.estado.color = error, ROJO_ERROR
            self.page.update()
        elif self.proveedor.value == "openrouter":
            self.clave.value = ""  # the saved key is used from now on
            self._cargar_modelos()

    # ----- save -----

    def _guardar(self, e) -> None:
        proveedor = self.proveedor.value
        clave = (self.clave.value or "").strip()
        try:
            if self._es_nube(proveedor) and clave:
                guardar_clave(proveedor, clave)
        except ErrorSecretos as exc:
            self.estado.value, self.estado.color = str(exc), ROJO_ERROR
            self.page.update()
            return
        modelo = self.modelo.value or ""
        extras = dict(self.config.extras)
        if self._es_nube(proveedor):
            # The last cloud model saved also extracts memories when the local
            # model fails (no key here: it stays in the keyring).
            extras["respaldo"] = {"proveedor": proveedor, "modelo": modelo}
        nueva = Config(proveedor=proveedor, modelo=modelo, extras=extras)
        try:
            guardar_config(nueva)
        except OSError as exc:
            self.estado.value = f"No pude guardar los ajustes: {exc}"
            self.estado.color = ROJO_ERROR
            self.page.update()
            return
        self._cancelar_inicio_sesion()
        self.page.pop_dialog()
        self.al_guardar(nueva)
