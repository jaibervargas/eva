# Plan de trabajo – Eva, el asistente de IA de IDEVA

Nov 16, 2026 · @Esperanza · actualizado el Nov 17, 2026 (bloque 2C terminado)

## 1. Resumen y objetivos

Vamos a construir, en unas 14 semanas y por fases, a Eva, el asistente de IA de IDEVA, con la que se pueda chatear por texto y por voz. Se instala como una aplicación de escritorio normal (doble clic en un instalador para Windows, Mac o Linux), sin necesidad de Docker. Docker queda como segunda opción para servidores o usuarios avanzados. Cada fase termina en algo que funciona y se puede probar.

**Objetivos del producto (versión 1.0):**

- Conversar por texto con memoria de la conversación y memoria persistente a largo plazo (Engram) que recuerda datos y preferencias del usuario entre conversaciones.
- Hablarle por micrófono y que responda en voz alta, en español.
- Elegir el "cerebro": modelo local (Ollama o LM Studio, sin internet) o modelo en la nube, conectado con clave de API (OpenAI, Claude o Gemini) o iniciando sesión con una cuenta (ChatGPT Plus/Pro, u OpenRouter para usar Claude, Gemini y otros modelos).
- Instalarse como aplicación de escritorio: `.exe` en Windows, `.dmg` en Mac y `.AppImage` en Linux.
- Opcional: instalarse con `docker compose up` en servidores.

**Fuera del alcance de la v1.0** (se deja para después): conectar documentos propios (RAG), app móvil, palabra de activación "Oye, Eva", usuarios múltiples con login y firma de la app en Mac (cuesta 99 USD al año).

## 2. Arquitectura e infraestructura

El mismo código se distribuye de dos formas: como aplicación de escritorio (forma principal) y como contenedores Docker (opcional). Lo único que cambia entre las dos es cómo se empaqueta; la lógica de IA, voz y memoria es la misma.

**Flujo de una pregunta por voz:**

1. El usuario pulsa el botón de micrófono en la app y graba su voz.
2. La app transcribe el audio a texto (STT con faster-whisper).
3. El texto, junto con el historial guardado y los recuerdos relacionados que devuelve Engram, va al modelo de IA (Ollama o LM Studio en local, o API en la nube).
4. La respuesta en texto se muestra en el chat y se guarda en el historial (SQLite); si trae un dato importante del usuario, también se guarda en Engram.
5. La respuesta se convierte en audio (TTS con Piper) y la app la reproduce automáticamente.

**Formas de distribución:**

| Forma | Para quién | Cómo se instala | Qué incluye |
| --- | --- | --- | --- |
| Aplicación de escritorio | Usuario común | Doble clic en `.exe`, `.dmg` o `.AppImage` | La app completa con Engram incluido; Ollama o LM Studio se instalan aparte si se quiere IA local |
| Docker (opcional) | Servidores y usuarios avanzados | `docker compose up` | Servicio `app` (puerto 8550, se abre en el navegador) y servicio `ollama` (puerto 11434) |

**Primer inicio de la app de escritorio:** la app revisa si Ollama o LM Studio están abiertos; si no hay ninguno, ofrece instalar uno o usar la nube. Luego descarga los modelos de voz (unos 500 MB) a la carpeta del usuario, para que el instalador sea liviano.

**Principio de diseño del código:** cada pieza (modelo de IA, STT, TTS, memoria) vive en su propio módulo con una interfaz simple. Ollama y LM Studio ofrecen la misma API que OpenAI, así que un solo cliente sirve para los tres: solo cambia la dirección del servidor (Ollama en localhost:11434, LM Studio en localhost:1234). Así se cambia de proveedor desde los ajustes, sin tocar el resto del código.

## 3. Stack tecnológico

Todo el proyecto se escribe en Python, el lenguaje estándar de la IA, para no tener que aprender varios lenguajes a la vez.

