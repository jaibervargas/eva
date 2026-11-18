# Fase 4 — Hablar: texto a voz

Eva reads her answers aloud with a local Piper voice, plays them with Flet's audio player, lets the user replay any answer, and the voice and when to speak are chosen in Ajustes.
Branch: `feat/fase-4-texto-a-voz` (from `main` 329034b). Python 3.11, Flet 1.1.2, Windows dev machine (Git Bash). Tests: `venv/Scripts/python.exe -m pytest -q` (702 passed, 3 skipped at start).

## Specs

Plan quotes (docs/plan-de-trabajo.md, Fase 4 and the voice part of the Fase 2 settings item) are verbatim; user decisions are quoted in the Log; the rest is the parent's technical design. User-facing text: neutral Spanish with "tú".

- S1 (plan): "Descargar una voz de Piper en español."
  User decision (L4): default voice `es_AR-daniela-high` ("en el momento me gusto mas es_AR-daniela-high pero no me convence del todo mas adelante se posria cambiar?" → changeable in Ajustes).
  Design: a catalog in `app/voice/tts.py` of the Spanish voices tried in L3: `es_AR-daniela-high` ("Daniela (Argentina)"), `es_MX-claude-high` ("Claude (México)"), `es_MX-ald-medium` ("Ald (México)"), `es_ES-davefx-medium` ("Davefx (España)"), each with its Hugging Face path under `https://huggingface.co/rhasspy/piper-voices/resolve/main/`. Files `<id>.onnx` and `<id>.onnx.json` live in `carpeta_datos()/"modelos"/"piper"`. A missing voice is downloaded automatically on first use (both files to temp names, then renamed; a failed or partial download leaves nothing behind) while the chat shows "Descargando la voz de Eva (unos 60–115 MB). Solo pasa la primera vez…". Download failure → `ErrorVoz("No pude descargar la voz de Eva. Revisa tu conexión a internet.")`.
- S2 (plan): "Implementar `voice/tts.py` que convierte la respuesta en un archivo de audio."
  Design: `Sintetizador(voz=..., carpeta=None, descargar=None)`: `necesita_descarga()`, `cargar()` (lazy, thread-safe, one loaded voice; changing the voice reloads), `sintetizar(texto) -> str` writes a WAV in the system temp folder and returns its path; failures → `ErrorVoz("No pude generar la voz: <detalle>")`. `limpiar_para_voz(texto) -> str` removes what should not be read aloud: `<think>…</think>` blocks, fenced code blocks (replaced by nothing), inline code backticks, Markdown markers (`#`, `*`, `_`, `>`, list bullets, table pipes), link syntax (keeps the link text, drops URLs), bare URLs, and emojis; collapses whitespace. Empty result → nothing is spoken. Synthesis time is logged at INFO (`app.voice`). Synthesis runs off the UI loop.
- S3 (plan): "Reproducir el audio automáticamente con el reproductor de Flet (`Audio`)."
  User decision (L4): "Al terminar la respuesta (Recomendado)".
  Design: package `flet-audio`. Each playback creates a new `Audio(src=<wav>, autoplay=True)` service (L3: changing `src` on an existing player did not play; creating it with the source did); the previous one is released and removed. Only one answer plays at a time: a new playback stops the current one; starting a microphone recording stops playback. Played WAVs are deleted when replaced or when the app closes. If synthesis fails the answer stays as text and a gray note "No pude leer la respuesta en voz alta." is shown (no red error, the chat keeps working).
- S4 (plan): "Agregar el interruptor "Responder en voz alta" y el botón para volver a escuchar."
  User decision (L4): "Solo si le hablaste por voz" as the default.
  Design: in Ajustes, "Responder en voz alta" with three options: "Solo cuando le hablas por voz" (default), "Siempre", "Nunca"; saved in `config.json` as `extras["responder_en_voz"]` = `"voz"` | `"siempre"` | `"nunca"`. After a completed answer Eva speaks it when the mode is "siempre", or "voz" and the question came from the microphone. Every Eva answer bubble gets a small "Escuchar" icon button (volume icon) that speaks that answer on demand regardless of the mode (synthesizing again).
- S5 (plan, Fase 2 settings): "…voz) que guarda en `config.json`."
  User decision (L4): "Sí, lista de voces en Ajustes".
  Design: Ajustes shows a "Voz de Eva" dropdown with the S1 catalog (default `es_AR-daniela-high`), saved as `extras["voz_eva"]` (not `voz`, which other tests use as an arbitrary unknown key), and a "Probar voz" button that speaks "Hola, soy Eva. Así suena mi voz." (downloading the voice first if needed). Existing settings behavior unchanged.
- S6: requirements pin `piper-tts==1.8.0` and `flet-audio==1.1.2`; unit tests fake the voice, the downloader and the player (no network, no sound); one optional test with a real voice, skipped unless the voice is already in the data folder; README and plan updated; review; live test by the user.

## Tasks

- T1 [S1, S2] `app/voice/tts.py` (catalog, download, synthesis, text cleanup) + tests + requirements — route: gentle-ai-worker (context backstop) — done (L5)
- T2 [S3, S4, S5] player, automatic speaking, replay button, Ajustes voice and mode, tests — route: same worker — done (L5)
- T3 [S6] docs, review, live test — pending

## Log

- L1 (user): "listo que vamos a hacer en la fase 4" → "si" to the approach (spike first).
- L2 (spike 1): `piper-tts` 1.8.0 and `flet-audio` 1.1.2 installed, `pip check` clean; 4 voices downloaded to `%APPDATA%/Eva/modelos/piper`. Synthesis worked, but no sound: user "le doy clic pero no se escucha nada" (src changed on an existing `Audio` then `play()`).
- L3 (spike 2): user "los dos sonaron ya": a new `flet_audio.Audio(src=wav, autoplay=True)` per playback plays, and so does `winsound`. Timings (CPU): 66 chars → 0.23–0.9 s; 288 chars (13.6–19.7 s of audio): claude 1.16 s, ald 1.08 s, davefx 0.92 s, daniela 3.3–3.55 s. Sizes: daniela 114 MB, others 63 MB.
- L4 (user): voice: "en el momento me gusto mas es_AR-daniela-high pero no me convence del todo mas adelante se posria cambiar?"; when: "Al terminar la respuesta (Recomendado)"; default: "Solo si le hablaste por voz"; settings: "Sí, lista de voces en Ajustes".
- L5 (T1+T2, worker evidence): RED: collection errors (no app.voice.tts / app.ui.reproductor). GREEN: test_tts 36 (real Daniela test ran; caught a `wave` close error hiding synthesis failures), 3 new T2 files 54; full suite 793 passed, 3 skipped (parent re-ran). New `app/voice/tts.py`, `app/ui/reproductor.py` (Reproductor, VozEva, borrar_audios); `PantallaChat(voz=...)`; `enviar_texto(..., por_voz=True)` from the mic; WAVs deleted on app close in main. Accepted deviations: voice on by default only with a real `ft.Page` (tests stay silent); defaults not written to config.json unless changed; a failed voice download shows the S1 download message as the gray note; Probar voz status in its own line under the voice controls; Escuchar button only on finished answers (and history); mic questions marked with `por_voz`.
