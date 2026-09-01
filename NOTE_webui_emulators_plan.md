# PLAN: "Emuladores" embebido en la web UI — plan de trabajo por fases

Compañero de [`NOTE_webui_emulators.md`](NOTE_webui_emulators.md), que tiene la
**investigación** (por qué el Camino B, viabilidad de PS2/3DS, bug de fondo
negro). Este archivo tiene el **plan de ejecución**. Lee aquel primero.

Objetivo final: modo Emuladores embebido en el panel web (no ventana externa),
llegando hasta PS2 y 3DS. Camino B de la nota, confirmado por el usuario.

---

## Hallazgos de la sesión de planificación (corrigen/afinan la nota)

Sondas ejecutadas con `.venv/bin/python` en esta máquina, antes de escribir
este plan. Lo que sigue está **medido, no supuesto**:

### 1. La hipótesis del QApplication headless se sostiene (a nivel Qt)

`QOffscreenSurface` + `QOpenGLContext` + `QOpenGLFramebufferObject` +
`getProcAddress("glReadPixels"/"glBindFramebuffer")` — todo lo que usa
`_init_hw_render` (`actions/libretro.py:1055`) — funciona sin ninguna ventana.

Y lo que la nota no llegó a contemplar: **funciona también creando el
`QApplication` en un hilo secundario**. Esto era imprescindible y no obvio:
`webui_host.py:88` bloquea el hilo principal con `webview.start()` (GTK), así
que el Qt headless *no puede* tener el hilo principal. Qt avisa por consola
cuando lo creas fuera del hilo principal, pero la ruta offscreen/GL funciona.

Sigue **sin validar** lo que de verdad importa: que `LibretroCore` arranque un
core real así (Fase 1a/1b) y que Qt conviva con GTK en el mismo proceso
(Fase 1c).

### 2. Esta máquina tiene DOS GPUs — la nota solo contaba la AMD

Portátil híbrido: **AMD Radeon 780M** (HawkPoint, RADV/radeonsi) +
**NVIDIA RTX 5060 Laptop**. A dónde va el contexto GL depende del plugin QPA:

| Configuración | GPU efectiva | GL obtenido | Estado |
|---|---|---|---|
| por defecto (plugin `wayland`) | AMD Radeon 780M | 4.6 core | ✅ |
| `QT_QPA_PLATFORM=offscreen` | **NVIDIA RTX 5060** | 4.3 core | ✅ |
| `__NV_PRIME_RENDER_OFFLOAD=1` | — | — | ❌ **core dump** |

El crash de PRIME es `MESA-LOADER: failed to open /usr/lib/gbm/_gbm.so`,
porque **`GBM_BACKEND` está exportado vacío a nivel de sesión** (verificado:
`GBM_BACKEND=[]`, y sí existen `dri_gbm.so`/`nvidia-drm_gbm.so`). Es
exactamente la misma avería que ya documenta el comentario de
`actions/webui_host.py:50-62` para WebKit, que por eso fuerza
`WEBKIT_DISABLE_DMABUF_RENDERER=1`.

**Reglas que salen de aquí, y valen para todo el plan:**
- **Nunca** usar las variables PRIME (`__NV_PRIME_RENDER_OFFLOAD`,
  `__GLX_VENDOR_LIBRARY_NAME`) para llegar a la dGPU: revientan por el GBM roto.
- La vía limpia a la RTX 5060 es **`QT_QPA_PLATFORM=offscreen`**.
- **GL 4.3 está disponible** → el requisito de renderer de azahar/3DS se cumple
  en este equipo. La conclusión "OpenGL y no Vulkan" de la nota se mantiene,
  pero con más margen del que se pensaba.

### 3. El picker de ROMs es mucho más barato de lo que dice la nota

La nota dice que no hay catálogo de ROMs. Cierto **solo** de
`emulator_runtime.py`. En realidad ya existen, y libres de Qt:
- `actions/rom_catalog.py` — índice, `search()`, `popular()`, `download()`,
  `artwork_urls()`, `local_path()`, `roms_dir()`.
