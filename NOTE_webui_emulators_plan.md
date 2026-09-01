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
- El hilo de la sesión es dueño del contexto GL y del bucle de pacing
  (invariante 2); el `QApplication` en sí lo crea `webui_host.py` en el hilo
  principal, una vez por proceso (invariante 2b) — la sesión solo lo exige
  con un guard explícito, nunca lo crea ella misma.
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

### ✅ RESULTADOS FASE 2 — ejecutada

Construido: `actions/emulator_pacing.py` (función pura, extraída de
`retro.py:511`), `actions/emulator_session.py` (`EmulatorSession` completa con
la API de arriba) y un cambio en `actions/webui_host.py`.

#### Bug real cazado por el test de integración, no por revisión de código

El plan decía "el hilo es dueño del `QApplication`" — probado en la Fase 1 y
correcto para emular, pero **revienta al cerrar** (ver invariante 2b, añadida
tras la Fase 1). La solución correcta es que `webui_host.mainloop()` cree el
`QApplication` en el **hilo principal**, antes de `webview.start()`, en una
función nueva `_boot_headless_qt()`.

El primer intento de esa función tenía un bug sutil: creaba el `QApplication`
como variable **local** dentro de la función, sin guardar ninguna referencia
Python fuera de ella. PyQt6 hace que Python sea dueño de un `QApplication` sin
padre — en cuanto la función terminaba, el recolector de basura se lo llevaba
por delante junto con el singleton de Qt, y `QApplication.instance()` volvía a
dar `None` como si nunca se hubiera creado. Los prototipos de la Fase 1 no lo
detectaron porque guardaban `app = QApplication(...)` en el ámbito del script,
vivo todo el proceso por casualidad — aquí no había ese ámbito. Lo detectó un
test de integración que reproduce el proceso completo (`_boot_headless_qt()`
real + ventana `webview` real + `EmulatorSession` real), no una revisión de
código: el fallo solo aparece al ejecutar. **Fix:** referencia módulo-global
`actions.webui_host._qt_app`, igual que el patrón que ya usa el módulo para
otros objetos de ciclo de vida largo (`_app`, `_started` en `webui_server.py`).

`_boot_headless_qt()` es *best-effort*: si PyQt6 falla al importar o crear el
`QApplication`, se loguea (`print(f"[WebUIHost] ...")`) y el arranque del resto
de la web UI sigue — Emuladores es opcional, no puede tumbar el modo web
entero.

#### Verificación end-to-end (no solo unitaria)

Antes de los tests unitarios con mocks, se probó el camino real: hilo
principal con `_boot_headless_qt()` tal cual quedó en el repo, una ventana
`webview` de verdad abierta y viva, y una `EmulatorSession` real dentro,
**dos veces** (GBA software y PS2 hardware):

| Escenario | Resultado |
|---|---|
| GBA, `_boot_headless_qt()` real + ventana webview real | ✅ 59.0 fps, `EXIT=0` |
| PS2 hardware, mismo arnés | ✅ 59.9 fps, `hw_render=True`, `EXIT=0` |

Cubre: `start()`/`stop()` reales, pausa/reanudación, doble `start()` rechazado,
`stop()` sin `start()` (no revienta), `save_state()`/`load_state()`
round-trip, contra un core PS2 real con renderer OpenGL forzado (el
`"Software"` de `emulator_runtime.py:151` es solo el diagnóstico temporal del
bug de fondo negro, no aplica aquí).

#### Tests unitarios

`tests/test_emulator_session.py` — 14 tests con un `_FakeCore` (sin Qt real,
sin ROM real, corre en CI): ciclo de vida, pacing, passthrough de input,
guardado/carga de estado, SRAM, doble `start()`, `stop()` sin `start()`, el
guard contra la ausencia de `QApplication`, y que `retro.py` sigue usando
**exactamente** la misma función de pacing (`assertIs`, no solo
"se comporta igual"). 3 ejecuciones seguidas sin fallos intermitentes.

