# PLAN: modo Anime de primera clase — plan de trabajo por fases

Compañero de [`NOTE_anime_shiru.md`](NOTE_anime_shiru.md), que tiene la
**investigación** (qué es Shiru, comparativa cara a cara, el problema de
licencias, la deuda técnica de las tres capas de metadatos, y los hallazgos de
la segunda ronda de sondas en su §9). Lee aquel primero.

**Objetivo:** dejar de tener "Películas con otros posters" y tener un modo
Anime real — que recuerde por dónde vas, encadene episodios solo, elija el
torrent bueno sin preguntar, y **que en la WebUI se vea con subtítulos**.

---

## Decisiones cerradas (sesión de planificación)

| # | Decisión | Quién |
|---|---|---|
| 1 | **AniList: sí, pero al final y solo como catálogo.** Ver §D1 — `ani.zip` nos da el esqueleto de mapeos sin AniList, así que AniList deja de ser un cimiento y pasa a ser una mejora separable de descubrimiento. | Yo (delegada) |
| 2 | **Prioridad WebUI.** El panel Qt va detrás, consumiendo los mismos módulos. | Tú |
| 3 | **Producto antes que multi-fuente, pero acopladas.** Ver §D2 — al filtrar por códec en la web, una sola fuente deja de bastar, así que la multi-fuente sube de prioridad respecto al plan original. | Yo (delegada) |
| 4 | **Castellano NO es fuente ni criterio primario.** Se prioriza audio japonés + calidad de subtítulos. El castellano queda como etiqueta y filtro opcional, sin bonificación en el ranking. | Tú |
| 5 | **Reproductor web: las dos cosas, por fases.** Primero VLC-como-motor en el PC, después reproductor web real para móvil/LAN. | Tú |
| 6 | **El calendario de emisión va dentro del panel Calendario existente**, no en una vista aparte. Ver §D4. | Tú (+ yo el cómo) |
| 7 | **La exportación a Google Calendar (5.2b) va activada desde el inicio**, con alcance `watching` + ventana deslizante de 60 días. La tabla local sigue siendo la fuente de verdad; Google es un espejo. Ver §D5. | Tú |

### D1 — Por qué AniList baja de prioridad

Sondeado en vivo durante la planificación:

```
GET https://api.ani.zip/mappings?anilist_id=154587   → 200
GET https://api.ani.zip/mappings?kitsu_id=46474      → 200   ← funciona igual
GET https://api.ani.zip/mappings?mal_id=52991        → 200   ← y por aquí también
```

Y devuelve, **en una sola llamada**, todo esto:

- `mappings`: `kitsu_id`, `mal_id`, `anilist_id`, `anidb_id`, `thetvdb_id`,
  `imdb_id`, `themoviedb_id`, `livechart_id`, `anisearch_id`, `animeplanet_id`
- `episodes`: 53 entradas para Frieren, cada una con `seasonNumber`,
  `episodeNumber`, **`absoluteEpisodeNumber`**, `title` en 7 idiomas,
  `airDateUtc`, `runtime`, `overview`, `image` (still de TheTVDB), `anidbEid`

Es decir: `ani.zip` resuelve **de golpe** tres cosas que en el plan original
estaban repartidas entre tres fases distintas —

1. metadatos de episodio mejores que los del addon Kitsu (imágenes de TheTVDB,
   sinopsis, duración real),
2. el `mal_id` que hace falta para AniSkip (Kitsu no lo da),
3. el mapeo temporada/episodio ↔ **episodio absoluto**, que es el problema
   difícil de las fuentes que no son Torrentio.

Y como acepta `kitsu_id`, se enchufa a nuestro catálogo actual **sin tocar
nada**. Por eso AniList deja de ser cimiento: lo único que aporta encima es
mejor descubrimiento (filtros, relaciones, recomendaciones). Eso vale, pero es
separable y va al final.

### D2 — Por qué la multi-fuente sube de prioridad

En la Fase 3 el reproductor web va a descartar releases Hi10P, HEVC, AC3 y DTS
porque ningún navegador los decodifica. Con **una sola fuente** (Torrentio),
después de ese filtro te puedes quedar sin ningún candidato reproducible para
un anime que no sea de la temporada actual. Con tres fuentes, casi siempre
queda uno.

O sea: la decisión 5 (reproductor web) hace la multi-fuente **necesaria**, no
opcional. Van juntas.

### D4 — Cómo encaja el calendario de anime en el que ya hay

El panel Calendario es **100 % Google Calendar sobre `primary`**, sin ningún
almacén local de eventos (`actions/google_calendar.py`, y ni `calendar.py` ni
`Calendar.svelte` pasan `calendar_id`). Así que "sincronizar" admite dos
lecturas y no son lo mismo:

1. **Pintar los estrenos en el panel** → capa local fusionada en la vista. Cero
   riesgo, instantáneo, reversible, sin tocar tu cuenta.
2. **Escribir los estrenos en tu Google Calendar** → se ven en el móvil sin
   Jarvis, pero son cientos de eventos en una cuenta real, con series que
   abandonas y episodios que se retrasan.

Voy con **1 por defecto y 2 como opción apagada**. El motivo es que el
calendario de anime es volátil por naturaleza (retrasos, series que dejas,
listas que cambian) y eso convierte la opción 2 en un flujo constante de
create/update/delete sobre tu cuenta. Si resulta que lo que querías era verlo
en el móvil, 5.2b lo cubre y está diseñado para poder apagarse limpio.

### D5 — Alcance de la exportación a Google Calendar

Va activada por defecto, pero **acotada**, porque el alcance es lo que decide si
esto es útil o es un vertedero:

| Alcance | Volumen | |
|---|---|---|
| `watching` + ventana de 60 días | ~10 series × ~8 eps = **80 eventos** | ✅ elegido |
| `watching`, cour entero | ~120 eventos | aceptable |
| + `plan_to_watch` | 100-300 títulos → **miles** | ❌ descartado |

`plan_to_watch` es una lista de deseos, no de seguimiento. La ventana de 60 días
se amplía sola conforme avanza el tiempo.

Guardarraíles que se mantienen **aunque el ajuste venga activado**:

1. Calendario secundario dedicado "Anime — Jarvis", **nunca `primary`**.
2. **Una confirmación en la primera sincronización**, con el número exacto de
   eventos. Después ya va solo.
3. Escrituras por lotes y con freno (límites por minuto de la API de Google).
4. Reconciliación por `kitsu_id:episodio` en `extendedProperties.private`.

### D3 — Consecuencia de la decisión 4

Se cae del ranking la bonificación `+25 audio castellano` que llevaba el plan
original. El nuevo criterio prioriza **audio japonés + subtítulos disponibles +
salud del swarm + códec reproducible**. El castellano se detecta y se muestra
como etiqueta, y hay un filtro opcional, pero no puntúa.

---

## Restricción no negociable

Shiru es **GPL-3.0**, Jarvis es **CC BY-NC 4.0**. **No se copia código.** Se
reimplementan conceptos y se consumen APIs públicas (`api.ani.zip`,
`api.aniskip.com`, AniList GraphQL), que es legítimo. Dependencias de terceros
verificadas y compatibles:

| Dependencia | Versión | Licencia | Para qué |
|---|---|---|---|
| `anitopy` | 2.1.1 (PyPI) | **MPL-2.0** ✅ | parsear nombres de release (Fase 4) |
| `JASSUB` | 1.8.8 (npm) | MIT (envuelve libass, ISC) ✅ | render ASS en la web (Fase 3.4) |
| `ffmpeg` | del sistema | — | extraer pistas de subtítulos del MKV |

`ffmpeg` ya está resuelto en el repo: `actions/audio_tags.py:88`
(`ffmpeg_path()`, con respaldo a mpv). Reutilizar eso, no escribir otro
localizador.

---

## Estado de partida (medido, no supuesto)

| Hecho | Dónde |
|---|---|
| **El reproductor web entero es `<video src={streamUrl} controls autoplay>`** | [`StreamingBrowser.svelte:244`](webui/src/lib/StreamingBrowser.svelte:244) |
| → sin subtítulos, sin pistas de audio, sin reanudar, sin progreso | ídem |
| La WebUI usaba TMDB→IMDb→Torrentio: 2 fuentes para One Piece 1044 frente a 22 por Kitsu, y en series con temporadas partidas devolvía otro episodio | [`webui_server.py:1227-1265`](actions/webui_server.py:1227) |
| `AnimeModePanel` es una subclase de `MoviesModePanel` | [`movies.py:3488`](ui/panels/movies.py:3488) |
| No hay ninguna persistencia de progreso de visionado | grep `progress\|resume\|watched` → solo el spinbox de MAL |
| `actions/anime_search.py` está muerto salvo el flag SFW | [`webui_server.py:1419`](actions/webui_server.py:1419) |
| El timeout de metadata es 125 s; la realidad medida, 45-65 s | [`vlc_player.py:26`](actions/vlc_player.py:26) |
| El diálogo de torrents no comprueba salud real del swarm | [`movies.py:822`](ui/panels/movies.py:822) |
| Ya tenemos DoH, trackers curados, daemon caliente, retiming de subs | `doh.py`, `trackers.py`, `daemon.mjs`, `subtitle_retime.py` |

