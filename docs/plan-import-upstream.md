# Plan de importación de features del upstream (Mark-XLVIII → Mark-XXXIX)

> **Documento de trabajo para agentes/modelos implementadores.**
> Cada fase es autocontenida: puede asignarse a un modelo distinto sin contexto previo.
> Leer primero las secciones "Contexto", "Convenciones del repo" y "Qué NO importar".

---

## 1. Contexto

- **Este repo (local)**: `C:\Users\j.canadas\Mark-XXXIX` — fork muy evolucionado del original.
- **Upstream**: https://github.com/FatihMakes/Mark-XLVIII (rama `main`, sin releases; commits tipo "Add files via upload", así que la única fuente de verdad es el árbol de archivos actual).
- El fork ha divergido **masivamente**: el local tiene ~120 archivos que el upstream no tiene (WhatsApp bridge completo, Movies/torrents, Games, CardTrader, Gmail, Drive, Calendar, Music/YTMusic, UI modular `ui/`, agente `agent/`, tests, instalador Inno Setup…). El upstream, en cambio, conserva pocas piezas únicas.
- Comparación realizada el **2026-07-11** cruzando `git ls-files` local contra el tree de la API de GitHub (`/git/trees/main?recursive=1`), más lectura del código fuente de cada archivo exclusivo del upstream.

### 1.1 Resultado de la comparación

**Archivos que SOLO existen en upstream** (candidatos a importar):

| Archivo upstream | Tamaño | Qué es |
|---|---|---|
| `core/llm_client.py` | 21.9 KB | Cliente LLM local (Ollama / OpenAI-compatible: LM Studio, Jan, vLLM…) con streaming, tool-calls, warmup y verificación de modelo |
| `core/stt.py` | 4.1 KB | STT local: faster-whisper / vosk |
| `core/tts.py` | 16.5 KB | TTS multi-motor: EdgeTTS / Kokoro (offline, multiidioma) / ElevenLabs, con `TTSPlayer` thread-safe y troceo por frases |
| `core/installer.py` | 4.8 KB | Instalador bajo demanda de dependencias opcionales por motor (pip programático) |
| `actions/proactive.py` | 2.9 KB | `ProactiveEngine`: check-ins proactivos tras silencio prolongado, con contexto de memoria |
| `actions/system_monitor.py` | 6.5 KB | Telemetría CPU/RAM/GPU/temperatura, alertas `[SYSTEM_ALERT]` con cooldown, y snapshot `get_system_status()` |
| `dashboard/server.py` | 34.2 KB | Dashboard web local FastAPI (puerto 8000), cifrado AES-256-CBC a nivel de aplicación, login, WebSocket |
| `dashboard/static/app.html` | 26.6 KB | SPA del dashboard |
| `dashboard/static/login.html` | 6.6 KB | Login del dashboard |
| `dashboard/static/crypto-js.min.js` | 60.8 KB | CryptoJS servido en local (se auto-descarga una vez) |
| `config/certs/jarvis.crt/.key` | — | Certificados (legado; el server actual usa HTTP plano + AES) |
| `config/jarvis.ico` | 72.9 KB | Icono (local ya tiene `installer/mpv-icon.ico` y assets propios — opcional) |
| `ui.py` | 97.7 KB | HUD monolítico del upstream — **NO importar entero**, el local ya tiene `ui/` modular; solo revisar features puntuales |

**Archivos comunes con divergencia grande (>20% de tamaño)**:

| Archivo | Local | Upstream | Veredicto |
|---|---|---|---|
| `actions/web_search.py` | 4.8 KB | 10.2 KB | **Upstream más completo**: búsqueda paralela Gemini+DDG "first result wins", modos (news/research/price/compare), noticias con artículos reales (no portadas), helper de briefing en dos fases → importar/fusionar |
| `main.py` | 151 KB | 64 KB | Local muy superior en features; upstream tiene *comportamientos* del core loop a portar selectivamente (ver Fase 4) |
| `actions/send_message.py` | 10.2 KB | 7.6 KB | Local superior (integración WhatsApp) — no tocar |
| `actions/youtube_video.py` | 29.7 KB | 13.4 KB | Local superior — no tocar |
| `core/prompt.txt` | 5.9 KB | 2.6 KB | Local extendido — solo revisar si upstream añadió reglas de ruteo útiles (Fase 7) |
| `config/__init__.py` | 534 B | 787 B | Diferencia menor — revisar en Fase 7 |
| `readme.md` | 7.2 KB | 10 KB | Documentación; el "What's New in XLVIII" del upstream es la lista de comportamientos de la Fase 4 |

