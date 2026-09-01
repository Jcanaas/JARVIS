# Plan: pulir Emuladores en la web + 3DS en el móvil con pantalla táctil

## Contexto

Las 6 fases de `NOTE_webui_emulators_plan.md` dejaron el modo Emuladores
funcional de principio a fin (GBA→PS2→3DS), pero con recortes de alcance
deliberados que ahora hay que rellenar. El usuario pidió, en orden de
prioridad creciente:

1. Poder remapear el teclado del PC para el emulador desde la web (hoy el
   mapeo está hardcodeado en `Emulators.svelte`, sin UI de remapeo — el
   comentario del propio código ya lo admite).
2. Que los atajos de un solo carácter para cambiar de panel (`P` → Películas,
   `U` → Emuladores…) se desactiven mientras se juega, como ya hace el legacy.
3. El descargador/catálogo de ROMs de la interfaz nueva — el backend ya existe
   y está probado (Fase 4), falta la UI.
4. **La tarea grande**: cuando se juega a 3DS, poder llevar la pantalla
   inferior (la táctil) al móvil junto con los controles, y poder
   reposicionar esos controles a gusto en la app móvil — la pantalla
   secundaria tiene que ser táctil de verdad en el móvil.

Importante, y ya aclarado por el usuario: el mando táctil **no va en el
navegador de escritorio** — ya existe una app móvil completa
(`mobile-app/`) con su propio `GamepadOverlay.tsx`/`AnalogStick.tsx` que ya
habla con `actions/lan_dashboard.py` (`/api/gamepad/*`, token-gated, LAN) y
que en la Fase 4 ya se conectó a la sesión headless vía `_active_emulator()`.
Ese mando de móvil ya funciona para GBA/SNES/PS2/3DS tal cual — el trabajo
de esta tarea es extenderlo, no rehacerlo.

Investigado antes de planear (agente Explore + lectura directa del código de
Azahar en GitHub, sin adivinar):

- **Rebind de teclado legacy**: `ui/widgets/controls.py`'s `ControlsDialog` —
  grid Botón/Teclado/Mando, clic para capturar, Escape cancela, timeout 6s.
  Llama a `input_config.load/bind_key/save/reset` (`actions/input_config.py`).
- **Guarda de atajos legacy**: `ui/__init__.py`'s `_shortcut_focus_is_editing()`
  bloquea los atajos de panel si el widget con foco (o un ancestro) es
  `RetroScreen`. `App.svelte` ya replica el filtro de "editable target" pero
  no tiene el equivalente para "hay una partida en marcha".
- **Catálogo/descargador legacy**: `EmulatorsModePanel` en
  `ui/panels/emulators.py` — grid con chips de consola/región, tarjetas con
  badge "EN DISCO", vista de detalle con selector de versiones
  (`rc.versions_for`), botón de acción dinámico (Descargar/Jugar/
  Descargando…/No disponible), página de importación manual para consolas
  sin catálogo (PS2 hoy, 3DS también).
- **Touch de 3DS en Azahar**: leído el código fuente real
  (`src/citra_libretro/input/mouse_tracker.cpp` en `azahar-emu/azahar`). El
  core lee el touch como `RETRO_DEVICE_POINTER` normalizado
  (-0x7fff..0x7fff) **sobre el framebuffer combinado completo**, y él mismo
  recorta internamente contra `layout.bottom_screen`. **No hace falta partir
  el vídeo en dos streams** — con `citra_layout_option: "default"` verifiqué
  contra un frame real (400×480) que la pantalla inferior ocupa exactamente
  el rectángulo `x∈[40,360], y∈[240,480]` (proporciones 10%-90% horizontal,
  50%-100% vertical — uso proporciones, no píxeles fijos, para que aguante
  si `resolution_factor` cambia). Con eso: recortar esa región para el móvil
  y traducir el toque de vuelta a coordenadas normalizadas es aritmética
  directa, no reingeniería del pipeline de vídeo.
- `actions/libretro.py`'s `_on_input_state` (línea 985) hoy solo entiende
  `DEVICE_JOYPAD` y `DEVICE_ANALOG` — hace falta añadir `DEVICE_POINTER`.
- Mobile app: `mobile-app/src/api.ts` habla con `lan_dashboard.py` (LAN,
  token), no con `webui_server.py` (loopback) — cualquier ruta nueva para el
  móvil va en `lan_dashboard.py`. `mobile-app/src/storage.ts` usa
  `AsyncStorage` — ahí se persiste el layout de botones reposicionado.
- Botón "📱 Mando móvil" en el toolbar legacy del reproductor
  (`ui/panels/emulators.py:639`) llama a
  `lan_dashboard.bump_gamepad_announce()` — función Python normal, sin Qt,
  reutilizable tal cual desde una ruta nueva en `webui_server.py`.

