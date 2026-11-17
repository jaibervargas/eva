"""Main screen: conversation sidebar and chat with streamed answers."""

import flet as ft

from app.config import PROVEEDORES, Config
from app.llm.base import ErrorLLM
from app.llm.conversacion import Conversacion
from app.llm.local_client import ClienteLocal, conectar
from app.memory.history import Historial
from app.ui.ajustes import DialogoAjustes

FONDO = "#0F1117"
FONDO_LATERAL = "#161A23"
AZUL_USUARIO = "#2563EB"
GRIS_EVA = "#1F2937"
TEXTO = "#E5E7EB"
GRIS_TEXTO = "#9CA3AF"
ROJO_ERROR = "#F87171"
RADIO = 12
ANCHO_LATERAL = 260
PENSANDO = "…"


def _burbuja(texto: ft.Text, de_usuario: bool) -> ft.Control:
    """User bubbles on the right in blue, Eva's on the left in gray."""
    globo = ft.Container(
        content=texto,
        bgcolor=AZUL_USUARIO if de_usuario else GRIS_EVA,
        border_radius=RADIO,
        padding=ft.Padding.symmetric(horizontal=14, vertical=10),
        expand_loose=True,
    )
    return ft.Container(
        content=ft.Row(
            [globo],
            alignment=ft.MainAxisAlignment.END if de_usuario else ft.MainAxisAlignment.START,
        ),
        padding=ft.Padding.only(left=60) if de_usuario else ft.Padding.only(right=60),
    )


class PantallaChat:
    def __init__(self, page: ft.Page, historial: Historial, config: Config) -> None:
        self.page = page
        self.historial = historial
        self.config = config
        self.cliente: ClienteLocal | None = None
        self.conversacion_id: int | None = None  # None: new, not yet saved
        self.ocupado = False

        # Chat area
        self.mensajes = ft.ListView(expand=True, spacing=10, padding=16, auto_scroll=True)
        self.campo = ft.TextField(
            hint_text="Escríbele a Eva…",
            expand=True,
            autofocus=True,
            shift_enter=True,
            min_lines=1,
            max_lines=5,
            border=ft.OutlineInputBorder(border_radius=RADIO),
            color=TEXTO,
            on_submit=self._enviar,
        )
        self.boton = ft.IconButton(icon=ft.Icons.SEND_ROUNDED, icon_color=AZUL_USUARIO, on_click=self._enviar)

        # Sidebar
        self.lista = ft.ListView(expand=True, spacing=2)
        self.etiqueta_modelo = ft.Text("", size=12, color=GRIS_TEXTO, max_lines=2, overflow=ft.TextOverflow.ELLIPSIS)

    # ----- layout -----

    def vista(self) -> ft.Control:
        lateral = ft.Container(
            width=ANCHO_LATERAL,
            bgcolor=FONDO_LATERAL,
            padding=12,
            content=ft.Column(
                [
                    ft.FilledButton(
                        "Nueva conversación",
                        icon=ft.Icons.ADD,
                        on_click=lambda e: self._nueva(),
                        width=ANCHO_LATERAL,
                    ),
                    ft.Text("Conversaciones", size=12, color=GRIS_TEXTO),
                    self.lista,
                    ft.Divider(height=1),
                    ft.Row(
                        [
                            ft.Container(self.etiqueta_modelo, expand=True),
                            ft.IconButton(icon=ft.Icons.SETTINGS, tooltip="Ajustes", on_click=lambda e: self._abrir_ajustes()),
                        ]
                    ),
                ],
                expand=True,
            ),
        )
        chat = ft.Column(
            [self.mensajes, ft.Container(ft.Row([self.campo, self.boton]), padding=12)],
            expand=True,
        )
        return ft.Row([lateral, chat], expand=True, spacing=0)

    def iniciar(self) -> None:
        """Connect to the model server and show the saved conversations."""
        self._conectar()
        self._refrescar_lista()
        self.page.update()

    # ----- model connection and settings -----

    def _conectar(self) -> None:
        proveedor = PROVEEDORES[self.config.proveedor].nombre
        try:
            self.cliente = conectar(self.config.servidor, self.config.modelo)
        except ErrorLLM as exc:
            self.cliente = None
            self.etiqueta_modelo.value = f"{proveedor} · sin conexión"
            self._mostrar_error(str(exc))
        else:
            self.etiqueta_modelo.value = f"{proveedor} · {self.cliente.modelo}"

    def _abrir_ajustes(self) -> None:
        if not self.ocupado:
            DialogoAjustes(self.page, self.config, self._ajustes_guardados).abrir()

    def _ajustes_guardados(self, config: Config) -> None:
        self.config = config
        self._conectar()
        self.page.update()

    # ----- conversations -----

    def _refrescar_lista(self) -> None:
        self.lista.controls = [
            ft.ListTile(
                title=ft.Text(c.titulo, max_lines=1, overflow=ft.TextOverflow.ELLIPSIS, size=14),
                selected=c.id == self.conversacion_id,
                selected_tile_color=GRIS_EVA,
                dense=True,
                on_click=lambda e, cid=c.id: self._abrir(cid),
            )
            for c in self.historial.listar_conversaciones()
        ]

    def _nueva(self) -> None:
        if self.ocupado:
            return
        self.conversacion_id = None
        self.mensajes.controls.clear()
        self._refrescar_lista()
        self.page.update()

    def _abrir(self, conversacion_id: int) -> None:
        if self.ocupado:
            return
        self.conversacion_id = conversacion_id
        self.mensajes.controls = [
            _burbuja(ft.Text(m["content"], color=TEXTO, selectable=True), de_usuario=m["role"] == "user")
            for m in self.historial.mensajes(conversacion_id)
        ]
        self._refrescar_lista()
        self.page.update()

    # ----- chat -----

    def _agregar(self, texto: ft.Text, de_usuario: bool) -> None:
        self.mensajes.controls.append(_burbuja(texto, de_usuario))

    def _mostrar_error(self, mensaje: str) -> None:
        self._agregar(ft.Text(mensaje, color=ROJO_ERROR, selectable=True), de_usuario=False)

    async def _enviar(self, e) -> None:
        texto = (self.campo.value or "").strip()
        if not texto or self.ocupado:
            return
        if self.cliente is None:
            # The server may have been started after the app opened.
            self._conectar()
            if self.cliente is None:
                self.page.update()
                return
        self.ocupado = True
        self.campo.value = ""
        self.boton.disabled = True
        self._agregar(ft.Text(texto, color=TEXTO, selectable=True), de_usuario=True)
        respuesta = ft.Text(PENSANDO, color=TEXTO, selectable=True)
        self._agregar(respuesta, de_usuario=False)
        self.page.update()
        await self.campo.focus()
        # Streaming blocks while waiting for the model, so it runs off the UI loop.
        self.page.run_thread(self._responder, texto, respuesta)

    def _responder(self, texto: str, respuesta: ft.Text) -> None:
        conversacion = Conversacion(self.cliente, self.historial, self.conversacion_id)
        acumulado = ""
        try:
            for parte in conversacion.enviar(texto):
                acumulado += parte
                respuesta.value = acumulado
                self.page.update()
            if not acumulado:
                respuesta.value = "(Eva no devolvió texto)"
            self.conversacion_id = conversacion.conversacion_id
        except ErrorLLM as exc:
            respuesta.value = str(exc)
            respuesta.color = ROJO_ERROR
        finally:
            self.ocupado = False
            self.boton.disabled = False
            self._refrescar_lista()
            self.page.update()