---

## Principios de diseño

1. **Un solo módulo de dominio, dos frontends.** Todo lo nuevo va a
   `actions/anime/` y lo consumen igual la WebUI y el panel Qt. Se acabaron las
   dos rutas distintas para lo mismo.
2. **La WebUI marca el ritmo**, pero nada se diseña de forma que el panel Qt no
   lo pueda reutilizar.
3. **VLC no se toca** como motor en el PC. Es nuestra mayor ventaja sobre Shiru.
4. **Nada de JS remoto.** Las fuentes son módulos Python locales, no
   extensiones descargadas.
5. **Fases entregables.** Se puede parar después de cualquiera y la app queda
   mejor que antes.

---

## Fase 0 — Cimientos y limpieza ✅ **HECHA** (2026-09-03)

> Resultado medido al terminarla:
> * `/api/anime/torrents` de One Piece ep 1044: **2 fuentes → 22**.
> * Parrilla de tendencias en frío 0,20 s → **0,001 s** desde caché.
> * Lista de episodios en la WebUI: no existía (un `<input type=number>`) →
>   1180 episodios con título en español, still de TheTVDB, fecha y duración.
> * Suite: **809 pasan**. El único fallo (`test_libretro`) ya fallaba antes de
>   tocar nada — verificado con `git stash`.
> * `actions/anime_search.py` borrado (240 líneas muertas) y las tres funciones
>   de anime de `movie_search.py` retiradas.



Sin esto, cada fase siguiente hay que hacerla dos veces.

### 0.1 Unificar la ruta de metadatos

- Crear `actions/anime/` y mover `actions/kitsu.py` → `actions/anime/catalog.py`
  (con shim de reexportación mientras queden imports viejos).
- Migrar `/api/anime/search`, `/api/anime/trending` y `/api/anime/torrents`
  ([`webui_server.py:1227-1265`](actions/webui_server.py:1227)) a la **misma
  ruta Kitsu → `torrentio.search_by_id("kitsu:<id>:<ep>")`** que usa el panel Qt.
- Añadir `GET /api/anime/episodes?kitsu_id=…`.
- Borrar `movie_search.search_anime` / `get_trending_anime` / `get_airing_anime`.
- Mover `get_sfw`/`set_sfw` a `actions/anime/prefs.py` y **borrar
  `actions/anime_search.py`**.

**Aceptación:** buscar "Frieren" en WebUI y en Qt da la misma lista, y pedir el
episodio 12 reproduce el 12 en ambos (hoy la WebUI falla en cuanto hay
temporadas).

### 0.2 Adoptar `ani.zip` como capa de mapeo y episodios

Nuevo `actions/anime/mappings.py`:

```python
def mappings(*, kitsu_id=None, mal_id=None, anilist_id=None) -> Mapping
    # -> kitsu_id, mal_id, anilist_id, anidb_id, tvdb_id, imdb_id
def episodes(kitsu_id) -> list[Episode]
    # number, absolute, season, title(es/en/ja), aired, runtime,
    # overview, image, anidb_eid
```

Sustituye a `kitsu.get_episodes()` como fuente principal (Kitsu queda de
respaldo si `ani.zip` no tiene la serie). Esto ya mejora visiblemente la lista
de episodios: imágenes de TheTVDB y duración real.

### 0.3 Caché de consultas

Nuevo `actions/anime/cache.py`: caché JSON en disco bajo `memory_path()`, TTL
por tipo. Es la versión pequeña y honesta de `cache.js` de Shiru.

```
catalog/trending      6 h        meta/<kitsu_id>      24 h
catalog/airing        1 h        episodes/<kitsu_id>   6 h  (1 h si está en emisión)
catalog/genre/<g>    12 h        mappings/<id>        30 d  (los ids no cambian)
```

Patrón: pintar desde caché al instante, refrescar en background, avisar si
cambió (SSE en la web, señal Qt en el panel).

**Aceptación:** abrir el panel Anime dos veces seguidas — la segunda pinta sin
ninguna petición de red.

---

## Fase 1 — Progreso y continuidad ✅ **HECHA** (2026-09-03)

> Resultado medido al terminarla:
> * `actions/anime/progress.py`: SQLite con WAL, `media_key` genérico
>   (`"kitsu:<id>"`, reutilizable por Películas/Series sin migración). 16 tests
>   unitarios contra una DB temporal aislada — nunca contra
>   `~/.jarvis/memory/`.
> * 6 endpoints nuevos (`/api/anime/progress` GET/POST, `/complete`,
>   `/continue`, `/remove`, `/meta`) + 2 de ajustes
>   (`anime_autoplay`, `anime_autocomplete`). 13 tests nuevos, todos con mocks
>   — no tocan el estado real.
> * WebUI: fila "Continuar viendo" con miniatura + barra de progreso + quitar;
>   punto de estado (visto/a medias) en cada fila de episodio; posición
>   guardada cada 10 s; auto-completado a partir del 85 % con sincronización a
>   MAL en segundo plano (nunca bloquea, nunca revienta el guardado local si
>   MAL falla); auto-siguiente con aviso cancelable de 8 s y **prebuffer de
>   fuentes al 80 %** para que el salto entre episodios no se coma los 45-65 s
>   de metadata.
> * Suite completa: **841 pasan** (el único fallo, `test_libretro`, es
>   preexistente — verificado con `git stash` en la Fase 0).

**Ampliación de alcance respecto al plan original:** para que "auto-next"
significara de verdad *encadena solo* y no *encadena y te pregunta qué
torrent*, esta fase incluyó una **auto-selección mínima de torrent** (el de
más seeders — la lista que sirve `torrentio.search_by_id` ya viene ordenada
así) tanto para "Continuar viendo" como para el salto automático de episodio.
El plan original reservaba la auto-selección para la Fase 4 (con salud real
del swarm vía scrape BEP15). Esa fase sigue haciendo falta — esto es
deliberadamente el mínimo que hace el auto-next honesto, no un sustituto.



Ninguna de estas cuatro cosas existe hoy, ninguna depende de servicios nuevos,
y son lo que separa "un buscador de torrents" de "una app de anime".

### 1.1 Historial de visionado

Nuevo `actions/anime/progress.py`, SQLite en `memory_path()`:

```python
WatchEntry(media_key, episode, position_s, duration_s,
           completed, updated_at, title, poster_url)
```

`save()`, `get()`, `continue_watching(limit)`,
`episode_states(media_key) -> {ep: 'seen'|'partial'|'new'}`.

> **Diseño:** `media_key` genérico (`kitsu:46474`) en vez de `kitsu_id`, para
> que Películas y Series lo reutilicen después sin reescribirlo. Coste marginal
> hoy, ahorro grande mañana.

Endpoints: `GET/POST /api/anime/progress`, `GET /api/anime/continue`.
El escritor es el reproductor: cada 10 s y al parar.

### 1.2 "Continuar viendo"

- **WebUI:** fila al principio del panel Anime, alimentada por
  `/api/anime/continue`. Al pulsar, reanuda en `position_s`.
- **Qt:** el mismo `_ContentRail` con un chip nuevo en `_chip_defs()`.

### 1.3 Estado por episodio en la lista

Punto de estado (visto / a medias con barra / nuevo) en cada fila de episodio,
en ambos frontends. Datos de `episode_states()`.

### 1.4 Auto-siguiente y auto-marcar visto

- **Auto-next:** al terminar, si existe `ep+1`, lanzarlo con aviso cancelable
  de 8 s. **La búsqueda del torrent del `ep+1` arranca al 80 %, no al 100 %** —
  si no, se come los 45-65 s de metadata y se ve un corte feo.
- **Auto-completado:** al pasar el **85 %**, marcar `completed` en local y
  empujar `update_status(mal_id, num_watched=ep)` a MAL si hay sesión. Hoy eso
  es un combo y un botón "Guardar".
- Interruptores en Ajustes: `anime_autoplay`, `anime_autocomplete`.

**Aceptación:** empezar Frieren ep 1, ver 20 min, cerrar Jarvis, reabrir →
aparece en "Continuar viendo" en el minuto 20. Al terminarlo, MAL marca 1/28
solo y arranca el ep 2.

---

## Fase 2 — El reproductor del PC: VLC como motor de la WebUI ✅ **HECHA** (2026-09-03)