- `actions/game_library.py` — `scan_roms()`, `list_entries()`, `add_local_rom()`,
  `mark_played()`, registro persistente.
- `~/.jarvis/roms/<consola>/` ya existe y está poblado.

La Fase 4 se reduce a exponer esto por HTTP, no a construirlo.

### 4. Material de pruebas ya en disco

- **GBA**: `Metroid - Zero Mission (USA).gba`, `Pokemon - Edicion Esmeralda (Spain).gba`
- **PS2**: `SLUS_210.15.Madagascar.iso` (el del bug), `LEGO Star Wars II`, `MGS3Subsistance1.iso`
- **BIOS PS2**: `SCPH-70012_BIOS_V12_USA_200.bin` ✅
- **3DS**: `~/Descargas/PKM US Random By PokeAndroid.3ds`
- **Cores instalados**: `gba`, `snes`, `ps2` ✅

### 5. MJPEG encaja sin fricción

`webui_server.py` ya corre Flask con `threaded=True` y ya sirve un generador en
streaming para SSE (`/api/events`, línea 242). Para codificar hay **cv2 5.0** y
**Pillow 12.3** (no hay turbojpeg/simplejpeg).

---

## Invariantes que aplican a TODAS las fases

Romper cualquiera de estos es la forma más rápida de romper algo que hoy
funciona:

1. **Un solo core vivo por proceso.** `libretro.load()` es un singleton global
   (`actions/libretro.py:1324`) y el propio docstring avisa de que los cores
   guardan estado de proceso y ctypes reutiliza el handle del .so. El gestor
   headless y el emulador Qt legacy **no pueden coexistir**: hay que impedirlo
   explícitamente, no confiar en que el usuario no lo intente.
2. **El contexto GL debe estar current en el mismo hilo que llama a
   `retro_run()`.** `run_frame()` (`libretro.py:1153`) hace `makeCurrent` cada
   frame justo por esto. Consecuencia: el hilo de pacing y el de render son el
   mismo hilo, siempre.
2b. **El `QApplication` se crea en el hilo principal, la emulación en el
   worker.** Verificado en la Fase 1: al revés emula bien pero el proceso muere
   con SIGSEGV al cerrar. Ver "Hallazgo crítico" en los resultados de la Fase 1.
3. **No romper el emulador Qt.** `ui/widgets/retro.py` y `ui/panels/emulators.py`
   están en uso. Se tocan lo mínimo, y cada fase que los toque verifica que
   `--legacy` sigue arrancando y jugando.
4. **`unload()` antes de `retro_unload_game()`.** Ya está resuelto en
   `LibretroCore.unload()` (`libretro.py:1168`) con un comentario que explica el
   deadlock de LRPS2. No reordenar eso.
5. **Sin regresiones en los 4 modos ya cerrados** (Películas, TV, Anime,
   YouTube). `tests/test_webui_server.py` los cubre.

---

## Fase 0 — Higiene y punto de retorno

**Por qué primero:** el árbol está sucio. `actions/libretro.py`,
`actions/emulator_runtime.py`, `actions/lan_dashboard.py`, `main.py` y varios
más tienen cambios sin commitear, y `mobile-app/node_modules` mete muchísimo
ruido en `git status`. Sin un punto de retorno limpio no se puede distinguir
"lo que rompí yo" de "lo que ya estaba a medias".

**Pasos**
1. `git status` completo y revisar con el usuario qué es WIP intencionado.
2. Crear rama `feat/webui-emulators-embedded` desde HEAD (el árbol sucio viaja
   con la rama; no se pierde nada).
3. Commit-snapshot del WIP actual, o `git stash` si el usuario prefiere — **su
   decisión, no la mía**. Nunca descartar esos cambios.
4. Revisar por qué `mobile-app/node_modules` aparece como modificado (parece
   `.gitignore` incompleto o cambios de modo/symlink). Anotarlo; arreglarlo solo
   si es trivial y el usuario quiere.

**Criterio de salida:** rama creada, `git diff` legible, capacidad de volver al
estado actual con un solo comando.