#### Regresiones: ninguna

Suite completa: **698 passed, 2 skipped** — los únicos 2 fallos
(`test_ps2_uses_the_hardware_renderer`, `test_log_interface_returns_a_callable_function_pointer`)
son **preexistentes**, confirmado haciendo `git stash` y reproduciéndolos en
el HEAD anterior a esta fase. El primero es el mismo `"Software"` temporal de
`emulator_runtime.py:151` (Fase 5 lo revierte); el segundo es ajeno a
emuladores (ctypes en `libretro.py:570`), no tocado por este plan.
`ui/widgets/retro.py` se comprobó además con `assertIs`, no solo con
"produce el mismo resultado": es literalmente la misma función.

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

### ✅ RESULTADOS FASE 3 — ejecutada

Construido: `actions/emulator_frame.py` (conversión pura de frame del core a
BGR — formato nativo de OpenCV, sin Qt), `frame_id()` en `EmulatorSession`
(para que el encoder no recodifique un frame sin cambios cuando está en
pausa), y la ruta `/api/emulator/stream` en `webui_server.py`.

#### El "margen ajustado" que temía la nota no aparece en la práctica

| Escenario | fps de cliente medido |
|---|---|
| GBA software (240x160) | **59.7 fps** |
| PS2 hardware (640x448), calidad 85 | **59.6 fps**, 27.8 KB/frame |
| PS2 hardware (640x448), calidad 70 | **60.0 fps**, 19.9 KB/frame |
| PS2 hardware (640x448), calidad 50 | **59.9 fps**, 16.7 KB/frame |

En los tres niveles de calidad probados en PS2, el fps de cliente iguala al de
emulación — el coste de `cv2.imencode` no es el cuello de botella, ni siquiera
a resolución nativa PS2 con renderer OpenGL. **Matiz que sigue en pie:** esto
es splash/menús, no gameplay real (mismo matiz que en la Fase 1); la medida de
gameplay de verdad es cosa de la Fase 5.

Frames verificados a ojo sobre los bytes JPEG que de verdad viajaron por HTTP,
no sobre datos internos: el texto de Metroid y el logo animado de DreamWorks
(globos + luna) de Madagascar, ambos nítidos y con color correcto.

#### Verificación real, no solo `curl` a la ruta

El panel de navegador de esta sesión no pudo abrir `localhost` (limitación del
entorno, no del código: `preview_start` sí conectó — quedó registrado un
`GET /api/emulator/stream` con 200 en el log de Flask — pero `screenshot`
nunca llegó a componer). Se sustituyó por una verificación igual de exigente:
un cliente HTTP que parsea el `multipart/x-mixed-replace` de verdad
(boundary, cabeceras `Content-Length`, cuerpo JPEG), sobre el servidor Flask
real (`webui_server._make_app`) con una `EmulatorSession` real dentro,
exactamente como lo consumiría el `<img>` del panel Svelte de la Fase 4.

Casos de diseño probados en vivo, no solo revisados por código:
- **Corte abrupto del cliente** (socket cerrado sin FIN limpio a media
  respuesta): el servidor sigue respondiendo a otras rutas después — no hay
  fuga ni caída.
- **Sin sesión activa**: la ruta no envía frames ni casca, se queda esperando
  (verificado con un socket crudo y timeout de 2s sin datos).
- Cabeceras correctas: `Content-Type: multipart/x-mixed-replace;
  boundary=jarvisframe`, `Cache-Control: no-cache, no-store, must-revalidate`.

#### Tests unitarios

`tests/test_emulator_frame.py` — 6 tests sobre `frame_to_bgr()` con datos
sintéticos (sin core, sin Qt): los tres formatos de píxel del core
(`XRGB8888`, `RGB565`, `0RGB1555`) decodificados a colores puros conocidos,
`pitch` mayor que el ancho visible (framebuffer con stride, como haría un
core real), y el mismo fallback a RGB565 que usa `retro.py` para formatos
desconocidos.

