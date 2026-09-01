# NOTA: puerto de "Emuladores" a la web UI — estado y lo investigado

Contexto: la nueva interfaz web (pywebview + Svelte, `actions/webui_server.py`
+ `webui/`) ya tiene 4 de los 5 modos que faltaban por terminar: **Películas,
TV, Anime y YouTube** (implementados y probados contra datos reales en esta
misma sesión). Queda **Emuladores**, el más grande de los cinco. Se dejó para
una sesión aparte a propósito — lee esto entero antes de tocar nada, ya
investigamos bastante y no hace falta repetirlo.

## La pregunta que originó esta nota

El usuario preguntó: *"ya que hemos cambiado el motor gráfico, ¿es posible
usar emuladores ya existentes en esa pantalla?"* — es decir, aprovechar
emuladores standalone (mGBA, VBA-M) en vez de construir streaming de
fotogramas desde cero.

**Respuesta investigada y confirmada:**
[`actions/emulator_runtime.py:510`](actions/emulator_runtime.py) `launch(rom_path, console_id)`
ya existe y ya funciona: localiza mGBA/VisualBoyAdvance-M instalados (según
`EMULATORS` dict, línea 333 — **hoy solo cubre GBA**, ningún otro consola) y
hace `subprocess.Popen(...)`, completamente **detached** (proceso y ventana
propios, independientes de Jarvis).

Esto es **fácil de conectar** desde la web (una ruta Flask que llama a
`launch()` — trivial, cero riesgo, reutiliza código ya probado). PERO:
**no se embebe "en esa pantalla"** — abre su propia ventana nativa de
escritorio, exactamente igual que si se lanzara desde la Qt legacy. Cambiar
el motor gráfico de la UI (Qt → WebKitGTK/navegador) **no cambia esto en
absoluto**: un navegador no puede embeber una ventana nativa arbitraria
dentro del DOM de la forma en que Qt sí puede embeber widgets nativos (así
es como `EmbeddedVideoPlayer` de YouTube mete mpv dentro de un widget Qt vía
`--wid`/reparenting de ventana — esa técnica no tiene equivalente en web).
El "cambio de motor gráfico" no abre ninguna puerta nueva aquí; la opción de
lanzar el emulador standalone ya estaba disponible desde antes de este
rediseño, solo que no estaba expuesta en ninguna ruta web.

## Dos caminos posibles, muy distintos en coste

### Camino A — Lanzador (barato, ventana separada)
Botón "Jugar" en la web → ruta Flask que llama a `emulator_runtime.launch()`
→ se abre mGBA/VBA-M en su propia ventana. Reutiliza 100% código existente.
**Limitación real: solo GBA tiene emulador standalone registrado hoy**
(`EMULATORS` en emulator_runtime.py). Para cualquier otra consola habría que
añadir su `EmulatorSpec` primero (mismo patrón, coste bajo por consola) o
caería al camino B.

Falta también: un picker de ROM (no existe endpoint de biblioteca de ROMs
hoy — `emulator_runtime.py` solo instala/localiza emuladores, no cataloga
ROMs). Lo más simple: reusar el flujo de subida de archivos que ya existe
para `FileDropZone`/`/api/files/upload`.

### Camino B — Embebido de verdad en el panel (caro, la tarea original)
Esto es lo que se estaba planeando antes de que el usuario preguntara por el
camino A. Requiere:

1. **Desacoplar el ciclo de vida de `LibretroCore` de `ui/widgets/retro.py`.**
   Confirmado en esta sesión: hoy `LibretroCore` (en `actions/libretro.py`,
   éste sí ya está libre de Qt) solo se instancia **dentro** del widget Qt
   `RetroScreen` (`ui/widgets/retro.py`) — no existe ninguna forma de
   arrancar un core sin la interfaz Qt. Hace falta un gestor headless nuevo
   (hilo de fondo que llame al método de avance de frame del core al ritmo
   correcto) totalmente independiente de `ui.widgets.retro`, con cuidado de
   no romper el emulador de la app Qt al tocar ese archivo compartido.