**Riesgo:** bajo. **Rollback:** n/a.

---

## Fase 1 — Prototipo de validación (lo que valida o tumba el plan entero)

**Cero cambios en el repo.** Todo son scripts desechables en el scratchpad. Si
esta fase falla, el plan cambia de forma, no se parchea.

### 1a — GBA, ruta software, fuera de `ui/widgets/retro.py`

**Script:** `proto_gba_headless.py`
- `QApplication` en hilo secundario (idempotente, guardando la referencia para
  que no lo recoja el GC).
- `libretro.load(er.find_core("gba"), er.system_dir(), er.saves_dir(), options=spec.options)`
- `core.load_game(".../Metroid - Zero Mission (USA).gba")`
- Bucle de ~900 frames llamando `run_frame()`, con pacing por `_advance_frame_debt`.
- Volcar el frame ~600 a PNG desde `current_frame()`, replicando la conversión
  de formato de `retro.py:398` (`PIXEL_XRGB8888` → RGB32, `PIXEL_0RGB1555` →
  RGB555, resto → RGB565) pero con numpy/Pillow, **sin QImage**, para probar de
  paso que el pipeline de frames no necesita Qt salvo para el GL.
- Medir fps sostenido y ms/frame.
- `libretro.unload()` limpio al final.

**Criterio de salida:** ≥60 fps sostenidos, el PNG muestra el juego de verdad
(no negro, no basura, colores correctos), y `unload()` no cuelga ni casca.

### 1b — PS2, ruta hardware (la que ejercita `_init_hw_render`)

**Script:** `proto_ps2_headless.py`
- Igual que 1a pero `console_id="ps2"`, **con `pcsx2_renderer: "OpenGL"`**
  (el `"Software"` de `emulator_runtime.py:151` es un diagnóstico temporal).
- Bajo `QT_QPA_PLATFORM=offscreen` para caer en la RTX 5060.
- Comprobar BIOS con `actions/bios.has_ps2_bios()` antes de arrancar.
- Loguear `GL_VENDOR`/`GL_RENDERER` reales del contexto que crea el core, para
  confirmar en qué GPU acaba de verdad.
- Medir ms/frame y contrastar con los **3.3 ms** que afirma el comentario de
  `emulator_runtime.py:145`.

**Criterio de salida:** frame no-negro de Madagascar, `hw_render_active=True`,
ms/frame medido y anotado. Aquí también se confirma si la RTX 5060 se usa o si
el core acaba en la iGPU pese a `offscreen`.

**Riesgo real:** que el core PS2 exija un contexto GL con requisitos que
`QOffscreenSurface` no dé bajo `offscreen`, o que casque en `context_reset`.
Mitigación: probar también con el plugin por defecto (iGPU AMD) antes de
concluir nada.

### 1c — Convivencia Qt + GTK/WebKit en un mismo proceso ← **el riesgo de verdad**

Las sondas hechas hasta ahora eran Qt puro, en un proceso limpio. Esta es la
prueba que puede tumbar el diseño.

**Script:** `proto_coexist.py`
- Arrancar pywebview/GTK como lo hace `webui_host.py` (incluido el
  `WEBKIT_DISABLE_DMABUF_RENDERER=1` de la línea 62).
- Con la ventana ya viva, arrancar el `QApplication` headless en un hilo y
  correr 1a dentro.
- Vigilar: choque de símbolos GL/EGL entre Mesa-para-GTK y Qt, dos conexiones
  Wayland en el mismo proceso, manejadores de señales, y que el `QApplication`
  fuera del hilo principal no rompa nada de GTK.

**Criterio de salida:** la ventana web sigue viva e interactiva **y** el
emulador produce frames correctos, sostenido durante minutos, sin crash.

**Si 1c falla → Plan B (no es callejón sin salida):** el emulador vive en un
**subproceso propio** (`python -m actions.emulator_worker`), que habla con Flask
por HTTP local o memoria compartida. Elimina la convivencia Qt/GTK por
completo, y de regalo aísla los cierres del core (un core que casca ya no se
lleva a Jarvis por delante). Cuesta más fontanería (IPC, ciclo de vida,
limpieza de huérfanos) y añade una copia de frame, pero **las Fases 2–6 se
mantienen prácticamente iguales**: solo cambia dónde vive el gestor, no su API.