| Componente | Tecnología elegida | Alternativa | Por qué |
| --- | --- | --- | --- |
| Lenguaje | Python 3.11 | — | Todas las librerías de IA están en Python |
| Interfaz | Flet (ventanas nativas hechas en Python) | Tauri + JavaScript | Crea apps de escritorio para Windows, Mac y Linux sin saber HTML/JS; graba y reproduce audio |
| IA local | Ollama (puerto 11434) o LM Studio (puerto 1234), con modelos como Llama 3.2 3B o Qwen 2.5 7B | — | Gratis, privado, corre sin internet |
| IA en la nube | API de OpenAI, Claude o Gemini con clave de API, o "Iniciar sesión con ChatGPT" u OpenRouter con la cuenta del usuario | — | Mejor calidad en equipos sin GPU; con la cuenta de ChatGPT, el usuario usa su plan Plus o Pro sin pagar la API aparte |
| Voz a texto (STT) | faster-whisper (modelo `small`) | API Whisper de OpenAI | Funciona sin internet y entiende bien el español |
| Texto a voz (TTS) | Piper (voz `es_ES` o `es_MX`) | edge-tts (en línea), pyttsx3 | Voz natural, offline y rápida en CPU |
| Memoria | SQLite (historial del chat) + Engram de Gentleman Programming (memoria a largo plazo) | Engram de engram.so | Engram es un solo programa con SQLite: funciona sin internet, es gratis (licencia MIT) y viene listo para Windows, Mac y Linux |
| Configuración | Pantalla de ajustes + archivo `config.json` | `.env` (en Docker) | El usuario cambia modelo o clave de API sin tocar archivos |
| Ejecutable | `flet pack` (usa PyInstaller por dentro) | Nuitka | Convierte la app en un ejecutable con todo incluido |
| Instaladores | Inno Setup (Windows), create-dmg (Mac), appimagetool (Linux) | — | Instaladores de doble clic, gratuitos |
| Construcción automática | GitHub Actions | — | Cada instalador debe construirse en su propio sistema; GitHub lo hace en la nube sin tener tres equipos |
| Docker (opcional) | Docker + Docker Compose | — | Segunda forma de instalación para servidores |
| Control de versiones | Git + GitHub | — | Historial de cambios y respaldo |
| Pruebas | pytest | — | Verificar cada módulo por separado |

## 4. Qué se necesita

**Hardware mínimo:**

- 8 GB de RAM si se usa IA en la nube; 16 GB si se usa Ollama local.
- 10 GB libres en disco para desarrollar (modelos de IA y de voz); el usuario final necesita unos 2 GB con IA en la nube.
- Micrófono y parlantes o audífonos.
- GPU opcional: acelera Ollama y Whisper, pero no es obligatoria.

**Software para instalar antes de empezar:**

- [ ] Python 3.11
- [ ] Git y una cuenta de GitHub
- [ ] Visual Studio Code (con la extensión de Python)
- [ ] Docker Desktop (solo para la Fase 7, opcional)
- [ ] Ollama o LM Studio (uno o ambos), si se va a usar IA local
- [ ] Una clave de API (OpenAI, Anthropic o Google) o una cuenta de ChatGPT Plus/Pro u OpenRouter, si se va a usar IA en la nube

**Conocimientos que conviene repasar:** Python básico (funciones, clases, módulos), comandos básicos de terminal y Git (`add`, `commit`, `push`). Lo demás se aprende sobre la marcha en cada fase.

## 5. Estructura de carpetas

Esta es la estructura que se crea en la Fase 0 y se va llenando en las siguientes.

```
eva/
├── app/
│   ├── main.py            # Interfaz Flet (punto de entrada)
│   ├── config.py          # Lee y guarda config.json
│   ├── auth.py            # Inicio de sesión con OpenRouter (y con ChatGPT en el bloque 2D)
│   ├── secretos.py        # Claves y tokens en el almacén seguro del sistema (keyring)
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

## 6. Propuesta de diseño (UI/UX y voz)

Una sola pantalla de chat, oscura y minimalista, donde hablar sea tan fácil como escribir.

**Distribución de la pantalla:**

- **Barra lateral:** selector de modelo (Local / Nube), selector de voz, interruptor "Responder en voz alta", botón "Nueva conversación" y lista de conversaciones anteriores.
- **Zona central:** burbujas de chat; las del usuario a la derecha y las del asistente a la izquierda, cada una con un botón pequeño para volver a escuchar la respuesta.
- **Zona inferior:** campo de texto y, al lado, un botón de micrófono grande y visible.

**Estados visibles para el usuario:**

| Estado | Indicador | Cuándo aparece |
| --- | --- | --- |
| Escuchando | Micrófono en rojo con pulso | Mientras se graba la voz |
| Transcribiendo | "Entendiendo lo que dijiste…" | Mientras Whisper convierte el audio |
| Pensando | Tres puntos animados | Mientras responde el modelo |
| Hablando | Icono de parlante + botón Detener | Mientras se reproduce la respuesta |
| Error | Mensaje en rojo con sugerencia | Sin micrófono, sin internet o modelo apagado |

**Guía visual:** fondo `#0F1117`, burbujas del usuario en azul `#2563EB`, burbujas del asistente en gris `#1F2937`, texto `#E5E7EB`, tipografía Inter o la del sistema, bordes redondeados de 12 px.