**Features de comportamiento anunciadas en el README del upstream** (viven dentro de su `main.py`/`ui.py`, no como archivos separados):

1. ✋ Instant Interrupt — ESC o botón corta al asistente al instante.
2. 👁️ Immediate Vision Acknowledgment — confirmación hablada inmediata al activar visión.
3. 📰 Parallel News Search — Gemini y DDG en paralelo, gana el primero.
4. 🗞️ Real News Articles — enlaza artículos, no homepages.
5. 🌅 Two-Phase Startup Briefing — briefing de arranque concurrente en dos fases.
6. 🔁 Smarter Reconnection — backoff exponencial (el local reconecta con 3 s fijos, `main.py:2239`).
7. 🛡️ Vision Cooldown & Echo Guard — anti-bucle de visión / anti-eco.
8. 🌐 Language-Aware Address — nunca mezcla idiomas al dirigirse al usuario.
9. 🪟 Zero Terminal Windows — subprocesos sin ventanas de consola (`CREATE_NO_WINDOW`).
10. 🔄 Session State Isolation — estado por sesión aislado entre reconexiones.

---

## 2. Qué NO importar (decisiones cerradas)

- **`actions/system_monitor.py` (telemetría)**: descartado por decisión del propietario (2026-07-11). No implementar.
- **`core/` modo offline (llm_client/stt/tts/installer)**: descartado por decisión del propietario. No implementar.
- **`dashboard/` del upstream (FastAPI + HTML + CryptoJS)**: NO se porta tal cual. El propietario quiere en el futuro una app móvil propia con **Expo + React Native**; el código upstream queda solo como referencia de la capa de API/cifrado (ver Fase 6).
- **`ui.py` monolítico**: el local ya migró a `ui/` (panels + widgets + theme). Cualquier feature de HUD del upstream se porta como cambio quirúrgico en `ui/`, jamás copiando `ui.py`.
- **`actions/send_message.py` del upstream**: el local integra el bridge de WhatsApp real.
- **Borrados del upstream**: upstream eliminó `agent/` y `setup.py`. El local **conserva** `agent/` (planner/executor/task_queue con tests) — no replicar el borrado.
- **`core/prompt.txt` del upstream como reemplazo**: el local tiene un prompt más rico; solo fusión selectiva.
- **Certificados `config/certs/`**: el dashboard actual del upstream usa HTTP plano + AES; los certs son legado. No traerlos salvo que se decida servir HTTPS.

---

## 3. Convenciones del repo (obligatorio para todo implementador)

- **Python se lanza con `py`** en esta máquina (no `python`/`python3`).
- **Rutas de config**: usar `actions/paths.py` → `config_path("api_keys.json")`. No hardcodear `BASE_DIR / "config"`.
- **Eventos UI ↔ backend**: `actions/event_bus.py` (publicar/suscribir). Los paneles se enteran por eventos, no por polling.
- **Registro de herramientas**: las tool declarations viven en `main.py` (clase con `_build_config()` → `types.LiveConnectConfig`, dispatch en `_execute_tool()` ~línea 1309). El catálogo hablado para el usuario está en `actions/capabilities.py` → `CAPABILITY_SECTIONS` (en español). **Toda tool nueva se añade en ambos sitios.**
- **Config de usuario**: `config/api_keys.json` + `memory/config_manager.py`. Los toggles de usuario van al panel `ui/panels/settings.py`.
- **Tests**: `tests/` con pytest. Para UI, smoke test con `grab()` sin `show()` (ver `tests/test_ui_smoke_all.py`); atención: renderizado offscreen da "tofu" en fuentes, es esperado.
- **Timestamps WhatsApp en milisegundos** — no mezclar segundos.
- **Subprocesos en Windows**: siempre `creationflags=subprocess.CREATE_NO_WINDOW` (coherente con feature 9 del upstream).
- **No romper**: el bridge de WhatsApp (`whatsapp_bridge/`), el flujo Gemini Live de `main.py` (hay guardas THINKING/header recién arregladas — ver commits `b593965`, `b1c15c1`), ni los `.mjs` vendorizados (`actions/vendor/`).
- **Estilo commits**: mensajes cortos imperativos en inglés, como el historial (`Fix header override on Gemini connect at startup`).
- Hay trabajo sin commitear (CardTrader, Games, downloads). **No hacer commit de archivos ajenos a tu fase.**

