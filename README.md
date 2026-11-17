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
│   ├── auth.py            # Inicio de sesión con OpenRouter (OAuth con PKCE)
│   ├── secretos.py        # Claves de API en el almacén seguro del sistema (keyring)
│   ├── setup_inicial.py   # Revisa Ollama y descarga modelos al primer inicio
│   ├── ui/
│   │   ├── chat.py        # Pantalla de chat
│   │   ├── ajustes.py     # Pantalla de configuración
│   │   ├── eva_avatar.py  # Eva animada por capas
│   │   └── eva_estados.py # Elige el estado de Eva según lo que pasa en la app
│   ├── llm/
│   │   ├── base.py        # Interfaz común para cualquier modelo
│   │   ├── local_client.py    # Ollama y LM Studio
│   │   └── cloud_client.py    # OpenAI, Claude, Gemini y OpenRouter
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

**Estado actual:** Fases 0 y 1 completas con el modelo local (LM Studio). De la Fase 2 está hecho el bloque 2A (historial en SQLite, barra lateral de conversaciones y ajustes locales en `config.json`) y el bloque 2B (memoria a largo plazo con Engram: Eva recuerda tu nombre y tus gustos entre conversaciones distintas, y la pantalla **Recuerdos** permite verlos y borrarlos). Con el bloque 2C, Eva también usa OpenAI, Claude, Gemini u OpenRouter con tu clave de API (guardada en el almacén seguro del sistema) o iniciando sesión con OpenRouter, y usa la nube como respaldo al extraer recuerdos.

**Siguiente paso concreto:** bloque 2D ("Iniciar sesión con ChatGPT" para usar el plan Plus/Pro).

## Instalación y ejecución

### 1. Preparar el entorno (solo la primera vez)

```powershell
cd C:\xampp\htdocs\eva
py -3.11 -m venv venv
venv\Scripts\Activate.ps1
pip install -r requirements.txt -r requirements-dev.txt
python scripts/descargar_engram.py   # baja Engram (memoria a largo plazo) a bin/
```

`scripts/descargar_engram.py` baja la versión fijada de Engram (v3.7.2) para tu
sistema desde GitHub, comprueba su huella SHA-256 y deja solo el programa en
`bin/` (que no se sube a git). Sin este paso la app funciona igual, pero sin
memoria a largo plazo. Para bajar el de otro sistema:
`python scripts/descargar_engram.py --plataforma linux-arm64`.

### 2. Prender el modelo local (LM Studio)

Abre LM Studio y activa su servidor local (pestaña *Developer* → *Start Server*),
o desde la terminal:

```powershell
lms server start
curl http://localhost:1234/v1/models      # debe listar los modelos descargados
```

No hace falta cargar el modelo a mano: LM Studio lo carga con la primera pregunta.

### 3. Correr la app

PowerShell:

```powershell
cd C:\xampp\htdocs\eva
venv\Scripts\Activate.ps1
python -m app.main             # abre la ventana de chat con Eva
```

CMD (cambia solo la activación):

```cmd
venv\Scripts\activate.bat
python -m app.main
```

Mac/Linux: `source venv/bin/activate` y luego `python -m app.main`.

> Si PowerShell bloquea `Activate.ps1` con un error de "ejecución de scripts
> deshabilitada", ejecuta una sola vez:
> `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`

**Qué deberías ver:** una ventana oscura con el campo de texto abajo. Tu mensaje
aparece a la derecha en azul y la respuesta de Eva a la izquierda en gris: primero
muestra "…" y luego se va escribiendo a medida que llega. Enter envía y
Shift+Enter hace un salto de línea. Si LM Studio está apagado, aparece un
mensaje en rojo explicándolo.

A la izquierda está la barra lateral: **Nueva conversación** empieza un chat en
blanco y la lista muestra las conversaciones guardadas (la más reciente arriba);
al hacer clic en una, se vuelve a abrir y Eva la retoma con sus últimos 20 mensajes.
Las conversaciones se guardan solas y siguen ahí al cerrar y volver a abrir la app.

### 4. Elegir el proveedor y el modelo

