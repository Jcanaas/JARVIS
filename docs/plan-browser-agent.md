# Plan: arreglar el agente de navegador (tareas web multi-paso)

> Documento de trabajo autocontenido. Pensado para que cualquier modelo/sesión pueda ejecutar el plan sin contexto previo. Todas las rutas son relativas a la raíz del repo (`Mark-XXXIX`).

## 1. Síntoma reportado

El usuario pide "súbeme un post a LinkedIn". El asistente:

1. A veces ni abre el navegador.
2. Cuando lo abre, llega a linkedin.com y se queda ahí sin hacer nada.
3. Al terminar, anuncia la tarea como completada ("All done, sir") aunque no ha publicado nada.

El punto 3 es el más grave: el sistema **miente** sobre el resultado. Los puntos 1-2 son consecuencia del diseño actual (plan ciego sin percepción de la página).

## 2. Arquitectura actual (cómo llega una orden al navegador)

Hay **dos rutas de entrada** independientes:

**Ruta A — llamada directa (comando simple).** Gemini Live (sesión de voz/texto en `main.py`) tiene `browser_control` como function-call. `main.py:1537` la ejecuta directamente:
`browser_control(parameters=args)` → `actions/browser_control.py`. Sirve para "abre YouTube", "busca X en Google". Un solo paso, sin planificación.

**Ruta B — tarea multi-paso (`agent_task`).** Para "sube un post a LinkedIn" Gemini debería invocar `agent_task` (declarada en `main.py:854`, despachada en `main.py:1680`). Flujo:

```
main.py (agent_task) → agent/task_queue.py (cola en background)
    → agent/executor.py  AgentExecutor.execute(goal)
        → agent/planner.py  create_plan(goal)      # Gemini flash-lite genera TODO el plan de golpe
        → bucle de pasos: _call_tool() por paso
            → actions/browser_control.py (Playwright, hilo + event loop propio por navegador)
        → agent/error_handler.py  analyze_error()  # solo se invoca si _call_tool LANZA excepción
        → planner.replan() si un paso "falla" (máx 2 replanes)
        → _summarize() → "All done, sir."
```

**`actions/browser_control.py`** ya es bastante capaz: sesiones persistentes por navegador (`_SessionRegistry`), perfil real del usuario (login de LinkedIn ya iniciado si usa su perfil), y acciones: `go_to, search, click, type, scroll, press, get_text, get_url, fill_form, smart_click, smart_type, new_tab, close_tab, screenshot, back, forward, reload, close, switch, list_browsers, close_all`.

## 3. Diagnóstico — causas raíz, por orden de impacto

### RC1. Los errores del navegador no son errores para el executor

`actions/browser_control.py` **nunca lanza excepción** hacia arriba: captura todo y devuelve strings de error como valor de retorno:

- `"Element not found (timeout)."` (click, línea ~579)
- `"Could not open: <url>"` (go_to, línea ~556)
- `"Click error: ..."`, `"Type error: ..."`, `"Could not find element: '...'"`, `"Could not find input: '...'"`, `"Browser error (...): ..."`, `"Browser action '...' timed out (60s)."`

`agent/executor.py:297-301` solo considera fallo la **excepción**. Un string devuelto = paso ✅. Consecuencia: cada click fallido en LinkedIn cuenta como éxito, el plan "se completa" y `_summarize()` (executor.py:375) anuncia éxito. `analyze_error`/`replan` no se activan jamás para fallos web.

### RC2. Plan ciego: se decide todo antes de ver la página

`agent/planner.py:create_plan()` genera el plan completo con `gemini-2.5-flash-lite` **antes** de abrir el navegador. Para LinkedIn inventa textos/selectores ("Start a post", "Publicar", selectores CSS imaginarios). El DOM real de LinkedIn es dinámico, localizado (ES/EN) y cambia con frecuencia: los clicks ciegos fallan casi siempre. No existe bucle observar→decidir→actuar: nunca se hace `get_text`/screenshot entre pasos para decidir el siguiente.

Además el prompt del planner (`PLANNER_PROMPT`) impone: *"NEVER reference previous step results. Every step is independent"* — regla que hace estructuralmente imposible un flujo web encadenado.

