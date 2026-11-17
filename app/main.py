"""Eva entry point. Run with: python -m app.main

Phase 1: text chat with a local model (LM Studio by default).
Optional environment variables until the settings screen exists (Phase 2):
  EVA_SERVIDOR  server URL (default LM Studio: http://localhost:1234/v1)
  EVA_MODELO    model id (default: first chat model the server lists)
"""

import os

import flet as ft

from app.config import carpeta_datos
from app.llm.base import ErrorLLM
from app.llm.conversacion import Conversacion
from app.llm.local_client import LMSTUDIO_URL, ClienteLocal
from app.memory.history import Historial
from app.ui.chat import FONDO, PantallaChat


def crear_conversacion() -> tuple[Conversacion | None, str]:
    """Connect to the local server; return the conversation or a user-facing error."""
    cliente = ClienteLocal(modelo=os.environ.get("EVA_MODELO", ""), base_url=os.environ.get("EVA_SERVIDOR", LMSTUDIO_URL))
    if not cliente.modelo:
        try:
            modelos = cliente.listar_modelos()
        except ErrorLLM as exc:
            return None, str(exc)
        if not modelos:
            return None, "El servidor local no tiene modelos de chat. Descarga uno en LM Studio u Ollama."
        cliente.modelo = modelos[0]
    return Conversacion(cliente, Historial(carpeta_datos() / "eva.db")), ""


def main(page: ft.Page) -> None:
    page.title = "Eva — IDEVA"
    page.theme_mode = ft.ThemeMode.DARK
    page.bgcolor = FONDO
    page.padding = 0
    conversacion, error = crear_conversacion()
    page.add(PantallaChat(page, conversacion, error).vista())


if __name__ == "__main__":
    ft.run(main)