> Resultado medido al terminarla:
> * `actions/anime/playback.py`: libVLC controlado por HTTP, sin embeber en
>   pywebview (que no expone handle nativo) — VLC abre **su propia ventana**
>   (`player.set_fullscreen()`, sin `set_hwnd`/`set_xwindow`) y la WebUI pasa a
>   ser mando a distancia. 29 tests contra un doble de VLC (python-vlc está
>   fijado a `win32` en `requirements.txt`; instalé `python-vlc` en el venv
>   **solo para verificación manual** contra libVLC real — confirmado el ciclo
>   completo play/pause/seek/volumen/fullscreen/pistas — y lo desinstalé al
>   terminar; no toca `requirements.txt`).
> * 17 endpoints `/api/player/*` nuevos, con `/api/player/recommend` decidiendo
>   VLC vs. navegador por `request.remote_addr` (2.3).
> * Subtítulos externos (`actions/stremio_subs.py`) enganchados en segundo
>   plano, best-effort — nunca bloquean ni rompen la reproducción si fallan.
> * WebUI: selector "Reproducir en: este PC (VLC) / este navegador" en la
>   ficha; barra de transporte completa (posición, volumen, pistas de audio,
>   pistas de subtítulos, retardo de subs, pantalla completa) sustituyendo al
>   `<video>` cuando el motor es VLC.
> * Suite completa: **886 pasan** (4 ejecuciones seguidas, 0 intermitencias).
>   El único fallo, `test_libretro`, es preexistente.

**Bug real encontrado y corregido antes de cerrar la fase:** el primer diseño
de "episodio terminado" inferría el fin a partir de `is_playing() == false` +
posición ≥ 98 % de la duración. Eso es indistinguible de que **el usuario
pause manualmente** en los últimos segundos del episodio — habría disparado
auto-completado y auto-siguiente sin que el episodio hubiera terminado de
verdad. Solución: usar el estado real de libVLC (`vlc.State.Ended`), que sí
distingue pausa de fin. Quedó una prueba de regresión explícita para este caso
exacto (`test_ended_is_false_on_an_ordinary_pause_near_the_end`).

**Segundo bug, de mis propias pruebas:** dos tests de `test_webui_server.py`
sondeaban un hilo en segundo plano con un `time.sleep` en bucle y un límite
fijo de 500 ms; bajo la suite completa (más contención de CPU que en
aislamiento) fallaban de forma intermitente — reproducido y confirmado
comparando ejecuciones sueltas (100 % estables) contra la suite completa
(fallo ~1 de cada 2). Sustituidos por un `threading.Event` que el propio mock
señaliza, determinista en vez de basado en tiempo.



Decisión 5, primera mitad. **El salto de calidad más grande por hora de
trabajo de todo el plan**, porque hoy la WebUI reproduce anime sin subtítulos.

La WebUI corre en una ventana pywebview en la propia máquina
(`actions/webui_host.py`), así que puede mandar sobre un VLC local igual que el
panel Qt.

### 2.1 Modo "motor VLC"

- Nuevo `actions/anime/playback.py` que envuelve `vlc_player` +  `_VLCBackend`
  y expone una superficie de mando por HTTP:
  `play / pause / seek / volume / audio_tracks / subtitle_tracks /
  set_audio_track / set_subtitle_track / spu_delay / status`.
- Endpoints `POST /api/player/*` + estado por SSE (ya existe el bus,
  `webui_server.py:78-100`).
- La WebUI, en este modo, **no monta `<video>`**: monta una barra de transporte
  completa (posición, pistas de audio, pistas de subtítulos, retardo de subs) y
  VLC pinta a pantalla completa.

### 2.2 Reutilizar lo que ya tenemos

Enganchar en este modo lo que hoy solo ve el panel Qt:
`actions/stremio_subs.py` (subtítulos externos por id Kitsu) y
`actions/subtitle_retime.py` (corrección de deriva por FPS).

### 2.3 Selector de destino

Un control en el panel Anime: **"Reproducir en: este PC (VLC) / este navegador"**.
Detección por defecto: si la petición viene de `127.0.0.1` → VLC; si viene de
otra IP de la LAN → navegador (Fase 3).

**Aceptación:** desde la ventana WebUI del PC, dar a un episodio de un fansub
con subtítulos ASS → se ve a pantalla completa, con los subtítulos estilados, y
se puede cambiar de pista de audio y de subtítulos desde la WebUI.

---

## Fase 3 — El reproductor del móvil: reproductor web real (2,5-3 días)

Decisión 5, segunda mitad. Aquí sí seguimos el camino de Shiru, porque el
problema es literalmente el suyo.

### 3.1 Conocimiento de códecs

Nuevo `actions/anime/codecs.py` + sonda en cliente. El navegador declara qué
puede decodificar (`video.canPlayType`) y lo manda al backend; el backend
etiqueta cada torrent como reproducible o no.

Reglas para anime, por orden de importancia:

| Marca en el nombre del release | Efecto en el navegador |
|---|---|
| `Hi10P`, `10-bit`, `10bit` | ❌ **ningún navegador lo decodifica** — es el caso frecuente en anime antiguo |
| `HEVC`, `x265`, `H.265` | ⚠️ solo con decodificador HW de la plataforma |
| `AC3`, `AC-3`, `DTS`, `TrueHD`, `FLAC` | ❌ audio no soportado |
| `x264`, `H.264` 8-bit + `AAC` | ✅ el caso bueno (SubsPlease, Erai-raws actuales) |

### 3.2 Subtítulos externos como WebVTT (la parte barata)

`actions/stremio_subs.py` ya baja `.srt` por id Kitsu. Convertir SRT→WebVTT es
~30 líneas de Python sin dependencias. Servirlos en
`GET /api/anime/subtitles?kitsu_id=…&ep=…&lang=es` y montarlos como
`<track kind="subtitles">`.

**Esto solo ya elimina el "anime sin subtítulos" en el 80 % de los casos.**
Hacerlo primero dentro de la fase.

### 3.3 Subtítulos embebidos del MKV

`ffmpeg -i <url del daemon> -map 0:s:<n> -f webvtt -` en streaming, servido por
un endpoint. Reutilizar `audio_tags.ffmpeg_path()`. Sirve para los releases que
traen los subs dentro y no hay `.srt` externo.

### 3.4 Render ASS con JASSUB (la parte cara — **solo si hace falta**)

Los fansubs con karaoke y carteles se ven mal como WebVTT plano. JASSUB (MIT,
libass en WASM) los renderiza bien. Es la pieza más pesada de la fase.

**Puerta de decisión:** hacer 3.1→3.3 primero, usarlo una semana, y **solo
entonces** decidir si 3.4 merece la pena. Puede que no.

### 3.5 Barra de reproducción propia

Sustituir `controls` nativos: posición, volumen, pistas, subtítulos, siguiente
episodio, y escritura de progreso (Fase 1) cada 10 s.

**Aceptación:** desde el móvil en la LAN, ver el episodio 1 de un anime de la
temporada actual con subtítulos en español y que quede guardado el progreso.

---

## Fase 4 — Fuentes y velocidad (2,5-3 días)

Ahora es cuando la multi-fuente hace falta de verdad (§D2): con el filtro de
códec de la Fase 3, una sola fuente se queda corta.

### 4.1 Salud real del swarm

Nuevo `actions/anime/health.py`: scrape BEP15 (UDP) contra los 13 trackers de
`actions/trackers.py`, en paralelo, timeout 3 s, por lotes de infohashes.

Los listados ordenan por seeders **reales** y marcan en rojo los muertos. Hoy
puedes elegir un torrent con 0 peers vivos y esperar 125 s para enterarte. Es
lo más barato del plan y lo que más frustración quita.

*Respaldo:* si el scrape UDP está bloqueado en la red, caer a los seeders de
Torrentio sin ruido.

### 4.2 Contrato de fuente y fuentes nuevas

`actions/anime/sources/base.py`:

```python
@dataclass
class AnimeQuery:
    kitsu_id: str; mal_id: int; anilist_id: int; anidb_id: int
    titles: list[str]            # romaji, inglés, sinónimos
    season: int; episode: int
    absolute_episode: int        # de ani.zip (Fase 0.2)
    total_episodes: int
    want_batch: bool
    playable_only: bool          # filtro de códec de la Fase 3.1

class AnimeSource(Protocol):
    id: str; name: str; enabled: bool
    def query(self, q: AnimeQuery) -> list[TorrentResult]: ...
```

| Fuente | Cómo | Estado |
|---|---|---|
| `torrentio.py` | ya existe, envolver en el contrato | ya |
| `animetosho.py` | API JSON con **mapeos AniDB por episodio** — el match más limpio después de Torrentio | alta |
| `nyaa_rss.py` | RSS público → magnet (el feed trae `infoHash`) | alta |
| `seadex.py` | curaduría de "mejor release" por serie; ranking, no indexador | media |

