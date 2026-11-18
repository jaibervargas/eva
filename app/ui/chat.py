"""Main screen: conversation sidebar and chat with streamed answers."""

import logging
import threading

import flet as ft

from app.config import PROVEEDORES, Config
from app.llm.base import ClienteLLM, ErrorLLM
from app.llm.cloud_client import conectar_nube, crear_cliente
from app.llm.conversacion import Conversacion, MemoriaUsuario
from app.llm.local_client import conectar
from app.llm.sesion_chatgpt import cliente_respaldo, conectar_chatgpt
from app.secretos import ErrorSecretos, leer_clave
from app.memory.history import Historial
from app.ui.ajustes import DialogoAjustes
from app.ui.microfono import Microfono
from app.ui.recuerdos import DialogoRecuerdos
from app.ui.reproductor import ESCUCHAR, NO_PUDE_LEER, VozEva, modo_valido
from app.voice.stt import ErrorVoz, Transcriptor
from app.voice.tts import DESCARGANDO as DESCARGANDO_VOZ
from app.voice.tts import MENSAJE_SIN_DESCARGA as SIN_DESCARGA_VOZ
from app.voice.tts import voz_valida

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


def _burbuja(texto: ft.Text, de_usuario: bool, boton: ft.Control | None = None) -> ft.Control:
    """User bubbles on the right in blue, Eva's on the left in gray (with
    ``boton`` beside it, e.g. "Escuchar")."""
    globo = ft.Container(
        content=texto,
        bgcolor=AZUL_USUARIO if de_usuario else GRIS_EVA,
        border_radius=RADIO,
        padding=ft.Padding.symmetric(horizontal=14, vertical=10),
        expand_loose=True,
    )
    return ft.Container(
        content=ft.Row(
            [globo] if boton is None else [globo, boton],
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
        transcriptor: Transcriptor | None = None,
        grabador=None,
        voz: VozEva | None = None,
    ) -> None:
        self.page = page
        # Eva reads her answers aloud through the page's audio player; a page
        # that cannot play audio (e.g. a test double) leaves her silent.
        self.voz = voz if voz is not None else (VozEva(page) if isinstance(page, ft.Page) else None)
        self.historial = historial
        self.config = config
        # Long-term memory (Engram); None when it could not even be created.
        self.memoria = memoria
        self.cliente: ClienteLLM | None = None
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
        # Speak instead of typing; the recorder and transcriber can be replaced in tests.
        self.microfono = Microfono(self, transcriptor, grabador)

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
            [self.mensajes, ft.Container(ft.Row([self.campo, self.microfono.boton, self.boton]), padding=12)],
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
            # A cloud chat model already did the extraction: no second try.
            respaldo = None if PROVEEDORES[self.config.proveedor].nube else self._respaldo_nube
            self.memoria.aprender(cliente, texto, respaldo=respaldo)
        except Exception:  # aprender never raises; this is a last guard
            log.exception("Unexpected error saving long-term memories")
        try:
            self._actualizar_nota_memoria()
            self.page.update()
        except Exception:  # e.g. the window is closing: keep the extractor alive
            log.exception("Could not refresh the screen after learning")

    def _respaldo_nube(self) -> ClienteLLM | None:
        """Cloud client for fact extraction (the last cloud provider saved in
        settings), or ``None`` when there is none, its key is missing or its
        ChatGPT account is signed out."""
        respaldo = self.config.extras.get("respaldo")
        if not isinstance(respaldo, dict):
            return None
        proveedor, modelo = respaldo.get("proveedor"), respaldo.get("modelo")
        if not (isinstance(proveedor, str) and proveedor in PROVEEDORES and PROVEEDORES[proveedor].nube):
            return None
        if not (isinstance(modelo, str) and modelo):
            return None
        if PROVEEDORES[proveedor].cuenta:
            return cliente_respaldo(respaldo.get("cuenta"), modelo)
        clave = leer_clave(proveedor)
        return crear_cliente(proveedor, modelo, clave) if clave else None

    def _abrir_recuerdos(self) -> None:
        if self.memoria is not None:
            DialogoRecuerdos(self.page, self.memoria).abrir()

    # ----- model connection and settings -----

    def _ocupar(self, ocupado: bool) -> None:
        with self._estado:
            self.ocupado = ocupado
            self._estado.notify_all()
        self.boton.disabled = ocupado
        self.microfono.actualizar()

    def _conectar_en_segundo_plano(
        self, texto: str | None = None, del_campo: bool = True, por_voz: bool = False
    ) -> None:
        """Connect off the UI loop (it may wait for the server); then send ``texto`` if given."""
        self._ocupar(True)
        if texto is not None:
            # Lock the field so nothing typed while connecting can be lost.
            self.campo.disabled = True
        self.etiqueta_modelo.value = CONECTANDO
        self.page.update()
        self.page.run_thread(self._conectar_y_enviar, texto, del_campo, por_voz)

    def _conectar_y_enviar(self, texto: str | None, del_campo: bool = True, por_voz: bool = False) -> None:
        try:
            self._conectar()  # failures normally become the connection-error bubble
        except Exception:
            log.exception("Unexpected error while connecting")
            self.cliente = None
        finally:
            if texto is not None:
                self.campo.disabled = False  # only undo the lock this path set
        if texto is not None and self.cliente is None and not del_campo and not (self.campo.value or "").strip():
            # A spoken question is kept for retrying, like a typed one.
            self.campo.value = texto
        if texto is not None and self.cliente is not None:
            respuesta = self._empezar_turno(texto, del_campo)
            self._responder(texto, respuesta, por_voz)  # releases the screen when done
            return
        self._ocupar(False)
        self.page.update()

    def _conectar(self) -> None:
        datos = PROVEEDORES[self.config.proveedor]
        proveedor = datos.nombre
        try:
            if datos.cuenta:
                self.cliente = conectar_chatgpt(self.config.extras.get("chatgpt_cuenta"), self.config.modelo)
            elif datos.nube:
                clave = leer_clave(self.config.proveedor)
                self.cliente = conectar_nube(self.config.proveedor, self.config.modelo, clave)
            else:
                self.cliente = conectar(self.config.servidor, self.config.modelo)
        except Exception as exc:
            if isinstance(exc, (ErrorLLM, ErrorSecretos)):
                mensaje = str(exc)
            else:
                log.exception("Error inesperado al conectar con el modelo")
                mensaje = f"Ocurrió un error inesperado: {exc}"
            self.cliente = None
            self.etiqueta_modelo.value = f"{proveedor} · sin conexión"
            self._mostrar_error_conexion(mensaje)
        else:
            # ChatGPT models have a display name besides their id.
            nombre = getattr(self.cliente, "nombre_modelo", None) or self.cliente.modelo
            self.etiqueta_modelo.value = f"{proveedor} · {nombre}"
            self._quitar_error_conexion()

    def _abrir_ajustes(self) -> None:
        if not self.ocupado:
            DialogoAjustes(self.page, self.config, self._ajustes_guardados, voz=self.voz).abrir()

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
        self.mensajes.controls = []
        for m in self.historial.mensajes(conversacion_id):
            texto = ft.Text(m["content"], color=TEXTO, selectable=True)
            de_usuario = m["role"] == "user"
            self.mensajes.controls.append(_burbuja(texto, de_usuario, None if de_usuario else self._boton_escuchar(texto)))
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

    def mostrar_error(self, mensaje: str) -> None:
        """Show an error as a red bubble from Eva."""
        self._agregar(ft.Text(mensaje, color=ROJO_ERROR, selectable=True), de_usuario=False)

    def mostrar_aviso(self, texto: str) -> ft.Text:
        """Show a temporary status bubble; remove it with ``quitar_aviso``."""
        aviso = ft.Text(texto, color=GRIS_TEXTO, italic=True)
        self._agregar(aviso, de_usuario=False)
        return aviso

    def quitar_aviso(self, aviso: ft.Text) -> None:
        # By identity: Flet controls compare equal by value.
        self.mensajes.controls = [c for c in self.mensajes.controls if c.content.controls[0].content is not aviso]

    def _quitar_error_conexion(self) -> None:
        if self._error_conexion is not None:
            # By identity: Flet controls compare equal by value.
            self.mensajes.controls = [c for c in self.mensajes.controls if c is not self._error_conexion]
        self._error_conexion = None

    async def _enviar(self, e) -> None:
        await self.enviar_texto((self.campo.value or "").strip(), del_campo=True)

    async def enviar_texto(self, texto: str, del_campo: bool = False, por_voz: bool = False) -> None:
        """Send a question. ``del_campo``: it is the field's text, cleared once shown
        (a spoken question leaves whatever was typed in the field). ``por_voz``:
        it came from the microphone (Eva may answer aloud)."""
        if not texto or self.ocupado:
            return
        if self.cliente is None:
            # The server may have been started after the app opened; the
            # question stays in the field until the connection succeeds.
            self._conectar_en_segundo_plano(texto, del_campo, por_voz)
            return
        self._ocupar(True)
        respuesta = self._empezar_turno(texto, del_campo)
        await self.campo.focus()
        # Streaming blocks while waiting for the model, so it runs off the UI loop.
        self.page.run_thread(self._responder, texto, respuesta, por_voz)

    def _empezar_turno(self, texto: str, del_campo: bool = True) -> ft.Text:
        """Show the question and an answer bubble that will fill as Eva answers."""
        if del_campo:
            self.campo.value = ""
        self._agregar(ft.Text(texto, color=TEXTO, selectable=True), de_usuario=True)
        respuesta = ft.Text(PENSANDO, color=TEXTO, selectable=True)
        self._agregar(respuesta, de_usuario=False)
        self.page.update()
        return respuesta

    def _responder(self, texto: str, respuesta: ft.Text, por_voz: bool = False) -> None:
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
            if acumulado:
                self._agregar_escuchar(respuesta)
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
        if acumulado and respuesta.color != ROJO_ERROR and self._responder_en_voz(por_voz):
            self._hablar(acumulado)  # this thread is already off the UI loop

    # ----- Eva's voice -----

    def _responder_en_voz(self, por_voz: bool) -> bool:
        """Whether a finished answer is read aloud on its own (S4)."""
        modo = modo_valido(self.config.extras.get("responder_en_voz"))
        return self.voz is not None and (modo == "siempre" or (modo == "voz" and por_voz))

    def _boton_escuchar(self, texto: ft.Text) -> ft.Control | None:
        """The "Escuchar" button of an answer bubble (None without a voice)."""
        if self.voz is None:
            return None
        return ft.IconButton(
            icon=ft.Icons.VOLUME_UP,
            icon_size=18,
            icon_color=GRIS_TEXTO,
            tooltip=ESCUCHAR,
            # Read at click time: the bubble holds the whole answer by then.
            on_click=lambda e: self.page.run_thread(self._hablar, texto.value),
        )

    def _agregar_escuchar(self, respuesta: ft.Text) -> None:
        boton = self._boton_escuchar(respuesta)
        if boton is None:
            return
        for burbuja in self.mensajes.controls:
            fila = burbuja.content
            # By identity: Flet controls compare equal by value.
            if fila.controls[0].content is respuesta and len(fila.controls) == 1:
                fila.controls.append(boton)
                return

    def _hablar(self, texto: str) -> None:
        """Read ``texto`` aloud with the saved voice. Blocks: run it off the UI loop.

        A failure leaves a gray note; the answer stays as text."""
        if self.voz is None:
            return
        aviso: ft.Text | None = None

        def al_descargar() -> None:
            nonlocal aviso
            aviso = self.mostrar_aviso(DESCARGANDO_VOZ)
            self.page.update()  # show the bubble before the download starts

        nota = None
        try:
            self.voz.hablar(texto, voz_valida(self.config.extras.get("voz_eva")), al_descargar)
        except ErrorVoz as exc:
            nota = SIN_DESCARGA_VOZ if str(exc) == SIN_DESCARGA_VOZ else NO_PUDE_LEER
        except Exception:
            log.exception("Unexpected error reading the answer aloud")
            nota = NO_PUDE_LEER
        if aviso is not None:
            self.quitar_aviso(aviso)
        if nota is not None:
            self.mostrar_aviso(nota)
        if aviso is not None or nota is not None:
            self.page.update()