---

## 4. Fases de trabajo

Orden recomendado: **F0 → F8 (visión, prioritaria) → F3 → F9 (briefing) → F4 → F2 → F7**. F6 (app móvil Expo/React Native) queda para más adelante, cuando el core esté estable.

Fases descartadas por decisión del propietario: **F1** (telemetría/system monitor) y **F5** (modo offline). Se conservan abajo tachadas solo como registro de la decisión — ningún implementador debe ejecutarlas.

Dependencias: F2, F3, F4 y F7 son independientes entre sí. F6 depende de tener estable el core loop (F4).

---

### FASE 0 — Preparación (30 min)

**Objetivo**: dejar el código upstream accesible en local como referencia de solo lectura.

1. `git remote add upstream https://github.com/FatihMakes/Mark-XLVIII` (si no existe).
2. `git fetch upstream main`.
3. Crear rama de trabajo por fase: `feature/upstream-<fase>` (ej. `feature/upstream-system-monitor`).
4. Para consultar un archivo upstream sin merge: `git show upstream/main:actions/system_monitor.py`.
5. **No hacer `git merge upstream/main` jamás** — la divergencia es total y el merge sería destructivo.

**Criterio de aceptación**: `git show upstream/main:core/tts.py | head` imprime código.

---

### ~~FASE 1 — System Monitor + tool `system_status`~~ — DESCARTADA

Descartada por decisión del propietario (2026-07-11): no quiere telemetría. **No implementar.** Si en el futuro se reconsidera, la fuente es `upstream/main:actions/system_monitor.py` (autónomo, copiable casi tal cual).

---

### FASE 2 — ProactiveEngine (riesgo bajo, ~medio día)

**Fuente**: `upstream/main:actions/proactive.py` (57 líneas, copiable).

**Qué hace**: si el usuario lleva ≥15 min sin hablar y han pasado ≥10 min desde el último trigger, construye un prompt `[PROACTIVE_CHECK]` con hora actual + memoria del usuario y lo manda a Gemini para que decida si dice algo (1-3 frases) o calla.

**Pasos**:
1. Copiar a `actions/proactive.py`. Ajustar el import: upstream usa `from memory.memory_manager import format_memory_for_prompt` — verificar que esa función existe en el `memory/memory_manager.py` local; si no, implementar un formateador equivalente sobre `long_term.json`.
2. En `main.py`: registrar timestamp de última actividad de voz del usuario (en `_listen_audio` o donde se detecte input del usuario, actualizar `self._last_user_speech = time.monotonic()`).
3. Corrutina `_proactive_loop()` en el task group: cada 60 s, `if engine.should_trigger(self._last_user_speech):` → `engine.mark_triggered()` → enviar `engine.build_prompt(memoria)` vía `send_client_content` con `turn_complete=True`.
4. Guardas: no disparar si hay audio del asistente reproduciéndose, si el micro está muteado, o si hay una tool en ejecución. Revisar los flags de estado existentes (THINKING/header — commits recientes) y respetarlos.
5. Config: `proactive_enabled` (default **false** — es intrusivo), `proactive_min_silence` (900), `proactive_cooldown` (600) en settings + `ui/panels/settings.py`.
6. `CAPABILITY_SECTIONS`: mencionar en la sección de sistema/asistente.

**Tests**: unit del engine (should_trigger con tiempos simulados vía monkeypatch de `time.monotonic`).

**Riesgos**: interacción con el estado THINKING del header (bugs recientes en esa zona). El prompt exige responder en el idioma del usuario — el prompt local ya es español-céntrico, revisar coherencia.

---

### FASE 3 — Upgrade de `web_search.py` (riesgo medio, ~1 día)

**Fuente**: `upstream/main:actions/web_search.py` (10.2 KB). **No copiar encima**: el local ya tiene `_gemini_search` (usa `google.genai` nuevo + `gemini-3.1-flash-lite`, correcto), `_ddg_search`, `_compare` y `event_bus`. Es una **fusión**.

**Qué aporta upstream**:
- **Modos** de búsqueda: `news`, `research`, `price`, `compare` con prompts especializados por modo.
- **Paralelo "first result wins"**: lanza Gemini y DDG a la vez (threads), devuelve el primero que responda bien — corta latencia.
- **Noticias con artículos reales**: para modo news, DDG news + filtrado para devolver URLs de artículos, no portadas de medios.
- **Briefing helper**: función de briefing de arranque en dos fases (fase 1 rápida —hora/clima/etc.—, fase 2 con noticias en paralelo) que main.py invoca al arrancar.