Consulta en paralelo (`ThreadPoolExecutor`), dedup por infohash, fusión de
metadatos.

### 4.3 Parseo de nombres de release

Nyaa devuelve títulos crudos (`[SubsPlease] Frieren - 12 (1080p) [A1B2C3D4].mkv`).
Usar **`anitopy` 2.1.1 (MPL-2.0)** — mismo linaje que el `anitomyscript` de
Shiru, licencia compatible, cero código copiado. El mapeo
temporada↔absoluto sale de `ani.zip` (Fase 0.2), que es justo lo que Shiru
resuelve con 701 líneas de heurística propia.

### 4.4 Auto-selección de torrent

Nuevo `actions/anime/picker.py`. Ranking **sin bonificación de castellano**
(decisión 4):

```
reproducible en el destino elegido      +60   ← decisivo en destino navegador
resolución preferida (1080p)            +40
seeders vivos (log-escalado)            +0..30
audio japonés + subtítulos disponibles  +25
grupo de release conocido               +15
batch con metadata ya cacheada          +20
tamaño anómalo (<150 MB o >4 GB/ep)     −30
0 seeders vivos                         −999
no reproducible en el destino           −999
```

Si el mejor supera el umbral → reproducir directo. Si no → diálogo.
Siempre un toast: *"Reproduciendo <release> · <N> seeds · [cambiar fuente]"*.

### 4.5 Caché de metadata de torrent y prebuffer

- En `daemon.mjs`: guardar el `.torrent` por infohash y precargarlo con
  `client.add(parsedTorrent)`. Elimina la espera de metadata en repeticiones y,
  sobre todo, en el auto-next dentro de un batch (**una temporada completa es
  un solo infohash para todos los episodios**).
- Al 80 % del episodio, priorizar las primeras piezas del `ep+1`.

**Aceptación:** desactivar Torrentio en Ajustes y seguir pudiendo ver el
episodio 1044 de One Piece (numeración absoluta, el caso difícil). Primer
frame en < 15 s sin diálogo. Auto-next dentro de un batch en < 3 s.

---

## Fase 5 — Producto (2-2,5 días)

### 5.1 Skip de opening/ending ✅ **HECHA** (2026-09-03)

> Resultado medido al terminarla (sondeado en vivo contra
> `api.aniskip.com` antes de escribir una línea):
> * Frieren ep 1 (mal_id 52991): `op` 3.2s-93.2s, `ed` 1460s-1560s. Un anime
>   sin datos devuelve **HTTP 404** con `{"found": false}` — no es un fallo de
>   red, hay que tratarlo como "sin datos" y no como error.
> * `actions/anime/skip.py`: cliente + caché de 7 días (son datos aportados
>   por la comunidad, casi estáticos una vez emitido el episodio; un 404
>   genuino **no** se cachea, para reintentar si se envían tiempos más tarde).
> * `/api/anime/skip?kitsu_id=&episode=&episode_length_s=` resuelve el
>   `mal_id` internamente vía `mappings.resolve_mal_id` — el llamador nunca
>   necesita saber que existe un MAL id.
> * WebUI: banner flotante "Saltar opening/final/resumen" en **ambos motores**
>   (navegador y VLC) — mismo código de detección de ventana
>   (`checkSkipWindow`), solo cambia cómo se ejecuta el salto (`videoEl.
>   currentTime` vs `/api/player/seek`). Las ventanas se piden una vez se
>   conoce la duración real del episodio (mejor precisión de match, ver
>   docstring de `skip.py`) y se recuerda si el usuario ha ignorado una
>   ventana concreta para no volver a ofrecérsela en ese visionado.
> * 13 tests para `skip.py` (paginación de tipos, 404, JSON corrupto, ventana
>   de duración cero descartada, caché) + 4 para el endpoint. Suite completa:
>   **903 pasan**, mismo único fallo preexistente de siempre.

Los capítulos embebidos del MKV (gratis en destino VLC vía
`libvlc_media_player_get_chapter_description`) quedan fuera de esta pasada —
es un complemento, no lo que hacía falta para que el botón funcionase, y
AniSkip ya cubre la mayoría de casos reales.

**Confirmado: la mejora más visible por hora de trabajo de todo el
documento** — un módulo, un endpoint, un banner, reutilizado sin cambios
entre los dos motores de reproducción.

## Pasada de pulido — primera prueba real en el navegador ✅ **HECHA** (2026-09-04)

Hasta este punto todo se había verificado con tests unitarios y `npm run build`,
nunca abriendo la app de verdad. El usuario avisó: *"no se deja ver nada en la
ui normal"*. Se levantó el servidor Flask real
(`actions.webui_server.start`) y se navegó con el navegador en vivo. Aparecieron
**cuatro fallos reales**, ninguno detectable por unit tests o por `npm run
build` — los cuatro exigían la app corriendo de verdad:

### Hallazgo 1 — La ficha de anime no se veía: colapso de flexbox

`.detail-hero` (el banner con póster/título/sinopsis) tenía `overflow: hidden`
sin `flex-shrink: 0`, dentro de `.detail`, que es `display: flex;
flex-direction: column`. Por especificación CSS, `overflow` distinto de
`visible` en un hijo flex hace que su **tamaño mínimo automático** sea `0` en
vez del tamaño de su contenido. Con una lista de episodios larga (siempre en
anime — One Piece son 1180), el contenido total de `.detail` supera la altura
visible, y flexbox reparte el "encogimiento" entre los hijos: los que no
tienen `overflow` (la lista de episodios) se resisten a encogerse, así que
**todo el hueco lo absorbe `.detail-hero`, colapsando a 0px de altura.**
Confirmado en vivo (`getComputedStyle(hero).height === "0px"`) antes de tocar
el fichero, y la corrección (`flex-shrink: 0`) verificada in-page antes de
aplicarla al código fuente.

**Preexistente a toda esta sesión** — verificado con `git show HEAD` contra
el CSS original, sin diferencias. Afectaba a Películas/Series también en
cuanto su contenido fuera lo bastante largo; en Anime era **determinista**
porque la lista de episodios siempre desborda.

### Hallazgo 2 — El mismo colapso, en `.torrent-list`

Mismo patrón exacto: `overflow-y: auto` sin `flex-shrink: 0`. Al pedir fuentes
para un episodio de One Piece, las 25 filas de resultados existían en el DOM
pero con `height: 0px` — invisibles. Encontrado auditando **todas** las
declaraciones `overflow` del fichero tras el primer hallazgo, no solo la que
saltó a la vista.

### Hallazgo 3 — Los resultados de búsqueda quedaban a 114 000px de scroll

Estructural, no de CSS: el bloque de "Fuentes encontradas" se pintaba
**después** de la lista de episodios completa en el marcado. Para One Piece
(1180 filas sin virtualizar) eso son más de cien mil píxeles de scroll antes
de ver ningún resultado — invisible en la práctica aunque el colapso de
flexbox no existiera. Reordenado: el bloque de fuentes ahora se pinta
inmediatamente después del selector de motor/botón "Buscar fuentes", antes de
"Episodios".

### Hallazgo 4 — El demonio WebTorrent llevaba roto desde el `npm install`

Al probar la reproducción real (no simulada), el daemon `webtorrent-stream`
moría al primer intento con:

```
TypeError [ERR_INVALID_ARG_TYPE]: ... Received undefined
    at Torrent._onTorrentId (webtorrent/lib/torrent.js:281)
```

Causa raíz: `actions/vendor/webtorrent-stream/package.json` fijaba
`"webtorrent": "^2.4.1"` sin `package-lock.json` versionado. Un `npm install`
limpio resuelve **la última 2.x** (2.8.5), y esa versión de `webtorrent` es
incompatible con la versión actual de su dependencia `parse-torrent` (v11.x):
`parse-torrent@11` devuelve para magnets los campos crudos del URI (`xt`,
`dn`, `tr`...) en vez del objeto normalizado `{infoHash, name, ...}` que
`webtorrent@2.8.5` espera, así que `parsedTorrent.infoHash` llega `undefined`
y revienta. **Nadie lo habría detectado sin reproducir un torrent de
verdad** — ni los tests ni el build lo ejercitan.

Fix: subir a `webtorrent@^3.0.21` (la misma línea mayor que usa Shiru,
investigado al principio de esta sesión — buena señal de que es la
combinación que el ecosistema espera hoy). Verificado end-to-end contra la
red real: metadata en 12s, arranque de un segundo torrent en el mismo demonio
caliente tras `stop_streaming()`, y reproducción de One Piece ep. 1 completa
en el navegador — vídeo, audio, salto de opening por AniSkip, y guardado de
progreso, todo correcto.