### RC3. El planner no conoce las mejores acciones del navegador

`PLANNER_PROMPT` solo lista `go_to | search | click | type | scroll | get_text | press | close`. No expone `smart_click`, `smart_type`, `fill_form`, `get_url`, `screenshot`, que son justo las robustas (buscan por rol ARIA, placeholder, label, aria-label). El planner ni siquiera puede usarlas.

### RC4. No hay verificación de objetivo antes de declarar éxito

`_summarize()` redacta un resumen positivo a partir de las **descripciones** de los pasos ("Be direct and positive"), sin comprobar ningún estado real (URL final, texto de la página, existencia del post). Éxito declarado = éxito narrado.

### RC5. (Secundario) `generate_fix` viola las reglas del propio sistema

`agent/error_handler.py:generate_fix()` sustituye pasos fallidos por código Python generado (`code_helper`/`generated_code`), justo lo que el planner prohíbe. Para acciones de navegador esto no tiene sentido (el código generado no tiene acceso a la sesión Playwright viva). Ruido que enmascara fallos.

### RC6. (Secundario) Enrutado impreciso en la capa de Gemini Live

"Súbeme un post a LinkedIn" puede acabar en la ruta A (una sola llamada `browser_control` con `go_to`) en vez de en `agent_task`, según el humor del modelo. Eso explica el "ni caso" / "solo abre LinkedIn": una llamada suelta a `go_to` y fin. La descripción de `agent_task` (main.py:854) no menciona tareas de navegador multi-paso.

## 4. Plan de trabajo

Fases ordenadas por relación impacto/esfuerzo. F1 y F2 son pequeñas y desbloquean honestidad del sistema; F3 es el cambio grande que hace viables tareas como publicar en LinkedIn; F4-F5 rematan.

---

### FASE 1 — Dejar de mentir: propagar fallos del navegador (pequeña, hacer primero)

**Objetivo:** que un paso de navegador fallido cuente como fallo, dispare `analyze_error`/`replan`, y que la tarea nunca se anuncie completada si sus pasos web fallaron.

**Cambios:**

1. `actions/browser_control.py`: añadir a `browser_control()` un mecanismo de resultado estructurado sin romper la ruta A. Opción recomendada: prefijar los fallos con un marcador estable, p. ej. devolver `"ERROR: <mensaje>"` en todos los caminos de fallo (`Element not found`, `Could not open`, `Click error`, `Type error`, `Could not find element`, `Could not find input`, `Browser error`, `timed out`). Mantener los mensajes actuales detrás del prefijo.
2. `agent/executor.py::_call_tool`, rama `browser_control`: si el resultado empieza por `ERROR:` (o coincide con la lista de prefijos de fallo si se prefiere no tocar browser_control), lanzar `RuntimeError(result)`. Con eso el bucle de recuperación existente (analyze_error → retry/skip/replan/abort) empieza a funcionar para web.
3. `main.py` ruta A (línea ~1537): opcionalmente, si el resultado empieza por `ERROR:`, devolvérselo a Gemini tal cual (ya lo hace) — Gemini Live puede reintentar por sí mismo. No hace falta más.

**Criterio de aceptación:** pedir "haz click en el botón inexistente-xyz de esta página" termina en replan/abort con mensaje honesto, nunca en "All done, sir".

**Riesgo:** `analyze_error` con flash-lite puede decidir `skip` para pasos críticos no marcados `critical`. Mitigación: en el executor, tratar los pasos de `browser_control` de un goal cuyo objetivo es una acción web como `critical=True` por defecto (o simplemente marcar critical en el planner prompt para acciones web).

---

### FASE 2 — Verificación de objetivo antes de resumir (pequeña)

**Objetivo:** `_summarize()` no puede declarar éxito sin evidencia.

**Cambios en `agent/executor.py`:**

1. Antes de `_summarize`, si el plan contiene pasos `browser_control`, ejecutar una verificación: `get_url` + `get_text` de la sesión activa y pasar ambos a un modelo con el goal: *"Goal: X. URL final: Y. Texto visible (primeros 3000 chars): Z. ¿Se cumplió el objetivo? Responde JSON {"achieved": bool, "evidence": str}"*.
2. Si `achieved=false`: tratar como fallo → replan (si quedan intentos) o mensaje honesto: "No he podido completar X; me quedé en <estado>".
3. Cambiar el prompt de `_summarize` para que no exija positividad ("Be direct and positive" fuera); debe resumir lo que de verdad pasó.

