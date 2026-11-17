# Fase 2A — Historial en SQLite, barra lateral y ajustes

First block of Phase 2 (local only): persistent chat history, conversation sidebar and a settings screen saved to config.json.
Branch: `feat/fase-2a-historial-ajustes` (stacked on `feat/fase-1-chat-texto`). Blocks 2B (Engram) and 2C (cloud + login) come later.

## Specs

- S1 (plan, Fase 2): "Crear la base SQLite con las tablas `conversaciones` y `mensajes`, guardada en la carpeta del usuario."
- S2 (plan, Fase 2): "Guardar cada mensaje y enviar los últimos 20 al modelo como contexto."
- S3 (plan, Fase 2): "Agregar en la barra lateral: nueva conversación y lista de conversaciones anteriores."
- S4 (plan, Fase 2): "Crear la pantalla de ajustes (proveedor: Ollama, LM Studio o nube; modelo, elegido de la lista de modelos instalados; forma de acceso a la nube: clave de API o cuenta (ChatGPT u OpenRouter); voz) que guarda en `config.json`." — 2A scope: providers LM Studio and Ollama, model list from the server; cloud access (2C) and voice (Fase 4) are out of scope for this block.
- S5 (plan, Fase 2 entregable): "el asistente recuerda lo hablado" — "Recuerda la conversación después de cerrar y volver a abrir la app" (plan §9).

## Tasks

- T1 [S1,S2] `app/config.py` data folder + `app/memory/history.py` SQLite + `Conversacion` persisted with last-20 context, tests — inline — done, commit 91b622a
- T2 [S4] `app/config.py` config.json load/save + `conectar()` + settings dialog in `app/ui/ajustes.py` — inline — done, commits e337456, dc6ff2b
- T3 [S3,S5] Sidebar (new conversation, previous conversations) wired in `app/ui/chat.py` / `app/main.py` — done, commit dc6ff2b; README + plan update — see docs commit

## Log

- L1 (user): "listo ahora con que continuamos"
- L2 (user): "listo procedan¿mos con eso" — accepts block 2A (history SQLite, sidebar, settings with config.json), recommended first. Branch question unanswered → stacked on Phase 1 branch.
- L4: Evidence — 32 pytest tests pass; live LM Studio: a turn is saved to eva.db with its title; screen logic smoke (open saved chat → 2 bubbles + selected, new chat clears, settings lists 4 LM Studio models, Ollama offline shows error and disables Save, Save writes config.json); `python -m app.main` runs 20 s clean with DeprecationWarning as error.
- L5: Independent verify (Risk item 6): S1-S3, S5 met; S4 blocker — non-string `proveedor` in config.json crashed startup (TypeError). Fixed with RED tests in d23663c (also empty answers no longer saved). 36 tests pass. Open advisories: config.json write errors not caught in settings dialog; repeated offline error bubbles; no automated UI tests.
- L3: Decision — settings replace the EVA_MODELO / EVA_SERVIDOR env vars introduced in Phase 1 (unpublished branch); README updated accordingly.
