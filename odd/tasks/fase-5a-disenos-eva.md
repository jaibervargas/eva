# Fase 5A — Tres diseños seleccionables para Eva

Implement three selectable desktop layouts—Brisa, Marea and Horizonte—using the supplied reference boards while preserving Eva's real chat, streaming, history, Engram, microphone, TTS, settings and window flows.
Branch: `feat/fase-5a-disenos-eva`, based on the Phase 5 branch until its PR is merged. Flet `1.1.2`, Python 3.11. Technical artifacts remain in English; user-facing copy uses neutral Spanish with "tú".

## Specs

- S1 (user): "Si todavía no hay una preferencia válida, muestra «Elige el diseño de Eva» al iniciar. Incluye tres tarjetas, Brisa, Marea y Horizonte, con una vista previa de cada una y un botón «Usar este diseño»."
  Persist the validated value in the existing config as `eva.ui.design` equivalent to `extras["eva"]["ui"]["design"]`; valid values are `brisa`, `marea`, `horizonte`. Invalid/missing values open the chooser before normal chat startup.
- S2 (user): "Añade la opción «Ajustes → Apariencia» al menú izquierdo. Ahí se debe poder previsualizar otro diseño, aplicarlo o cancelar. Marca el diseño actual."
  Add a real appearance dialog; apply immediately without losing active conversation, draft, streaming answer or selected tab. Cancel restores the previous design.
- S3 (user): "Comparte los componentes de chat, escritura de mensajes, avatar y controles de ventana. Define una configuración visual por diseño con colores, tipografía, espacios, radios y bordes. Implementa las diferencias de distribución de cada propuesta."
  Use one shared state model and a design catalog. Brisa: light aqua sidebar, one history rail. Marea: petroleum dark, menu plus history column. Horizonte: petroleum sidebar, light chat, tabs and task-card presentation only when real structured task data exists.
- S4 (user): "Implementa los tres modos de ventana" and specifically "Pantalla completa", "Ventana mediana" and "Isla dinámica".
  Fullscreen keeps scrolling chat and fixed composer; medium starts at 1024×700 and is adjustable; island starts around 300×56, shows avatar/real status/"Volver al chat", preserves previous size/position and keeps an active response running.
- S5 (user): "Usa contenido real en el chat." and "Si alguna función representada en las imágenes requiere datos o capacidades que todavía no existen en Eva, indica cuál falta y evita mostrar controles que aparenten funcionar."
  Do not show fake Favorites, editable tabs or task checkboxes without backing data. The existing history has no pinned flag, and messages are plain text/Markdown; document these omissions.
- S6: use Flet 1.1.2 APIs, supplied references under `docs/referencias`, and the original Eva seed image where technically suitable; exclude the IDEVA logo. Keep accessible focus, labels and contrast. Capture nine Windows states (3 designs × 3 modes) or report unavailable capture tooling.

## Tasks

- T1 [S1, S2] shared UI state, design catalog, persistence and chooser — done (L4; 808 passed, 3 skipped)
- T2 [S3] Brisa, Marea and Horizonte layouts — done (L5; 808 passed, 3 skipped)
- T3 [S4] fullscreen/medium/island integration per design — done (L6; 808 passed, 3 skipped)
- T4 [S5, S6] tests, docs, captures and Windows validation — pending
- T5 [S1-S6] review, PR and delivery — pending

## Log

- L1 (user): "Implementa estos  tres diseños seleccionables para Eva, mi agente . Usa las imágenes adjuntas de Brisa, Marea y Horizonte como referencia ubicadas en  C:\\xampp\\htdocs\\eva\\docs\\referencias. ... analiza esto y validalo antes de implementyar los cambios"
- L2 (analysis): Validated entry point `app/main.py`, Flet 1.1.2, `PantallaChat`/`DialogoAjustes`/`Config.extras`/SQLite history, current window adapter, and reference images. No production changes were made before validation. No assets exist under `assets/eva/`; the seed image is under `docs/referencias` and has a white background.
- L3 (decision): User approved implementation and asked to add it to the work plan as Phase 5A: "si me parece de paso agrega estos cambios en el pland e trabajo como fase 5A".
- L4 (T1 verification): Added `app/ui/disenos.py` with the three structural design tokens, nested preference helpers, shared `EstadoInterfaz`, and first-run `SelectorDiseno`; wired selection into `app/main.py` and the live chat theme. Focused tests: 48 passed. Full suite: 808 passed, 3 skipped.
- L5 (T2 verification): Added structural layout branches in `PantallaChat`: Brisa uses one history rail, Marea uses menu plus a separate history column, and Horizonte uses real-conversation tabs. Focused layout/regression tests: 66 passed. Full suite: 808 passed, 3 skipped. Unsupported Favorites and fake task cards remain omitted.
- L6 (T3 verification): Updated `IslaFlotante` to a 300×56 capsule, centered relative to the prior window near the top edge, preserving and restoring size, position and normal window properties. Focused window checks: 12 passed. Full suite: 808 passed, 3 skipped.