2. **Streaming de fotogramas en tiempo real** — hoy solo existe una captura
   PNG bajo demanda (`/api/debug/emulator_frame` en `lan_dashboard.py:1494`,
   literalmente comentada como "Diagnostic-only"). Habría que construir algo
   tipo MJPEG (multipart/x-mixed-replace sobre un `<img>`, sin necesitar
   WebSocket ni JS del lado cliente) leyendo `core.current_frame()` a ritmo
   controlado.
3. **Input de mando**: la parte fácil — `/api/gamepad/input` en
   `lan_dashboard.py:1555` ya llama a `core.set_button()`/`set_axis()`
   exactamente como hace falta, solo hay que repuntarlo al nuevo gestor
   headless en vez de a `_retro_screen()` (que sigue dependiendo de Qt).
4. Mando táctil en pantalla para navegador (no existe, hay que construirlo).
5. Picker de ROM (mismo problema que en el camino A).

**Tamaño real: grande**, y con el riesgo añadido de tocar código Qt
compartido y en uso. No es cosa de una tarde.

## Actualización: bug de fondo negro en PS2 (Madagascar, HW renderer) — pista concreta encontrada

Resumen de la sesión anterior (pegado por el usuario): en PCSX2 vía libretro
(core LRPS2), jugando DreamWorks Madagascar, la pantalla de selección de
nivel muestra el fondo 3D completamente en negro con el renderer OpenGL
(HW) — el HUD/texto 2D se ve bien. Con `pcsx2_renderer: "Software"` se
arregla pero mucho más lento. Se descartaron ya: extensiones GL, shaders
rotos, fixes de `GameIndex.yaml` para ese juego, `texture_inside_rt` +
`framebuffer_conversion` (sin efecto). Se dejó como hipótesis de trabajo el
chip IPU (vídeo MPEG) por el patrón "funciona en software, no en hardware".

**Investigado en esta sesión (búsqueda web + código fuente del core):**