De paso: instalar las dependencias del demonio reveló que 5 paquetes nativos
(`bufferutil`, `utf-8-validate`, `utp-native`, `node-datachannel`, `ip-set`)
tienen sus scripts de instalación bloqueados por npm por defecto (protección
de cadena de suministro). Se aprobaron explícitamente vía `npm install-scripts
approve` y quedó registrado en `package.json` (`allowScripts`), para que una
instalación limpia en otra máquina no se quede a medias sin que nadie lo note.

### Hallazgo 5 (menor) — "Continuar viendo" no se refrescaba al volver

El `$effect` que carga la fila de inicio solo dependía de `active`/`kind`, así
que se ejecutaba una vez al montar el panel y nunca más. Volver de ver un
episodio no lo actualizaba — la fila se quedaba con el estado (vacío) del
primer render. Corregido añadiendo `view === 'browse'` a la condición, para
que el efecto reactivo de Svelte 5 lo re-dispare cada vez que se vuelve a la
pantalla de inicio.

**Nota sobre el propio proceso:** varias de mis pruebas manuales escribieron
en `~/.jarvis/memory/anime_progress.sqlite3`, el directorio real de datos del
usuario en esta máquina, en vez de un scratchpad. Detectado y limpiado dos
veces durante esta pasada (tres entradas de series que nunca vi, fruto de
clics de navegación durante las pruebas). Sin residuos al terminar — verificado
con una consulta directa a la base antes de cerrar.

## Pasada de pulido 2 — pantalla completa y subtítulos ✅ **HECHA** (2026-09-04)

El usuario probó la app real tras la primera pasada de pulido: *"la pantalla
completa y los subs no furulan"*. Dos hallazgos más, ninguno anticipado por el
plan original — el de pantalla completa en particular es una limitación real
de una librería de terceros, no un bug de nuestro código.

### Hallazgo 6 — La API nativa de pantalla completa no está conectada por `pywebview`

`video.requestFullscreen()` fallaba con `TypeError: Permissions check
failed` incluso con `document.fullscreenEnabled === true` y la Feature Policy
en `true`. Investigado hasta la causa raíz en el código fuente de
`pywebview` (instalado en el venv): **ni el backend GTK (Linux) ni el
backend WinForms/EdgeChromium (Windows) conectan la señal de fullscreen del
motor web embebido a su ventana nativa** — la propiedad
`webkit_settings.enable_fullscreen` nunca se activa en `gtk.py`, y el backend
de Windows tiene el mismo patrón (`toggle_fullscreen()` solo mueve la
ventana, nunca escucha el evento de fullscreen del contenido). Esto afecta a
las **dos** plataformas objetivo del proyecto, no solo a este entorno Linux
de desarrollo.

**No es arreglable editando nuestro propio código Python/Flask** — es una
limitación de cómo `pywebview` integra el motor web. Solución: dejar de
depender de la API nativa. Nuevo "pantalla completa" por CSS
(`position: fixed; inset: 0`) que expande el contenedor del reproductor
sobre todo el viewport, con `controlslist="nofullscreen"` para ocultar el
botón nativo (que fallaría igualmente) en los motores basados en Chromium.
Verificado en vivo: sidebar y cabecera desaparecen, el vídeo llena la
pantalla, los subtítulos se mantienen visibles, y "Salir de pantalla
completa" devuelve todo a su sitio.

El motor VLC no está afectado — su pantalla completa corre sobre la ventana
propia de libVLC, fuera de `pywebview` por completo.

### Hallazgo 7 — Los subtítulos no existían en el motor de navegador

Cierto: `actions/anime/playback.py` (motor VLC) descarga subtítulos externos
desde el principio de la Fase 2, pero el `<video>` del navegador nunca tuvo
ni `<track>` ni una fuente de subtítulos propia — jugaba en silencio salvo
que el release trajera subs incrustados. Esto era exactamente el hueco que
`NOTE_anime_shiru.md` §9.1 documentó y que la Fase 3 (§3.2) tenía planeado
resolver más adelante; se adelantó aquí al ser parte de lo reportado.

Nuevo `actions/anime/subtitles.py`: reutiliza `actions.stremio_subs`
(ya construido) para descargar el `.srt`, y lo convierte a WebVTT — la
única diferencia real de formato es el separador decimal de las marcas de
tiempo (coma → punto) y la cabecera `WEBVTT`, así que no hace falta ninguna
dependencia nueva. Nuevo endpoint `/api/anime/subtitles`, y un `<track
kind="subtitles" default>` en el `<video>` para `kind === 'anime'`.
Verificado con datos reales (One Piece ep. 1, subtítulo en español real de
OpenSubtitles) y en vivo en el navegador: los subtítulos se ven quemados
sobre el vídeo, con estilo nativo del navegador.

**Un susto que no era bug:** al probarlo en vivo la primera vez, el endpoint
devolvía 404 de forma consistente — pero funcionaba perfectamente contra el
mismo proceso servidor recién reiniciado, y contra un cliente de test Flask
en un proceso nuevo. Diagnosticado como agotamiento de algún recurso
(probablemente límite de peticiones del addon de OpenSubtitles) acumulado
tras horas de pruebas intensivas en el mismo proceso Python de larga
duración — no una condición de carrera ni un fallo del código. Confirmado
estable con 5 peticiones seguidas tras reiniciar el proceso.

Suite completa tras esta pasada: **915 tests pasan**, mismo único fallo
preexistente de siempre.

## Pasada de pulido 3 — dos botones de pantalla completa y episodios de otras temporadas ✅ **HECHA** (2026-09-04)

Dos reportes más del usuario, ambos con causa raíz real y ninguno anticipado.

### Hallazgo 8 — Dos superficies de controles a la vez

El arreglo de pantalla completa de la pasada anterior añadió un botón propio,
pero **dejó los controles nativos del `<video>` puestos** (`controls`), así
que quedaban dos barras de control coexistiendo: la nativa del navegador (con
su propio icono de pantalla completa, que no funciona por el Hallazgo 6) y la
fila de botones que añadí debajo. El usuario lo vio exactamente como lo que
era: dos botones de pantalla completa, solo uno funcionando.

Corrección real, no un parche: se quitaron los controles nativos por
completo y se construyó **una única barra de controles superpuesta sobre el
vídeo** (posición absoluta, degradado oscuro de fondo) — reproducir/pausa,
barra de progreso con tiempo, silenciar/volumen, pantalla completa y
"Detener y volver", todo en un solo sitio, dentro del propio reproductor
como pedía el usuario. El aviso de saltar opening y el de auto-siguiente se
reposicionaron como una franja flotante en la parte superior del vídeo para
no chocar con la barra inferior.

Verificado en vivo: reproducir/pausar, arrastrar la barra de progreso,
silenciar, entrar y salir de pantalla completa — todo dentro de un único
conjunto de controles, sin duplicados.

### Hallazgo 9 — Attack on Titan mezclaba temporadas: el problema está en Torrentio, no en nuestro catálogo

Comprobé primero lo obvio: los 5 `kitsu_id` de AoT (S1 a Final Season)
devuelven, cada uno, una lista de episodios limpia y correcta vía
`ani.zip` — nada mezclado ahí. El problema estaba un nivel más abajo, en
**los resultados de torrents**.

Para `kitsu:7442:1` (AoT S1, episodio 1), Torrentio devuelve como resultado
mejor puntuado varios torrents "Complete Collection (S01-S04+OVA+Movies)" —
y para ese *mismo* torrent, **tres `file_idx` distintos** con el mismo
número de seeders. Cada temporada dentro de un batch así reinicia su propia
numeración interna ("01", "02"...), así que "episodio 1" es ambiguo dentro
de un batch de cuatro temporadas de un modo que nunca lo es en un release de
una sola temporada — Torrentio no siempre acierta con el fichero correcto.
Nuestro selector automático (que coge el primer resultado) podía aterrizar
en cualquiera de esos tres.

Esto es justo el problema que el usuario señaló mirando Shiru — su
`animeresolver.js` existe precisamente para esto (filtrado por límites de
temporada vía AniDB). No se copia código (GPL, ver §2 de la nota de
investigación), pero el concepto es válido: nuevo
`actions.torrentio._is_multi_season_batch()`, un detector por patrones de
título (`S01-S04`, `S01-04`, `Season 1 to 4`, `ALL SEASONS`, `Complete
Collection/Series`, `Season 1+2+3+4`) verificado contra las respuestas
reales de Torrentio para AoT antes de escribir una línea. `search_by_id()`
ahora ordena estos batches **por debajo** de las fuentes de una sola
temporada sin ambigüedad — no los descarta (siguen siendo una opción legítima
para quien quiera la colección completa), solo deja de ser lo primero que se
autoselecciona o lo primero que se ve.