**Pasos**:
1. `git show upstream/main:actions/web_search.py > tmp` y diff manual contra el local.
2. Portar los modos y el patrón first-wins **conservando** los helpers locales (`config_path`, `event_bus`, cliente genai nuevo — memoria del repo: hay dos SDKs de Gemini instalados, usar `google.genai`, NO `google.generativeai`).
3. Revisar la firma pública `web_search(...)`: main.py local la llama con su convención (`parameters=..., speak=...` o similar) — mantener compatibilidad exacta; añadir parámetro `mode` opcional.
4. Actualizar la declaración de la tool en `main.py` para exponer `mode` (enum: general/news/research/price/compare) y actualizar la descripción para que el modelo lo use.
5. Briefing de arranque: decidir si se quiere (toggle `startup_briefing_enabled`, default false). Si sí: al conectar la sesión por primera vez (no en reconexiones — usar un flag), inyectar fase 1 inmediata y fase 2 cuando lleguen las noticias.
6. `CAPABILITY_SECTIONS`: actualizar la entrada de búsqueda web con los modos.

**Tests**: `tests/test_web_search.py` — mockear DDGS y genai; verificar first-wins (el lento no pisa al rápido), fallback cuando uno falla, y filtro de artículos en modo news.

**Riesgos**: dependencia `ddgs` vs `duckduckgo_search` (el local ya maneja ambas — conservar ese try/except). Rate-limit de DDG en tests → todo mockeado.

---

### FASE 4 — Comportamientos del core loop (riesgo alto, ~2-3 días, cambios quirúrgicos en `main.py`)

Los `main.py` divergieron tanto que **no hay diff útil**: cada item se implementa leyendo el patrón upstream (`git show upstream/main:main.py`) y reescribiéndolo en el flujo local. **Un commit por item.** Antes de cada item, verificar si el local ya lo tiene (algunos pueden existir con otro nombre).

**4.1 Reconexión con backoff exponencial** — confirmado que falta: `main.py:2239` local usa 3 s fijos.
- Sustituir por backoff: 1 s → 2 → 4 → 8 → … cap 60 s, con reset a 1 s tras una conexión que dure >60 s estable.
- Log claro de intento N y espera.

**4.2 Instant Interrupt (ESC / botón)** — verificar primero: buscar en `ui/` si existe botón/atajo de interrupción. Si falta:
- Atajo global ESC en la ventana HUD + botón en el header.
- Acción: vaciar cola de audio de salida (`_play_audio`), enviar señal de interrupción a la sesión Live (el SDK de Live maneja `activity_start`/interrupciones por VAD; en manual, basta cortar reproducción local y descartar chunks pendientes).
- Publicar evento por `event_bus` para que la UI refleje el corte.

**4.3 / 4.4 — MOVIDOS A FASE 8**: el cooldown de visión, el echo guard y el acknowledgment inmediato son parte integral del rediseño de visión (Fase 8) — no implementarlos sueltos aquí.

**4.5 Session State Isolation** — auditar qué estado de instancia sobrevive entre reconexiones en la clase principal de `main.py` (flags de conversación, buffers de audio, colas). Al reconectar, resetear estado por-sesión y conservar solo lo que debe persistir (memoria, cooldowns de monitor, config). Upstream lo anuncia como fix de bugs fantasma tras reconexión.

**4.6 Zero Terminal Windows** — auditoría mecánica: `grep -n "Popen\|subprocess.run\|check_output" main.py actions/*.py` y asegurar `CREATE_NO_WINDOW` (o `startupinfo`) en todos los lanzamientos en Windows. Crear helper `actions/perf_helpers.py` o similar si no existe ya uno (¡revisar `actions/perf_helpers.py`, existe!).

**4.7 Language-Aware Address** — revisar `core/prompt.txt` local: si ya fija español consistente, marcar como hecho. Si no, añadir la regla del upstream (nunca mezclar idiomas al dirigirse al usuario).

**Criterios de aceptación (por item)**: 4.1 — matar la red y ver la progresión 1/2/4/8 s en logs y recuperación limpia. 4.2 — hablar, pulsar ESC a mitad de respuesta, el audio muere <200 ms. 4.5 — reconectar 3 veces y verificar que no quedan estados colgados (header/THINKING correcto — zona de bugs recientes).

