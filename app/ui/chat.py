"""Chat screen: message bubbles and an input field, with streamed answers."""

import flet as ft

from app.llm.base import ErrorLLM
from app.llm.conversacion import Conversacion

FONDO = "#0F1117"
AZUL_USUARIO = "#2563EB"
GRIS_EVA = "#1F2937"
TEXTO = "#E5E7EB"
ROJO_ERROR = "#F87171"
RADIO = 12
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
    def __init__(self, page: ft.Page, conversacion: Conversacion | None, error_inicial: str = "") -> None:
        self.page = page
        self.conversacion = conversacion
        self.ocupado = False

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

        if error_inicial:
            self._agregar(ft.Text(error_inicial, color=ROJO_ERROR, selectable=True), de_usuario=False)

    def vista(self) -> ft.Control:
        return ft.Column(
            [self.mensajes, ft.Container(ft.Row([self.campo, self.boton]), padding=12)],
            expand=True,
        )

    def _agregar(self, texto: ft.Text, de_usuario: bool) -> None:
        self.mensajes.controls.append(_burbuja(texto, de_usuario))

    async def _enviar(self, e) -> None:
        texto = (self.campo.value or "").strip()
        if not texto or self.ocupado or self.conversacion is None:
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
        acumulado = ""
        try:
            for parte in self.conversacion.enviar(texto):
                acumulado += parte
                respuesta.value = acumulado
                self.page.update()
            if not acumulado:
                respuesta.value = "(Eva no devolvió texto)"
        except ErrorLLM as exc:
            respuesta.value = str(exc)
            respuesta.color = ROJO_ERROR
        finally:
            self.ocupado = False
            self.boton.disabled = False
            self.page.update()