Verificado en vivo contra la app real: la lista de fuentes para AoT episodio
1 pasó de encabezarse con "Complete Collection S01-S04" a encabezarse con
"[Salieri] Attack on Titan - Shingeki No Kyojin S1 BD" — cinco lanzamientos
de una sola temporada, cero batches multi-temporada, en el top de la lista.

12 tests nuevos para el detector + reordenación (regex por patrón, orden por
niveles, caso real de AoT). Suite completa: **927 tests pasan**, mismo único
fallo preexistente de siempre.

## Contraste real con Shiru + estabilidad ✅ **HECHA** (2026-09-04)

El usuario: *"ni de coña funciona igual"*. Tenía razón, y la comparación seria
contra el repo destapó una diferencia arquitectónica de fondo más cuatro
fallos concretos. Decisiones tomadas por él tras consultarle: **AnimeTosho
primero con Torrentio de respaldo**, y **AniList principal con Kitsu de
respaldo**.

### La diferencia de fondo

| | Shiru | Nosotros (antes) |
|---|---|---|
| Catálogo | AniList | addon Kitsu — **strem.fun** |
| Mapeos | `ani.zip` | `ani.zip` ✅ igual |
| Fuentes de torrents | N extensiones enchufables | Torrentio — **strem.fun**, única |
| Qué fichero es el episodio | lee la lista real y parsea cada nombre | confía en el `fileIdx` remoto |

`strem.fun` nos servía cat��logo *y* torrents: un único host cuya caída se
llevaba por delante toda la función. **Y ocurrió durante esta sesión**: el
dominio entero quedó inalcanzable ~30 min mientras AniList, ani.zip,
AnimeTosho y Nyaa seguían en pie. Shiru habría seguido funcionando.

### Hallazgo 10 — La caché tiraba el dato bueno justo cuando hacía falta

`cached()` propagaba el error de red aunque tuviera una entrada caducada
utilizable en disco. Con strem.fun caído había un trending de 22 h guardado y
el panel moría igual. Ahora sirve lo caducado cuando el productor falla
(*serve-stale-on-error*), y solo propaga el error si de verdad no hay nada.
Verificado contra la caída real: el panel volvió a abrir y navegar. 16 tests.

### Hallazgo 11 — El regex de la pasada anterior era un parche; la raíz es otra

AnimeTosho se puede consultar por **id de episodio de AniDB**, y devuelve
`num_files`: un release de un solo fichero *es demostrablemente* el episodio
pedido. Y ese `anidb_eid` ya lo guardábamos desde la Fase 0.2 sin usarlo.

    Torrentio  -> encabezado por batches "Complete Collection S01-S04", y el
                  MISMO torrent con tres fileIdx distintos empatados a seeders
    AnimeTosho -> 49 resultados para el eid, 46 de un solo fichero;
                  el primero 152 seeders, un .mkv limpio de S01E01

Nuevo `actions/anime/sources.py`. AnimeTosho primero, Torrentio de respaldo,
secuencial (una primera versión lanzaba ambas en paralelo, pero
`ThreadPoolExecutor` espera a todas al salir del `with`, así que el camino
bueno pagaba el timeout de 12 s del respaldo). AoT S2 ep1 resuelve a
`"S2 - 26"` — numeración absoluta correcta entre temporadas. 32 tests.

### Hallazgo 12 — `canPlayType()` miente, y el fichero correcto no se veía

Con la fuente ya acertando el episodio, el vídeo seguía en negro. Medido:

    canPlayType('video/mp4; codecs="hev1..."')  -> "probably"   (MENTIRA)
    ffprobe sobre el stream real -> hevc / Main 10 / yuv420p10le + opus + ass
    bytes: 2 MB en 0,005 s  -> los datos llegaban; fallaba la DECODIFICACIÓN

Así que la reproducibilidad se infiere del título (como hace Shiru en
`handler.js`), no se le pregunta al navegador. Conservador: solo los marcadores
*conocidos como malos* penalizan; un título que no dice nada se asume bueno.
El ranking es consciente del motor — al motor VLC no se le penaliza nada,
porque lo decodifica todo.

### Hallazgo 13 — La mayor ventaja sobre Shiru estaba apagada en Linux

Y esta es la de fondo. `requirements.txt` pineaba `python-vlc` a
`sys_platform == "win32"`. El usuario está en Linux con VLC instalado
(`/usr/bin/vlc`, libvlc presente), así que `HAS_VLC` era False, el motor caía
siempre al `<video>` del navegador, y de ahí venían los fallos de códec.
`python-vlc` es un binding ctypes puro (no compila nada), así que ahora se
instala en todas las plataformas; si no hay libvlc, el import falla solo y se
cae al navegador exactamente como antes.

Verificado en vivo: el mismo fichero HEVC 10-bit que el navegador dejaba en
negro **reproduce en VLC**, y VLC además expone las pistas de subtítulos ASS
incrustadas (inglés + honoríficos) que un `<video>` ni siquiera puede ver.

### Hallazgo 14 — Dos títulos sin mapeo rompían la parrilla entera

AniList lista estrenos que `ani.zip` todavía no mapea (8 de 10 resueltos;
los 2 fallos eran premieres de 2026). Ambos quedaban con `kitsu_id` vacío →
misma clave `"kitsu:"` → `each_key_duplicate` de Svelte → parrilla en blanco.
La clave cae al título cuando no hay id, así que esos estrenos se ven en vez
de tumbar la pantalla.

### Estado

`actions/anime/anilist.py` (catálogo AniList) + `catalog.py` orquestando
AniList → Kitsu → caché obsoleta: tres capas independientes. Búsqueda notablemente
más precisa (*"attack on titan"* devuelve exactamente las 4 temporadas).
Suite: **999 tests**, mismo único fallo preexistente de siempre.

**Lo que sigue sin poder replicarse:** `animeresolver.js` de Shiru lleva ~200
líneas de correcciones a mano para nombres concretos (Steins;Gate, Code Geass,
Symphogear, Ranma ½...). Son años de ajuste contra casos reales; nos acercamos
mucho, pero no se iguala a corto plazo.

## Reproductor solo web ✅ **HECHA** (2026-09-04)

Decisión del usuario: *"El reproductor va a tener que ser SIEMPRE el web"*.
Eso invierte la Fase 2 (VLC como motor) y reordena todo: si el `<video>` del
navegador es el único reproductor, el problema de códecs deja de ser un
matiz y pasa a ser **el** problema.

**Se elimina el motor VLC del flujo de anime**: `actions/anime/playback.py`,
sus 29 tests, los 17 endpoints `/api/player/*`, sus bindings en `api.js`, el
selector "Reproducir en:" y la barra de transporte VLC. Código inalcanzable
es peor que código ausente. El `_VLCBackend` del panel Qt
(`ui/panels/movies.py`) no se toca: es independiente.

`prefer_playable` deja de ser condicional — ya no hay un segundo motor que
pudiera reproducir un HEVC, así que el ranking penaliza siempre lo que el
navegador no decodifica. Un cliente antiguo que aún mande `engine=vlc` no
puede desactivarlo (hay test).

### Hallazgo 15 — Los subtítulos no se podían activar porque quité el menú

La pasada anterior sustituyó los controles nativos del `<video>` por una barra
propia... y con ellos se fue **el menú de subtítulos del navegador**, que era
la única forma de encenderlos. El `<track>` estaba puesto y cargaba, pero no
había interruptor.

Ahora hay menú propio (Desactivados / Español / Inglés) y un `<track>` por
idioma. La disponibilidad se deduce de los eventos `load`/`error` del propio
`<track>`: un idioma sin subtítulo para ese episodio devuelve 404, se marca
como no disponible y se cae al siguiente. Se descartó sondear con `HEAD`
porque el servidor descargaría y convertiría el subtítulo entero solo para
tirarlo.

### Hallazgo 16 — "Le cuesta mucho cargar": elegíamos 3,67 GB para 24 minutos

Para AoT ep1 el candidato ganador era un encode de **3,67 GB para un episodio
de 24 min** (~20 Mbps, calidad Blu-ray). Dos problemas a la vez: hay que
bufferizar muchísimo antes de arrancar, y un fichero así es casi siempre
HEVC/10-bit aunque el título no lo diga — ese mismo `[MTBB] ... (BD 1080p)`
pasaba limpio el filtro por título y `ffprobe` lo delató como
`hevc/Main 10`. **El tamaño es la señal que el título no da.** Nuevo flag
`oversized` (>2,5 GB por episodio suelto), que ordena por debajo.

### Hallazgo 17 — Los batches de AnimeTosho servían el episodio equivocado