**Decisión que sale de esta fase:** in-process vs. subproceso. Es la única
bifurcación arquitectónica del plan, y por eso va antes de escribir código de
producción.

### ✅ RESULTADOS FASE 1 — ejecutada, las tres subfases pasan

**Veredicto: in-process. El Plan B del subproceso NO hace falta.**

| Prueba | Resultado |
|---|---|
| 1a GBA software, fuera de `retro.py` | ✅ **59.7 / 59.7 fps**, peor frame 5.0 ms |
| 1b PS2 hardware GL (`hw_render=True`) | ✅ **59.6 / 59.9 fps** en RTX 5060 |
| 1c GBA + WebKit vivos en el mismo proceso | ✅ 59.7 fps sostenidos, cierre limpio |
| 1c PS2 hardware GL + WebKit | ✅ **59.6 fps sostenidos**, cierre limpio |

Frames verificados a ojo, no solo por contador: Metroid Zero Mission (240x160,
RGB565) y el splash de Madagascar (640x448, XRGB8888) salen con colores,
geometría y degradados correctos. La conversión de frame se hizo con
**numpy + Pillow, sin `QImage`** — confirma que del pipeline de frames Qt solo
hace falta para el contexto GL, nada más.

#### Hallazgo crítico: el `QApplication` tiene que nacer en el hilo principal

La nota (y mi propio plan inicial) daban por bueno crear el `QApplication` en
un hilo secundario. **Emula perfectamente, pero el proceso muere con SIGSEGV al
cerrar el intérprete** — reproducido en los 4 plugins QPA (`wayland`,
`offscreen`, `xcb`, `minimal`), o sea que es la regla de Qt de "QCoreApplication
se crea en el hilo principal", no un problema de plataforma.
`sip.transferto()` no lo evita.

**La solución no cuesta nada:** crear el `QApplication` en el **hilo principal**
(sin llamar nunca a `exec()`) y dejar la emulación + el contexto GL en el
worker. Probado: `EXIT=0`, sin crash. Y encaja exactamente donde hace falta —
`webui_host.mainloop()` ya corre en el hilo principal y puede crearlo **antes**
de `webview.start()`.

#### Qué GPU usar (matiza el hallazgo 2)

Ambas mueven Madagascar a velocidad plena, así que **la elección es por
robustez, no por rendimiento**:

| Plugin QPA | GPU | fps | Avisos |
|---|---|---|---|
| por defecto (`wayland`) | AMD 780M | 59.5 | ninguno |
| `offscreen` | NVIDIA RTX 5060 | 59.6 | *"QWindow-based QOffscreenSurface outside the gui thread. Expect failures"* |

→ **Default: el plugin por defecto (iGPU AMD)**, que no usa ninguna API fuera
de contrato. `offscreen`/NVIDIA queda como **escape configurable** para cuando
haga falta más músculo (upscale >1x, o el core de 3DS en la Fase 6).

#### Avisos anotados, no bloqueantes

- `Gdk-WARNING: eglMakeCurrent failed` aparece **una vez** al arrancar WebKit
  con el core GL ya vivo. La ventana siguió pintando y la emulación no se
  inmutó, pero es señal de que GTK y Qt se disputan EGL. Si en fases
  posteriores aparecen cuelgues raros de la ventana, empezar a mirar por aquí.
- Bajo `offscreen`, `QOffscreenSurface` es QWindow-based y Qt avisa de que
  crearlo fuera del hilo gui puede fallar. Funcionó, pero es API fuera de
  contrato: otra razón para que ese modo no sea el default.

#### Lo que NO se ha medido (para no vender de más)

Las medidas de PS2 son de **intro y menús**, no de gameplay. El dato de
11-14 fps de la nota era *gameplay* con renderer software. Que el splash vaya a
60 fps **no demuestra** que el gameplay vaya a 60 fps — eso se mide en la Fase 5,
con `upscale_multiplier` real y una partida de verdad. Tampoco se ha probado
nada de audio, ni sesiones largas (lo más largo han sido ~50 s).