**Riesgo principal**: `main.py` es el corazón (151 KB) y hay fixes frescos de estado. Cambios mínimos, un commit por item, smoke test manual tras cada uno (`py main.py`).

---

### ~~FASE 5 — Paquete `core/`: modo offline/local~~ — DESCARTADA

Descartada por decisión del propietario (2026-07-11): no quiere modo offline (LLM local/STT/TTS). **No implementar.** Fuente de referencia si se reconsiderara: `upstream/main:core/{llm_client.py, stt.py, tts.py, installer.py}`.

---

### FASE 6 — App móvil de control remoto con Expo + React Native (FUTURA, tras F4)

**Decisión del propietario (2026-07-11)**: NO portar el dashboard HTML del upstream. En su lugar, construir una app móvil propia con **Expo + React Native**. El `dashboard/server.py` del upstream queda solo como **referencia de diseño** para la capa de API (login por clave de sesión, cifrado AES-256-CBC a nivel de aplicación, WebSocket de estado en vivo).

Se divide en dos entregables independientes:

**6A — Backend API en el asistente (Python)**
1. Nuevo módulo `remote/server.py`: FastAPI + `uvicorn[standard]` en thread daemon, arrancado desde `main.py` solo si `remote_api_enabled` (default false) en settings. Puerto configurable (default 8000).
2. Endpoints mínimos:
   - `POST /auth` — login con clave de sesión (mostrada en el HUD al arrancar), devuelve token.
   - `WS /ws` — estado en vivo: suscribirse a `actions/event_bus` y reenviar eventos (estado del header, log, canción actual, descargas, mensajes WhatsApp entrantes…).
   - `POST /command` — texto libre hacia el asistente → `_send_text_command` de main.py (main.py:1143).
   - `GET /state` — snapshot inicial (modo activo, reproducción, cola de descargas).
3. Seguridad: escuchar solo en LAN; token por sesión; opcionalmente AES a nivel de aplicación como upstream (leer su `server.py` para el esquema de derivación de clave). No exponer a internet.
4. Diseñar el contrato de la API en `docs/api-remote.md` (JSON schemas de eventos y comandos) — este documento es la interfaz con 6B.
5. Tests: login rechaza clave errónea; `/command` inyecta en la sesión; eventos del bus llegan por WS.

**6B — App Expo + React Native (repo/carpeta aparte, ej. `mobile/`)**
1. `npx create-expo-app` con TypeScript; navegación con expo-router.
2. Pantallas mínimas v1: conexión/login (IP + clave), consola de chat (enviar comandos, ver respuestas/log), estado en vivo (modo, canción, descargas), acciones rápidas (pausar música, interrupt).
3. Cliente WS con reconexión y el mismo token de 6A; tipos TS generados del contrato `docs/api-remote.md`.
4. v2 (opcional): notificaciones push locales para alertas/mensajes WhatsApp, control de reproducción tipo media-controls.
5. Distribución: Expo Go para desarrollo; EAS build solo si se quiere APK instalable.

**Prerequisito**: F4 terminada (core loop estable, interrupt disponible como acción remota).
**Riesgos**: superficie de red nueva (mitigada: LAN + token + default off); acoplamiento con event_bus — definir bien el contrato antes de empezar 6B.

---

### FASE 8 — Visual Awareness en la sesión principal (✅ IMPLEMENTADA v1 el 2026-07-11; pendiente vista de cámara en HUD)

> **Estado**: núcleo implementado en `main.py` (flags de estado, dispatch con cooldown 4 s, inyección base64 en `_receive_audio`, reset por reconexión), `core/prompt.txt` (nuevo contrato) y `actions/capabilities.py`. La captura reutiliza `_capture_screen`/`_capture_camera` de `actions/screen_processor.py`; `_VisionSession` queda como legado (el agente `agent/executor.py` aún la usa). **Pendiente**: widget de cámara en vivo en `ui/` (`start_camera_stream`/`stop_camera_stream` — main.py ya los invoca guardados con `hasattr`) y tool `close_camera`. Tests: 223 pasan.

**Por qué importa**: es la feature que hace posible el demo de Instagram del upstream (la IA ve la pantalla y sube un vídeo en segundos). La clave NO es la captura — es **dónde vive la imagen**:

- **Local hoy** (`actions/screen_processor.py:208`): `screen_process` arranca una **sesión Gemini Live separada** (`_VisionSession` con sus propios `_session_loop/_send_loop/_recv_loop/_play_loop`) que "habla directamente" mientras la sesión principal calla (main.py:681, 1441). Consecuencia: **el modelo principal nunca ve la imagen** → no puede encadenar visión con acciones ("mira la pantalla y haz click en X" es imposible).
- **Upstream**: captura en main.py e **inyecta la imagen en la sesión principal**. El modelo ve la pantalla/cámara con todo su contexto y sus ~80 tools disponibles → puede mirar y actuar en el mismo turno (`browser_control` + `computer_control`, que el local YA tiene, son las que ejecutan el "subir a Instagram").

**Fuente exacta del patrón** (leer con `git show upstream/main:main.py`, zona líneas ~530-540, ~700-740, ~915-930, ~1220-1226):

1. **Estado en la clase principal** (upstream main.py:530-534):
   ```python
   self._pending_vision       = None    # (img_bytes, mime_type, question, angle)
   self._vision_cam_active    = False   # cámara abierta → auto-cerrar tras la respuesta
   self._vision_close_pending = False   # tras inyectar; el próximo turn_complete cierra cámara
   self._vision_last_time     = 0.0     # cooldown guard
   self._vision_busy          = False   # ciclo captura/inyección en vuelo
   ```
2. **Dispatch de `screen_process`** (upstream ~702-726): cooldown 4 s (`_vision_busy` o `now - _vision_last_time < 4` → responder "still processing, do not call again"); si pasa: capturar con `_capture_screen()`/`_capture_camera()` (en executor), guardar en `_pending_vision`, y devolver como tool-result el mensaje `[VISION_ACTIVE] … Immediately say ONE natural sentence in the user's language … Do NOT describe or guess content — the actual image arrives in the NEXT message.` → esto ES el "Immediate Vision Acknowledgment" (ex-4.4).
3. **Inyección** (upstream ~918-925): tras enviar el tool response, si `self._pending_vision and self.session` → base64 de la imagen → `await self.session.send_client_content(turns={"parts": [imagen inline_data + texto de la pregunta]}, turn_complete=True)`.
4. **Cámara en vivo**: al capturar con angle=camera, `self.ui.start_camera_stream()` muestra vista en vivo en el HUD; tool `close_camera` → `self.ui.stop_camera_stream()`; auto-cierre vía `_vision_close_pending` en el siguiente `turn_complete`.
5. **Reset en reconexión** (upstream ~1222-1226): TODOS los flags de visión a estado inicial (parte de F4.5 session isolation).

**Pasos para el implementador**:
1. Conservar de `actions/screen_processor.py` local los helpers de captura (`_capture_screen`, `_capture_camera`, `_compress`, detección de cámara) — son equivalentes a los del upstream y ya funcionan.
2. **Eliminar el uso de `_VisionSession`** como vía por defecto (mantener la clase de momento, marcada deprecated, por si se quiere fallback).
3. Añadir los 5 flags de estado + dispatch nuevo de `screen_process` en `_execute_tool()` de main.py siguiendo el patrón de arriba (adaptar el mensaje `[VISION_ACTIVE]` al español).
4. Inyección post-tool-response en `_receive_audio` (main.py local ~2079-2155, después de `send_tool_response`).
5. UI: implementar `start_camera_stream`/`stop_camera_stream` en el HUD local (`ui/` — hay precedente de overlays en `ui/widgets/overlays.py`); registrar tool `close_camera` en `_build_config()` + dispatch + `CAPABILITY_SECTIONS`.
6. Actualizar la descripción de la tool `screen_process` en `_build_config()` copiando la semántica del upstream (main.py upstream ~201-218): "MUST be called when user asks what is on screen…", parámetro `angle` screen|camera.
7. Echo guard (ex-4.3): el cooldown de 4 s del paso 2 "covers echo window after speaking ends" — mantener el valor y comentar por qué.
8. Reset de flags en el bucle de reconexión (coordinar con F4.5).
9. Ajustar `core/prompt.txt` si hace falta para el nuevo contrato ("la imagen llega en el siguiente mensaje").

**Criterio de aceptación (el del demo)**: decir "mira mi pantalla y dime qué ves" → una frase inmediata + análisis correcto en la MISMA voz/sesión; luego "ahora abre el navegador y busca eso" → el modelo usa lo que vio. Cámara: "mírame" abre vista en vivo, se cierra sola tras responder o con "cierra la cámara".

