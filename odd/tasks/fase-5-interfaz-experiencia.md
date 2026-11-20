# Fase 5 — Interfaz y experiencia

Make Eva's existing functional desktop UI comfortable, responsive and ready for beta users. Preserve the completed chat, voice input, local TTS, cloud providers, memory and settings behavior.
Branch: `feat/fase-5-interfaz-experiencia` (based on the Phase 4 branch until PR #8 is merged). User-facing Spanish uses neutral "tú".

## Specs

- S1 (plan): "Aplicar la guía visual de la sección 6 (tema oscuro de Flet y colores)."
  Apply the documented dark palette, hierarchy, spacing, typography, rounded surfaces and clear primary actions without breaking existing behavior.
- S2 (plan): "Agregar los indicadores de estado (escuchando, pensando, hablando)."
  Make recording, transcribing, connecting, thinking, speaking, downloading and error states visible and consistent; status must not freeze the UI or obscure the conversation.
- S3 (plan): "Manejar errores: sin micrófono, sin internet, Ollama o LM Studio apagado, clave de API inválida." 
  Finish the missing unexpected-connection error state and preserve the existing provider, microphone, TTS and settings errors with actionable neutral-Spanish messages.
- S4 (plan): "Escribir `setup_inicial.py`: detectar si Ollama o LM Studio están abiertos y descargar los modelos de voz al primer inicio, con barra de progreso."
  Implement only the setup behavior needed for this phase, with no credentials, real downloads or external services in tests; use injectable fakes and keep the UI responsive.
- S5 (user design request): the interface must account for "la pantalla completa", "los diferentes tamaños de pantalla que puede tener" and, when minimized, "una isla flotante, no tan grande con bordes redondeados".
  Implement responsive layouts with no horizontal overflow, accessible touch targets and a compact floating-island mode where Flet/window support permits it; otherwise isolate a testable layout/state adapter and document the platform limitation.

## Tasks

- T1 [S1, S2, S5] responsive visual shell and state indicators — done (L4; 796 passed, 3 skipped)
- T2 [S3] error-state completion and regression coverage — done (L5; 796 passed, 3 skipped)
- T3 [S4] setup_inicial.py detection/progress and tests — done (L6; 800 passed, 3 skipped)
- T4 [S5] floating island behavior or bounded platform adapter, tests and docs — done (L7; 803 passed, 3 skipped)
- T5 [S1-S5] docs, full verification, live test and review — done (L8-L10; 804 passed, 3 skipped; native assess not-due)

## Log

- L1 (user): "listo continuemos entonces" after Phase 4 was verified and PR #8 was opened.
- L2 (user): "en el plan de trabajo en la fase 5 cambiale el titulo dejalo como Interfaz  y experiencia (semana 7), de paso en la seccion 6 en la parte de distribucion de panta lla ajustalo para que sea mas detallado en la estructura , la distribucion , enfocate en un diseño innovador comodo para el usuario , ajusta tambien para que tenga en cuenta 3 aspectos 1. la pantalla completa  , 2 . los diferentes tamaños de pantalla que puede tener, 3. cuando se minimiza quisiera que quedara una isla flotante , no tan grande con bordes redondeados"
- L3: The work plan now records the responsive shell, full-screen hierarchy and compact floating-island direction. This phase turns that documented direction into working UI while preserving Phase 4.
- L4 (T1 verification): Added the three-zone shell, top status bar, Eva context panel, responsive collapse below 900 px, mobile input sizing, and visible connection/thinking/listening/transcribing states. Focused UI/microphone tests: 56 passed. Full suite: 796 passed, 3 skipped. T1 is verified; no floating-island implementation yet.
- L5 (T2 verification): Unexpected connection exceptions now show `Sin conexión`, an actionable error bubble, and preserve the retry text while unlocking the UI. Added regression coverage; focused UI/microphone tests: 56 passed. Full suite: 796 passed, 3 skipped.
- L6 (T3 verification): Added `app/setup_inicial.py` with injectable server detection, Piper/Whisper preparation, progress callbacks and CLI entry point. Setup tests: 4 passed. Full suite: 800 passed, 3 skipped. T3 is verified; no real network or model download was used.
- L7 (T4 verification): Added `app/ui/isla.py`, wired minimize/restore events in `app/main.py`, and added the compact 280×88 rounded always-on-top window with restore action. Focused checks: 64 passed. Full suite: 803 passed, 3 skipped. The adapter isolates Flet platform-specific window behavior.
- L8 (T5 verification): Updated `docs/plan-de-trabajo.md`; native assess returned not-due (medium risk, under budget); full suite remained 803 passed, 3 skipped. User tested the maximized layout, responsive collapse, visible states, connection error handling and floating island in the Windows app and reported `listo`. Phase 5 is complete; Phase 5B is next.
- L9 (live-test follow-up): The app log exposed a close race where background memory refresh tried to update a destroyed Flet session. The UI now ignores only that expected shutdown RuntimeError and keeps logging other refresh errors.
- L10 (verification): Added a regression test for the destroyed-session race. Full suite: 804 passed, 3 skipped.
