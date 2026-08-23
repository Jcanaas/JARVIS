# NOTA: "los ajustes no se guardan" — historial y causa real

Este bug se reportó varias veces ("el panel de actividad no recuerda si
estaba plegado", "el micro no recuerda su estado", "el crossfade se
resetea", "se han reseteado todos los ajustes"). Cada vez parecía un bug
distinto. No lo era. Antes de volver a investigar esto desde cero, lee
esto entero.

Todo pasa por [`actions/app_settings.py`](actions/app_settings.py) — el
único sitio que lee/escribe `%LOCALAPPDATA%\Jarvis\config\app_settings.json`.

## Causa raíz real (la que importaba)

`app_settings.py` guardaba en memoria (`_cache`) el JSON cargado la primera
vez, y lo reutilizaba durante toda la vida del proceso. `set(key, value)`
hacía: coger `_cache`, añadir la clave nueva, escribir `_cache` ENTERO a
disco.

Problema: si el archivo en disco cambiaba después de que `_cache` se
cargara — la app se reinicia, otro proceso/script toca el archivo, un test
corre contra la ruta real por error — el siguiente `set()` de ese proceso
escribía su copia vieja encima del disco, **borrando cualquier clave que no
estuviera en esa copia vieja**. Así se explica que desaparecieran ajustes
sueltos (crossfade, dispositivo de audio, panel plegado, mic) uno detrás de
otro, en vez de todos a la vez — dependía de qué clave se hubiera añadido
al disco después de que el caché de ese proceso se cargara.

Confirmado en vivo: dos procesos separados escribiendo claves distintas se
pisaban entre sí antes del fix; después del fix, ninguna clave se pierde
(ver commits/diff en `actions/app_settings.py`, funciones `_read_disk` y
`set`).

**Fix**: `set()` ya NUNCA usa el caché como base. Relee el archivo fresco
de disco justo antes de fusionar la clave nueva y escribir. `get()` sigue
usando caché (vale para lectura, no importa que esté un poco desfasado).

**Si esto vuelve a pasar**: no reintroducir un caché de escritura. El
patrón correcto es siempre: leer disco fresco → fusionar → escribir. Punto.

## Otras dos causas que ya nos hicieron perder tiempo (probar primero)

1. **Build vieja**: `dist/Jarvis/Jarvis.exe` es un build de PyInstaller
   congelado en el tiempo. Si alguien prueba con ese exe en vez de
   ejecutar desde código fuente, cualquier fix en `actions/app_settings.py`
   no existe para él. Comparar fecha del exe vs fecha de edición del
   archivo fuente antes de asumir que el bug sigue vivo.

2. **Permisos (ACL) en `%LOCALAPPDATA%\Jarvis`**: en esta máquina de
   pruebas existe una cuenta (`Glad-Os\CodexSandboxUsers`) con permiso
   solo de Lectura+Ejecución (RX) sobre toda la carpeta, heredado en
   subcarpetas y archivos. Si la build de pruebas corre bajo esa cuenta,
   cada escritura falla con `PermissionError`. Antes esto se tragaba en
   silencio (`except Exception: pass`); ahora `set()` imprime a stderr
   `[app_settings] failed to persist '<key>': <error>` si falla — mirar ahí
   primero. Para arreglar el permiso (acción manual, no la hace el asistente
   porque es cambio de seguridad del sistema):

   ```powershell
   icacls "%LOCALAPPDATA%\Jarvis" /grant "Glad-Os\CodexSandboxUsers:(OI)(CI)M" /T
   ```

## Otra fuente del mismo bug: sesiones/scripts de prueba tocando la ruta real

Confirmado que esto también dispara el mismo mecanismo: cualquier proceso
corto (una sesión de Claude probando `actions/app_settings.py`, un script
suelto, otra IA/agente corriendo contra este repo) que llame a
`app_settings.set(...)` directamente contra la ruta real
(`%LOCALAPPDATA%\Jarvis\config\app_settings.json`) en vez de una ruta de
prueba, actúa exactamente como "abre la app, no entra en Ajustes, cierra" —
un escritor que no conoce las demás claves. Antes del fix a `set()` esto
bastaba para borrar ajustes ajenos. Con el fix ya no borra nada (relee
disco fresco), pero sigue ensuciando el archivo real con basura de test
(pasó en esta misma investigación: apareció una clave `my_key` de una
prueba manual).

**Regla para cualquier sesión/agente que toque este código**: para probar
`actions/app_settings.py` o simular otro escritor, usar SIEMPRE
`unittest.mock.patch("actions.app_settings._FILE", ruta_temporal)` como
hace `tests/test_app_settings.py` — nunca importar el módulo real y llamar
`set()`/`get()` a pelo, eso escribe contra la config real del usuario.

## Fix definitivo: solo una sesión real de la app puede escribir

En vez de confiar en que cualquiera siga la regla de arriba, se hizo
automático. `set()` en `actions/app_settings.py` ahora comprueba la
variable de entorno `JARVIS_REAL_SESSION`:

- `main()` en [`main.py`](main.py) la pone a `"1"` como primerísima acción,
  antes de nada más — así CUALQUIER instancia real de la app (fuente o
  `.exe` empaquetado) la tiene.
- Si la variable no vale `"1"`, `set()` se niega a escribir, imprime aviso
  a stderr (`[app_settings] refusing to persist '<key>': not inside a real
  Jarvis session...`) y no toca ni caché ni disco.
- Un script suelto, un `python -c`, otra sesión de Claude/agente que
  importe `actions.app_settings` directamente — nada de eso tiene la
  variable puesta, así que por defecto NO puede tocar la config real.
  Ya no depende de que nadie recuerde la regla de usar rutas de prueba.
- Los tests (`tests/test_app_settings.py`) la activan explícitamente en
  `setUp` vía `patch.dict(os.environ, {"JARVIS_REAL_SESSION": "1"})` — pero
  siguen patcheando `_FILE` a una ruta temporal, así que nunca tocan la
  config real aunque tengan el flag puesto.

Verificado en vivo: un script sin el flag intenta escribir una clave y el
archivo real queda exactamente igual (con toda la config real intacta:
crossfade, EQ, mandos, etc).

**Si vuelve a pasar**: comprobar que `main()` sigue poniendo
`os.environ["JARVIS_REAL_SESSION"] = "1"` como primera línea, y que nadie
ha quitado la comprobación al principio de `set()`.

## Checklist si vuelve a reportarse

1. Mirar stderr / consola al momento del guardado — ¿aparece
   `[app_settings] failed to persist ...`? Si sí, es permisos → ver punto 2
   arriba.
2. ¿Qué build se está probando? ¿`dist/Jarvis/Jarvis.exe` o código fuente?
   Comparar fechas.
3. Si ninguna de las dos aplica y una clave puntual desaparece sin error:
   revisar que `set()` en `actions/app_settings.py` siga leyendo disco
   fresco (`_read_disk()`) y no un `_cache` como base — es la regresión
   más fácil de reintroducir sin querer.
4. Tests: `tests/test_app_settings.py` — cualquier fix debe mantenerlos en
   verde, y si se reintroduce este bug debería haber un test que lo
   detecte (`test_set_recovers_from_corrupt_primary_via_backup` cubre
   corrupción; si hace falta, añadir uno que simule dos `set()` con
   `_cache` reseteado entre medias para el caso de clobber).
5. Si aparece `[app_settings] refusing to persist ...` en stderr durante
   una prueba manual/real: falta `os.environ["JARVIS_REAL_SESSION"] = "1"`
   en el proceso que lo lanza — normal si estás importando el módulo suelto,
   anormal si es la app real (revisar que `main()` la sigue poniendo).
