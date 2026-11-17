"""Memories dialog: see what Eva remembers about the user and delete it."""

import logging

import flet as ft

from app.llm.conversacion import MemoriaUsuario
from app.memory.engram_client import Recuerdo

log = logging.getLogger(__name__)

ROJO_ERROR = "#F87171"
GRIS_TEXTO = "#9CA3AF"
CARGANDO = "Cargando…"
BORRANDO = "Borrando…"
VACIO = "Eva todavía no tiene recuerdos tuyos."


class DialogoRecuerdos:
    def __init__(self, page: ft.Page, memoria: MemoriaUsuario) -> None:
        self.page = page
        self.memoria = memoria
        self.recuerdos: list[Recuerdo] = []

        self.estado = ft.Text("", size=12, color=GRIS_TEXTO)
        self.lista = ft.ListView(spacing=2, height=360)
        self.dialogo = ft.AlertDialog(
            modal=True,
            title=ft.Text("Recuerdos"),
            content=ft.Column([self.estado, self.lista], tight=True, width=460),
            actions=[ft.TextButton("Cerrar", on_click=lambda e: self.page.pop_dialog())],
        )

    def abrir(self) -> None:
        self.page.show_dialog(self.dialogo)
        self._avisar(CARGANDO)
        self.lista.controls = []
        self.page.update()
        # Listing runs `engram export`, so it stays off the UI loop.
        self.page.run_thread(self._cargar)

    def _avisar(self, texto: str, error: bool = False) -> None:
        self.estado.value = texto
        self.estado.color = ROJO_ERROR if error else GRIS_TEXTO

    def _cargar(self) -> None:
        try:
            self.recuerdos = self.memoria.listar()
        except Exception as exc:  # never leave the dialog stuck on "Cargando…"
            log.warning("Could not list the memories: %s", exc)
            self._avisar(f"No pude cargar los recuerdos: {exc}", error=True)
        else:
            self._mostrar()
        self.page.update()

    def _mostrar(self) -> None:
        self._avisar("" if self.recuerdos else VACIO)
        self.lista.controls = [self._fila(r) for r in self.recuerdos]

    def _fila(self, recuerdo: Recuerdo) -> ft.ListTile:
        return ft.ListTile(
            title=ft.Text(recuerdo.titulo, weight=ft.FontWeight.BOLD),
            subtitle=ft.Text(recuerdo.contenido, selectable=True),
            trailing=ft.IconButton(
                icon=ft.Icons.DELETE_OUTLINE,
                tooltip="Borrar recuerdo",
                on_click=lambda e, r=recuerdo: self._confirmar(r),
            ),
        )

    def _confirmar(self, recuerdo: Recuerdo) -> None:
        self.page.show_dialog(
            ft.AlertDialog(
                modal=True,
                title=ft.Text("¿Borrar este recuerdo?"),
                content=ft.Text(f"Eva olvidará para siempre «{recuerdo.titulo}»."),
                actions=[
                    ft.TextButton("Cancelar", on_click=lambda e: self.page.pop_dialog()),
                    ft.FilledButton("Borrar", on_click=lambda e: self._borrar(recuerdo)),
                ],
            )
        )

    def _borrar(self, recuerdo: Recuerdo) -> None:
        self.page.pop_dialog()  # the confirmation
        self._avisar(BORRANDO)
        self.page.update()
        self.page.run_thread(self._borrar_en_segundo_plano, recuerdo)

    def _borrar_en_segundo_plano(self, recuerdo: Recuerdo) -> None:
        try:
            self.memoria.borrar(recuerdo.id)  # permanent (hard delete)
        except Exception as exc:
            log.warning("Could not delete memory #%s: %s", recuerdo.id, exc)
            self._avisar(f"No pude borrar el recuerdo: {exc}", error=True)
        else:
            self.recuerdos = [r for r in self.recuerdos if r.id != recuerdo.id]
            self._mostrar()
        self.page.update()