---

## Fase 2 — Gestor headless (`actions/emulator_session.py`, nuevo)

Sustituto Qt-libre de `RetroScreen` como *driver* del core.

**Trabajo previo (mínimo y quirúrgico):** `_advance_frame_debt`
(`ui/widgets/retro.py:511`) es una función pura y sin Qt; moverla a un módulo
compartido y **reimportarla desde `retro.py`** para que el widget legacy siga
funcionando byte a byte. Es el único cambio que este plan hace en `retro.py`.

**API de `EmulatorSession`** (pensada para que Fase 3 y 4 no la tengan que tocar):
- `start(console_id, rom_path, title)` — valida core, BIOS si PS2, carga, arranca hilo
- `stop()` — flush de SRAM, `libretro.unload()`, cierre del hilo
- `pause(bool)`, `reset()`
- `latest_frame()` — último frame ya convertido a RGB, para el encoder
- `set_button()` / `set_axis()` / `clear_input()` — passthrough al core
- `save_state()` / `load_state()` — replicando la lógica de rutas de
  `ui/panels/emulators.py:797-845` (`.srm` + `.state` por stem de ROM)
- `status()` — console, título, fps real, pausa, hw_render sí/no

**Detalles que no se pueden improvisar:**
- El hilo es dueño del `QApplication`, del contexto GL y del bucle: invariante 2.
- Autosave de SRAM periódico, como el `QTimer` de `emulators.py:661`.
- **Audio: fuera de alcance en esta fase.** `QAudioSink` está atado al widget y
  al hilo de Qt; meterlo aquí mezcla dos problemas. Se drena `take_audio()` y se
  descarta para que no crezca el buffer, y se decide en una fase posterior
  (candidatos: `sounddevice`, o WebAudio en el navegador vía el stream).
  **Consecuencia honesta: hasta entonces se juega sin sonido.**
- Candado explícito contra el emulador Qt legacy (invariante 1).

**Tests:** `tests/test_emulator_session.py` con un core falso — pacing, ciclo de
vida, passthrough de input, doble `start()`, `stop()` sin `start()`.

**Criterio de salida:** tests verdes, `--legacy` sigue jugando igual,
`tests/test_libretro.py` y `test_emulator_input.py` siguen verdes.

---

## Fase 3 — Streaming MJPEG

Ruta `/api/emulator/stream` como `multipart/x-mixed-replace`, consumida por un
`<img>` — sin WebSocket y sin JS de decodificación, como ya apunta la nota.

**Puntos de diseño:**
- El encoder **no bloquea al emulador**: coge el último frame disponible y, si
  el cliente va lento, se salta frames. El bucle de emulación nunca espera a HTTP.
- `cv2.imencode(".jpg", ...)` con calidad configurable; medir su coste real
  contra el presupuesto de frame (crítico en PS2, donde según la nota el margen
  es ajustado). Si no cabe: bajar calidad, escalar, o limitar fps de stream por
  debajo del fps de emulación.
- Cerrar el stream limpiamente al desconectar (Flask `threaded=True` gasta un
  hilo por stream abierto).
- Cabeceras anti-caché y anti-buffering.

**Criterio de salida:** vídeo en vivo en el navegador, con fps de cliente medido
y anotado junto al de emulación. Si divergen mucho, el cuello está identificado
antes de construir la UI encima.

---

## Fase 4 — Input, rutas de biblioteca y panel Svelte

**Backend** (`actions/webui_server.py`):
- `/api/emulator/status`, `/start`, `/stop`, `/pause`, `/reset`
- `/api/emulator/input` — portar la lógica ya probada de
  `lan_dashboard.py:1555` (que ya llama a `set_button`/`set_axis` exactamente
  como hace falta) apuntando al gestor headless