#### Regresiones: ninguna

Suite completa: **704 passed, 2 skipped** — mismos 2 fallos preexistentes de
la Fase 2, sin relación con este trabajo.

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

### ✅ RESULTADOS FASE 4 — ejecutada, hito "jugable de principio a fin" alcanzado

**Backend:** 17 rutas nuevas en `webui_server.py`, todas bajo `/api/emulator/*`
(estado, ciclo de vida, input, save states, biblioteca, consolas, pad, cores
+ instalación, catálogo de búsqueda/populares/descarga). El catálogo
(`rom_catalog`) y cores (`install_core`) quedaron **solo en backend, probados,
sin UI todavía** — el bullet de frontend del plan original solo pedía
"biblioteca", no un flujo de descarga, así que ahí se paró a propósito para no
meter alcance no pedido.

**Alcance recortado a propósito:** `game_library.list_entries()` mezcla ROMs
con juegos de Steam/Epic (`ui/panels/games.py`, el hub "Juegos" con FitGirl
Repacks). Ese hub es una función aparte que **NO** aparece en ningún sitio de
`NOTE_webui_emulators.md` ni de este plan — todas las rutas de biblioteca
filtran explícitamente a `kind=="rom"`.

**Mando del móvil:** `lan_dashboard.py` ganó `_active_emulator()`, que mira
primero el widget Qt (`_retro_screen()`) y cae a la sesión headless
(`emulator_session.get_session()`) si no hay nada ahí — ambos exponen
`set_button`/`set_axis`/`clear_input` con la misma forma, así que
`api_gamepad_status`/`api_gamepad_input` no necesitaron saber cuál de los dos
tenían delante. Verificado con el core real (no solo con mocks): el botón
pulsado desde `/api/gamepad/input` aparece en `sess._core._pressed`.

**Frontend:** `webui/src/panels/Emulators.svelte` — biblioteca en grid,
reproductor con `<img>` de streaming, mando táctil generado a partir de
`pad_layout()` (D-pad, ABXY, hombros/gatillos condicionales, stick analógico
si `sticks>=1` aunque GBA/SNES no lo ejerciten todavía), teclado (mismo mapeo
por defecto que `input_config._BASE_KEYBOARD`), guardar/cargar estado,
pantalla completa. Registrado en `nav.js`/`PanelHost.svelte` — el hueco
`{ id: 'games', available: false }` ya existía, solo hubo que activarlo.

#### Verificación real en navegador, no solo `test_client()`

Con la biblioteca de tests ya en verde, se sirvió el `dist/` compilado de
verdad detrás de `webui_host._boot_headless_qt()` + Flask, y se navegó con el
Browser pane de esta sesión:

- **Biblioteca real:** 7 tarjetas cargadas desde `/api/emulator/library`,
  incluyendo un juego de N64 (`Super Mario 64`) que ni siquiera se sabía que
  estaba en `~/.jarvis/roms/` hasta este momento.
- **Clic en "Metroid - Zero Mission" → jugando de verdad:** el `<img>` recibió
  el MJPEG real (`naturalWidth=240`, resolución GBA), a **60 fps**. Se
  capturó un frame directamente del `<img>` renderizado vía `<canvas>` y se
  verificó a ojo: el starfield real de la intro de Metroid, no un frame en
  negro ni basura.
- **Mando táctil real:** `pointerdown`/`pointerup` sobre el botón "A" del DOM
  disparó `POST /api/emulator/input` (200, confirmado en el log de Flask).
- **Ciclo completo confirmado en el log del servidor:** `start` → `input` ×2
  → `stop`, los cuatro con 200.
- El panel táctil renderizó exactamente los controles esperados para GBA
  (D-pad, Select/Start, Y/X/A/B, L/R) y **omitió correctamente** el stick
  analógico (`pad_layout("gba").sticks == 0`).