**Experiencia de voz:** el texto transcrito se muestra antes de la respuesta, para que el usuario confirme que se entendió bien. Las respuestas habladas deben ser cortas (el prompt del sistema le pide al modelo responder en 2 o 3 frases cuando la pregunta llega por voz).

## 6.1 Personaje: Eva, la semilla de luz

Eva tiene dos guías oficiales: la guía de marca (cómo se ve) y la guía de movimiento (cómo se mueve). Las dos imágenes de esta sección son la referencia visual para todo el desarrollo.

### Guía de marca

Paleta de cinco colores: turquesa, azul, violeta, lavanda y perla. Valores del personaje: cercana, inteligente y única. Corrección pendiente en la imagen: el estado "Pablando" debe decir "Hablando".

&#91;image: Guía de marca de Eva: el personaje, sus estados, la paleta de colores, el ícono y la pantalla de chat de IDEVA\]

### Guía de movimiento y animación

Eva no tiene partes rígidas: es un cuerpo líquido y luminoso que flota en el centro de su espacio, se mueve con aceleración suave y se desliza en vez de saltar.

&#91;image: Guía de movimiento y animación de Eva: reposo, escuchando, pensando, hablando y recordando, cuadro por cuadro\]

| Estado | Movimiento | Detalle | Se activa cuando |
| --- | --- | --- | --- |
| Reposo | Respira: se estira y encoge 3–5 % en ciclos de 4 s | Ojos cerrados o semicerrados; el aura pulsa tenue | No hay actividad |
| Escuchando | Se inclina unos 10° hacia el usuario | Se abre la base de luz (pétalos) y las partículas se estiran | El micrófono graba o el usuario escribe |
| Pensando | Su núcleo gira lento sobre su eje | Partículas en órbita y mirada reflexiva | La app espera la respuesta del modelo |
| Hablando | Contorno y aura vibran con el volumen de la voz | Rebotes en palabras clave y boca sincronizada | Suena el audio de Piper |
| Recordando | Un destello ilumina todo su cuerpo | Aparece la red geométrica de Engram con chispas | Engram devuelve un recuerdo |

### Contrato IA–cuerpo (eva\_behavior\_mapping.json)

El archivo se guarda en `assets/eva/eva_behavior_mapping.json` con un cambio: los cinco estados los activa la app con sus propios eventos, no el modelo escribiendo etiquetas como `[STATE:THINKING]`. Los modelos locales pequeños no siempre escriben esas etiquetas, y si falta una, Eva se queda congelada; la app, en cambio, sabe exactamente cuándo graba, espera, reproduce audio o encuentra un recuerdo. Las etiquetas del modelo quedan solo para matices de emoción opcionales (por ejemplo `[EMO:FELIZ]`), y la app las borra antes de mostrar el texto y de leerlo en voz alta.

| Sonido | Forma de la boca | Intensidad |
| --- | --- | --- |
| A, O | Muy abierta | 1,2 |
| E, I | Sonrisa estrecha | 1,0 |
| U, M, B, P | Cerrada y comprimida | 0,9 |
| Silencio | Curva suave | 0,5 |

### Qué se requiere para verla, que se mueva y hable

Las dos imágenes son ilustraciones planas: sirven de referencia, pero no se pueden animar directamente. Para que Eva aparezca en la app, se mueva y hable hace falta:

1. **Redibujar a Eva por capas** en vector (Figma, Illustrator o Inkscape): cuerpo, brillo, mejillas, cuatro juegos de ojos (abiertos, cerrados tranquilos, cerrados felices, mirando arriba), cinco bocas (A/O, E/I, U/M/B/P, silencio y sonrisa) y los efectos (anillo, pétalos, partículas, ondas y red de Engram), exportados en SVG y PNG a 2x. Las imágenes actuales fueron generadas con IA: antes de usarlas como marca comercial, revisa los términos de uso de la herramienta que las generó; la versión redibujada es la que conviene registrar.
2. **Un componente de avatar en Flet** (`ui/eva_avatar.py`): apila las capas y las anima con las animaciones de Flet (escala para respirar, rotación para inclinarse y girar, opacidad para destellos y desplazamiento para flotar). Ofrece dos funciones: `cambiar_estado()` y `cambiar_boca()`.
3. **Un controlador de estados** (`ui/eva_estados.py`): escucha los eventos de la app y cambia el estado con transiciones suaves de 300 a 500 ms.
4. **Sincronización de labios (lip-sync)**. Versión 1, por volumen: antes de reproducir, se analiza el audio de Piper en bloques de 50 ms con numpy y se calcula qué tan abierta va la boca; mientras suena, la posición del audio elige la boca. Versión 2: Rhubarb Lip Sync en modo fonético (sirve para español) para elegir la forma exacta de cada sonido.
5. **Vida en reposo**: parpadeo aleatorio cada 3 a 6 segundos y una flotación suave, para que nunca parezca congelada.
6. **Rendimiento y accesibilidad**: máximo 30 cuadros por segundo, pausar la animación con la ventana minimizada y una opción "Reducir movimiento" en ajustes.

Para una versión futura con animaciones más ricas se puede usar Rive. Flet lo muestra con la extensión flet-rive, pero su documentación solo ofrece el archivo, la velocidad y un marcador de carga, sin forma de cambiar estados desde Python; haría falta escribir una extensión propia en Dart. Por eso la versión 1 usa capas animadas desde Python.

## 7. Plan de trabajo por fases

Son 10 fases; cada una termina con un entregable que se puede probar. Marca las tareas a medida que las completes.

### Fase 0 — Preparación del entorno (semana 1)

- [x] Instalar Python, Git, VS Code, Ollama y LM Studio; en LM Studio, descargar un modelo y activar su servidor local. *(Python 3.11, Git y LM Studio con su servidor en el puerto 1234; Ollama queda pendiente porque por ahora se trabaja con LM Studio.)*
- [x] Crear el repositorio en GitHub y la estructura de carpetas de la sección 5.
- [x] Crear el entorno virtual (`python -m venv venv`) e instalar Flet (`pip install flet`).
- [ ] Descargar un modelo en Ollama (`ollama pull llama3.2`) y probarlo en la terminal. *(Pospuesto: se usa LM Studio; el mismo cliente sirve para Ollama cuando se instale.)*
- [x] Abrir una ventana de prueba con Flet ("Hola mundo").

**Entregable:** repositorio creado, Ollama respondiendo y una ventana de Flet abierta. **Estado: completada con LM Studio en lugar de Ollama.**

### Fase 1 — Chat de texto con la IA (semana 2)

- [x] Escribir `llm/base.py` con una función común `responder(mensajes)`.
- [x] Implementar `local_client.py` y `cloud_client.py`. *(`local_client.py` listo para LM Studio y Ollama; `cloud_client.py` se movió a la Fase 2, junto con las claves de API y el inicio de sesión.)*
- [x] Escribir el prompt del sistema (se presenta como Eva, de IDEVA; personalidad, idioma español, tono).
- [x] Crear la pantalla de chat en Flet: lista de burbujas y campo de texto.
- [x] Mostrar la respuesta en streaming (palabra por palabra).

**Entregable:** chat por texto funcionando con el modelo local y con el de la nube. **Estado: chat local funcionando y verificado (11 pruebas con pytest); falta la revisión visual. La parte de la nube pasa a la Fase 2.**

### Fase 2 — Memoria, Engram y ajustes (semanas 3 y 4)

