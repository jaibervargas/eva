# Eva — el asistente de IA de IDEVA

Asistente de escritorio con el que se puede chatear por texto y por voz, en español. Se instala como una aplicación normal (doble clic en `.exe`, `.dmg` o `.AppImage`), sin necesidad de Docker. Docker queda como segunda opción para servidores o usuarios avanzados.

## Objetivos del producto (versión 1.0)

- Conversar por texto con memoria de la conversación y memoria persistente a largo plazo (Engram) que recuerda datos y preferencias del usuario entre conversaciones.
- Hablar por micrófono y responder en voz alta, en español.
- Elegir el "cerebro": modelo local (Ollama o LM Studio, sin internet) o modelo en la nube (clave de API de OpenAI, Claude o Gemini, o iniciando sesión con ChatGPT Plus/Pro u OpenRouter).
- Instalarse como aplicación de escritorio: `.exe` en Windows, `.dmg` en Mac y `.AppImage` en Linux.
- Opcional: instalarse con `docker compose up` en servidores.

**Fuera del alcance de la v1.0:** conectar documentos propios (RAG), app móvil, palabra de activación "Oye, Eva", usuarios múltiples con login y firma de la app en Mac.

## Stack tecnológico

| Componente | Tecnología | Por qué |
|---|---|---|
| Lenguaje | Python 3.11 | Todas las librerías de IA están en Python |
| Interfaz | Flet | Apps de escritorio para Windows, Mac y Linux sin HTML/JS; graba y reproduce audio |
| IA local | Ollama (puerto 11434) o LM Studio (puerto 1234) | Gratis, privado, sin internet |
| IA en la nube | API de OpenAI, Claude o Gemini; o "Iniciar sesión con ChatGPT" u OpenRouter | Mejor calidad en equipos sin GPU |
| Voz a texto (STT) | faster-whisper (modelo `small`) | Funciona sin internet y entiende bien el español |
| Texto a voz (TTS) | Piper (voz `es_ES` o `es_MX`) | Voz natural, offline y rápida en CPU |
| Memoria | SQLite (historial del chat) + Engram (memoria a largo plazo) | Funciona sin internet, licencia MIT, listo para Windows, Mac y Linux |
| Configuración | Pantalla de ajustes + archivo `config.json` | Cambiar modelo o clave de API sin tocar archivos |
| Ejecutable | `flet pack` | Convierte la app en un ejecutable con todo incluido |
| Instaladores | Inno Setup (Windows), create-dmg (Mac), appimagetool (Linux) | Instaladores de doble clic, gratuitos |
| Construcción automática | GitHub Actions | Construye los tres instaladores en la nube |
| Pruebas | pytest | Verificar cada módulo por separado |

## Qué se necesita

**Hardware mínimo:**
- 8 GB de RAM si se usa IA en la nube; 16 GB si se usa Ollama local.
- 10 GB libres en disco para desarrollar (modelos de IA y de voz); el usuario final necesita unos 2 GB con IA en la nube.
- Micrófono y parlantes o audífonos.
- GPU opcional: acelera Ollama y Whisper, pero no es obligatoria.

**Software para instalar antes de empezar:**
- Python 3.11
- Git y una cuenta de GitHub
- Visual Studio Code (con la extensión de Python)
- Docker Desktop (solo para la Fase 7, opcional)
- Ollama o LM Studio (uno o ambos), si se va a usar IA local
- Una clave de API (OpenAI, Anthropic o Google) o una cuenta de ChatGPT Plus/Pro u OpenRouter, si se va a usar IA en la nube

## Estructura de carpetas

```
eva/
├── app/
│   ├── main.py            # Interfaz Flet (punto de entrada)
│   ├── config.py          # Lee y guarda config.json
│   ├── auth.py            # Inicio de sesión con ChatGPT y claves guardadas de forma segura
│   ├── setup_inicial.py   # Revisa Ollama y descarga modelos al primer inicio
│   ├── ui/
│   │   ├── chat.py        # Pantalla de chat
│   │   ├── ajustes.py     # Pantalla de configuración
│   │   ├── eva_avatar.py  # Eva animada por capas
│   │   └── eva_estados.py # Elige el estado de Eva según lo que pasa en la app
│   ├── llm/
│   │   ├── base.py        # Interfaz común para cualquier modelo
│   │   ├── local_client.py    # Ollama y LM Studio
│   │   └── cloud_client.py
│   ├── voice/
│   │   ├── stt.py         # Voz a texto (faster-whisper)
│   │   └── tts.py         # Texto a voz (Piper)
│   └── memory/
│       ├── history.py     # Historial en SQLite
│       └── engram_client.py  # Memoria a largo plazo (Engram por MCP)
├── assets/                # Ícono, sonidos y capas de Eva (assets/eva/)
├── bin/                   # Programa de Engram para cada sistema
├── installer/
│   ├── windows.iss        # Script de Inno Setup
│   ├── build_mac.sh       # Crea el .dmg
│   └── build_linux.sh     # Crea el .AppImage
├── .github/workflows/
│   └── build.yml          # Construye los 3 instaladores en GitHub
├── docker/                # Opcional
│   ├── Dockerfile
│   └── docker-compose.yml
├── tests/                 # Pruebas con pytest
├── requirements.txt
└── README.md
```