Abajo en la barra lateral se ve el modelo en uso (por ejemplo
`LM Studio · qwen/qwen3-1.7b`; mientras busca el servidor dice `Conectando…`, y
si no lo encuentra muestra un solo aviso en rojo que desaparece al conectarse).
El botón de engranaje abre **Ajustes**, donde se
elige el proveedor (LM Studio, Ollama, OpenAI, Claude, Gemini u OpenRouter) y uno
de sus modelos. Al guardar, el cambio se aplica enseguida. La primera vez se usa
LM Studio con el primer modelo de chat que liste (los modelos Qwen3 "piensan" unos
segundos antes de contestar).

Para un proveedor de la nube, escribe tu **clave de API** y pulsa Enter (o el botón
de recargar) para ver sus modelos. Con OpenRouter también puedes pulsar
**Iniciar sesión con OpenRouter**: se abre el navegador, entras con tu cuenta y
Eva recibe una clave ligada a ella (el límite de gasto lo fijas tú en OpenRouter;
un segundo clic cancela). Las claves se guardan en el almacén seguro del sistema
(Administrador de credenciales en Windows, Llavero en Mac), **nunca** en
`config.json`; si ya hay una guardada, el campo dice "Clave guardada" y
**Borrar clave** la elimina. No se usan las suscripciones Claude Pro/Max ni Google
AI Pro, porque sus términos no lo permiten en apps de terceros. El inicio de
sesión con ChatGPT llegará en el bloque 2D.

### 5. Memoria a largo plazo (Engram)

Además del historial de cada conversación, Eva recuerda datos tuyos entre
conversaciones distintas. Por ejemplo, si le dices "Me llamo Ana y me gusta el
café" y luego, en una **conversación nueva**, le preguntas "¿Cómo me llamo?",
Eva sabe que te llamas Ana.

Cómo funciona:

- Al abrir la app, Eva arranca [Engram](https://github.com/Gentleman-Programming/engram)
  en segundo plano (el programa de `bin/`, o `engram` si está instalado en el
  sistema) y se conecta a él por MCP.
- **Antes de cada respuesta** busca recuerdos relacionados con tu pregunta y le
  pasa al modelo hasta 8 recuerdos (primero los encontrados y luego los demás
  datos que conoce de ti). Los recuerdos son solo contexto: no se guardan en el
  historial del chat.
- **Después de cada respuesta**, en segundo plano y sin demorar el chat, le pide
  al mismo modelo local que extraiga datos duraderos de tu mensaje (nombre,
  gustos, decisiones) y los guarda. Si un dato cambia (por ejemplo, tu nombre
  completo), se actualiza en lugar de duplicarse. Si el modelo local falla o
  su respuesta no se entiende, se reintenta **una vez** con el último modelo de
  la nube que guardaste en Ajustes (si su clave está guardada); si el modelo
  local contesta que no hay nada que guardar, no se usa la nube.
- El botón **Recuerdos** de la barra lateral muestra lo que Eva recuerda de ti
  (título y contenido). Cada recuerdo tiene un botón para borrarlo: pide
  confirmación y lo borra para siempre.
- Si Engram no está, no arranca o falla, el chat sigue funcionando sin memoria
  a largo plazo y bajo el modelo aparece la nota discreta "Memoria a largo
  plazo no disponible" (el detalle queda en el registro de errores). Al cerrar
  la app, Eva cierra también Engram.

Los recuerdos se guardan en la subcarpeta `engram/` de la carpeta de datos de
Eva (ver abajo), nunca en `~/.engram`, que es la de otros usos de Engram.

### Dónde se guardan los datos

| Sistema | Carpeta |
| --- | --- |
| Windows | `%APPDATA%\Eva` |
| Mac | `~/Library/Application Support/Eva` |
| Linux | `~/.local/share/eva` |

Ahí están `eva.db` (el historial del chat, en SQLite), `config.json` (los
ajustes) y la carpeta `engram/` (los recuerdos a largo plazo, con el registro
`engram-mcp.log` de Engram). En Mac y Linux la carpeta queda con permisos `700`: solo tu usuario
puede leer tus conversaciones. Para usar otra carpeta, define la variable `EVA_DATA_DIR` antes de
correr la app; por ejemplo, en PowerShell: `$env:EVA_DATA_DIR = "C:\pruebas\eva"`.

### 6. Correr las pruebas

No necesitan LM Studio ni Engram: usan un servidor y una memoria simulados. Si
el programa de Engram está en `bin/` (o en el PATH), unas pruebas extra lo usan
de verdad, siempre en una carpeta temporal.

```powershell
python -m pytest -q
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