---

## Fase A — Desactivar atajos de panel mientras se juega

La más rápida y la que desbloquea probar B con comodidad (capturar teclas
para remapear necesita la misma guarda).

- Nuevo store `webui/src/stores/emulator.js`: `export const emulatorPlaying = writable(false)`.
- `Emulators.svelte` lo pone a `true` en `play()` (tras `start()` con éxito) y
  a `false` en `stop()` — el `EmulatorSession` real ya expone `status().running`,
  pero el store es más simple para un guard puramente de UI.
- `App.svelte`'s `onKeydown` (línea ~60): antes de `panelByKey`, si
  `get(emulatorPlaying)` es `true`, `return` — replica
  `_shortcut_focus_is_editing()`'s comprobación de `RetroScreen`.
- El propio `onKeydown` de `Emulators.svelte` (el mapeo de teclado al mando)
  ya usa `<svelte:window>` con su propio handler — no compite con este cambio,
  solo hay que asegurar que el de `App.svelte` ceda primero.

**Verificación**: con una partida en marcha, pulsar `P`/`U`/etc. no debe
cambiar de panel; sí debe seguir moviendo al personaje. Sin partida, los
atajos siguen funcionando como hoy.

---

## Fase B — Remapeo de teclado (PC) para el emulador

**Decisión de diseño — un solo almacén compartido con el legacy.**
`input_config.Bindings.keyboard` guarda códigos de tecla de Qt (enteros). El
teclado del navegador entrega `KeyboardEvent.code` (strings tipo
`"ArrowUp"`, `"KeyX"`). En vez de crear un segundo almacén de preferencias
paralelo (que divergiría del legacy y confundiría al usuario si algún día
usa `--legacy`), traducir en el backend: la mayoría de teclas imprimibles
(letras, dígitos) ya coinciden 1:1 con el valor ASCII que usa Qt
(`Qt.Key_A == 0x41`, etc.), y las teclas especiales que ya usa
`_BASE_KEYBOARD` (flechas, Enter, Backspace, Shift, Espacio) son una tabla
fija de ~10 entradas. Nueva función pura
`actions/input_config.py::qt_key_from_js_code(code: str) -> int | None`
(y su inversa `js_code_from_qt_key` para pintar el rótulo en la UI), con
tests dedicados.

**Backend** (`actions/webui_server.py`, sección `-- Emuladores --`):
- `GET /api/emulator/controls?console_id=` — `input_config.load(console_id)`,
  devuelve `button_order(console_id)` + bindings actuales ya traducidos a
  `code` de JS (o `null` si esa entrada no es traducible, para no romper la
  UI con un valor Qt-only).
- `POST /api/emulator/controls/bind` — body `{console_id, button, code}` →
  `qt_key_from_js_code` → `bindings.bind_key(...)` → `input_config.save(...)`.
  Si `qt_key_from_js_code` devuelve `None` (tecla sin traducción, p.ej. una
  tecla muerta/IME), 400 con mensaje claro.
- `POST /api/emulator/controls/reset` — body `{console_id}` →
  `input_config.reset(console_id)`.

**Frontend**: modal nuevo `webui/src/lib/ControlsDialog.svelte` (reusable,
no solo dentro de `Emulators.svelte`), replicando la UX ya investigada del
legacy: fila por botón lógico, celda "pulsa una tecla" con captura vía
`window.addEventListener('keydown', ...)` de un solo uso, Escape cancela,
timeout de 6s igual que `_CAPTURE_TIMEOUT_MS`. Botón "Restaurar por
defecto". Se abre desde un nuevo botón "🎮 Controles" en el toolbar del
reproductor de `Emulators.svelte` (mismo sitio que el legacy) — al abrir,
pausar la partida (`api.pauseEmulator(true)`) igual que hace
`GamePlayerPage.open_controls()`, y usar el store de la Fase A para que la
captura de teclas no dispare atajos de panel mientras el diálogo está
abierto.
- Tras guardar, recargar el mapeo activo del `KEY_MAP` hardcodeado actual en
  `Emulators.svelte` — sustituirlo por el resultado de `GET /controls`
  (con `_BASE_KEYBOARD` como *fallback* solo si el usuario no ha
  personalizado nada, no como valor quemado).

**Tests**: `tests/test_input_config.py` (nuevo, o añadir a uno existente)
para `qt_key_from_js_code`/`js_code_from_qt_key` (ida y vuelta, y el caso
sin traducción); `tests/test_webui_server.py` para las 3 rutas nuevas,
mockeando `input_config`.

---

