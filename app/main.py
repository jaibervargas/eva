"""Eva entry point. Phase 0: a minimal Flet window to verify the toolchain."""

import flet as ft


def main(page: ft.Page) -> None:
    page.title = "Eva — IDEVA"
    page.bgcolor = "#0F1117"
    page.vertical_alignment = ft.MainAxisAlignment.CENTER
    page.horizontal_alignment = ft.CrossAxisAlignment.CENTER
    page.add(ft.Text("Hola mundo, soy Eva", size=28, color="#E5E7EB"))


if __name__ == "__main__":
    ft.run(main)