**Criterio de aceptación:** si el post no se publicó, el mensaje final lo dice explícitamente.

---

### FASE 3 — Bucle reactivo observar→decidir→actuar para tareas web (el cambio grande)

**Objetivo:** sustituir el plan ciego por un agente de navegador iterativo. Es la única vía realista para "publica un post en LinkedIn".

**Diseño propuesto — nuevo módulo `agent/browser_agent.py`:**

```
def run_browser_goal(goal: str, browser: str | None, speak, cancel_flag, max_steps: int = 25) -> str
```

Bucle:

1. **Observar**: obtener estado de la página. Base: `get_url` + snapshot de accesibilidad. Playwright ofrece `page.accessibility.snapshot()` (árbol ARIA con roles y nombres) — MUY superior a `inner_text` para decidir clicks. Añadir en `browser_control.py` una acción nueva `get_state` que devuelva `{url, title, aria_snapshot_recortado, texto_visible_recortado}`. Recortar a ~6-8k chars priorizando elementos interactivos (button, link, textbox, combobox con sus nombres accesibles).
2. **Decidir**: llamar a un modelo (ver "modelo" abajo) con: goal, historial de acciones ya ejecutadas con sus resultados, y el estado actual. El modelo devuelve UNA acción JSON: `{action, args, reasoning, done: bool, result_summary}`. Acciones permitidas = las de `browser_control` (priorizar `smart_click`/`smart_type`/`press`/`scroll`/`go_to`) + `finish(success, summary)`.
3. **Actuar**: ejecutar vía `browser_control(parameters=...)`. Registrar `(acción, resultado)` en el historial. Los strings `ERROR:` de F1 entran al historial como feedback — el modelo decide la alternativa en la siguiente iteración (esto sustituye al replan ciego para web).
4. **Parar**: cuando el modelo emite `done=true`, ejecutar la verificación de F2 antes de aceptar `success=true`. Límite duro `max_steps` y `cancel_flag` respetado en cada iteración.

**Modelo para el bucle:** empezar con `gemini-2.5-flash` (no lite; el lite no da para razonar sobre el snapshot). Interfaz vía `actions/genai_client.get_model` como el resto del proyecto. Dejar el nombre del modelo en una constante arriba del módulo para poder cambiarlo fácil.

**Integración:**

- `agent/planner.py`: añadir al `PLANNER_PROMPT` una pseudo-herramienta `browser_goal { goal: string, browser?: string }` con la instrucción: *"para CUALQUIER tarea que requiera interactuar con una página web (formularios, publicar, iniciar sesión, comprar, descargar desde web), usa UN SOLO paso browser_goal con el objetivo completo; NO generes pasos click/type individuales"*. Mantener `browser_control` suelto solo para "abre X".
- `agent/executor.py::_call_tool`: rama nueva `browser_goal` → `run_browser_goal(...)`.
- Seguridad: el bucle NO debe rellenar credenciales ni datos de pago. Si la página pide login que el perfil real no cubre, terminar con `finish(success=false, "necesito que inicies sesión manualmente")` y avisar por `speak`.

**Criterio de aceptación (test manual):**
1. "Busca en Google el tiempo en Barcelona y dime el primer resultado" — completa sin intervención.
2. "Abre LinkedIn y publica un post que diga 'Hola mundo'" — con sesión iniciada en el perfil real de Chrome: abre feed, encuentra el composer ("Crear publicación"/"Start a post"), escribe, pulsa "Publicar", verifica y reporta. Si algo falla, lo dice.
3. Cancelación desde la UI a mitad de tarea detiene el bucle en <5 s.

**Notas técnicas:**
- Reutilizar la sesión/registro existente (`_registry`) — no abrir navegador nuevo por iteración.
- `smart_click`/`smart_type` ya buscan por rol/label/placeholder/aria-label con fallbacks; el modelo debe pasar el **nombre accesible visible** (p. ej. "Crear publicación") extraído del snapshot, no selectores CSS.
- LinkedIn en español: los nombres accesibles vendrán en ES si la cuenta está en ES. El snapshot resuelve esto solo (el modelo ve los nombres reales).
- Timeout por acción ya existe (60 s en `sess.run`). Presupuesto total del bucle: ~5 min o `max_steps`.