**Riesgos**: tokens — cada imagen inyectada consume contexto de la sesión Live (por eso el cooldown y una sola imagen por llamada, nada de streaming continuo de frames). Zona `_receive_audio` es delicada (fixes THINKING recientes) — cambios mínimos y smoke test tras cada commit.

---

### FASE 9 — Morning Briefing al arrancar (riesgo bajo-medio, ~1 día, depende de F3)

**Qué hace en upstream**: una vez por proceso (flag `_briefing_sent`, upstream main.py:541, 1244-1248), tras la primera conexión estable lanza `tg.create_task(self._send_startup_briefing())`: saluda, dice la hora, clima y titulares de noticias reales — en dos fases concurrentes (fase 1 instantánea: saludo+hora+clima; fase 2 cuando lleguen las noticias, buscadas en paralelo Gemini+DDG).

**Pasos**:
1. Prerequisito: helper de briefing de F3 (`web_search.py` upgrade) implementado.
2. Extraer `_send_startup_briefing` del upstream (`git show upstream/main:main.py`, buscar el método) y adaptarlo al flujo local: usar `actions/weather_report.py` local (ya existe) + helper de noticias de F3.
3. Flag `_briefing_sent` fuera del bucle de reconexión (una vez por proceso, NO por reconexión).
4. Inyectar cada fase vía `send_client_content` con `turn_complete=True` (mismo patrón que `_send_text_command`, main.py:1143).
5. Config: `startup_briefing_enabled` (default false hasta probarlo) + toggle en `ui/panels/settings.py`; opcional `briefing_topics` (temas de noticias preferidos).
6. En español: el saludo y formato deben salir en el idioma del prompt local.
7. `CAPABILITY_SECTIONS`: mencionar el briefing.

**Criterio de aceptación**: arrancar la app → saludo con hora y clima en <5 s de la conexión; titulares llegan después sin bloquear; matar la red y reconectar → NO se repite el briefing.

**Riesgos**: briefing largo puede pisarse con lo primero que diga el usuario — mantenerlo corto y asegurarse de que una interrupción del usuario lo corta (interacción con F4.2).

---

### FASE 7 — Flecos menores (riesgo nulo, ~2 h)

0. **`actions/reminder.py`**: ya está importado en local (archivo casi idéntico, tool registrada en main.py:634/1392; el local incluso usa `paths.config_path`, mejor que upstream). Único delta del upstream: dict `_CNW` con `CREATE_NO_WINDOW` en sus subprocesos (`schtasks`) — portarlo (encaja con F4.6). Nada más que hacer.
1. **`core/prompt.txt`**: `git show upstream/main:core/prompt.txt` y comparar con el local. Fusionar solo reglas de ruteo/estilo que falten (p. ej. la regla language-aware de 4.7). El local manda.
2. **`config/__init__.py`**: diff (534 B vs 787 B); portar lo que falte si es útil.
3. **`readme.md`**: actualizar el README local mencionando las features importadas.
4. **`config/jarvis.ico`**: opcional, solo si se quiere el icono del upstream.
5. Actualizar `docs/` con lo implementado.

---

## 5. Matriz resumen para el gestor

