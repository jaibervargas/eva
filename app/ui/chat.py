"""Main screen: conversation sidebar and chat with streamed answers."""

import logging
import threading

import flet as ft

from app.config import PROVEEDORES, Config
from app.llm.base import ErrorLLM
from app.llm.conversacion import Conversacion, MemoriaUsuario
from app.llm.local_client import ClienteLocal, conectar
from app.memory.history import Historial
from app.ui.ajustes import DialogoAjustes
from app.ui.recuerdos import DialogoRecuerdos

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
CONECTANDO = "Conectando…"
SIN_MEMORIA = "Memoria a largo plazo no disponible"

log = logging.getLogger(__name__)


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
    def __init__(
        self,
        page: ft.Page,
        historial: Historial,
        config: Config,
        memoria: MemoriaUsuario | None = None,
    ) -> None:
        self.page = page
        self.historial = historial
        self.config = config
        # Long-term memory (Engram); None when it could not even be created.
        self.memoria = memoria
        self.cliente: ClienteLocal | None = None
        self.conversacion_id: int | None = None  # None: new, not yet saved
        # True while connecting or answering: send, new, open and settings wait.
        self.ocupado = False
        # Guards ocupado and the extraction queue; notified when either changes.
        self._estado = threading.Condition()
        # User messages waiting for fact extraction, and whether an extractor runs.
        self._por_aprender: list[str] = []
        self._aprendiendo = False
        self._cliente_aprender = None
        self._error_conexion: ft.Control | None = None  # at most one shown

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
        # Discreet note under the model label; hidden while memory works.
        self.nota_memoria = ft.Text(SIN_MEMORIA, size=11, color=GRIS_TEXTO, italic=True, visible=False)
        self.boton_recuerdos = ft.TextButton(
            "Recuerdos",
            icon=ft.Icons.PSYCHOLOGY_OUTLINED,
            tooltip="Lo que Eva recuerda de ti",
            on_click=lambda e: self._abrir_recuerdos(),
            disabled=memoria is None,
        )

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
                    self.boton_recuerdos,
                    ft.Divider(height=1),
                    ft.Row(
                        [
                            ft.Container(
                                ft.Column([self.etiqueta_modelo, self.nota_memoria], spacing=2, tight=True),
                                expand=True,
                            ),
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
        """Show the saved conversations, connect to the model server and load
        the long-term memory (both off the UI loop)."""
        self._refrescar_lista()
        self._conectar_en_segundo_plano()
        if self.memoria is None:
            self._actualizar_nota_memoria()
        else:
            self.page.run_thread(self._cargar_memoria)

    # ----- long-term memory -----

    def _actualizar_nota_memoria(self) -> None:
        self.nota_memoria.visible = self.memoria is None or not self.memoria.disponible

    def _cargar_memoria(self) -> None:
        try:
            self.memoria.cargar()
        except Exception:  # cargar logs its own failures; this is a last guard
            log.exception("Unexpected error loading long-term memory")
        self._actualizar_nota_memoria()
        self.page.update()

    def _encolar_aprendizaje(self, cliente, texto: str) -> None:
        """Queue the user's message for fact extraction. At most one extractor
        runs; messages queued meanwhile are coalesced into its next extraction."""
        with self._estado:
            self._por_aprender.append(texto)
            self._cliente_aprender = cliente
            if self._aprendiendo:
                return
            self._aprendiendo = True
        # Its own thread: the screen is already free for the next question.
        self.page.run_thread(self._aprender_en_cola)

    def _aprender_en_cola(self) -> None:
        """Extract queued messages, one extraction at a time, never starting
        one while an answer is being streamed."""
        while True:
            with self._estado:
                while self.ocupado:
                    self._estado.wait()
                if not self._por_aprender:
                    self._aprendiendo = False
                    return
                textos, self._por_aprender = self._por_aprender, []
                cliente = self._cliente_aprender
            self._aprender(cliente, "\n".join(textos))

    def _aprender(self, cliente, texto: str) -> None:
        """Save facts from the user's message(s) (background, after the answer)."""
        try:
            self.memoria.aprender(cliente, texto)
        except Exception:  # aprender never raises; this is a last guard
            log.exception("Unexpected error saving long-term memories")
        try:
            self._actualizar_nota_memoria()
            self.page.update()
        except Exception:  # e.g. the window is closing: keep the extractor alive
            log.exception("Could not refresh the screen after learning")

    def _abrir_recuerdos(self) -> None:
        if self.memoria is not None:
            DialogoRecuerdos(self.page, self.memoria).abrir()

    # ----- model connection and settings -----

    def _ocupar(self, ocupado: bool) -> None:
        with self._estado:
            self.ocupado = ocupado
            self._estado.notify_all()
        self.boton.disabled = ocupado

    def _conectar_en_segundo_plano(self, texto: str | None = None) -> None:
        """Connect off the UI loop (it may wait for the server); then send ``texto`` if given."""
        self._ocupar(True)
        if texto is not None:
            # Lock the field so nothing typed while connecting can be lost.
            self.campo.disabled = True
        self.etiqueta_modelo.value = CONECTANDO
        self.page.update()
        self.page.run_thread(self._conectar_y_enviar, texto)

    def _conectar_y_enviar(self, texto: str | None) -> None:
        try:
            self._conectar()  # failures normally become the connection-error bubble
        except Exception:
            log.exception("Unexpected error while connecting")
            self.cliente = None
        finally:
            if texto is not None:
                self.campo.disabled = False  # only undo the lock this path set
        if texto is not None and self.cliente is not None:
            respuesta = self._empezar_turno(texto)
            self._responder(texto, respuesta)  # releases the screen when done
            return
        self._ocupar(False)
        self.page.update()

    def _conectar(self) -> None:
        proveedor = PROVEEDORES[self.config.proveedor].nombre
        try:
            self.cliente = conectar(self.config.servidor, self.config.modelo)
        except Exception as exc:
            if isinstance(exc, ErrorLLM):
                mensaje = str(exc)
            else:
                log.exception("Error inesperado al conectar con el modelo")
                mensaje = f"Ocurrió un error inesperado: {exc}"
            self.cliente = None
            self.etiqueta_modelo.value = f"{proveedor} · sin conexión"
            self._mostrar_error_conexion(mensaje)
        else:
            self.etiqueta_modelo.value = f"{proveedor} · {self.cliente.modelo}"
            self._quitar_error_conexion()

    def _abrir_ajustes(self) -> None:
        if not self.ocupado:
            DialogoAjustes(self.page, self.config, self._ajustes_guardados).abrir()

    def _ajustes_guardados(self, config: Config) -> None:
        self.config = config
        self._conectar_en_segundo_plano()

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
        self._error_conexion = None
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
        self._error_conexion = None
        self._refrescar_lista()
        self.page.update()

    # ----- chat -----

    def _agregar(self, texto: ft.Text, de_usuario: bool) -> None:
        self.mensajes.controls.append(_burbuja(texto, de_usuario))

    def _mostrar_error_conexion(self, mensaje: str) -> None:
        """Show a connection error, replacing the previous one instead of piling up."""
        self._quitar_error_conexion()
        texto = ft.Text(mensaje, color=ROJO_ERROR, selectable=True)
        self._agregar(texto, de_usuario=False)
        self._error_conexion = self.mensajes.controls[-1]

    def _quitar_error_conexion(self) -> None:
        if self._error_conexion is not None:
            # By identity: Flet controls compare equal by value.
            self.mensajes.controls = [c for c in self.mensajes.controls if c is not self._error_conexion]
        self._error_conexion = None

    async def _enviar(self, e) -> None:
        texto = (self.campo.value or "").strip()
        if not texto or self.ocupado:
            return
        if self.cliente is None:
            # The server may have been started after the app opened; the
            # question stays in the field until the connection succeeds.
            self._conectar_en_segundo_plano(texto)
            return
        self._ocupar(True)
        respuesta = self._empezar_turno(texto)
        await self.campo.focus()
        # Streaming blocks while waiting for the model, so it runs off the UI loop.
        self.page.run_thread(self._responder, texto, respuesta)

    def _empezar_turno(self, texto: str) -> ft.Text:
        """Show the question and an answer bubble that will fill as Eva answers."""
        self.campo.value = ""
        self._agregar(ft.Text(texto, color=TEXTO, selectable=True), de_usuario=True)
        respuesta = ft.Text(PENSANDO, color=TEXTO, selectable=True)
        self._agregar(respuesta, de_usuario=False)
        self.page.update()
        return respuesta

    def _responder(self, texto: str, respuesta: ft.Text) -> None:
        cliente = self.cliente
        conversacion = Conversacion(cliente, self.historial, self.conversacion_id, memoria=self.memoria)
        acumulado = ""
        aprender = False
        try:
            for parte in conversacion.enviar(texto):
                acumulado += parte
                respuesta.value = acumulado
                self.page.update()
            if not acumulado:
                respuesta.value = "(Eva no devolvió texto)"
            self.conversacion_id = conversacion.conversacion_id
            aprender = bool(acumulado) and self.memoria is not None
        except ErrorLLM as exc:
            respuesta.value = str(exc)
            respuesta.color = ROJO_ERROR
        except Exception as exc:
            # Any other failure (e.g. the history database) must still reach the user.
            log.exception("Error inesperado al responder")
            respuesta.value = f"Ocurrió un error inesperado: {exc}"
            respuesta.color = ROJO_ERROR
        finally:
            self._ocupar(False)
            self._refrescar_lista()
            self._actualizar_nota_memoria()
            self.page.update()
        if aprender:
            self._encolar_aprendizaje(cliente, texto)