Al mirar por qué un batch se colaba arriba salió un bug peor: para los
resultados de AnimeTosho mandábamos `file_idx=-1` porque AnimeTosho no da
índice, y el demonio entonces cogía **el fichero más grande** del torrent. En
un batch de 25 episodios eso es, casi con seguridad, el episodio equivocado —
exactamente el bug original, por otra puerta.

Arreglado en la raíz y como lo hace Shiru: `daemon.mjs` ahora **lee la lista
real de ficheros del torrent** y elige por nombre (`findEpisodeFile`). Estaba
tirando esa información desde el principio. El número de episodio se pasa
desde el frontend → `/api/movies/play` → `vlc_player.start_streaming` → el
demonio. Sólo casa el número como token delimitado, así que `01` no coincide
con el `01` de `1080p` ni con un CRC `[A1B2C301]`; si hay ambigüedad devuelve
`null` y se mantiene el comportamiento anterior en vez de adivinar. Probado
contra 6 formas reales de nombre (Erai-raws, S01E01, 3 cifras, CRC engañoso,
ambiguo, fichero único).

Con esto los batches WEB (Erai-raws y compañía) pasan a ser utilizables: son
H.264/AAC, ~1 GB por episodio, y ahora sirven el episodio correcto.

**Orden final del ranking:** vivo → decodificable → episodio inequívoco →
tamaño sensato → seeders. Acertar el episodio va por delante del tamaño a
propósito: un episodio equivocado que carga rápido es justo el bug que este
camino existe para arreglar.

### Botones

