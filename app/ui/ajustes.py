"""Settings dialog: choose a local or cloud provider and one of its models.

Cloud providers need an API key (or, for OpenRouter, a sign-in); keys go to
the OS keyring through ``app.secretos``, never to ``config.json``. ChatGPT uses
a saved account instead (see ``app.ui.panel_chatgpt``).

Eva's voice and when she answers aloud are saved in ``config.json`` as
``extras["voz_eva"]`` and ``extras["responder_en_voz"]``.
"""

import logging
import threading
from copy import deepcopy
from collections.abc import Callable

import flet as ft

from app.auth import MENSAJE_CANCELADO, ErrorAuth, iniciar_sesion_openrouter
from app.config import PROVEEDORES, Config, guardar_config, resolver_modelo
from app.llm.base import ErrorLLM
from app.llm.cloud_client import crear_cliente
from app.llm.local_client import ClienteLocal
from app.secretos import ErrorSecretos, borrar_clave, guardar_clave, leer_clave
from app.ui.disenos import DISENOS, SelectorDiseno, diseno_valido, leer_diseno
from app.ui.panel_chatgpt import PanelChatGPT
from app.ui.reproductor import MODO_POR_DEFECTO, MODOS, PRUEBA, VozEva, modo_valido
from app.voice.stt import ErrorVoz
from app.voice.tts import DESCARGANDO as DESCARGANDO_VOZ
from app.voice.tts import PREFIJO_FALLO as FALLO_VOZ
from app.voice.tts import VOCES, VOZ_POR_DEFECTO, voz_valida

log = logging.getLogger(__name__)

ROJO_ERROR = "#F87171"
GRIS_TEXTO = "#9CA3AF"
CONECTANDO = "Conectando…"
CLAVE_GUARDADA = "Clave guardada"
INICIAR_OPENROUTER = "Iniciar sesión con OpenRouter"
CANCELAR_OPENROUTER = "Cancelar inicio de sesión"
ESPERANDO_NAVEGADOR = "Esperando el inicio de sesión en el navegador…"
ABRE_LA_DIRECCION = "No pude abrir el navegador. Abre esta dirección para iniciar sesión con OpenRouter: {url}"