- [x] Implementar `llm/cloud_client.py` (OpenAI, Claude y Gemini con clave de API), movido desde la Fase 1. *(Claude con el SDK nativo `anthropic`; OpenAI, Gemini y OpenRouter con el SDK `openai` y sus direcciones compatibles oficiales.)*
- [x] Crear la base SQLite con las tablas `conversaciones` y `mensajes`, guardada en la carpeta del usuario.
- [x] Guardar cada mensaje y enviar los últimos 20 al modelo como contexto.
- [x] Agregar en la barra lateral: nueva conversación y lista de conversaciones anteriores.
- [ ] Crear la pantalla de ajustes (proveedor: Ollama, LM Studio o nube; modelo, elegido de la lista de modelos instalados; forma de acceso a la nube: clave de API o cuenta (ChatGPT u OpenRouter); voz) que guarda en `config.json`. *(Parcial: elige LM Studio, Ollama, OpenAI, Claude, Gemini u OpenRouter, el modelo, y la clave de API o el inicio de sesión con OpenRouter (bloque 2C); faltan la cuenta de ChatGPT (bloque 2D) y la voz (Fase 4).)*
- [x] Descargar el programa de Engram para Windows, Mac y Linux desde sus versiones publicadas en GitHub y guardarlo en `bin/`. *(`python scripts/descargar_engram.py` baja Engram v3.7.2 y comprueba su SHA-256; con `--plataforma` baja el de otro sistema. Probado en Windows; Mac y Linux se probarán al empaquetar en la Fase 6.)*
- [x] Implementar `memory/engram_client.py`: la app abre Engram en segundo plano y se conecta a él por MCP con la librería `mcp` de Python. *(Los recuerdos de Eva van en su propia carpeta `engram/`, nunca en la de otro uso de Engram. Falta ubicar el programa dentro de la app empaquetada, en la Fase 6.)*
- [x] Antes de cada respuesta, buscar en Engram los recuerdos relacionados con la pregunta y agregarlos al contexto del modelo. *(Como Engram busca palabras completas, también se agregan los demás datos conocidos del usuario, hasta 8 recuerdos en total.)*
- [x] Después de cada respuesta, guardar en Engram los datos importantes del usuario (nombre, gustos, decisiones). Si el modelo local no detecta bien qué guardar, usar el de la nube solo para esa tarea. *(El mismo modelo local extrae los datos en segundo plano, sin demorar el chat; si falla o su respuesta no se entiende, se reintenta una vez con el último modelo de la nube guardado en Ajustes.)*
- [x] Agregar en ajustes una pantalla para ver y borrar recuerdos. *(Botón **Recuerdos** en la barra lateral: lista los recuerdos y borra cada uno para siempre, previa confirmación.)*
- [ ] Implementar "Iniciar sesión con ChatGPT" en `auth.py` (OAuth con PKCE): la app abre el navegador, el usuario entra con su cuenta y la app usa su plan Plus o Pro. *(Bloque 2D. OpenAI ofrece "Sign in with ChatGPT" en vista previa. Como Eva es open-source y se instala en el equipo, no hace falta pedir un client ID: la app se registra sola la primera vez (`client_id=dynamic_agent_client` y un `ext_agent_host_id` propio de cada equipo) y después reusa el client ID que recibe. El plan solo se puede usar con la Responses API (`stream: true`, `store: false`). Si Eva se vendiera o se alojara en un servidor, habría que llenar el formulario de interés de OpenAI.)*
- [x] Para Claude y Gemini, ofrecer clave de API o "Iniciar sesión con OpenRouter" (OAuth con PKCE, sin necesidad de registrar la app): el usuario entra con su cuenta de OpenRouter, la app recibe una clave ligada a esa cuenta y el usuario fija su propio límite de gasto. No se usa la suscripción Claude Pro/Max ni Google AI Pro, porque sus términos no lo permiten en apps de terceros.
- [x] Guardar el token de la cuenta y las claves de API con la librería `keyring` (el almacén seguro del sistema operativo), nunca en texto plano dentro de `config.json`.

**Entregable:** el asistente recuerda lo hablado, recuerda datos del usuario entre conversaciones distintas y se configura sin tocar archivos. **Estado: bloques 2A, 2B y 2C terminados, revisados y subidos a GitHub (412 pruebas con pytest). Falta el bloque 2D (cuenta de ChatGPT); la voz de los ajustes llega en la Fase 4.**

### Fase 3 — Escuchar: voz a texto (semana 5)

