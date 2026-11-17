"""Eva entry point. Run with: python -m app.main

Chat history and settings (config.json) live in the user data folder;
see app.config.carpeta_datos.
"""

import flet as ft

from app.config import cargar_config, carpeta_datos
from app.memory.history import Historial
from app.ui.chat import FONDO, PantallaChat


def main(page: ft.Page) -> None:
    page.title = "Eva — IDEVA"
    page.theme_mode = ft.ThemeMode.DARK
    page.bgcolor = FONDO
    page.padding = 0
    pantalla = PantallaChat(page, Historial(carpeta_datos() / "eva.db"), cargar_config())
    page.add(pantalla.vista())
    pantalla.iniciar()


if __name__ == "__main__":
    ft.run(main)