class DialogoAjustes:
    def __init__(
        self,
        page: ft.Page,
        config: Config,
        al_guardar: Callable[[Config], None],
        voz: VozEva | None = None,
        al_apariencia: Callable[[Config], None] | None = None,
    ) -> None:
        self.page = page
        self.config = config
        self.al_guardar = al_guardar
        self.voz = voz  # reads the "Probar voz" sample; None disables the button
        self.al_apariencia = al_apariencia or (lambda config: None)
        self._inicio_sesion: threading.Event | None = None  # set while signing in

        clave_diseno = diseno_valido(leer_diseno(config)) or "marea"
        self.diseno = DISENOS[clave_diseno]
        self.proveedor = ft.Dropdown(
            label=None,
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
        self.modelo = ft.Dropdown(label=None, options=[], expand=True)
        # Selectable so the sign-in address can be copied when no browser opens.
        self.estado = ft.Text("", size=12, color=GRIS_TEXTO, selectable=True)
        self.chatgpt = PanelChatGPT(page, self.estado, self._cargar_modelos, self._vaciar_modelos)
        self.boton_guardar = ft.FilledButton("Guardar", on_click=self._guardar)
        self.voz_eva = ft.Dropdown(
            label="Voz de Eva",
            value=voz_valida(config.extras.get("voz_eva")),
            options=[ft.DropdownOption(key=k, text=v.nombre) for k, v in VOCES.items()],
            expand=True,
        )
        self.boton_probar = ft.OutlinedButton(
            "Probar voz", icon=ft.Icons.VOLUME_UP, on_click=self._probar_voz, disabled=voz is None
        )
        self.responder = ft.Dropdown(
            label="Responder en voz alta",
            value=modo_valido(config.extras.get("responder_en_voz")),
            options=[ft.DropdownOption(key=k, text=texto) for k, texto in MODOS.items()],
            expand=True,
        )
        self.estado_voz = ft.Text("", size=12, color=GRIS_TEXTO, selectable=True)
        for control in (self.proveedor, self.modelo, self.voz_eva, self.responder):
            control.color = self.diseno.texto
            control.bgcolor = self.diseno.superficie_mensaje
            control.border = ft.OutlineInputBorder(side=ft.BorderSide(1, self.diseno.acento))
            control.focused_border = ft.OutlineInputBorder(side=ft.BorderSide(2, self.diseno.acento))
        self.boton_probar.style = ft.ButtonStyle(
            color=self.diseno.texto,
            side=ft.BorderSide(1, self.diseno.acento),
            shape=ft.RoundedRectangleBorder(radius=self.diseno.radio),
        )
        self.boton_apariencia = ft.OutlinedButton(
            content=ft.Row(
                [
                    ft.Text(self.diseno.nombre, color=self.diseno.texto),
                    ft.Icon(ft.Icons.CHEVRON_RIGHT, color=self.diseno.texto),
                ],
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            ),
            on_click=self._abrir_apariencia,
            expand=True,
            style=ft.ButtonStyle(
                color=self.diseno.texto,
                side=ft.BorderSide(1, self.diseno.acento),
                shape=ft.RoundedRectangleBorder(radius=self.diseno.radio),
            ),
        )
        self.boton_guardar.style = ft.ButtonStyle(
            bgcolor={ft.ControlState.DEFAULT: self.diseno.acento},
            color={ft.ControlState.DEFAULT: self.diseno.texto},
            shape=ft.RoundedRectangleBorder(radius=self.diseno.radio),
        )
        campo = ft.TextStyle(color=self.diseno.texto)
        self.dialogo = ft.AlertDialog(
            modal=True,
            bgcolor=self.diseno.fondo_lateral,
            shape=ft.RoundedRectangleBorder(radius=self.diseno.radio),
            title=ft.Row(
                [
                    ft.Text("Ajustes", size=28, weight=ft.FontWeight.W_600, color=self.diseno.texto),
                    ft.IconButton(
                        icon=ft.Icons.CLOSE,
                        icon_color=self.diseno.texto,
                        tooltip="Cerrar",
                        on_click=self._cancelar,
                    ),
                ],
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            ),
            content=ft.Column(
                [
                    self._seccion("Modelo de IA", ft.Icons.MEMORY),
                    self._etiqueta("Proveedor"),
                    ft.Row([self.proveedor]),
                    self.fila_nube,
                    self.chatgpt.contenedor,
                    self._etiqueta("Modelo"),
                    ft.Row(
                        [
                            self.modelo,
                            ft.IconButton(
                                icon=ft.Icons.REFRESH,
                                icon_color=self.diseno.acento,
                                tooltip="Volver a buscar modelos",
                                on_click=lambda e: self._cargar_modelos(),
                            ),
                        ]
                    ),
                    ft.Row(
                        [ft.Container(width=8, height=8, bgcolor=self.diseno.acento, border_radius=4), self.estado],
                        spacing=8,
                    ),
                    ft.Divider(height=1, color=self.diseno.acento),
                    self._seccion("Voz", ft.Icons.GRAPHIC_EQ),
                    self._etiqueta("Voz de Eva"),
                    ft.Row([self.voz_eva, self.boton_probar]),
                    self._etiqueta("Responder en voz alta"),
                    ft.Row([self.responder]),
                    ft.Divider(height=1, color=self.diseno.acento),
                    self._seccion("Apariencia", ft.Icons.PALETTE),
                    # Inside a Row the button expands sideways; as a direct
                    # child of the Column its expand would grow vertically and
                    # leave the row without a clickable height.
                    ft.Row([self.boton_apariencia]),
                    self.estado_voz,
                ],
                tight=True,
                width=560,
                spacing=8,
            ),
            actions=[
                ft.TextButton(
                    "Cancelar",
                    on_click=self._cancelar,
                    style=ft.ButtonStyle(color=self.diseno.texto),
                ),
                self.boton_guardar,
            ],
        )
        self._mostrar_campos()

    def _etiqueta(self, texto: str) -> ft.Text:
        return ft.Text(texto, color=self.diseno.texto, size=13, weight=ft.FontWeight.W_500)

    def _seccion(self, texto: str, icono: str) -> ft.Row:
        return ft.Row(
            [
                ft.Icon(icono, color=self.diseno.acento, size=24),
                ft.Text(texto, color=self.diseno.texto, size=18, weight=ft.FontWeight.W_600),
            ],
            spacing=10,
        )

    def abrir(self) -> None:
        self.page.show_dialog(self.dialogo)
        self._cargar_modelos()

    def _abrir_apariencia(self, e) -> None:
        SelectorDiseno(
            self.page,
            self.config,
            lambda diseno: self.al_apariencia(self.config),
            al_preview=lambda diseno: self.al_apariencia(self._config_con_diseno(diseno)),
            permite_cancelar=True,
        ).abrir()

    def _config_con_diseno(self, diseno: str) -> Config:
        temporal = Config(self.config.proveedor, self.config.modelo, deepcopy(self.config.extras))
        from app.ui.disenos import guardar_diseno

        guardar_diseno(temporal, diseno)
        return temporal

    def _es_nube(self, proveedor: str | None = None) -> bool:
        return PROVEEDORES[proveedor or self.proveedor.value].nube

    def _con_clave(self, proveedor: str | None = None) -> bool:
        """A cloud provider used with an API key (not with a saved account)."""
        datos = PROVEEDORES[proveedor or self.proveedor.value]
        return datos.nube and not datos.cuenta

    def _mostrar_campos(self) -> None:
        self.fila_nube.visible = self._con_clave()
        self.chatgpt.contenedor.visible = PROVEEDORES[self.proveedor.value].cuenta
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
        self.page.run_thread(
            self._buscar_modelos, proveedor, self._carga, (self.clave.value or "").strip(), self.chatgpt.cuenta.value
        )

    def _vaciar_modelos(self) -> None:
        """No models (e.g. after signing out); any load still running is dropped."""
        self._carga = getattr(self, "_carga", 0) + 1
        self.modelo.options, self.modelo.value = [], None
        self.boton_guardar.disabled = True

    def _buscar_modelos(
        self, proveedor: str, carga: int = 0, clave_escrita: str = "", cuenta: str | None = None
    ) -> None:
        datos = PROVEEDORES[proveedor]
        aviso = None  # a gray hint instead of a red error
        error = None
        guardada = None
        consulta = None  # ChatGPT's accounts
        try:
            if datos.cuenta:
                consulta = self.chatgpt.consultar(cuenta, self.config.extras.get("chatgpt_cuenta"))
                modelos, error, aviso = consulta.modelos, consulta.error, consulta.aviso
            elif datos.nube:
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
        if carga != getattr(self, "_carga", carga) or self.proveedor.value != proveedor:
            return  # a newer load (or another provider) started meanwhile; it wins
        if consulta is not None:
            self.chatgpt.mostrar(consulta)
        elif datos.nube:
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
        # ChatGPT lists (slug, display name); the others just the model id.
        opciones = [m if isinstance(m, tuple) else (m, m) for m in modelos]
        self.modelo.options = [ft.DropdownOption(key=clave, text=texto) for clave, texto in opciones]
        self.modelo.value = resolver_modelo(self.config.modelo, [clave for clave, _ in opciones]) or None
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
        self.chatgpt.cancelar()

    def _mostrar_direccion(self, cancelado: threading.Event, url: str) -> None:
        """No browser opened: show the address; the sign-in keeps waiting."""
        if self._inicio_sesion is not cancelado or cancelado.is_set():
            return
        self.estado.value = ABRE_LA_DIRECCION.format(url=url)
        self.estado.color = GRIS_TEXTO
        self.page.update()

    def _esperar_inicio_sesion(self, cancelado: threading.Event) -> None:
        try:
            clave = iniciar_sesion_openrouter(
                cancelado=cancelado,
                al_sin_navegador=lambda url: self._mostrar_direccion(cancelado, url),
            )
            # Cancelling (dialog closed, provider changed, second click) is
            # honoured until the key is saved.
            if cancelado.is_set():
                raise ErrorAuth(MENSAJE_CANCELADO)
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

    # ----- voice -----

    def _probar_voz(self, e) -> None:
        """Read the sample aloud with the selected (maybe unsaved) voice."""
        if self.voz is None:
            return
        self.boton_probar.disabled = True
        self.estado_voz.value, self.estado_voz.color = "", GRIS_TEXTO
        self.page.update()
        self.page.run_thread(self._leer_prueba, self.voz_eva.value)

    def _leer_prueba(self, voz: str) -> None:
        def al_descargar() -> None:
            self.estado_voz.value, self.estado_voz.color = DESCARGANDO_VOZ, GRIS_TEXTO
            self.page.update()  # show it before the download starts

        try:
            self.voz.hablar(PRUEBA, voz, al_descargar)
        except ErrorVoz as exc:
            self.estado_voz.value, self.estado_voz.color = str(exc), ROJO_ERROR
        except Exception as exc:
            log.exception("Unexpected error trying Eva's voice")
            self.estado_voz.value, self.estado_voz.color = f"{FALLO_VOZ}{exc}", ROJO_ERROR
        else:
            self.estado_voz.value, self.estado_voz.color = "", GRIS_TEXTO
        self.boton_probar.disabled = False
        self.page.update()

    # ----- save -----

    def _guardar(self, e) -> None:
        proveedor = self.proveedor.value
        clave = (self.clave.value or "").strip()
        try:
            if self._con_clave(proveedor) and clave:
                guardar_clave(proveedor, clave)
        except ErrorSecretos as exc:
            self.estado.value, self.estado.color = str(exc), ROJO_ERROR
            self.page.update()
            return
        modelo = self.modelo.value or ""
        extras = dict(self.config.extras)
        if PROVEEDORES[proveedor].cuenta:
            # Only the account id: its tokens stay in the encrypted account file.
            cuenta = self.chatgpt.cuenta.value
            extras["chatgpt_cuenta"] = cuenta
            extras["respaldo"] = {"proveedor": proveedor, "modelo": modelo, "cuenta": cuenta}
        elif self._es_nube(proveedor):
            # The last cloud model saved also extracts memories when the local
            # model fails (no key here: it stays in the keyring).
            extras["respaldo"] = {"proveedor": proveedor, "modelo": modelo}
        for clave, valor, por_defecto in (
            ("voz_eva", voz_valida(self.voz_eva.value), VOZ_POR_DEFECTO),
            ("responder_en_voz", modo_valido(self.responder.value), MODO_POR_DEFECTO),
        ):
            # Untouched defaults are not written, so older configs stay as they were.
            if clave in extras or valor != por_defecto:
                extras[clave] = valor
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