## Fase C — Catálogo/descargador de ROMs en la interfaz nueva

Backend ya completo y probado (Fase 4:
`/api/emulator/catalog/search|popular|download`, `/api/emulator/cores`,
`/api/emulator/cores/install`). Falta:

- `GET /api/emulator/catalog/versions?console_id=&stem=` — expone
  `rc.versions_for(rom)` (no tenía ruta propia). Necesario para el selector
  de versiones/regiones de la vista de detalle.
- `POST /api/emulator/library/import` — sube un archivo (multipart, mismo
  patrón que `/api/emulator/bios/import` de la Fase 5) y llama a
  `game_library.add_local_rom(path, console_id, title)` — cubre tanto "ROM
  suelta que ya tengo" como el camino de importación manual que usan hoy PS2
  y usará 3DS (`has_catalog=False`).

**Frontend**: extender `Emulators.svelte` con una segunda vista (o una
pestaña dentro del mismo panel) "Catálogo", reproduciendo la estructura ya
mapeada del legacy sin copiar su Qt 1:1:
- Chips de consola (`GET /api/emulator/consoles`) + chips de región.
- Grid de tarjetas con carátula, badge "EN DISCO" (`rom.downloaded`, ya lo
  devuelve el backend), buscador con *debounce* ~280ms cayendo a "populares"
  si está vacío (mismo patrón que `_queue_search`/`_load_popular` del
  legacy, con un token de petición para descartar respuestas obsoletas).
- Vista de detalle: metadatos, selector de versión (`GET .../versions`),
  aviso si `!rom.available`, barra de progreso de descarga escuchando los
  eventos SSE `emulator_download_progress|ready|error` ya emitidos por el
  backend (patrón idéntico al de `stream_progress` que ya consume
  `StreamingBrowser.svelte`), botón de acción dinámico con los mismos 4
  estados que el legacy.
- Para consolas con `has_catalog=False` (PS2, 3DS): en vez del buscador,
  mostrar directamente el formulario de importación local
  (`POST .../library/import`) — mismo criterio que `_show_grid_view` del
  legacy.

**Tests**: `tests/test_webui_server.py` para la ruta de versiones y la de
importación (con un archivo real pequeño de prueba, patrón ya usado en la
Fase 5 para el import de BIOS).

---

## Fase D — 3DS: pantalla táctil inferior + mando reposicionable en el móvil

La pieza grande. Dividida en 3 sub-partes que se pueden verificar por
separado antes de unirlas.

### D1 — Backend: que el core reciba touch de verdad

- `actions/libretro.py`: añadir `DEVICE_POINTER = 6` (valor estándar de
  libretro.h). En `_on_input_state`, rama nueva:
  ```
  if device == DEVICE_POINTER:
      if button == ID_POINTER_X: return self._pointer_x
      if button == ID_POINTER_Y: return self._pointer_y
      if button == ID_POINTER_PRESSED: return 1 if self._pointer_pressed else 0
      return 0
  ```
  (`ID_POINTER_X/Y/PRESSED = 0/1/2`, estándar libretro). Nuevo método
  `set_pointer(x: int, y: int, pressed: bool)` — mismo patrón que
  `set_axis`, sin tocar hilos (ya se llama desde donde ya se llama
  `set_button`/`set_axis`, protegido por las mismas garantías de la Fase 2).
- `actions/emulator_session.py`: `set_pointer()` como passthrough directo,
  igual que `set_button`/`set_axis` (no toca el core desde otro hilo, esto
  es Python puro, no GL — no necesita pasar por `_pending_actions`).
- `actions/emulator_frame.py`: nueva función pura
  `crop_bottom_screen_3ds(bgr: np.ndarray) -> np.ndarray` — recorta
  `x∈[10%,90%]·ancho, y∈[50%,100%]·alto` del frame combinado. Con tests
  contra un array sintético (no hace falta un core real para probar el
  recorte).

### D2 — Backend: ruta LAN para el móvil

Todo en `actions/lan_dashboard.py` (no `webui_server.py` — el móvil habla
con éste, confirmado en `mobile-app/src/api.ts`):

- `GET /api/gamepad/screen` (MJPEG, `multipart/x-mixed-replace`, mismo
  patrón de generador que `webui_server.py`'s `_mjpeg_frames` de la Fase 3,
  pero leyendo de `_active_emulator()` y aplicando
  `crop_bottom_screen_3ds()` antes de codificar — solo tiene sentido cuando
  `console == "3ds"`, devolver 404 si no). Token-gateado como el resto de
  `lan_dashboard.py`.
