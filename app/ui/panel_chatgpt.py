"""ChatGPT account controls of the settings dialog (Fase 2D, S4).

Picks one of the saved ChatGPT accounts (or adds a new one), signs in, asks
for plan permission and signs out. Signing in, signing out and reading the
saved accounts run off the UI loop; the dialog owns the model list.
"""

import logging
import threading
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field

import flet as ft

from app.auth_chatgpt import MENSAJE_CANCELADO, ErrorAuth, cerrar_sesion, iniciar_sesion_chatgpt
from app.cuentas_chatgpt import AlmacenCuentas, CuentaChatGPT, ErrorCuentas
from app.llm.base import ErrorLLM
from app.llm.sesion_chatgpt import MENSAJE_PLAN, MENSAJE_SIN_MODELOS, cerrar_sesion_cuenta
from app.llm.sesion_chatgpt import listar_modelos as listar_modelos_chatgpt
from app.secretos import ErrorSecretos

log = logging.getLogger(__name__)

ROJO_ERROR = "#F87171"
GRIS_TEXTO = "#9CA3AF"

# Dropdown key of "Agregar cuenta…" (account ids are hex, so it never clashes).
NUEVA = "__nueva__"
AGREGAR_CUENTA = "Agregar cuenta…"
SIN_CORREO = "Cuenta de ChatGPT"
CONTINUAR = "Continuar con ChatGPT"
CANCELAR = "Cancelar inicio de sesión"
PERMITIR = "Permitir usar mi plan"
CERRAR_SESION = "Cerrar sesión"
USO_DEL_PLAN = "Uso del plan: chatgpt.com/settings/usage"
ESPERANDO_NAVEGADOR = "Esperando el inicio de sesión en el navegador…"
ABRE_LA_DIRECCION = "No pude abrir el navegador. Abre esta dirección para iniciar sesión con ChatGPT: {url}"
PIDE_SESION = "Inicia sesión con ChatGPT para ver sus modelos."
CERRANDO_SESION = "Cerrando sesión…"
SESION_CERRADA = "Cerré tu sesión de ChatGPT en Eva."
SESION_SIN_CONFIRMAR = (
    "Cerré tu sesión en Eva, pero no pude confirmarlo con OpenAI. "
    "Puedes desconectar Eva en la configuración de ChatGPT."
)


@dataclass
class Consulta:
    """What a background load found: accounts, the chosen one and its models."""

    cuentas: list[tuple[str, str]]  # (id, label) of the saved accounts
    elegida: str  # an account id or NUEVA
    conectada: bool = False
    plan: bool = False
    modelos: list[tuple[str, str]] = field(default_factory=list)  # (slug, display name)
    error: str | None = None  # red
    aviso: str | None = None  # gray


def etiquetas(cuentas: list[CuentaChatGPT]) -> list[tuple[str, str]]:
    """``(id, label)`` per account: its email, numbered when repeated ("email (2)")."""
    vistas: Counter[str] = Counter()
    resultado = []
    for cuenta in cuentas:
        base = cuenta.email or SIN_CORREO
        vistas[base] += 1
        resultado.append((cuenta.id, base if vistas[base] == 1 else f"{base} ({vistas[base]})"))
    return resultado