---

### FASE 4 — Enrutado: que "sube un post a LinkedIn" llegue al agente (pequeña)

**Cambios en `main.py`:**

1. Descripción de `agent_task` (línea ~854): añadir explícitamente *"Use ALSO for any multi-step web/browser task: posting on social media, filling web forms, buying, downloading from websites. Examples: 'post X on LinkedIn', 'fill in this web form'."*
2. Descripción de la function-call `browser_control` en `main.py`: acotarla a acciones sueltas: *"Single browser actions only (open a site, search, scroll). For multi-step web tasks (posting, forms, login flows) use agent_task."*
3. Revisar `core/prompt.txt` por si hay instrucciones que empujen los pedidos web a otra herramienta (hacer grep de "browser", "web", "linkedin").

**Criterio de aceptación:** decir "súbeme un post a LinkedIn que diga hola" dispara `agent_task` (visible en logs `[Executor] 🎯 Goal:`), no una llamada suelta `browser_control go_to`.

---

### FASE 5 — Limpieza de recuperación de errores (opcional, tras F1-F4)

1. `agent/error_handler.py::generate_fix`: no generar código Python para pasos de `browser_control`/`browser_goal`; para web el "fix" es siempre replan o el propio bucle reactivo. Limitar `generate_fix` a herramientas de fichero/sistema.
2. `agent/executor.py`: al agotar `MAX_REPLAN_ATTEMPTS`, incluir en el mensaje final QUÉ paso falló y por qué (hoy: "Task failed after N replan attempts" sin detalle).
3. Logs: `_log()` en browser_control trunca a 80 chars; subir a 200 para debugging o loguear completo a fichero.

---

## 5. Orden de ejecución recomendado y estimación

| Fase | Alcance | Ficheros | Tamaño |
|------|---------|----------|--------|
| F1 | Propagar fallos | `actions/browser_control.py`, `agent/executor.py` | ~30 líneas |
| F2 | Verificación pre-resumen | `agent/executor.py` | ~60 líneas |
| F4 | Enrutado | `main.py`, `core/prompt.txt` | ~10 líneas |
| F3 | Bucle reactivo | nuevo `agent/browser_agent.py`, `actions/browser_control.py` (`get_state`), `agent/planner.py`, `agent/executor.py` | ~250-350 líneas |
| F5 | Limpieza | `agent/error_handler.py`, `agent/executor.py` | ~40 líneas |

F1+F2+F4 se pueden hacer en una sola sesión y ya cambian el comportamiento visible (deja de mentir, enruta bien). F3 en sesión aparte.

## 6. Cómo probar (entorno)

- Ejecutar la app: `py main.py` (el alias de Python en esta máquina es `py`, no `python`).
- Los logs del navegador salen por consola con prefijo `[Browser]`, los del agente `[Executor]`, `[Planner]`, `[ErrorHandler]`.
- Playwright ya está instalado (import directo en `browser_control.py`). Usa el perfil real de Chrome del usuario (`%LOCALAPPDATA%/Google/Chrome/User Data`) — **Chrome debe estar cerrado** o el `launch_persistent_context` sobre el perfil real fallará y caerá al perfil `~/.jarvis_profiles/chrome` (sin sesión de LinkedIn). Este detalle puede ser por sí solo otra causa del "abre pero no está logueado": verificar en logs cuál perfil cargó (`✅ Real profile found` vs `⚠️ Real profile not found`/`Retrying with JARVIS profile`).
- Tests existentes relacionados: `tests/test_browser_control.py`.
- No hay CI; validación manual con los criterios de aceptación de cada fase.

## 7. Fuera de alcance (decidido)

- API oficial de LinkedIn (requiere app OAuth aprobada; overkill para uso personal).
- Visión/screenshots como percepción primaria (el snapshot ARIA es más barato y suficiente; screenshots solo como fallback futuro).
- Rehacer el planner general para tareas no-web.