**Un hallazgo aparte, fuera del alcance de esta fase:** la pantalla
"Conectando con J.A.R.V.I.S…" (`Splash.svelte`) se quedó colgada
indefinidamente en el navegador automatizado de esta sesión — reproducible
**ya en el panel de Inicio**, antes de tocar nada de Emuladores, en código
(`App.svelte`/`Splash.svelte`/`sse.js`) que esta fase no modificó. Todas las
llamadas de red que la bloquean (`/api/state`, `/api/setup/status`)
devolvían 200 con normalidad; el bloqueo parece del lado del navegador
automatizado (una conexión SSE de larga duración interactuando raro con este
entorno concreto), no del backend. Se rodeó ocultando el overlay por JS para
poder seguir verificando con clics de coordenadas reales — no se tocó
`App.svelte` para no arreglar a ciegas algo no reproducido en el target real
(pywebview/WebKitGTK). **Merece una comprobación aparte en la app de
escritorio de verdad**, no dar por sentado que es solo cosa de este entorno.

#### Tests

`tests/test_webui_server.py` — clase `EmulatorRouteTests` nueva, 26 tests
(contrato de cada ruta, mapeo de errores a 409/404/400, publicación de
eventos SSE de progreso vía `SynchronousThread`, siguiendo el patrón exacto
de `MoviesRouteTests`). `tests/test_lan_dashboard.py` ganó 3 tests para el
fallback de `_active_emulator()` (Qt activo, headless activo, Qt gana si
ambos están — caso que no debería poder pasar en producción pero que
conviene que no explote si pasa).

#### Regresiones: ninguna

733 passed, 2 skipped — mismos dos fallos preexistentes de fases anteriores.
`npm run build` limpio (0 errores, 0 avisos propios tras corregir dos usos de
`bind:this` que Svelte 5 quiere declarados con `$state()` — el único aviso
restante es de accesibilidad en el stick analógico, de la misma clase que ya
tolera el resto del código base, p. ej. `Calendar.svelte`).

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

### ✅ RESULTADOS FASE 5 — ejecutada

**`pcsx2_renderer` revertido a `"OpenGL"`** en `emulator_runtime.py` (se quitó
el comentario de diagnóstico temporal). Efecto colateral bueno: esto arregla
también `test_ps2_uses_the_hardware_renderer`, el único test que llevaba en
rojo desde antes de empezar esta tarea entera.

**Gate de BIOS:** el *check* ya existía desde la Fase 2
(`EmulatorSession.start()` ya rechazaba con un mensaje claro si faltaba). Lo
que de verdad faltaba por "replicar" de `emulators.py:1580` era la
*importación* — se añadió `/api/emulator/bios/import` (equivalente web del
`QFileDialog` nativo de `_import_bios()`, ya que un navegador solo tiene
`<input type=file>`), con un control mínimo en `Emulators.svelte` que aparece
solo si `start()` falla por BIOS ausente. Probado con un dump falso de 4 MiB
**real** (no mockeado) contra un `system_dir` temporal — nunca contra el BIOS
real de esta máquina.

**Rendimiento medido en gameplay real, no solo splash/menú** (el hueco de
honestidad que quedó anotado dos veces en fases anteriores): arranque
completo de Madagascar desde cero bajo OpenGL, pulsando Cruz/Start de verdad
a través de BIOS → avisos → intro → menú, hasta **gameplay 3D real** (Marty
en el zoo) — **57.2 fps de media sobre 250s reales**, incluyendo las
transiciones de menú (que son las que más CPU piden, no solo el render 3D).
`pcsx2_upscale_multiplier` se deja en `"1x Native (PS2)"`: no hay ninguna
señal de cuello de botella que justifique subirlo, ni en splash (Fase 1/3) ni
en gameplay real medido aquí.

**Mando analógico: verificado con evidencia cuantitativa, no solo "no
casca".** Con el personaje en reposo, el diff medio de píxel entre frames
consecutivos es **0.5** (ruido, prácticamente estático). Con el stick a la
izquierda a fondo, 90 frames después el diff sube a **57.5**; hacia delante,
**47.7** — dos órdenes de magnitud por encima del reposo. Las capturas
confirman visualmente que es movimiento coherente: la cebra gira y cruza a
una pasarela con un NPC visible, no un frame corrupto.

