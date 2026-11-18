"""Eva entry point. Run with: python -m app.main

Chat history and settings (config.json) live in the user data folder;
see app.config.carpeta_datos. Long-term memory (Engram) lives in its
``engram`` subfolder and its process is closed when the app closes, as are
the temporary WAVs of Eva's voice deleted.
"""

import logging
from collections.abc import Callable

import flet as ft

from app.config import cargar_config, carpeta_datos
from app.llm.conversacion import MemoriaUsuario
from app.memory.engram_client import MemoriaEngram
from app.memory.history import Historial
from app.ui.chat import FONDO, PantallaChat
from app.ui.isla import IslaFlotante
from app.ui.reproductor import borrar_audios

log = logging.getLogger(__name__)


def crear_memoria() -> MemoriaEngram | None:
    """Start Engram in the background; None (chat without memory) if that fails."""
    try:
        return MemoriaEngram()
    except Exception:
        log.exception("Could not create the long-term memory")
        return None


def crear_main(memoria: MemoriaEngram | None) -> Callable[[ft.Page], None]:
    def main(page: ft.Page) -> None:
        page.title = "Eva — IDEVA"
        page.theme_mode = ft.ThemeMode.DARK
        page.bgcolor = FONDO
        page.padding = 0
        pantalla = PantallaChat(
            page,
            Historial(carpeta_datos() / "eva.db"),
            cargar_config(),
            MemoriaUsuario(memoria) if memoria is not None else None,
        )
        completa = pantalla.vista()
        if hasattr(pantalla, "vista_isla"):
            isla = pantalla.vista_isla()
            controlador = IslaFlotante(page, completa, isla)
            pantalla.asignar_restaurar_isla(controlador.restaurar)
            controlador.instalar()
            page.add(ft.Stack([completa, isla], expand=True))
        else:  # compatibility with small integration doubles
            page.add(completa)
        pantalla.iniciar()

    return main


def ejecutar() -> None:
    memoria = crear_memoria()
    try:
        # On desktop ft.run returns when the window closes.
        ft.run(crear_main(memoria))
    finally:
        borrar_audios()  # the answers read aloud
        if memoria is not None:
            memoria.cerrar()  # stops the engram process


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    # Model load, transcription and synthesis times are worth seeing (plan, Fases 3 and 4).
    logging.getLogger("app.voice").setLevel(logging.INFO)
    ejecutar()
