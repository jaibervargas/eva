# Fase 2B — Memoria a largo plazo con Engram

Eva remembers user facts across different conversations using Engram (Gentleman Programming) over MCP.
Branch: `feat/fase-2b-engram` (from `main` after the 2A merge). Python 3.11, Flet 1.1.2, Windows dev machine (Git Bash; convert paths with `cygpath -m`). Tests: `venv/Scripts/python.exe -m pytest -q` (68 passed, 2 skipped at start).

## Specs

Plan quotes (docs/plan-de-trabajo.md, Fase 2) are verbatim; decisions below them are the parent's technical design.

- S1 (plan): "Descargar el programa de Engram para Windows, Mac y Linux desde sus versiones publicadas en GitHub y guardarlo en `bin/`."
  Design: `scripts/descargar_engram.py` downloads the pinned release **v3.7.2** asset for the current OS/arch (`engram_3.7.2_{darwin,linux}_{amd64,arm64}.tar.gz`, `engram_3.7.2_windows_{amd64,arm64}.zip`) from `https://github.com/Gentleman-Programming/engram/releases/download/v3.7.2/`, **verifies its SHA-256 against `checksums.txt`** of the same release (abort on mismatch, nothing written to bin/), and extracts only the `engram`/`engram.exe` binary into `bin/`. `bin/` stays git-ignored. `--plataforma` option to fetch another OS's binary (for packaging later). Prints a clear Spanish message on success/failure.
- S2 (plan): "Implementar `memory/engram_client.py`: la app abre Engram en segundo plano y se conecta a él por MCP con la librería `mcp` de Python."
  Design:
  - Binary lookup order: `bin/engram(.exe)` in the repo/app folder, else `engram` on PATH; if none, memory is unavailable (see S6).
  - Process env: `ENGRAM_DATA_DIR = carpeta_datos()/"engram"` (Eva's own memory, NEVER the developer's `~/.engram`), `ENGRAM_PROJECT = "eva"`. Args: `mcp --tools=agent,admin` (admin is needed for `mem_delete`; confirm live with `tools/list`).
  - Python `mcp` package pinned in requirements.txt (choose and justify: `mcp>=1.27,<2` preferred unless 1.x does not work on Windows with engram; v2.x needs `pywin32`). Run the async MCP session in ONE dedicated background thread with its own asyncio loop; expose a synchronous facade `MemoriaEngram` with timeouts (e.g. 10 s): `buscar(texto, limite=5) -> list[Recuerdo]`, `guardar(titulo, contenido) -> int | None`, `listar() -> list[Recuerdo]`, `borrar(id) -> None`, `cerrar()`. `Recuerdo` = dataclass(id, titulo, contenido).
  - Listing all memories: there is no list tool in 1.19; check 3.7.2 `tools/list`. If none, implement `listar()` via the most reliable available path (e.g. `engram export` CLI with the same env into a temp file, filtered to project "eva" and non-deleted), documented in code.
  - Saves use `type="preference"`, `capture_prompt=false`, and a stable `topic_key` derived from the title (e.g. `usuario/<slug>`) so the same fact is updated, not duplicated, if the server supports it.
- S3 (plan): "Antes de cada respuesta, buscar en Engram los recuerdos relacionados con la pregunta y agregarlos al contexto del modelo."
  Design: `Conversacion` accepts an optional memory object; before calling the model it calls `buscar(pregunta)`; if there are results, adds ONE extra system message right after the system prompt: "Recuerdos sobre el usuario (de conversaciones anteriores):\n- <titulo>: <contenido>\n..." (max 5). Memories are context only: they are NOT stored in the chat history.
- S4 (plan): "Después de cada respuesta, guardar en Engram los datos importantes del usuario (nombre, gustos, decisiones). Si el modelo local no detecta bien qué guardar, usar el de la nube solo para esa tarea."
  Design: after a completed turn, in the background (never delaying or blocking the chat), ask the same local model with an extraction prompt to return ONLY JSON: `[{"titulo": "...", "contenido": "..."}]` with durable facts about the user stated in the user's message (name, likes, decisions), or `[]`. Parse robustly (strip `<think>...</think>`, code fences, surrounding text); invalid output saves nothing. Save each fact with `guardar`. The cloud fallback is OUT OF SCOPE until block 2C (record as pending).
- S5 (plan): "Agregar en ajustes una pantalla para ver y borrar recuerdos."
  Design: a "Recuerdos" button in the sidebar (or settings dialog) opens a dialog listing memories (title + content) with a delete button per memory and a confirmation; deletion is permanent (`hard_delete`). Empty state: "Eva todavía no tiene recuerdos tuyos." Loads off the UI loop with "Cargando…".
- S6 (robustness): If Engram is missing, fails to start, times out or errors, the chat keeps working without long-term memory: no crash, no blocking; the model label area shows a discreet note "Memoria a largo plazo no disponible" and the error is logged. The Engram process is closed when the app closes.
- S7 (plan entregable): "el asistente recuerda lo hablado, recuerda datos del usuario entre conversaciones distintas" — end-to-end: say "Me llamo Ana y me gusta el café" in one conversation; in a NEW conversation ask "¿Cómo me llamo?" → the model receives the memory in context.

## Tasks

- T1 [S1,S2,S6] `scripts/descargar_engram.py` + `app/memory/engram_client.py` (MemoriaEngram facade, MCP thread, binary lookup, isolated data dir), requirements pin, tests (unit + integration against the real binary, skipped when no binary) — route: gentle-ai-worker — commit: pending
- T2 [S3,S4,S5,S6,S7] wire memory into `Conversacion` (context + background extraction), memories dialog, unavailable note, app shutdown, tests, README + plan update — route: gentle-ai-worker (after T1 is committed) — commit: pending

## Log

- L1 (user): "ya realice el merge , sigue con el bloque 2B"
- L2: Evidence — Engram 1.19.0 installed at /c/Users/jaibe/AppData/Local/engram/bin/engram (developer's own memory in ~/.engram — must never be used by Eva). `engram mcp [--tools=PROFILE] [--project NAME]`; env ENGRAM_DATA_DIR, ENGRAM_PROJECT. Explorer (source-read only, not run): 1.19 tools mem_save(title*, content, type, topic_key, capture_prompt, ...), mem_search(query*, limit<=20, ...), mem_get_observation(id*), mem_delete(id*, hard_delete) only in admin profile; results are one TextContent with a JSON string (search previews 300 chars; full text via mem_get_observation); latest release v3.7.2 assets engram_3.7.2_<os>_<arch>.(tar.gz|zip) + checksums.txt; PyPI mcp latest 2.3.0 (v2 breaking, pywin32 on Windows), 1.x ≤1.29.
- L3: Decisions — pin Engram v3.7.2 downloaded into bin/ with checksum verification; Eva's memory isolated in carpeta_datos()/engram with project "eva"; extraction with the local model only (cloud fallback waits for 2C).
