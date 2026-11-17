# Fase 1 — Chat de texto con la IA (solo local, LM Studio)

Feature: text chat with Eva against a local OpenAI-compatible server (LM Studio on localhost:1234).
Branch: `feat/fase-1-chat-texto`. Cloud client is deferred to Phase 2 (user decision, L2).

## Specs

- S1 (plan, Fase 0): "en LM Studio, descargar un modelo y activar su servidor local." — verified: `GET http://localhost:1234/v1/models` lists models.
- S2 (plan, Fase 1): "Escribir `llm/base.py` con una función común `responder(mensajes)`."
- S3 (plan, Fase 1): "Implementar `local_client.py`" — one client for Ollama and LM Studio: "solo cambia la dirección del servidor (Ollama en localhost:11434, LM Studio en localhost:1234)".
- S4 (plan, Fase 1): "Escribir el prompt del sistema (se presenta como Eva, de IDEVA; personalidad, idioma español, tono)."
- S5 (plan, Fase 1): "Crear la pantalla de chat en Flet: lista de burbujas y campo de texto." Visual guide: "burbujas del usuario en azul `#2563EB`, burbujas del asistente en gris `#1F2937`, texto `#E5E7EB`", fondo `#0F1117`, "bordes redondeados de 12 px"; "las del usuario a la derecha y las del asistente a la izquierda".
- S6 (plan, Fase 1): "Mostrar la respuesta en streaming (palabra por palabra)."
- S7 (L2, deferred): "`cloud_client.py`" moves to Phase 2.

## Tasks

- T1 [S1] Verify LM Studio server — done (5 models listed; qwen3 streams reasoning in `reasoning_content`, answer in `content`).
- T2 [S2,S3,S6] `app/llm/base.py` + `app/llm/local_client.py` + pytest with fake server — inline — done, commit 6e8dbe5
- T3 [S4] `app/llm/prompts.py` system prompt + `app/llm/conversacion.py` — inline — done, commit c8c130c
- T4 [S5,S6] `app/ui/chat.py` + wire `app/main.py` — inline — done (window starts clean; visual check by the user pending), commit: see git log
- T5 [S7] cloud client — deferred to Phase 2

## Log

- L1 (user): "vale ahora cuentame con que podemos seguir de acuerdo al plan de trabajo"
- L2 (user): "sigamos con el orden que me dices" — accepts proposal: check LM Studio, base.py, local_client.py with pytest, system prompt, chat screen with streaming; cloud client deferred to the end of the phase or Phase 2 (recommended: local only).
- L3: openai 3.52.0 and pytest 9.2.0 installed in venv.
- L4: Evidence — 10 pytest tests pass; real LM Studio smoke run streams '¡Hola! Estoy bien, gracias. ¿Cómo estás tú?'; offline server raises the ErrorLLM message; `python -m app.main` runs 20 s with DeprecationWarning as error and no output. Run command changed from `python app/main.py` to `python -m app.main` (README updated).