- [ ] Agregar el botón de micrófono con el grabador de audio de Flet (`AudioRecorder`).
- [ ] Implementar `voice/stt.py` con faster-whisper (modelo `small`, idioma español).
- [ ] Mostrar el texto transcrito y enviarlo al chat.
- [ ] Medir el tiempo de transcripción; si pasa de 5 segundos, probar el modelo `base`.

**Entregable:** el usuario habla y su pregunta aparece escrita y respondida.

### Fase 4 — Hablar: texto a voz (semana 6)

- [ ] Descargar una voz de Piper en español.
- [ ] Implementar `voice/tts.py` que convierte la respuesta en un archivo de audio.
- [ ] Reproducir el audio automáticamente con el reproductor de Flet (`Audio`).
- [ ] Agregar el interruptor "Responder en voz alta" y el botón para volver a escuchar.

**Entregable:** conversación completa por voz, ida y vuelta.

### Fase 5 — Interfaz final y experiencia (semana 7)

- [ ] Aplicar la guía visual de la sección 6 (tema oscuro de Flet y colores).
- [ ] Agregar los indicadores de estado (escuchando, pensando, hablando).
- [ ] Manejar errores: sin micrófono, sin internet, Ollama o LM Studio apagado, clave de API inválida. *(Parcial, adelantado en el bloque 2A: LM Studio u Ollama apagado o que se corta a mitad de respuesta, errores inesperados, ajustes que no se pueden guardar y conexión en segundo plano sin congelar la ventana. Pendiente de la revisión: si la conexión falla de forma inesperada, la etiqueta queda en "Conectando…" sin burbuja de error.)*
- [ ] Escribir `setup_inicial.py`: detectar si Ollama o LM Studio están abiertos y descargar los modelos de voz al primer inicio, con barra de progreso.

**Entregable:** versión beta lista para que otra persona la pruebe desde el código.

### Fase 5B — Eva animada y con voz (semanas 8 y 9)

- [ ] Redibujar a Eva por capas a partir de la guía de marca y exportarla a `assets/eva/`.
- [ ] Implementar `ui/eva_avatar.py` con los cinco estados de la guía de movimiento.
- [ ] Implementar `ui/eva_estados.py` y conectarlo a los eventos de grabación, modelo, audio y Engram.
- [ ] Implementar el lip-sync por volumen con el audio de Piper.
- [ ] Agregar parpadeo, flotación en reposo y la opción "Reducir movimiento".
- [ ] Guardar `eva_behavior_mapping.json` y borrar las etiquetas de emoción del texto antes de mostrarlo y de leerlo en voz alta.

**Entregable:** Eva aparece en la app, cambia de estado sola y mueve la boca cuando habla.

### Fase 6 — Aplicación de escritorio e instaladores (semanas 10 y 11)

- [ ] Crear el ícono de Eva con la identidad visual de IDEVA y nombrar los instaladores Eva-Setup.exe, Eva.dmg y Eva.AppImage.
- [ ] Generar el ejecutable con `flet pack` y probar que abre en un equipo sin Python.
- [ ] Crear el instalador de Windows con Inno Setup (`.exe` con acceso directo en el escritorio).
- [ ] Crear el `.dmg` para Mac y el `.AppImage` para Linux.
- [ ] Configurar GitHub Actions para que construya los tres instaladores automáticamente.
- [ ] Probar la instalación en al menos un equipo Windows y uno Mac o Linux.

**Entregable:** instaladores de doble clic para los tres sistemas operativos.

### Fase 7 — Docker, opcional (semana 12)

- [ ] Escribir el `Dockerfile` (Python 3.11-slim) que corre la app de Flet en modo web.
- [ ] Escribir `docker-compose.yml` con los servicios `app` y `ollama` y volúmenes para modelos y datos.
- [ ] Probar que se abre en el navegador con `docker compose up`.

**Entregable:** segunda forma de instalación para servidores.

### Fase 8 — Pruebas, documentación y lanzamiento (semanas 13 y 14)