#### El bug de fondo negro de Madagascar: reproducido y descartada la hipótesis del `hw_download_mode`

Se intentó llegar a la pantalla exacta navegando el menú de pausa por script
(`Exit to Map`), con éxito parcial: se alcanzó el diálogo de confirmación
correcto, pero el cursor no se movía de "No" a "Yes" con las pulsaciones
sintéticas de "arriba" — probablemente un problema de timing del script, no
del juego, no diagnosticado a fondo por priorizar el tiempo.

**El usuario reprodujo el bug jugando de verdad** y guardó el estado
(`DreamWorks Madagascar (Spain).state`, pantalla "Minigolf tiki" del mapa).
Cargado ese estado bajo la configuración actual (OpenGL):

- **9.7% de píxeles no-negros** — el HUD y el texto ("Minigolf tiki", "Jugar
  minijuego") se ven perfectamente; el fondo 3D del mapa es negro sólido.
  Coincide exactamente con la descripción de la nota.
- Se confirmó primero, volcando las opciones reales que reporta el core
  (`JARVIS_CORE_LOG=1`), que `pcsx2_hw_download_mode` existe con
  **exactamente** los 4 valores usados en el barrido — nada de typos
  silenciados.
- **Barrido de los 4 valores contra este mismo estado: resultado idéntico en
  los cuatro** (`Accurate`, `Disable Readbacks`, `Unsynchronized`,
  `Disabled`), 9.7% no-negro en los cuatro casos, píxel a píxel indistinguibles.
  **La opción no tiene ningún efecto sobre este bug.**

**Conclusión de esa sesión:** la hipótesis de `hw_download_mode` queda
descartada con evidencia directa. El volcado de opciones confirma que este
fork de LRPS2 (a fecha de esa prueba) solo tenía 4 valores, sin el quinto
(`"Accurate (Force Full)"`) que PCSX2 mainline añadió en 2.8.0 para el mismo
patrón de síntoma.

### ✅ ACTUALIZACIÓN — el bug arreglado de verdad, y una corrección importante de causa

El usuario pidió intentar portar el parche de `Force Full` (mainline commit
[`4b76157b9`](https://github.com/PCSX2/pcsx2/commit/4b76157b9e0be3b7a8937d93e981c4380cce35b4))
al fork LRPS2. Se investigó el diff real (5 archivos, ~10 líneas de lógica en
`GSTextureCache.cpp`), se confirmó que el fork tiene el mismo código en esos
puntos exactos (`readbacks_since_draw`, `GSHardwareDownloadMode`), se clonó
`libretro/ps2` (rama `libretroization`), se parcheó (`Config.h`,
`GSTextureCache.cpp` en 5 sitios — el fork duplica la lógica en rama
profundidad/color, mainline no en esa versión —, `main.cpp`,
`libretro_core_options.h`), se resolvió un problema de portabilidad ajeno
(`fallocate` sin `_GNU_SOURCE` en `libretro-common/vfs/vfs_implementation.c`,
por una glibc más estricta que la de la CI oficial) y se compiló con éxito.

**Probado contra el savestate real del usuario (Minigolf tiki): pasó de 9.7%
a 99.4% no-negro con `Accurate (Force Full)`.** Corregido de verdad.

**Pero antes de instalarlo, control de causa — y ahí vino la sorpresa:** se
compiló una segunda versión, del mismo commit de upstream, **sin** el parche
de Force Full (solo con el fix de `_GNU_SOURCE`, imprescindible para que
compile en absoluto). Probada contra el mismo savestate: **también 99.4%
no-negro, píxel a píxel idéntica.** El parche de Force Full no tuvo nada que
ver — el core instalado en esta máquina (fechado 24 de agosto) simplemente
estaba desactualizado. La rama `libretroization` está muy activa (commits de
hace 3 días en el momento de la prueba) y algún cambio de los ~100 commits en
`GS/HW`/`GS/TC` entre el 24 de agosto y ahora ya arregló este bug por otra
vía — no identificado el commit exacto, y no hacía falta: la corrección real
es mucho más simple.

**Comprobación final, la más simple de todas:** el propio buildbot oficial de
libretro (`buildbot.libretro.com`, el mismo que ya usa
`emulator_runtime.install_core()`) tenía una build de **ese mismo día**
(`Last-Modified` de hace pocas horas). Descargada y probada:
**99.4% no-negro, con render por hardware real (`hw_render_active=True`) y
110 fps sin capar en gameplay real** (muy por encima de los 59.9 objetivo).

**Se instaló esa build oficial** vía la propia función `install_core("ps2")`
de JARVIS (no una copia manual de archivo — ejercitando el código de
producción real), con copia de seguridad del core anterior
(`pcsx2_libretro.so.backup-20260824`) antes de sobrescribir. Verificado de
nuevo contra el core ya instalado (`find_core("ps2")`, la misma ruta que usa
la app): sigue en 99.4%.

**Conclusión final:** el arreglo real es "actualiza el core", no un parche.
Mucho mejor resultado que el previsto — cero divergencia de fork que mantener,
cero riesgo de que `install_core()` sobrescriba silenciosamente un binario
casero en la próxima actualización. El clon local con el parche de Force Full
se descartó (no llegó a usarse en producción); el diff investigado
(mainline `4b76157b9`) queda documentado arriba por si en el futuro hiciera
falta portarlo de verdad para otro juego con el mismo síntoma.

#### Hallazgo colateral: bug real de concurrencia en `save_state`/`load_state`/`reset`, encontrado y arreglado

Verificando el core recién instalado a través de la API pública real de
`EmulatorSession` (no un script suelto), `load_state()` **hizo crashear el
proceso** (SIGSEGV) al llamarlo desde el hilo principal mientras el hilo de
emulación tenía el contexto GL activo en el suyo. `save_state()`, `load_state()`
y `reset()` (de la Fase 2) llamaban directamente a `core.*` desde el hilo que
los invocara — para GBA/SNES (software) es inofensivo, pero para un core con
render por hardware (PS2, y cualquier consola futura que lo use) es acceso a
GL desde el hilo equivocado. En producción, `/api/emulator/state/save|load`
lo llaman desde un hilo de petición Flask — **este bug era real y explotable
en producción**, no un artefacto de mi script de prueba.

**Arreglado:** las tres operaciones ahora se encolan (`_pending_actions`,
`queue.Queue`) y se ejecutan en el propio hilo de emulación, drenadas una vez
por iteración del bucle en `_run()` — colocado **antes** del `continue` de
pausa, así que funciona incluso con el juego en pausa. El invariante 2 del
módulo (documentado desde la Fase 2: "el contexto GL solo puede tocarse desde
el hilo de emulación") ahora se aplica también a estas tres llamadas, no solo
a `run_frame()`.

Verificado: 2 tests nuevos en `test_emulator_session.py` (que la llamada
ocurre en el hilo de emulación y no en el del llamador; que `save_state()`
sigue funcionando en pausa) y reproducción en vivo del crash original seguida
de la misma llamada ya arreglada, sin fallo, contra el core real de PS2 recién
instalado.

#### Regresiones: ninguna

Suite completa, incluyendo el fix de concurrencia: **739 passed, 2
skipped, 1 failed** — el revert del renderer dejó en verde
`test_ps2_uses_the_hardware_renderer` (se arregló solo, sin tocarlo
directamente). Queda un único fallo preexistente, el mismo desde la Fase 2:
`test_log_interface_returns_a_callable_function_pointer`, un problema de
ctypes en `libretro.py` ajeno a emuladores y no tocado por este plan.

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