| Fase | Feature | Valor | Riesgo | Esfuerzo | Estado |
|---|---|---|---|---|---|
| ~~F1~~ | ~~System monitor + alertas~~ | — | — | — | **Descartada** (no se quiere telemetría) |
| F2 | Proactive check-ins | Medio | Bajo | 0.5 d | **✅ Implementada** (2026-07-11) — toggle "Modo proactivo" en Inicio, default off |
| F3 | web_search modos + first-wins | Alto | Medio | 1 d | **✅ Implementada** (2026-07-11) — modos news/research/price/compare, news paralelo verificado en vivo; instalado `ddgs` |
| F4.1 | Backoff reconexión | Alto | Bajo | 2 h | **✅ Implementada** — 1→2→4… cap 60 s, reset tras 60 s estables |
| F4.2 | Interrupt ESC | Alto | Medio | 0.5 d | **✅ Implementada** — ESC en HUD → drena cola + descarta audio hasta fin de turno |
| F4.3-4.4 | ~~Vision guard + ack~~ | — | — | — | Absorbidos por F8 ✅ |
| F4.5 | Session isolation | Alto | Alto | 1 d | **✅ Implementada** — visión + interacción + recovery task reseteados por reconexión |
| F4.6 | Zero terminal windows | Bajo | Bajo | 2 h | **✅ Implementada** — shim global de `subprocess.Popen` con `CREATE_NO_WINDOW` (cubre los 171 call sites y el fleco de reminder) |
| F4.7 | Language-aware | Bajo | Nulo | 1 h | **✅ Implementada** — regla en prompt |
| ~~F5~~ | ~~Modo offline (LLM+STT+TTS local)~~ | — | — | — | **Descartada** (no se quiere offline) |
| F6 | App móvil Expo + React Native (API 6A + app 6B) | Estratégico | Medio-alto | 1-2 sem | **Futura** — core ya estable |
| F7 | Flecos | Bajo | Nulo | 2 h | **✅ Implementada** — config/__init__ robusto+autodetección OS, prompt fusionado (language detection, [BRIEFING], [PROACTIVE_CHECK], ruteo web_search) |
| **F8** | **Visión en sesión principal (ver + actuar)** | **Muy alto** | Alto | 2-3 d | **✅ Completa (2026-07-11)** — v1 (inyección en sesión Live) + v2 (vista de cámara en vivo + tool `close_camera`, bridge cross-thread por señales Qt) |
| F9 | Morning briefing (hora+noticias) | Medio | Bajo-medio | 1 d | **✅ Implementada** — dos fases, flag por proceso, toggle "Briefing matinal" en Inicio, default off |
| — | Smart Reminders | — | — | — | **Ya en local** (fleco `_CNW` cubierto por F4.6) |

**Estado global (2026-07-11)**: todas las fases no descartadas implementadas, incluida F8 v2 (cámara en vivo). Solo queda F6 (app móvil Expo/RN, futura). Suite: 239 tests en verde. Sin commitear — mezclado con WIP previo del propietario en el working tree.

### FASE 8 v2 — Vista de cámara en vivo (✅ implementada 2026-07-11)

Detalle de implementación:
- `ui/widgets/overlays.py` → clase `CameraLiveWidget`: QWidget flotante (220×165) sobre el `_center_stack`, hijo del HUD. `start()` abre `cv2.VideoCapture` (reusa `_cv2_backend`/`_get_camera_index` de `actions/screen_processor.py`), un `QTimer` a 90 ms sondea frames y los pinta en un `QLabel`. `stop()` para el timer y libera la cámara.
- `ui/__init__.py` (`MainWindow`): crea el widget, lo reposiciona en `resizeEvent` (se fusionó con el `resizeEvent` preexistente del overlay de setup — **había dos definiciones duplicadas en la clase, la segunda pisaba a la primera**, se corrigió).
- **Bug real encontrado y corregido durante la implementación**: `jarvis.run()` (el bucle asyncio de Gemini Live) corre en un **hilo de fondo** (`threading.Thread(target=runner)`), mientras Qt vive en el hilo principal (`ui.root.mainloop()`). Llamar directamente a métodos de `QWidget`/`QTimer` desde ese hilo es cross-thread y no es seguro. Se resolvió con señales Qt (`_camera_start_sig`/`_camera_stop_sig` en `MainWindow`, conectadas con auto-connection → Qt las despacha en cola al hilo del receptor). Los proxies `JarvisUI.start_camera_stream()`/`stop_camera_stream()` emiten esas señales en vez de tocar el widget directamente.
- Tool `close_camera` registrada en `main.py` (declaración + dispatch), resetea los flags de visión.
- **Verificado end-to-end**: cámara real detectada y abierta, pixmap 640×480 recibido tras bombear el loop de eventos Qt, y el bridge cross-thread confirmado (`True` tras `start_camera_stream()` desde un hilo worker, `False` tras `stop_camera_stream()`).

## 6. Reglas de entrega (todas las fases)

- Rama `feature/upstream-<fase>`, commits atómicos en inglés imperativo.
- Ejecutar `py -m pytest tests/ -x -q` antes de cada commit (los tests existentes deben seguir verdes).
- Smoke manual: `py main.py` arranca, conecta Gemini, header correcto, WhatsApp bridge no afectado.
- No tocar archivos del trabajo en curso sin commitear (CardTrader/Games/downloads) salvo que la fase lo exija.
- Documentar cada tool nueva en `actions/capabilities.py` (español) y en la declaración de `main.py` (descripción clara para el modelo).