## Desarrollo (por fases)

El plan dura 14 semanas (13 si se omite Docker) y se divide en 10 fases. Cada fase termina con algo que funciona y se puede probar:

| Fase | Semanas | Qué se construye |
|---|---|---|
| 0 — Preparación del entorno | 1 | Python, Git, VS Code, Ollama/LM Studio, repositorio, entorno virtual, ventana "Hola mundo" con Flet |
| 1 — Chat de texto con la IA | 2 | Clientes LLM (local y nube), pantalla de chat, respuesta en streaming |
| 2 — Memoria, Engram y ajustes | 3–4 | SQLite, historial de conversaciones, pantalla de ajustes, Engram, inicio de sesión con ChatGPT/OpenRouter |
| 3 — Escuchar: voz a texto | 5 | Botón de micrófono, faster-whisper, transcripción en español |
| 4 — Hablar: texto a voz | 6 | Piper, reproducción automática, interruptor "Responder en voz alta" |
| 5 — Interfaz final y experiencia | 7 | Tema oscuro, indicadores de estado, manejo de errores, setup_inicial.py |
| 5B — Eva animada y con voz | 8–9 | Avatar por capas, estados (escuchando, pensando, hablando, recordando), lip-sync |
| 6 — App de escritorio e instaladores | 10–11 | Ícono, ejecutable con `flet pack`, instaladores para Windows/Mac/Linux, GitHub Actions |
| 7 — Docker, opcional | 12 | `docker compose up` para servidores |
| 8 — Pruebas, documentación y lanzamiento | 13–14 | Pruebas con pytest, README, pruebas con usuarios, versión 1.0 publicada |

**Siguiente paso concreto:** completar la Fase 0 esta semana. Después, el siguiente bloque de código a escribir es `llm/base.py` y la primera pantalla de chat en Flet de la Fase 1.

## Instalación y ejecución

```bash
py -3.11 -m venv venv
venv\Scripts\activate          # Windows
pip install -r requirements.txt -r requirements-dev.txt
python -m app.main             # abre la ventana de chat con Eva
python -m pytest               # corre las pruebas (no necesita servidor de modelo)
```

El chat se conecta a LM Studio en `http://localhost:1234/v1` y usa el primer
modelo de chat que liste el servidor. Hasta que exista la pantalla de ajustes
(Fase 2), ambos se pueden cambiar con variables de entorno:

```bash
set EVA_MODELO=qwen/qwen3-1.7b                 # Windows (export en Mac/Linux)
set EVA_SERVIDOR=http://localhost:11434/v1     # Ollama en lugar de LM Studio
```

Para verificar el servidor de modelo local (LM Studio):

```bash
lms server start
curl http://localhost:1234/v1/models
```

## Formas de distribución

| Forma | Para quién | Cómo se instala | Qué incluye |
|---|---|---|---|
| Aplicación de escritorio | Usuario común | Doble clic en `.exe`, `.dmg` o `.AppImage` | La app completa con Engram incluido; Ollama o LM Studio se instalan aparte si se quiere IA local |
| Docker (opcional) | Servidores y usuarios avanzados | `docker compose up` | Servicio `app` (puerto 8550, se abre en el navegador) y servicio `ollama` (puerto 11434) |

**Primer inicio de la app de escritorio:** la app revisa si Ollama o LM Studio están abiertos; si no hay ninguno, ofrece instalar uno o usar la nube. Luego descarga los modelos de voz (unos 500 MB) a la carpeta del usuario, para que el instalador sea liviano.

## Licencia

MIT

Más detalles en `docs/plan-de-trabajo.md`.