- [ ] Escribir pruebas con pytest para memoria, STT, TTS y clientes de IA. *(Parcial: 68 pruebas cubren memoria, cliente local, configuración y pantalla. Pendiente: fijar la plataforma en dos pruebas de permisos para que en Mac no toquen la carpeta real del usuario.)*
- [ ] Escribir el `README.md`: requisitos, instalación, uso, problemas frecuentes y cómo saltar la advertencia de seguridad en Mac y Windows.
- [ ] Pedir a 2 o 3 personas que lo instalen y anotar sus problemas.
- [ ] Corregir lo encontrado y publicar la versión 1.0 con sus instaladores en GitHub Releases.

**Entregable:** versión 1.0 publicada y documentada.

## 8. Cronograma

El plan dura 14 semanas dedicando unas 8 a 10 horas por semana; si se omite Docker (Fase 7) queda en 13.

| Semana | Fase | Hito al terminar |
| --- | --- | --- |
| 1 | 0 — Preparación | Ollama responde y Flet abre una ventana |
| 2 | 1 — Chat de texto | Primer chat funcionando |
| 3–4 | 2 — Memoria, Engram y ajustes | Recuerda conversaciones y datos del usuario |
| 5 | 3 — Voz a texto | Entiende lo que se le dice |
| 6 | 4 — Texto a voz | Responde en voz alta |
| 7 | 5 — Interfaz final | Versión beta |
| 8–9 | 5B — Eva animada | Eva se mueve y habla en la app |
| 10–11 | 6 — App de escritorio | Instaladores para Windows, Mac y Linux |
| 12 | 7 — Docker (opcional) | Instalación con un comando en servidores |
| 13–14 | 8 — Pruebas y lanzamiento | Versión 1.0 publicada |

## 9. Riesgos y cuándo está terminado

| Riesgo | Qué hacer |
| --- | --- |
| El modelo local es muy lento en equipos sin GPU | Usar un modelo más pequeño (Llama 3.2 3B) o cambiar a la nube desde los ajustes |
| Whisper tarda demasiado en transcribir | Pasar del modelo `small` al `base`, o usar la API de Whisper |
| Windows muestra "Windows protegió su PC" o el antivirus marca el `.exe` | Explicar en el README el botón "Ejecutar de todas formas"; más adelante, firmar el ejecutable |
| Mac bloquea la app por no estar firmada | Explicar en el README cómo abrirla con clic derecho → Abrir; firmarla cuando haya presupuesto (99 USD/año) |
| La app pide permiso de micrófono y el usuario lo niega | Mostrar un mensaje que explique cómo activarlo en la configuración del sistema |
| El instalador pesa demasiado | Descargar los modelos de voz al primer inicio, no incluirlos en el instalador |
| Costos de la API en la nube | Usar modelos económicos (GPT-4o mini, Claude Haiku, Gemini Flash) y fijar un límite de gasto en el panel del proveedor |
| OpenAI cambia o limita "Iniciar sesión con ChatGPT" (todavía está en vista previa) | Mantener siempre la opción de clave de API y OpenRouter como respaldo, y la IA local |
| Una clave de API se filtra o se gasta de más | Las claves viven solo en el almacén seguro del sistema (nunca en `config.json`, en los registros ni en los mensajes de error); con OpenRouter el usuario fija su propio límite de gasto, y "Borrar clave" la quita de Eva |

**La versión 1.0 está terminada cuando:**

- Una persona que no conoce el proyecto la instala con el instalador, sin ayuda y sin Docker.
- Responde por voz en menos de 8 segundos con el modelo en la nube.
- Recuerda la conversación después de cerrar y volver a abrir la app.
- Funciona en Windows y en al menos otro sistema operativo.

**Siguiente paso concreto:** las Fases 0 y 1 están completas con el modelo local (LM Studio). De la Fase 2 están terminados, revisados y en GitHub los bloques 2A (historial en SQLite, barra lateral y ajustes locales), 2B (memoria a largo plazo con Engram) y 2C (OpenAI, Claude, Gemini y OpenRouter con clave de API o inicio de sesión con OpenRouter, claves en `keyring` y respaldo de la nube para extraer recuerdos); cada bloque está en su rama (`feat/fase-2b-engram` y `feat/fase-2c-nube`, apilada sobre la anterior), con sus PR pendientes. Sigue el bloque 2D ("Iniciar sesión con ChatGPT" con registro dinámico) y luego la Fase 3 (voz a texto).