Los controles eran etiquetas de texto ("Pantalla completa", "Detener y
volver"). Ahora son iconos redondos de 34 px con `title`/`aria-label`,
usando el set propio de `Icon.svelte`, al que se añadieron `fullscreen`,
`fullscreen-exit` y `subtitles`.

### Verificado en vivo

One Piece ep 1: arranque en ~10 s con un WEB H.264 de 669 MB (antes: BD de
3,67 GB que no llegaba a arrancar), subtítulos en español con 320 cues
visibles en pantalla, cambio de idioma funcionando, y pantalla completa.
Suite: **959 tests**, mismo único fallo preexistente.

## "Nada me encuentra seeds" — no eran los trackers ✅ **HECHA** (2026-09-05)

El usuario preguntó si era cosa de los trackers. Medido, **no lo era** — pero
la investigación destapó la causa real.

### Lo que se descartó, con datos

Sonda BEP15 contra los 13 trackers de `actions/trackers.py`: **9 vivos**, 4
caídos (uno, `open.tracker.cl`, resuelve a `127.0.0.1` — DNS secuestrado, el
mismo patrón que ya documentaba ese fichero). Se dejan puestos: los announces
salen en paralelo, un tracker muerto no cuesta más que el socket, y vuelven.

Luego la prueba que importaba — ¿nos falta el tracker de anime?

    lista actual        vs   + nyaa.tracker.wf y compañía
    AoT S1E1:    7 peers          7 peers   (+0)
    One Piece:  35 peers         35 peers   (+0)
    Frieren:   143 peers        151 peers   (+8)

Los trackers específicos de anime encuentran **los mismos peers** que ya
encontrábamos. La teoría era razonable y era falsa.

### Hallazgo 18 — Ordenábamos por números de seeders inventados

Contrastando lo que anuncia la fuente con un scrape BEP15 real:

    anuncia   real
       541 ->    0    [HorribleSubs] Shingeki no Kyojin S2 - 26 [720p]
      1209 ->  132    [SubsPlease] Sousou no Frieren - 01 (1080p)
       298 ->   35    [SubsPlease] One Piece - 1044 (1080p)
       152 ->    6    [MTBB] Shingeki no Kyojin - 01 (BD 1080p)

Inflados ~10x de forma sistemática y, en el peor caso, pura ficción: el
primer resultado de AoT S2E1 anunciaba **541 seeders y tenía 0**. Pinchabas y
no encontraba nada. Estábamos ordenando por fantasía y poniendo arriba un
torrent muerto — eso era el "nada me encuentra seeds".

Nuevo `actions/anime/health.py`: scrape BEP15 real, un paquete por tracker
para *todos* los infohashes de la página a la vez (BEP15 admite 74 por
paquete), caché de 10 min. `find_episode` verifica **antes** de ordenar — al
revés no serviría de nada, solo elegiría otro ganador equivocado.

Distingue "sin datos" de "muerto": si ningún tracker responde, se conserva el
número anunciado y se marca sin verificar, porque silencio no es lo mismo que
swarm vacío. En la UI los seeders verificados van sin más, los no verificados
con `?`, los de 0 seeders salen en gris con "sin seeds", y los de códec
incompatible con su propia etiqueta.

22 tests nuevos. Suite: **981**.

Esto es la Fase 4.1 del plan, que se había saltado por asumir que los
seeders de la fuente eran buenos.

## AnimeTosho se muere: Nyaa como segunda fuente ✅ **HECHA** (2026-09-05)

El usuario pasó la lista de fuentes de Shiru, y la ficha de Anime Tosho traía
un aviso que yo no había visto: **cierre permanente**. Verificado en vivo el
mismo día, y es exacto:

    lo más nuevo que devuelve feed.animetosho.org  ->  2026-05-08
    hoy                                            ->  2026-09-05

Lleva **4 meses sin indexar nada** y se apaga del todo en octubre de 2026.
Y yo lo acababa de poner como **fuente principal**. Funcionaba en las pruebas
porque todas eran con anime antiguo (AoT 2013, One Piece, Frieren 2023) —
justo lo que sí está en el archivo. Para cualquier cosa de la temporada
actual no tenía nada, y el respaldo de Torrentio era lo único que lo tapaba.

### Nyaa

`actions/anime/nyaa.py`: RSS público, contenido del mismo día, y con los
seeders en el propio feed (`nyaa:seeders`). Cubre exactamente lo que Tosho ya
no da.

Su punto débil es la precisión: no tiene API por episodio, solo búsqueda de
texto, así que "Sousou no Frieren 01" devuelve también los batches de la S2.
De ahí `_covers_episode()`, que solo acepta un resultado si nombra el
episodio como token delimitado o es un rango que lo contiene ("01-28",
"01~12").

**Bug encontrado por un test** al escribirlo: en `"S2 - 01~12"` el regex de
rango casaba antes `"2 - 01"` (el dígito de la temporada contra el inicio del
rango) — un rango invertido que además se comía el bueno, así que el batch
quedaba descartado. Arreglado con un `(?<![A-Za-z])` delante.

### Fusión en vez de sustitución

Ninguna de las dos fuentes cubre el catálogo entera: Tosho es preciso
(ids de episodio de AniDB, no puede equivocarse de episodio) pero está
congelado; Nyaa es actual pero casa por título. Así que ahora se consultan
**en paralelo y se fusionan**, deduplicando por infohash y quedándose con la
copia mejor informada (la que sabe si es de un solo fichero). Torrentio queda
como último recurso si ambas fallan.

Esto solo es seguro ahora porque el ranking ya se apoya en salud de swarm
verificada (Hallazgo 18) y el demonio elige el fichero por nombre (Hallazgo
17): sin esas dos piezas, mezclar habría devuelto la ambigüedad al montón.

`defusedxml` para parsear el RSS — es XML de un sitio público, o sea entrada
no confiable, y el parser de la stdlib es vulnerable a expansión de
entidades. Ya estaba instalado; queda fijado en `requirements.txt`.

### Verificado en vivo

"Smoking Behind the Supermarket with You" (emitido en julio de 2026, o sea
posterior al congelamiento de Tosho): **Nyaa lo encuentra con 50 seeders
verificados**, marcado "códec no compatible" porque es HEVC. Antes de esto no
había ninguna fuente para ese título. Suite: **1003 tests**.

### Pendiente de tu lista

De las 6 fuentes de Shiru quedan sin portar **nekoBT** (índice comunitario,
alta precisión), **SeaDex** (curaduría de la mejor versión por serie) y
**Anime Tosho (New)** (el mirror que sustituye al que cierra). Nyaa era la
que más hueco tapaba y la de API más estable; las otras tres son mejora
incremental, no supervivencia.

### 5.2 Calendario de emisión — **dentro del calendario que ya tiene la app**

Decisión 6 (ver §D4). No una vista semanal aparte que haya que acordarse de
abrir: los estrenos de anime se pintan **en el panel Calendario existente**,
junto a los eventos de Google, distinguidos por color e icono.

**Fuente de verdad: local.** `actions/anime/schedule.py` mantiene una tabla
SQLite con los estrenos de las series que tengas en `watching` /
`plan_to_watch`:

```python
AiringEntry(kitsu_id, mal_id, title, episode, airs_at_utc,
            poster_url, source, updated_at)
```

Refresco cada 6 h desde el catálogo `airing` de Kitsu (o `airingSchedule` de
AniList si se hace la Fase 6), cruzado con tu lista de MAL.

**Puntos de enganche, uno por frontend — los dos son simétricos y limpios:**

| Frontend | Dónde | Cómo |
|---|---|---|
| WebUI | `loadMonth()` → `events` → `eventsByDay` ($derived) en [`Calendar.svelte:107`](webui/src/panels/Calendar.svelte:107) | fusionar la respuesta de `/api/anime/airing?from=&to=` en `events` con `source:'anime'` |
| Qt | `CalendarModePanel._events_by_day` ([`calendar.py:473`](ui/panels/calendar.py:473)) | misma fusión en la carga del mes |

Los eventos de anime son **de solo lectura**: `openEditDialog` /
`CalendarEventDialog` no se abren para ellos. Al pulsarlos, la acción es
**"Ver episodio"**, que salta al panel Anime ya en ese episodio.

Un interruptor en Ajustes (`calendar_show_anime`) los enciende y apaga, y los
días del grid cuentan los estrenos aparte de los eventos reales para que el
recuento "+N" no engañe.

**Coste:** casi nada más allá de la tabla, porque `schedule.py` ya hace falta
para 5.3 y las dos vistas leen la misma tabla.

### 5.2b Exportar a Google Calendar (**activado**, alcance acotado — §D5)

Para verlo en el móvil sin abrir Jarvis. Activado desde el inicio con el
alcance de §D5 (`watching` + 60 días). La tabla local de 5.2 sigue siendo la
fuente de verdad: Google es un **espejo**, no el almacén — así el panel sigue
funcionando sin conexión y la reconciliación no gasta cuota leyendo Google.

Reglas:

- **Nunca sobre `primary`.** Se crea un calendario secundario dedicado
  ("Anime — Jarvis"). Un cour de 12 episodios × N series son cientos de
  eventos; eso no puede acabar mezclado con tu calendario real, y así
  desactivarlo es borrar un calendario, no cazar eventos uno a uno.
- Reconciliación por `kitsu_id:episodio` guardado en `extendedProperties.private`,
  para que un retraso de emisión **actualice** el evento en vez de duplicarlo, y
  quitar una serie de la lista borre los suyos.
- Confirmación explícita la primera vez, con el número de eventos que va a crear.

**Fontanería que hace falta** (pequeña, pero hoy no existe):

- El scope ya da: `actions/google_auth.py:23` pide
  `https://www.googleapis.com/auth/calendar` completo, así que **no hay
  re-autorización**.
- `actions/google_calendar.py` no tiene `list_calendars()` ni
  `ensure_calendar(name)` — hay que añadirlas.
- `calendar_id` está **cableado a `"primary"`** en los dos frontends (ninguno lo
  pasa como parámetro, aunque `list_events_range` sí lo acepta). Hay que
  propagarlo.

### 5.3 Notificaciones de episodio nuevo

Reutilizar `actions/background_monitor.py`: comprobación cada 30 min,
notificación del sistema al salir un episodio de tu lista, con acción
"Ver ahora" que abre el panel ya en ese episodio.

**Sale casi gratis después de 5.2**: lee la misma tabla `AiringEntry`; lo único
nuevo es comparar `airs_at_utc` contra la última comprobación y disparar el
aviso.

---

## Fase 6 — AniList (opcional, 2 días)

Solo catálogo, **solo lectura**, sin OAuth: mejores filtros de búsqueda (año,
formato, género, estado, orden), relaciones, secuelas y recomendaciones. MAL
sigue siendo el único destino de escritura, así que no hay dos capas de
tracking que sincronizar.

El puente hacia Torrentio ya está resuelto: `anilist_id` → `ani.zip` →
`kitsu_id` (verificado en §D1).

**Se puede saltar entera** sin afectar a nada anterior.

---

## Esfuerzo y orden

| Fase | Esfuerzo | Impacto | Riesgo |
|---|---|---|---|
| 0 — Cimientos, `ani.zip`, caché | 0,5-1 d | bajo (desbloquea todo) | bajo |
| **1 — Progreso y continuidad** | 1,5-2 d | **muy alto** | bajo |
| **2 — VLC como motor de la WebUI** | 1-1,5 d | **muy alto** | bajo |
| 3 — Reproductor web (móvil) | 2,5-3 d | alto | **medio-alto** |
| 4 — Fuentes y velocidad | 2,5-3 d | alto | medio |
| 5 — Skip, calendario, avisos | 2-2,5 d | alto | bajo |
| 6 — AniList | 2 d | medio | bajo |

**Orden: 0 → 1 → 2 → 5.1 → 3 → 4 → 5.2/5.3 → 6.**
Total ≈ 14-17 días de trabajo efectivo, parable en cualquier punto.

`5.1` (skip de opening) se cuela entre la 2 y la 3 porque cuesta media tarde y
es de las cosas que más se notan a diario.

**Después de 0+1+2 (≈ 3-4 días)** la WebUI ya reproduce anime con subtítulos y
recuerda por dónde vas — que es el 80 % de la distancia con Shiru.

---

## Riesgos principales

| Riesgo | Fase | Mitigación |
|---|---|---|
| `ani.zip` no cubre una serie o cae | 0.2 | Kitsu como respaldo; los mapeos se cachean 30 días |
| Chromium no reproduce el MKV aunque el códec sea válido | 3.1 | Sonda real con `canPlayType` + un caso de prueba por códec antes de construir sobre ello |
| JASSUB pesa y complica el bundle | 3.4 | **Puerta de decisión explícita**: no se hace hasta usar 3.1-3.3 una semana |
| La auto-selección elige mal y no se entiende | 4.4 | Toast con el release elegido y "[cambiar fuente]" siempre visible |
| Scrape UDP bloqueado en la red | 4.1 | Caída silenciosa a los seeders de Torrentio |
| Nyaa/AnimeTosho cambian de formato | 4.2 | Cada fuente aislada tras el contrato; si una falla, las otras responden |
| `actions/anime/` acaba siendo otro `movies.py` de 4000 líneas | todas | Un módulo por responsabilidad desde el principio; ninguno pasa de ~300 líneas |

---

## Ficheros

**Nuevos**
```
actions/anime/__init__.py
actions/anime/catalog.py        # ex actions/kitsu.py                (F0.1)
actions/anime/prefs.py          # ex flag SFW de anime_search.py     (F0.1)
actions/anime/mappings.py       # ani.zip: ids + episodios           (F0.2)
actions/anime/cache.py                                              # (F0.3)
actions/anime/progress.py                                           # (F1.1)
actions/anime/playback.py       # mando VLC por HTTP                 (F2.1)
actions/anime/codecs.py                                             # (F3.1)
actions/anime/subtitles.py      # SRT→WebVTT, extracción por ffmpeg  (F3.2-3.3)
actions/anime/health.py         # scrape BEP15                       (F4.1)
actions/anime/sources/{base,torrentio,animetosho,nyaa_rss,seadex}.py# (F4.2)
actions/anime/picker.py                                             # (F4.4)
actions/anime/skip.py           # AniSkip                            (F5.1)
actions/anime/schedule.py       # tabla AiringEntry + refresco     (F5.2)
actions/anime/anilist.py        # opcional                           (F6)
webui/src/panels/Anime.svelte   # deja de ser un shim de 6 líneas
webui/src/lib/AnimePlayer.svelte
webui/src/lib/VlcTransport.svelte
```

**Modificados**
```
actions/webui_server.py         # /api/anime/*, /api/player/*  (1227-1265, 1419-1428)
webui/src/lib/StreamingBrowser.svelte   # deja de servir para anime
ui/panels/movies.py             # AnimeModePanel (3488), _EpisodeRow (1349),
                                # _TorrentSelectDialog (822), _PanelControls (274)
actions/vlc_player.py           # prebuffer, timeouts
actions/vendor/webtorrent-stream/daemon.mjs   # caché de metadata, priorización
ui/panels/calendar.py           # fusionar estrenos en _events_by_day (F5.2)
webui/src/panels/Calendar.svelte# fusionar estrenos en eventsByDay    (F5.2)
actions/google_calendar.py      # list_calendars/ensure_calendar + calendar_id (F5.2b)
ui/panels/settings.py + webui/src/panels/Settings.svelte   # ajustes de anime
requirements.txt                # + anitopy
```

**Borrados**
```
actions/anime_search.py
movie_search.search_anime / get_trending_anime / get_airing_anime
```

---

## Siguiente paso

Empiezo por la **Fase 0** salvo que digas otra cosa. Es la que menos se ve y la
que hace que todo lo demás no haya que hacerlo dos veces; sale en un día y deja
la WebUI ya arreglada en el bug de multi-temporada.