- La tabla completa de opciones del core LRPS2 está en
  [`libretro/ps2:libretro/libretro_core_options.h`](https://github.com/libretro/ps2/blob/libretroization/libretro/libretro_core_options.h)
  (rama `libretroization`). **No existe** en este fork el toggle
  "Use Software Renderer For FMVs" que sí tiene el PCSX2 mainline standalone
  para exactamente esta clase de bug (vídeo/FMV en negro con HW, bien con
  SW) — así que la hipótesis del IPU no tiene un interruptor directo
  disponible en este core tal cual está hoy.
- **Pista más prometedora, sin probar todavía**: la opción
  `pcsx2_hw_download_mode` ("Hardware Download Mode", por defecto
  `"Accurate"`) controla exactamente las lecturas GPU→CPU de contenido ya
  renderizado — y su propia descripción en el código nombra literalmente
  **"stage-select menus"** como uno de los escenarios que ejercitan esta
  ruta. Es la coincidencia más directa con el síntoma descrito (pantalla de
  selección de nivel, fondo que viene de un render previo).
- Además, PCSX2 mainline (standalone) añadió en **v2.8.0 (2026)** un modo
  nuevo, `"Accurate (Force Full)"`, precisamente porque algunos juegos
  hacen múltiples lecturas parciales GPU→CPU por frame y eso causaba
  glitches — la propia nota de la release lo conecta con un bug de fondo
  negro en menús ("Black" game). **El fork libretro LRPS2 solo tiene
  4 valores** (`Accurate` / `Disable Readbacks` / `Unsynchronized` /
  `Disabled`) — no tiene todavía ese `Force Full` que mainline sí tiene.
  Esto sugiere que el bug de Madagascar puede ser una versión del mismo
  problema de "lecturas parciales" que mainline ya solucionó, pero que
  LRPS2 (más antiguo / no sincronizado con mainline en este punto) aún no
  ha portado.

**Próximo paso concreto para la sesión que retome esto**: probar
`pcsx2_hw_download_mode` con los 3 valores no-default (`Disable
Readbacks`, `Unsynchronized`, `Disabled`) contra la misma pantalla de
Madagascar — es la opción con la coincidencia más directa con el síntoma y
todavía no se ha probado. Si ninguno arregla el fondo, el problema es
probablemente el mismo bug de "readback parcial" que mainline arregló en
2.8.0 con `Force Full`, y la solución real sería actualizar/portar esa
mejora al fork LRPS2 (o cambiar de core), no una opción que ya exista hoy
en LRPS2.

## Actualización: el usuario ya respondió — quiere el Camino B (embebido)

Tras leer la comparación de arriba, el usuario contestó explícitamente:
*"yo lo quiero embebido, y me gustaría llegar a emular ps2 y 3ds"*. Así que
el Camino A (lanzador en ventana separada) NO es el objetivo, como mucho un
peldaño intermedio si se quiere algo funcionando antes. El objetivo real es
Camino B, y además con el listón puesto en PS2/3DS, no solo consolas
ligeras tipo GBA/SNES. Esto cambia bastante el análisis de coste — más
detalles abajo, investigados en esta misma sesión.

### PS2: ya hay trabajo empezado en este repo — y confirma el problema del contexto GL

`actions/emulator_runtime.py:151` ya tiene un `CoreSpec` para PS2
(`pcsx2_libretro`, marcado `"sin verificar"` — WIP activo, no de cero). El
comentario en la línea 145 es oro puro para dimensionar esto:

> PS2 scenes overwhelm the CPU rasterizer even at native resolution
> (Madagascar gameplay measured 11–14 fps on an 8-core Ryzen). The same core
> settles around 3.3 ms/frame with OpenGL after its initial shader
> compilation, so hardware rendering is the usable default. **The frontend
> owns the offscreen GL context and reads the native-resolution frame back
> for the embedded QWidget.**

Confirmado explícitamente: **PS2 necesita el renderer OpenGL (hw_render)
para ser jugable** — el software renderer (11-14fps) no sirve, solo está
forzado ahora mismo (`pcsx2_renderer: "Software"`) para diagnosticar un bug
de fondo negro no relacionado, con nota de revertir a OpenGL. Es decir: el
camino "solo cores ligeros software-rendered" que asumí en el análisis
anterior **no aplica a PS2** — hace falta sí o sí la ruta de hardware
rendering.

Y esa ruta (confirmado leyendo `actions/libretro.py:61-1090`) usa
`QOpenGLContext`/`QOffscreenSurface`/`QOpenGLFramebufferObject` de **PyQt6
directamente** (`_init_hw_render`, línea 1055) — no es solo
`ui/widgets/retro.py` el que está acoplado a Qt, el propio núcleo de
renderizado hardware de `LibretroCore` también lo está.

**Pero hay un detalle importante que lo hace mucho más viable de lo que
parece a primera vista**: `QOffscreenSurface` está diseñado precisamente
para renderizar sin ninguna ventana visible — solo necesita que exista un
`QGuiApplication`/`QApplication` corriendo su bucle de eventos en el
proceso, no una ventana. Es decir, el camino realista NO es "arrancar
sacar Qt del todo" (eso sí sería reescribir el renderer GL entero, mucho
más caro) sino: **en modo web, arrancar un `QApplication` headless de fondo
(sin ninguna ventana visible) solo para que `LibretroCore` pueda seguir
usando su `QOffscreenSurface` tal cual está**, y construir el gestor
headless nuevo (streaming + ciclo de vida) alrededor de eso, en vez de
alrededor de `ui/widgets/retro.py`. Mucho menos invasivo que sustituir la
librería de GL offscreen. Esto no se ha probado todavía — es la hipótesis
de trabajo, verificar primero con un prototipo pequeño antes de comprometerse.

**Implicación de rendimiento para el streaming**: si PS2 solo es jugable a
~3.3ms/frame (con GPU) y el software renderer ya iba a 11-14fps en un
Ryzen de 8 núcleos, el margen para además codificar cada frame a JPEG y
mandarlo por HTTP (MJPEG) es ajustado pero parece viable ya que el cuello
de botella real es el renderer, no la codificación — de todas formas medirlo
pronto con un prototipo antes de construir todo lo demás alrededor, no dar
por hecho que "OpenGL + MJPEG" rinde bien sin probarlo primero.

### 3DS: investigado (búsqueda web) — SÍ hay un core viable hoy

No hay ningún `CoreSpec` de 3DS en `emulator_runtime.py` todavía, pero la
investigación (búsqueda web, septiembre 2026) despeja la duda principal:

- Tras el cierre de Citra, el fork **Azahar** (fusión de Lime3DS y el fork
  de PabloMK7) es el heredero activo. Publicó su **core libretro oficial el
  3 de marzo de 2026** (v2105.0 Alpha 4) — se mantiene activamente, con
  actualizaciones recientes (caché de shaders en disco, modo 3D
  estereoscópico). Repo: [azahar-emu/azahar](https://github.com/azahar-emu/azahar).
- Está en el **buildbot oficial de libretro**
  (`buildbot.libretro.com/nightly/linux/x86_64/`), el mismo mecanismo que ya
  usa `_buildbot_platform()`/`install_core()` en `emulator_runtime.py` para
  el resto de cores — encaja con la infraestructura existente sin necesitar
  nada nuevo para instalarlo. También hay paquete AUR
  (`libretro-azahar`), relevante porque el usuario está en CachyOS (base
  Arch).
- **Requisito de renderer: OpenGL 4.3 o Vulkan 1.1.** Esto es la pieza
  importante para esta máquina en concreto: hay un bug abierto confirmado
  de **crash inmediato con el renderer Vulkan en GPUs AMD (driver RADV) en
  Arch Linux** — exactamente la combinación de esta máquina (iGPU AMD
  HawkPoint + CachyOS). **Conclusión: usar el renderer OpenGL, no Vulkan**,
  en este equipo. Esto además encaja mejor con la infraestructura GL que ya
  existe en `actions/libretro.py` (pensada para OpenGL vía
  `QOpenGLContext`, igual que PS2) — no hace falta construir un camino
  Vulkan aparte.

**Conclusión sobre 3DS: viable, no es un callejón sin salida.** Sigue
siendo trabajo nuevo (no hay `CoreSpec` todavía, hay que probarlo en esta
máquina concreta primero por el tema RADV/Vulkan), pero la incertidumbre
de "puede que no haya ningún core mantenido" queda resuelta: sí lo hay, y
es reciente (2026), no algo abandonado.

## Recomendación para la próxima sesión

1. Confirmar con un prototipo pequeño (sin todavía construir streaming ni
   UI) que un `QApplication` headless + `QOffscreenSurface` puede vivir
   dentro del proceso de `webui_host.py` sin pywebview/GTK y sin romper
   nada, y que `LibretroCore` con un core ligero (GBA) funciona así fuera
   de `ui/widgets/retro.py`. Esto valida o descarta la hipótesis de arriba
   antes de comprometerse a construir todo el gestor headless sobre ella.
2. Con eso validado, construir el gestor headless + streaming MJMPEG +
   input, empezando por GBA/SNES (software, ya probado) para tener algo
   embebido funcionando end-to-end, y DESPUÉS subir a PS2 (hardware
   rendering) reusando el mismo pipeline.
3. 3DS: viabilidad confirmada (ver arriba) — core `azahar_libretro`,
   instalable vía el mismo buildbot que ya usa `emulator_runtime.py`.
   Añadir su `CoreSpec` con `pcsx2`-style options forzando **OpenGL, no
   Vulkan** (crash conocido con Vulkan+RADV en Arch, la combinación exacta
   de esta máquina). Mismo pipeline que PS2 una vez ese esté funcionando.
4. El Camino A (lanzador standalone) sigue disponible como red de seguridad
   si el prototipo del punto 1 falla o resulta demasiado frágil — no es el
   objetivo pero es la opción de respaldo barata.

## Estado de los otros 4 modos (para referencia, ya cerrados)

Películas/TV comparten `webui/src/lib/StreamingBrowser.svelte` (backend:
`actions/movie_search.py` + `actions/torrentio.py` + `actions/vlc_player.py`,
rutas `/api/movies/*` en `webui_server.py`). Anime reutiliza el mismo
componente con `kind="anime"` (rutas `/api/anime/*`, mismo pipeline de
reproducción que movies). YouTube es `webui/src/panels/YouTube.svelte`
(rutas `/api/youtube/search|recommendations`) — la reproducción es el
iframe oficial de YouTube, no un resolutor de URL directa (se probó en vivo
que YouTube ya no sirve formatos pre-mezclados para la inmensa mayoría de
vídeos, así que un `<video src>` no es viable). Todos con tests en
`tests/test_webui_server.py` (clases `MoviesRouteTests` y las de anime/
youtube al final del archivo) y verificados contra datos reales de TMDB/
Torrentio/YouTube en esta sesión.
