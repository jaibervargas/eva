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
from app.ui.disenos import DISENOS, EstadoInterfaz, diseno_valido, leer_diseno
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
AVATAR_SRC = "docs/referencias/Meshy_AI_EvaSeedOfLight.png"
RADIO = 12
ANCHO_LATERAL = 280
ANCHO_PANEL_EVA = 248
PENSANDO = "…"
ESTADO_PENSANDO = "Pensando…"
CONECTANDO = "Conectando…"
LISTO = "Listo"
ESCUCHANDO = "Escuchando…"
HABLANDO = "Hablando…"
SIN_MEMORIA = "Memoria a largo plazo no disponible"

log = logging.getLogger(__name__)


def _burbuja(texto: ft.Text, de_usuario: bool, boton: ft.Control | None = None, avatar: ft.Control | None = None, fondo: str | None = None) -> ft.Control:
    """User bubbles on the right in blue, Eva's on the left in gray (with
    ``boton`` beside it, e.g. "Escuchar")."""
    globo = ft.Container(
        content=texto,
        bgcolor=fondo or (AZUL_USUARIO if de_usuario else GRIS_EVA),
        border=ft.Border.all(1, "#4C7884") if fondo == "#0B3442" else None,
        border_radius=RADIO,
        padding=ft.Padding.symmetric(horizontal=14, vertical=10),
        expand_loose=True,
    )
    controles = [globo] if boton is None else [globo, boton]
    if avatar is not None:
        controles.insert(0, avatar)
    return ft.Container(
        content=ft.Row(
            controles,
            alignment=ft.MainAxisAlignment.END if de_usuario else ft.MainAxisAlignment.START,
            vertical_alignment=ft.CrossAxisAlignment.END,
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
        self.estado_ui = EstadoInterfaz(diseno=leer_diseno(config))
        self.diseno = self.estado_ui.diseno
        self.color_texto = TEXTO
        self.titulo_conversacion = ft.Text("Nueva conversación", size=22, weight=ft.FontWeight.W_600, color=TEXTO)
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
        self.estado = ft.Text("", size=12, color=GRIS_TEXTO)
        self.estado_panel = ft.Text("", size=12, color=GRIS_TEXTO)
        self.estado_isla = ft.Text("Eva", size=11, color=GRIS_TEXTO)
        self.lateral: ft.Control | None = None
        self.panel_eva: ft.Control | None = None
        self._restaurar_isla = lambda: None
        self._activar_isla = lambda: setattr(self.page.window, "minimized", True)
        self._cambiar_chrome = lambda custom: None

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
        self.lista_historial = ft.ListView(expand=True, spacing=2)
        self.historial_visible = False
        self.pestanas = ft.Row(scroll=ft.ScrollMode.AUTO, spacing=4)
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

    def aplicar_diseno(self, diseno: str) -> None:
        """Apply a catalog design and rebuild only the presentation shell."""
        clave = diseno_valido(diseno)
        if clave is None:
            return
        # A design change is a presentation transition, never a window-mode
        # transition. Restore the native window first if the island is open so
        # the next layout is not left transparent, frameless, or off-screen.
        if getattr(getattr(self, "_isla_control", None), "visible", False):
            self._restaurar_isla()
        self.estado_ui.diseno = clave
        self.diseno = clave
        tema = DISENOS[clave]
        ventana = getattr(self.page, "window", None)
        if ventana is not None:
            # Marea owns the top bar; hide Flet's native title-bar buttons to
            # avoid showing two minimize/maximize/close sets.
            ventana.title_bar_hidden = clave == "marea"
            ventana.title_bar_buttons_hidden = clave == "marea"
            self._cambiar_chrome(clave == "marea")
        self.page.bgcolor = tema.fondo_chat
        self.color_texto = tema.texto
        self.campo.bgcolor = tema.fondo_chat
        self.campo.border = ft.OutlineInputBorder(
            border_radius=tema.radio, side=ft.BorderSide(1, tema.acento)
        )
        self.boton.icon_color = tema.acento
        if hasattr(self, "_isla_surface"):
            self._isla_surface.bgcolor = tema.fondo_chat
            self._isla_surface.border = ft.Border.all(1, tema.acento)
        fondos = {"brisa": ("#B8F0E8", "#DDF3FA"), "marea": ("#0B3442", "#102F3A"), "horizonte": ("#CBEAF0", "#DDF1F5")}[clave]
        for burbuja in self.mensajes.controls:
            fila = getattr(burbuja, "content", None)
            controles = getattr(fila, "controls", [])
            if controles:
                controles[0].bgcolor = fondos[1]
                texto = getattr(controles[0], "content", None)
                if texto is not None:
                    texto.color = tema.texto
        if hasattr(self, "_shell"):
            nuevo = self._construir_layout()
            self._shell.controls = nuevo.controls
        self.page.update()

    def _crear_menu(self, ancho: int, incluir_historial: bool = True) -> ft.Container:
        tema = DISENOS.get(self.diseno or "brisa", DISENOS["brisa"])
        avatar = (
            ft.Image(src="docs/referencias/Meshy_AI_EvaSeedOfLight.png", width=42, height=42)
            if self.diseno == "brisa"
            else ft.Icon(ft.Icons.AUTO_AWESOME, color=tema.acento, size=30)
        )
        controles = [
            ft.Row(
                [avatar, ft.Text("Eva", size=20, weight=ft.FontWeight.W_600, color=tema.texto)],
                spacing=8,
            ),
            ft.FilledButton("Nueva conversación", icon=ft.Icons.ADD, on_click=lambda e: self._nueva(), width=ancho),
            ft.TextButton("Historial", icon=ft.Icons.HISTORY, on_click=lambda e: self._alternar_historial()),
            self.boton_recuerdos,
        ]
        if incluir_historial:
            controles.extend([ft.Text("Conversaciones", size=12, color=tema.texto), self.lista])
        controles.extend(
            [
                ft.Container(expand=True),
                ft.Divider(height=1),
                ft.Row(
                    [
                        ft.Container(ft.Column([self.etiqueta_modelo, self.nota_memoria], spacing=2, tight=True), expand=True),
                        ft.IconButton(icon=ft.Icons.SETTINGS, tooltip="Ajustes", on_click=lambda e: self._abrir_ajustes()),
                    ]
                ),
            ]
        )
        return ft.Container(width=ancho, bgcolor=tema.fondo_lateral, padding=16, content=ft.Column(controles, expand=True))

    @staticmethod
    def _fecha_corta(fecha: str) -> str:
        if not fecha:
            return ""
        try:
            from datetime import datetime, timezone

            cuando = datetime.fromisoformat(fecha)
            ahora = datetime.now(timezone.utc)
            dias = (ahora.date() - cuando.date()).days
            return "Hoy" if dias == 0 else "Ayer" if dias == 1 else "Esta semana" if dias < 7 else cuando.strftime("%d/%m/%Y")
        except (TypeError, ValueError):
            return ""

    def _filas_marea(self) -> list[ft.Control]:
        tema = DISENOS["marea"]
        filas = []
        datos = self.historial.listar_conversaciones_con_fecha()
        for cid, titulo, fecha in datos:
            activo = cid == self.conversacion_id
            indicador = ft.Container(width=3, bgcolor=tema.acento if activo else "#00000000", border_radius=2)
            fila = ft.Container(
                content=ft.Row(
                    [indicador, ft.Column([ft.Text(titulo, color=tema.texto, max_lines=1, overflow=ft.TextOverflow.ELLIPSIS), ft.Text(self._fecha_corta(fecha), color="#A8C0CC", size=12)], spacing=2, expand=True)],
                    spacing=8,
                ),
                bgcolor="#103742" if activo else None,
                border_radius=10,
                padding=ft.Padding.symmetric(horizontal=8, vertical=9),
                tooltip=titulo,
                on_click=lambda e, cid=cid: self._abrir(cid),
            )
            filas.append(fila)
        return filas

    def _crear_historial_marea(self) -> ft.Container:
        tema = DISENOS["marea"]
        self.lista_historial.controls = self._filas_marea()
        self.historial_marea = ft.Container(
            width=258,
            visible=self.historial_visible,
            bgcolor=tema.fondo_historial,
            padding=14,
            border=ft.Border.only(right=ft.BorderSide(1, "#193844")),
            content=ft.Column([ft.Text("Historial", size=18, color=tema.texto), self.lista_historial], expand=True),
        )
        return self.historial_marea

    def _controles_ventana_marea(self) -> ft.Row:
        def boton(icon, tooltip, accion):
            return ft.IconButton(icon=icon, tooltip=tooltip, icon_size=18, width=32, height=32, padding=0, on_click=accion)

        return ft.Row(
            [
                boton(ft.Icons.MINIMIZE, "Minimizar a isla dinámica", lambda e: self._activar_isla()),
                boton(ft.Icons.CROP_SQUARE, "Maximizar o restaurar", lambda e: self._alternar_maximizado()),
                boton(ft.Icons.CLOSE, "Cerrar Eva", lambda e: self.page.window.close()),
            ],
            spacing=2,
            tight=True,
        )

    def _alternar_maximizado(self) -> None:
        self.page.window.maximized = not bool(getattr(self.page.window, "maximized", False))
        self.page.window.update()

    def _alternar_historial(self) -> None:
        self.historial_visible = not self.historial_visible
        if hasattr(self, "historial_marea"):
            self.historial_marea.visible = self.historial_visible
            self.page.update()

    def _crear_barra(self) -> ft.Container:
        controles = self._controles_ventana_marea() if self.diseno == "marea" else None
        libre = ft.GestureDetector(content=ft.Container(expand=True), expand=True, on_pan_start=lambda e: self.page.window.start_dragging()) if self.diseno == "marea" else ft.Container(expand=True)
        piezas = [self.titulo_conversacion, libre]
        if controles:
            piezas.append(ft.Container(content=controles, alignment=ft.Alignment(1, 0)))
        else:
            piezas.append(self.estado)
        return ft.Container(
            bgcolor=DISENOS.get(self.diseno or "brisa", DISENOS["brisa"]).fondo_historial,
            content=ft.Row(piezas),
            height=40 if self.diseno == "marea" else None,
            padding=ft.Padding.only(left=20, right=8, top=4 if self.diseno == "marea" else 14, bottom=4 if self.diseno == "marea" else 8),
        )

    def _crear_panel_eva(self) -> ft.Container:
        tema = DISENOS.get(self.diseno or "brisa", DISENOS["brisa"])
        return ft.Container(
            width=ANCHO_PANEL_EVA,
            bgcolor=tema.fondo_historial,
            border_radius=22,
            padding=20,
            content=ft.Column(
                [ft.Icon(ft.Icons.AUTO_AWESOME, size=48, color=tema.acento), ft.Text("Eva", size=22, weight=ft.FontWeight.W_600, color=tema.texto), ft.Text("Tu espacio para pensar, hablar y crear.", color=GRIS_TEXTO, size=12), ft.Divider(height=20, color="#2B3445"), self.estado_panel],
                spacing=8,
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            ),
        )

    def _crear_tabs_horizonte(self) -> ft.Row:
        self.pestanas.controls = []
        for resumen in self.historial.listar_conversaciones()[:6]:
            self.pestanas.controls.append(ft.TextButton(resumen.titulo, on_click=lambda e, cid=resumen.id: self._abrir(cid)))
        return self.pestanas

    def _construir_layout(self) -> ft.Row:
        clave = self.diseno or "brisa"
        barra = self._crear_barra()
        composer = ft.Container(ft.Row([self.campo, self.microfono.boton, self.boton]), padding=16)
        if clave == "marea":
            self.lateral = self._crear_menu(210, incluir_historial=False)
            historial = self._crear_historial_marea()
            self.panel_eva = None
            chat = ft.Column([ft.Column([barra, self.mensajes], expand=True), composer], expand=True)
            return ft.Row([self.lateral, historial, chat], expand=True, spacing=0)
        self.lateral = self._crear_menu(ANCHO_LATERAL)
        self.panel_eva = self._crear_panel_eva()
        if clave == "horizonte":
            cabecera = ft.Column([self._crear_tabs_horizonte(), barra], tight=True)
            chat = ft.Column([ft.Column([cabecera, self.mensajes], expand=True), composer], expand=True)
            return ft.Row([self.lateral, chat], expand=True, spacing=0)
        self.panel_eva = None
        chat = ft.Column([ft.Column([barra, self.mensajes], expand=True), composer], expand=True)
        return ft.Row([self.lateral, chat], expand=True, spacing=0)

    def vista(self) -> ft.Control:
        self._shell = self._construir_layout()
        self._ajustar_layout(None)
        if self.diseno is not None:
            self.aplicar_diseno(self.diseno)
        self.page.on_resize = self._ajustar_layout
        return self._shell

    def vista_isla(self) -> ft.Control:
        """Compact view used while the desktop window is minimized."""
        tema = DISENOS.get(self.diseno or "brisa", DISENOS["brisa"])
        self._isla_surface = ft.Container(
            bgcolor=tema.fondo_chat,
            border=ft.Border.all(1, tema.acento),
            border_radius=28,
            padding=ft.Padding.symmetric(horizontal=16, vertical=6),
            content=ft.Row(
                [
                    ft.Icon(ft.Icons.AUTO_AWESOME, color="#67E8F9", size=28),
                    ft.Column([ft.Text("Eva", color=TEXTO, weight=ft.FontWeight.W_600), self.estado_isla], spacing=0, expand=True),
                    ft.IconButton(ft.Icons.OPEN_IN_NEW, tooltip="Volver a la ventana", on_click=lambda e: self._restaurar_isla()),
                ],
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
        )
        self._isla_control = ft.GestureDetector(
            visible=False,
            content=self._isla_surface,
            on_pan_start=lambda e: self.page.window.start_dragging(),
        )
        return self._isla_control

    def asignar_restaurar_isla(self, restaurar) -> None:
        self._restaurar_isla = restaurar

    def asignar_activar_isla(self, activar) -> None:
        self._activar_isla = activar

    def asignar_chrome(self, cambiar) -> None:
        self._cambiar_chrome = cambiar

    def _ajustar_layout(self, e) -> None:
        """Collapse secondary panels when the window becomes tablet-sized."""
        ancho = getattr(self.page, "width", None)
        if not isinstance(ancho, (int, float)) or ancho <= 0:
            ancho = 1200
        compacto = ancho < 900
        movil = ancho < 680
        if self.diseno == "marea" and hasattr(self, "historial_marea"):
            estrecho = ancho < 900
            self.lateral.width = 165 if estrecho else 210
            self.historial_marea.width = 190 if estrecho else 258
            self.lateral.visible = ancho >= 720
            self.historial_marea.visible = self.historial_visible and ancho >= 720
        elif self.lateral is not None:
            self.lateral.visible = not compacto
        if self.panel_eva is not None:
            self.panel_eva.visible = not compacto
        if movil:
            self.campo.max_lines = 3
        elif self.campo.max_lines != 5:
            self.campo.max_lines = 5
        if getattr(self.page, "controls", None):
            self.page.update()

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
        except RuntimeError as exc:  # the background extractor may outlive the Flet window
            if "destroyed session" not in str(exc).lower():
                log.exception("Could not refresh the screen after learning")
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

    def _poner_estado(self, texto: str, color: str) -> None:
        visible = "" if texto == LISTO else texto
        self.estado.value = visible
        self.estado.color = color
        self.estado_panel.value = visible
        self.estado_panel.color = color
        self.estado_isla.value = "Eva" if texto == LISTO else texto
        self.estado_isla.color = color

    def _ocupar(self, ocupado: bool) -> None:
        with self._estado:
            self.ocupado = ocupado
            self._estado.notify_all()
        self.boton.disabled = ocupado
        self.microfono.actualizar()
        self._poner_estado(ESTADO_PENSANDO if ocupado else LISTO, GRIS_TEXTO)

    def _conectar_en_segundo_plano(
        self, texto: str | None = None, del_campo: bool = True, por_voz: bool = False
    ) -> None:
        """Connect off the UI loop (it may wait for the server); then send ``texto`` if given."""
        self._ocupar(True)
        if texto is not None:
            # Lock the field so nothing typed while connecting can be lost.
            self.campo.disabled = True
        self.etiqueta_modelo.value = CONECTANDO
        self._poner_estado(CONECTANDO, GRIS_TEXTO)
        self.page.update()
        self.page.run_thread(self._conectar_y_enviar, texto, del_campo, por_voz)

    def _conectar_y_enviar(self, texto: str | None, del_campo: bool = True, por_voz: bool = False) -> None:
        try:
            self._conectar()  # failures normally become the connection-error bubble
        except Exception as exc:
            log.exception("Unexpected error while connecting")
            self.cliente = None
            self.etiqueta_modelo.value = f"{PROVEEDORES[self.config.proveedor].nombre} · sin conexión"
            self._poner_estado("Sin conexión", ROJO_ERROR)
            self._mostrar_error_conexion(f"Ocurrió un error inesperado al conectar con el modelo: {exc}")
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
        if self.cliente is None:
            self._poner_estado("Sin conexión", ROJO_ERROR)
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
            self._poner_estado("Sin conexión", ROJO_ERROR)
            self._mostrar_error_conexion(mensaje)
        else:
            # ChatGPT models have a display name besides their id.
            nombre = getattr(self.cliente, "nombre_modelo", None) or self.cliente.modelo
            self.etiqueta_modelo.value = f"{proveedor} · {nombre}"
            self._poner_estado(LISTO, GRIS_TEXTO)
            self._quitar_error_conexion()

    def _abrir_ajustes(self) -> None:
        if not self.ocupado:
            opciones = {"voz": self.voz, "al_apariencia": self._apariencia_aplicada}
            try:
                dialogo = DialogoAjustes(self.page, self.config, self._ajustes_guardados, **opciones)
            except TypeError as exc:
                if "al_apariencia" not in str(exc):
                    raise
                # Keep small test/integration doubles compatible with the real dialog.
                dialogo = DialogoAjustes(self.page, self.config, self._ajustes_guardados, voz=self.voz)
            dialogo.abrir()

    def _apariencia_aplicada(self, config: Config) -> None:
        self.config = config
        diseno = leer_diseno(config)
        if diseno is not None:
            self.aplicar_diseno(diseno)

    def _ajustes_guardados(self, config: Config) -> None:
        self.config = config
        self._conectar_en_segundo_plano()

    # ----- conversations -----

    def _refrescar_lista(self) -> None:
        conversaciones = self.historial.listar_conversaciones()

        def fila(c):
            return ft.ListTile(
                title=ft.Text(c.titulo, max_lines=1, overflow=ft.TextOverflow.ELLIPSIS, size=14),
                selected=c.id == self.conversacion_id,
                selected_tile_color=GRIS_EVA,
                dense=True,
                on_click=lambda e, cid=c.id: self._abrir(cid),
            )

        self.lista.controls = [fila(c) for c in conversaciones]
        self.lista_historial.controls = self._filas_marea() if self.diseno == "marea" else [fila(c) for c in conversaciones]
        if self.diseno == "horizonte":
            self._crear_tabs_horizonte()

    def _nueva(self) -> None:
        if self.ocupado:
            return
        self.conversacion_id = None
        self.estado_ui.conversacion_id = None
        self.mensajes.controls.clear()
        self._error_conexion = None
        self._refrescar_lista()
        self.page.update()

    def _abrir(self, conversacion_id: int) -> None:
        if self.ocupado:
            return
        self.conversacion_id = conversacion_id
        self.estado_ui.conversacion_id = conversacion_id
        resumen = next((c for c in self.historial.listar_conversaciones() if c.id == conversacion_id), None)
        self.titulo_conversacion.value = resumen.titulo if resumen else "Conversación"
        self.mensajes.controls = []
        for m in self.historial.mensajes(conversacion_id):
            texto = ft.Text(m["content"], color=self.color_texto, selectable=True)
            de_usuario = m["role"] == "user"
            self.mensajes.controls.append(
                _burbuja(texto, de_usuario, None if de_usuario else self._boton_escuchar(texto), self._avatar_mensaje() if not de_usuario else self._icono_usuario(), self._fondo_mensaje(de_usuario))
            )
        self._error_conexion = None
        self._refrescar_lista()
        self.page.update()

    # ----- chat -----

    def _avatar_mensaje(self) -> ft.Control | None:
        if not isinstance(self.page, ft.Page):
            return None
        return ft.Image(src=AVATAR_SRC, width=34, height=34)

    def _icono_usuario(self) -> ft.Control | None:
        if not isinstance(self.page, ft.Page) or self.diseno != "marea":
            return None
        return ft.Icon(ft.Icons.PERSON_OUTLINE, color="#A8C0CC", size=22)

    def _fondo_mensaje(self, de_usuario: bool) -> str:
        if self.diseno == "brisa":
            return "#B8F0E8" if de_usuario else "#DDF3FA"
        if self.diseno == "marea":
            return "#0B3442" if de_usuario else "#00000000"
        if self.diseno == "horizonte":
            return "#CBEAF0" if de_usuario else "#DDF1F5"
        return AZUL_USUARIO if de_usuario else GRIS_EVA

    def _agregar(self, texto: ft.Text, de_usuario: bool) -> None:
        self.mensajes.controls.append(
            _burbuja(texto, de_usuario, avatar=self._avatar_mensaje() if not de_usuario else self._icono_usuario(), fondo=self._fondo_mensaje(de_usuario))
        )

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
        if self.conversacion_id is None:
            self.titulo_conversacion.value = texto[:42] + ("…" if len(texto) > 42 else "")
        self.estado_ui.borrador = "" if del_campo else (self.campo.value or "")
        self.estado_ui.respuesta_en_curso = True
        self._agregar(ft.Text(texto, color=self.color_texto, selectable=True), de_usuario=True)
        respuesta = ft.Text(PENSANDO, color=self.color_texto, selectable=True)
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
            self.estado_ui.respuesta_en_curso = False
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