- `/api/emulator/state/save|load`
- `/api/emulator/library` — sobre `game_library.list_entries()`/`scan_roms()`
- `/api/emulator/catalog/search|popular|download` — sobre `rom_catalog`
- `/api/emulator/cores` + instalación vía `emulator_runtime.install_core()`
- `/api/emulator/pad` — sobre `emulator_runtime.pad_layout()`, para que el mando
  táctil dibuje la forma correcta por consola en vez de un D-pad genérico

**Además:** hacer que `_retro_screen()` de `lan_dashboard.py:1485` contemple
también la sesión headless, para que el **mando del móvil siga funcionando** en
modo web. Hoy solo mira el widget Qt.

**Frontend:** `webui/src/panels/Emulators.svelte` — biblioteca, `<img>` del
stream, mando táctil (guiado por `pad_layout()`), teclado, guardar/cargar
estado, pantalla completa. Registrar en `NavRail`/`PanelHost`.

**Tests:** ampliar `tests/test_webui_server.py` siguiendo el patrón de las
clases de movies/anime/youtube que ya existen al final del archivo.

**Criterio de salida:** GBA/SNES jugables de principio a fin desde el panel web.
Primer hito realmente utilizable.

---

## Fase 5 — Subir a PS2

- Revertir `pcsx2_renderer` a `"OpenGL"` en `emulator_runtime.py:151` (quitando
  el comentario de diagnóstico temporal).
- Gate de BIOS en la ruta de arranque, replicando `emulators.py:1580`.
- Ajustar `pcsx2_upscale_multiplier` según lo medido en 1b.
- Verificar que el mando analógico funciona (`pcsx2_analog_mode1: "enabled"` ya
  está, y el comentario de `libretro.py:1019` advierte de no tocar
  `retro_set_controller_port_device`).

**Hilo paralelo — bug de fondo negro de Madagascar.** En cuanto 1b funcione,
tenemos banco de pruebas sin UI: barrer `pcsx2_hw_download_mode` por sus tres
valores no-default (`"Disable Readbacks"`, `"Unsynchronized"`, `"Disabled"`)
contra la pantalla de selección de nivel, capturando PNG de cada uno. Mucho más
rápido y reproducible que a mano en la app Qt. El razonamiento de por qué esta
opción está en `NOTE_webui_emulators.md:100-117`. Necesita un savestate en esa
pantalla concreta — hay que crearlo primero (o llegar jugando una vez).

Si ninguno lo arregla, la conclusión de la nota se mantiene: probablemente es el
bug de readback parcial que mainline arregló en 2.8.0 con `Force Full`, y la
salida real es actualizar/portar el core, no una opción existente.

---

## Fase 6 — 3DS (azahar)

- `CoreSpec` nuevo para `azahar_libretro`, instalable por el mismo buildbot que
  ya usa `install_core()` (`emulator_runtime.py:257`) — sin infraestructura nueva.
- Opciones forzando **OpenGL, no Vulkan** (crash conocido con RADV; y GL 4.3 ya
  confirmado disponible en esta máquina, ver hallazgo 2). Hay que **investigar
  los nombres exactos de las opciones del core** — todavía no verificados.
- `_PAD_LAYOUTS["3ds"]`: un stick, gatillos, botones de cara Nintendo.
- Probar con el `.3ds` de `~/Descargas`.
- Pendiente sin resolver: **el 3DS tiene dos pantallas**. Hay que decidir cómo
  se presentan en un solo `<img>` (el core suele ofrecer opciones de layout).

**Criterio de salida:** arranca y se juega. Si el core no rinde o casca, se
documenta y se para — es la fase más especulativa de todas.

---

## Orden y por qué

Fase 1 va primero porque es la única que puede cambiar la arquitectura. Fases
2–4 construyen el pipeline completo con la consola más barata (GBA), para tener
algo end-to-end antes de meter la dificultad de PS2. Fase 5 sube el listón
reusando el mismo pipeline. Fase 6 es la más incierta y va al final a propósito.

El **Camino A** (lanzador standalone, `emulator_runtime.launch()`) sigue siendo
la red de seguridad barata si 1c falla *y* el Plan B del subproceso resulta
demasiado frágil.