class PanelChatGPT:
    """The "Cuenta" picker and the sign-in buttons shown for the ChatGPT provider.

    ``estado`` is the dialog's status line; ``al_cambiar`` reloads the
    dialog's models (after choosing an account or signing in) and
    ``al_cerrar_sesion`` empties them.
    """

    def __init__(
        self,
        page: ft.Page,
        estado: ft.Text,
        al_cambiar: Callable[[], None],
        al_cerrar_sesion: Callable[[], None],
        almacen: AlmacenCuentas | None = None,
    ) -> None:
        self.page = page
        self.estado = estado
        self.al_cambiar = al_cambiar
        self.al_cerrar_sesion = al_cerrar_sesion
        self.almacen = almacen or AlmacenCuentas()
        self._inicio: threading.Event | None = None  # set while signing in

        self.cuenta = ft.Dropdown(
            label="Cuenta",
            options=[ft.DropdownOption(key=NUEVA, text=AGREGAR_CUENTA)],
            on_select=self._cambio_cuenta,
            expand=True,
        )
        self.boton_continuar = ft.OutlinedButton(CONTINUAR, on_click=self._continuar)
        self.boton_permitir = ft.OutlinedButton(PERMITIR, on_click=self._permitir, visible=False)
        self.boton_cerrar = ft.TextButton(CERRAR_SESION, on_click=self._cerrar_sesion, visible=False)
        self.uso = ft.Text(USO_DEL_PLAN, size=12, color=GRIS_TEXTO, selectable=True)
        self.contenedor = ft.Column(
            [
                ft.Row([self.cuenta]),
                ft.Row([self.boton_continuar, self.boton_permitir, self.boton_cerrar], wrap=True),
                self.uso,
            ],
            tight=True,
        )

    # ----- loading (off the UI loop) -----

    def consultar(self, preferida: str | None, guardada: object = None) -> Consulta:
        """Read the saved accounts and list the chosen one's models.

        ``preferida`` is the account picked in the dialog; when it is gone,
        ``guardada`` (the account in the settings) or else the first one is
        chosen. Blocks on the network: call it off the UI loop.
        """
        try:
            cuentas = self.almacen.listar()
        except (ErrorCuentas, ErrorSecretos) as exc:
            return Consulta([], NUEVA, error=str(exc))
        ids = [c.id for c in cuentas]
        if preferida == NUEVA or preferida in ids:
            elegida = preferida
        elif guardada in ids:
            elegida = guardada
        else:
            elegida = ids[0] if ids else NUEVA
        consulta = Consulta(etiquetas(cuentas), elegida)
        cuenta = next((c for c in cuentas if c.id == elegida), None)
        if cuenta is None or not cuenta.conectada:
            consulta.aviso = PIDE_SESION
            return consulta
        consulta.conectada, consulta.plan = True, cuenta.plan_habilitado
        if not cuenta.plan_habilitado:
            consulta.aviso = MENSAJE_PLAN
            return consulta
        try:
            consulta.modelos = listar_modelos_chatgpt(cuenta.id, self.almacen)
        except ErrorLLM as exc:
            consulta.error = str(exc)
            # The token refresh may have signed the account out (session expired).
            try:
                actual = self.almacen.obtener(cuenta.id)
            except (ErrorCuentas, ErrorSecretos):
                actual = None
            consulta.conectada = actual is not None and actual.conectada
            consulta.plan = actual is not None and actual.plan_habilitado
            return consulta
        if not consulta.modelos:
            consulta.error = MENSAJE_SIN_MODELOS
        return consulta

    def mostrar(self, consulta: Consulta) -> None:
        """Show what ``consultar`` found (the caller updates the page)."""
        self.cuenta.options = [
            *(ft.DropdownOption(key=id_, text=texto) for id_, texto in consulta.cuentas),
            ft.DropdownOption(key=NUEVA, text=AGREGAR_CUENTA),
        ]
        self.cuenta.value = consulta.elegida
        self.boton_cerrar.visible = consulta.conectada
        self.boton_permitir.visible = consulta.conectada and not consulta.plan

    def _cambio_cuenta(self, e) -> None:
        self.cancelar()
        self.al_cambiar()

    # ----- signing in -----

    def _continuar(self, e) -> None:
        self._iniciar(pedir_consentimiento=False)

    def _permitir(self, e) -> None:
        self._iniciar(pedir_consentimiento=True)

    def _iniciar(self, pedir_consentimiento: bool) -> None:
        """Sign in with the chosen account (or a new one); a second click cancels."""
        if self._inicio is not None:
            self.cancelar()
            return
        cancelado = threading.Event()
        self._inicio = cancelado
        self.boton_continuar.content = CANCELAR
        self.boton_cerrar.disabled = True
        self.estado.value, self.estado.color = ESPERANDO_NAVEGADOR, GRIS_TEXTO
        self.page.update()
        self.page.run_thread(self._esperar_inicio, cancelado, self.cuenta.value, pedir_consentimiento)

    def cancelar(self) -> None:
        """Cancel a pending sign-in (dialog closed, provider or account changed)."""
        if self._inicio is not None:
            self._inicio.set()

    def _mostrar_direccion(self, cancelado: threading.Event, url: str) -> None:
        """No browser opened: show the address; the sign-in keeps waiting."""
        if self._inicio is not cancelado or cancelado.is_set():
            return
        self.estado.value, self.estado.color = ABRE_LA_DIRECCION.format(url=url), GRIS_TEXTO
        self.page.update()

    def _esperar_inicio(self, cancelado: threading.Event, cuenta_id: str | None, pedir_consentimiento: bool) -> None:
        nueva = None
        try:
            host_id = self.almacen.host_id()
            guardada = self.almacen.obtener(cuenta_id) if cuenta_id and cuenta_id != NUEVA else None
            resultado = iniciar_sesion_chatgpt(
                host_id,
                client_id=guardada.client_id if guardada else None,
                id_token_hint=(guardada.id_token or None) if guardada else None,
                login_hint=(guardada.email or None) if guardada else None,
                subject_esperado=guardada.subject if guardada else None,
                pedir_consentimiento=pedir_consentimiento,
                al_sin_navegador=lambda url: self._mostrar_direccion(cancelado, url),
                cancelado=cancelado,
            )
            # Cancelling is honoured until the account is saved.
            if cancelado.is_set():
                raise ErrorAuth(MENSAJE_CANCELADO)
            nueva = CuentaChatGPT(
                email=resultado.email or (guardada.email if guardada else ""),
                subject=resultado.subject,
                client_id=resultado.client_id,
                id_token=resultado.id_token,
                access_token=resultado.access_token,
                refresh_token=resultado.refresh_token,
                expira=resultado.expira,
                scopes=list(resultado.scopes),
            )
            self.almacen.guardar(nueva)
        except (ErrorAuth, ErrorCuentas, ErrorSecretos) as exc:
            error = str(exc)
        except Exception as exc:
            log.exception("Unexpected error signing in with ChatGPT")
            error = f"Ocurrió un error inesperado: {exc}"
        else:
            error = None
        if self._inicio is cancelado:
            self._inicio = None
            self.boton_continuar.content = CONTINUAR
            self.boton_cerrar.disabled = False
        if error is not None:
            self.estado.value, self.estado.color = error, ROJO_ERROR
            self.page.update()
            return
        self.cuenta.value = nueva.id
        self.al_cambiar()

    # ----- signing out -----

    def _cerrar_sesion(self, e) -> None:
        cuenta_id = self.cuenta.value
        if not cuenta_id or cuenta_id == NUEVA or self._inicio is not None:
            return
        self.boton_cerrar.disabled = True
        self.estado.value, self.estado.color = CERRANDO_SESION, GRIS_TEXTO
        self.page.update()
        self.page.run_thread(self._cerrar, cuenta_id)

    def _cerrar(self, cuenta_id: str) -> None:
        """Revoke at OpenAI (best effort), then always forget the local tokens.

        Serialized with the chat's token refresh for the same account.
        """
        confirmado = True
        try:
            confirmado = cerrar_sesion_cuenta(self.almacen, cuenta_id, cerrar_sesion)
        except (ErrorCuentas, ErrorSecretos) as exc:
            error = str(exc)
        except Exception as exc:
            log.exception("Unexpected error signing out of ChatGPT")
            error = f"Ocurrió un error inesperado: {exc}"
        else:
            error = None
        self.boton_cerrar.disabled = False
        if error is not None:
            self.estado.value, self.estado.color = error, ROJO_ERROR
            self.page.update()
            return
        self.boton_cerrar.visible = False
        self.boton_permitir.visible = False
        self.al_cerrar_sesion()
        mensaje = SESION_CERRADA if confirmado else SESION_SIN_CONFIRMAR
        self.estado.value, self.estado.color = mensaje, GRIS_TEXTO
        self.page.update()