- `POST /api/gamepad/touch` — body `{x: 0..1, y: 0..1, pressed: bool}`
  (coordenadas ya normalizadas 0..1 *dentro del recorte*, para que el móvil
  no necesite saber nada del tamaño real del framebuffer). El backend hace
  la tradución inversa exacta a la de `mouse_tracker.cpp`: de 0..1 dentro
  del recorte → posición absoluta en el framebuffer combinado → normalizado
  -0x7fff..0x7fff → `core.set_pointer(...)`.
- `api_gamepad_status` (ya existente) gana un campo `has_touch_screen: bool`
  (`console_id == "3ds"`) para que el móvil sepa si debe pedir el stream de
  pantalla o no.

### D3 — Móvil: pantalla táctil + mando reposicionable

- **Vista de pantalla táctil**: nuevo componente
  `mobile-app/src/components/TouchScreen3DS.tsx` — una `<Image>` apuntando
  a `${baseUrl}/api/gamepad/screen?token=...` (igual que ya hace
  `whatsappMediaUrl` para servir imágenes con token en la URL) con un
  `View` encima capturando touch (`onResponderGrant/Move/Release`, mismo
  patrón de responder único que ya usa `GamepadPad` en
  `GamepadOverlay.tsx` para no competir con `Pressable`), traduciendo la
  posición del toque a fracción 0..1 del `<Image>` renderizado y llamando a
  `api.sendGamepadTouch({x, y, pressed})` (nueva función en `api.ts`,
  mismo patrón que `sendGamepadInput`).
- **Modo reposicionable**: nuevo estado "editar layout" en `GamepadOverlay.tsx`
  — un botón (icono de "editar") activa un modo donde cada `PadButton`
  se vuelve arrastrable (usar el mismo sistema de responder por touch que ya
  existe, no una librería de drag-and-drop nueva) y las posiciones
  (offsets respecto al layout por defecto) se guardan en
  `mobile-app/src/storage.ts` vía `AsyncStorage`, **por dispositivo, no
  sincronizado con el backend** — es una preferencia de la app, no del
  emulador. Nueva función `loadPadLayout()/savePadLayout(overrides)` en
  `storage.ts`. `GamepadPad` lee los offsets guardados al montar y los
  aplica sobre los estilos posicionales actuales (`dUp`, `fRight`, etc. hoy
  fijos en `StyleSheet.create` — pasan a calcularse con el offset guardado
  sumado).
- **Layout de pantalla cuando el juego es 3DS**: `GamepadPad` (o un
  variante `GamepadPad3DS`) compone `TouchScreen3DS` a un lado y el mando ya
  existente al otro — en landscape (que ya es el modo forzado del mando,
  `ScreenOrientation.OrientationLock.LANDSCAPE`), pantalla táctil a la
  izquierda, mando a la derecha, ambos reposicionables/redimensionables
  igual que los botones. Decisión: reusar el `layout.sticks/shoulders/...`
  ya presente para las consolas normales, y activar este modo compuesto
  solo si `layout.console === "3ds"` (o el nuevo `has_touch_screen`).

**Verificación** (sin emulador real de por medio donde se pueda evitar):
- `crop_bottom_screen_3ds` con test unitario sobre un array sintético.
- `set_pointer`/`_on_input_state(DEVICE_POINTER, ...)` con test unitario
  sobre un core falso, igual que ya existen para `set_button`/`set_axis` en
  `tests/test_libretro.py`.
- La traducción 0..1 → normalizado -0x7fff..0x7fff con un test de ida y
  vuelta contra la fórmula real de `mouse_tracker.cpp` (valores conocidos:
  centro del recorte → pointer≈0,0; esquina → pointer≈±0x7fff).
- Con el core Azahar real (ya instalado): cargar el `.3ds` del usuario,
  enviar un toque sintético dentro del recorte de pantalla inferior (p.ej.
  en mitad de un botón de menú visible) y confirmar por diff de frame que
  el juego reaccionó — misma técnica ya usada para el stick de PS2 y el
  botón A de 3DS en la Fase 5/6.
- Verificación real en el móvil: esto **no lo puedo probar yo** (requiere
  un dispositivo físico o emulador Android/iOS con la app instalada). Lo
  dejo explícito: construyo y verifico todo lo que se puede probar sin
  hardware (backend, lógica de recorte/traducción, que el core reacciona),
  y el usuario prueba la experiencia real en su móvil al final.

---

## Orden de trabajo

A (rápida) → B (habilita usar el emulador con comodidad mientras se
construye el resto) → C (backend ya listo, solo UI) → D (la más larga,
con sub-verificación en 3 pasos). Cada fase se commitea por separado, con
tests y verificación en vivo antes de pasar a la siguiente — mismo patrón
que ya funcionó bien en `NOTE_webui_emulators_plan.md`.
