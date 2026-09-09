# INVESTIGACIÓN: Shiru vs. el modo Anime de Jarvis

Compañero de [`NOTE_anime_shiru_plan.md`](NOTE_anime_shiru_plan.md), que tiene
el **plan de ejecución**. Este archivo tiene la **comparativa y los hallazgos**.
Lee este primero.

Repo analizado: [RockinChaos/Shiru](https://github.com/RockinChaos/Shiru)
(clonado en `--depth 1`, `master`, revisado el 2026-09-03).
572 ★ · 38 forks · Svelte · GPL-3.0 · activo (último push 2026-08-30).
Es un fork mantenido de **Miru** (ThaUnknown), con Android/AndroidTV añadidos.

---

## 1. Resumen ejecutivo (el veredicto)

**Shiru es claramente mejor como aplicación de anime. Jarvis es mejor como
motor de reproducción integrado.**

Shiru gana en todo lo que es *"saber qué ver y no perder el hilo"*: catálogo,
seguimiento, calendario de emisión, notificaciones, continuar viendo,
auto-siguiente episodio, dubs, y una capa de resolución de torrents mucho más
robusta que la nuestra. Nuestro modo Anime es literalmente una subclase de
`MoviesModePanel` (`ui/panels/movies.py:3488`) con otra fuente de metadatos:
no tiene progreso local, ni "continuar viendo", ni auto-next, ni calendario,
ni notificaciones.

Jarvis gana en la reproducción real: VLC nativo con selección de pistas de
audio/subtítulos, `spu-delay`, corrección de deriva de subtítulos por FPS
(`actions/subtitle_retime.py`), subtítulos externos vía addon Stremio
(`actions/stremio_subs.py`), gestor de descargas persistente, y todo eso
integrado en el resto del asistente (mando, WebUI, panel flotante). Shiru
reproduce en un `<video>` de Chromium, lo que le obliga a excluir HEVC / AC3 /
DTS / TrueHD del listado de torrents cuando el códec no es reproducible
(`common/modules/extensions/handler.js:22-28`) — un problema que nosotros
simplemente no tenemos.

**Conclusión operativa:** no migrar a Shiru ni portarlo. Traer *ideas y
fuentes de datos* concretas a nuestro stack Python/Qt, manteniendo VLC y el
demonio WebTorrent que ya funcionan.

---

## 2. Restricción legal — esto condiciona TODO el plan

| | Licencia |
|---|---|
| Shiru | **GPL-3.0-or-later** |
| Jarvis | **CC BY-NC 4.0** (ver `readme.md`, badge de licencia) |

CC BY-NC **no es compatible con GPL-3.0**. Copiar código de Shiru a este repo
obligaría a relicenciar el trabajo derivado bajo GPL-3.0 (que además prohíbe
la restricción "NonCommercial" que tú tienes puesta). Es una contradicción
irresoluble sin cambiar la licencia de Jarvis.

**Regla para todo el plan:** se pueden reimplementar **conceptos, endpoints,
formatos de API y algoritmos** (eso son hechos, no obra protegida), pero **no
se copia ni se traduce línea a línea** ningún fichero de Shiru. En la práctica
esto casi no duele, porque el 90 % del valor de Shiru para nosotros son
*fuentes de datos* (`api.ani.zip`, `api.aniskip.com`, AniList GraphQL,
AnimeSchedule) y *decisiones de producto*, no su código Svelte.

---

## 3. Arquitectura de Shiru

Monorepo pnpm con tres capas:

```
common/     # UI Svelte 4 + toda la lógica de dominio (compartida)
client/     # motor WebTorrent (webtorrent 3, bittorrent-tracker, parse-torrent)
electron/   # empaquetado escritorio (+ Discord RPC, protocolo shiru://)
capacitor/  # empaquetado Android / Android TV (nodejs-mobile)
```

`common/` es donde está todo lo interesante. ~19 000 líneas de JS de dominio:

| Módulo | LOC | Qué hace |
|---|---|---|
| `modules/cache.js` | 1442 | Caché en disco por dominios (`QUERY_RSS`, `MEDIA`, `EXTENSIONS`, `HISTORY`…) con TTL. Es la pieza que lo hace "rápido". |
| `modules/providers/anilist/anilist.js` | 1216 | Cliente GraphQL de AniList: listas, progreso, secciones, mutaciones. |
| `modules/anime/anime.js` | 1064 | Metadatos de episodios (`api.ani.zip`), capítulos AniSkip, `trace.moe`, `handlePlay`. |
| `modules/extensions/manager.js` | 953 | Gestor de **fuentes de torrents como extensiones remotas** (ver §4). |
| `modules/util.js` | 829 | Utilidades + `defaults` de settings (~90 opciones). |
| `modules/anime/animeresolver.js` | 701 | **Resolución fuzzy título-de-torrent → media**: `anitomyscript` + títulos alternativos + numeración absoluta. |
| `modules/navigation.js` | 651 | Router + estado de navegación. |
| `modules/anime/animeschedule.js` | 455 | Calendario sub (AniChart) y **dub** (animeschedule.net) + notificaciones. |
| `modules/sections.js` | 394 | Secciones configurables de la home ("Continuar viendo", "Secuelas que te perdiste"…). |
| `modules/providers/myanimelist/myanimelist.js` | 384 | Cliente MAL (equivalente a nuestro `actions/mal_api.py`). |
| `modules/subtitles.js` | 311 | Extracción de subs del MKV en cliente + render ASS con **JASSUB** (WASM). |
| `modules/torrent.js` | 272 | Puente UI ↔ motor WebTorrent. |
| `modules/rss.js` | 240 | Feeds RSS de releases (Nyaa), parseo a magnet, autoplay de nuevos episodios. |
| `client/core/webtorrent.js` | 856 | Motor: servidor HTTP, scrape de trackers, seeding persistente, gestión de disco. |
| `client/lib/torrentcache.js` | 119 | **Caché de metadatos de torrent** — evita re-descargar el `.torrent` del swarm. |

UI: `common/routes/` (home, player, search, settings, torrentManager, w2g) +
`common/modals/` (details, torrent, manager, notification, profiles, trailer).
`PlayerPage.svelte` solo tiene 2606 líneas — es el fichero más grande del repo.

### APIs externas que usa (medido con grep sobre `common/modules/`)

- `graphql.anilist.co` — catálogo + tracking principal
- `api.myanimelist.net` / `myanimelist.net` — tracking alternativo
- `api.ani.zip` — **mappings AniList↔AniDB↔TVDB↔IMDb + metadatos de episodios**
- `api.aniskip.com` — timestamps de opening/ending/recap para auto-skip
- `api.trace.moe` — identificar anime a partir de un fotograma
- `api.jikan.moe`, `kitsu.app` — fallbacks de metadatos
- `cloudflare-dns.com` — DoH opcional (nosotros ya tenemos `actions/doh.py`)
- `esm.sh` — CDN desde el que carga las extensiones de fuentes

---

## 4. La pieza más interesante: fuentes como extensiones

Shiru **no trae ningún indexador integrado**. En su lugar
(`common/modules/extensions/`):

1. El usuario añade una URL de "repositorio de fuentes" (`gh:`, `npm:`,
   `http(s):`, `file:`, `extension:`).
2. `manager.js` descarga el manifest y el código JS del módulo.
3. `worker.js` lo carga en un Worker aislado vía `comlink`, con un contrato
   fijo: `query(options, queryTypes, online, settings) → { results, errors }`.
4. `handler.js` consulta **todas las fuentes activas en paralelo**, deduplica,
   parsea cada título con `anitomyscript`, y actualiza los contadores de peers
   con un scrape real a los trackers antes de mostrarte nada.

El `options` que reciben las fuentes es rico y es exactamente lo que a nosotros
nos falta (`handler.js:44-70`): `anilistId`, `anidbAid`, `anidbEid`, `tvdbAid`,
`tvdbEid`, `imdbAid`, `mvdbAid`, `season`, `episode`, `absoluteEpisode`,
`beforeSeason`/`afterSeason`, `titles[]`, `resolution`, `exclusions[]`.

Esto tiene dos consecuencias:

- **Legal:** el repo no distribuye acceso a ningún tracker. Es la razón por la
  que el README es tan cauto ("content you legally own").
- **Técnica:** es genuinamente mejor que nuestro camino único por Torrentio,
  porque si Torrentio cae, nosotros nos quedamos sin anime. Punto.

---

## 5. Qué tenemos nosotros hoy

### Ruta de datos actual (panel Qt)

```
kitsu.py  (addon Stremio anime-kitsu.strem.fun)
   └─ Movie(kitsu_id=…)  ──►  torrentio.search_by_id("kitsu:<id>:<ep>")
                                  └─ _TorrentSelectDialog (elige el usuario)
                                        └─ vlc_player.start_streaming (daemon.mjs)
                                              └─ _VLCBackend (libVLC embebido)
```

Ficheros: `actions/kitsu.py` (205), `actions/torrentio.py` (236),
`actions/vlc_player.py` (231), `actions/vendor/webtorrent-stream/daemon.mjs`
(217), `actions/mal_api.py` (125), `actions/mal_auth.py` (243),
`actions/stremio_subs.py` (171), `actions/subtitle_retime.py` (107),
`actions/trackers.py` (42). Panel: `ui/panels/movies.py:3488-4161`
(`AnimeModePanel`, ~670 líneas sobre las ~2000 heredadas de `MoviesModePanel`).

**Lo bueno de esta ruta, y no es poco:** delegamos el problema difícil
(título → torrent correcto, con numeración absoluta) al índice servidor de
Torrentio, que ya tiene columnas `kitsuId` + `kitsuEpisode`. Shiru resuelve
eso en cliente con 701 líneas de heurística. Nosotros lo resolvemos con una
URL. Cuando Torrentio está vivo, nuestra ruta es más simple **y más exacta**.

### Deuda técnica encontrada durante la investigación

Hay **tres capas de metadatos de anime coexistiendo**, y no todas se usan:

| Módulo | Fuente | Quién lo usa | Estado |
|---|---|---|---|
| `actions/kitsu.py` | addon Kitsu Stremio | `AnimeModePanel` (Qt) | ✅ el bueno |
| `actions/anime_search.py` | Jikan / MyAnimeList | solo `get_sfw`/`set_sfw` desde `webui_server.py:1419` | ⚠️ 240 líneas muertas salvo el flag SFW |
| `movie_search.search_anime/get_trending_anime/get_airing_anime` | TMDB (`with_genres=16`) | **la WebUI** (`webui_server.py:1227-1265`) | ❌ ruta claramente peor |

**La WebUI está en una ruta distinta y peor que el panel Qt.**
`/api/anime/torrents` (`actions/webui_server.py:1250`) hacía
TMDB → `get_imdb_id` → `torrentio.search_anime(imdb_id, season, episode)`. Es
decir: numeración por temporada/episodio contra un índice de anime que usa
numeración absoluta de Kitsu.

**Medido, para no exagerarlo:** la ruta antigua no devolvía cero, devolvía
*peor y a veces otro episodio*. Para el episodio 1044 de One Piece:

```
ruta antigua  tt0388629:1:1044   ->   2 fuentes
ruta antigua  tt0388629:21:25    ->  15 fuentes   (la temporada/episodio de TVDB)
ruta nueva    kitsu:12:1044      ->  22 fuentes
```

El problema real es que la WebUI nunca calculaba ese `21:25`: fijaba
`season = 1` y mandaba el número que escribieras. Así que en una serie con
temporadas partidas, pedir "episodio 12" te traía el 12 de la temporada 1 —
otro episodio distinto — sin ningún aviso.
 El panel Svelte (`webui/src/lib/StreamingBrowser.svelte`, 337 líneas
compartidas con Películas y Series) además solo ofrece un `<input type=number>`
para el episodio — ni siquiera lista episodios.

---

## 6. Comparativa cara a cara

Leyenda: 🟢 claramente mejor · 🟡 comparable · 🔴 claramente peor · ⬛ no existe

| Área | Jarvis | Shiru | Nota |
|---|---|---|---|
| **Catálogo / descubrimiento** | 🟡 Kitsu: trending, airing, 10 géneros | 🟢 AniList: secciones configurables, temporada, formatos, filtros, adult gating | AniList tiene mejor catálogo y ordenación que el addon Kitsu |
| **Búsqueda** | 🔴 texto plano, 15 resultados | 🟢 `SearchBar` con filtros (año, formato, género, estado, sort) | |
| **Ficha de detalle** | 🟡 hero + sinopsis + lista de episodios | 🟢 + relaciones, secuelas, recomendaciones, personajes, tráiler, "siguiendo" | |
| **Lista de episodios** | 🟡 número, título, fecha, sinopsis, thumbnail (Kitsu) | 🟢 lo mismo + estado visto/no visto + filler/recap + agrupación por temporada | Nuestros datos de episodio ya son buenos, falta el estado |
| **Progreso local / continuar viendo** | ⬛ **no existe** | 🟢 `Continue Watching` en home, resume por segundo | La carencia nº1 |
| **Auto-siguiente episodio** | ⬛ **no existe** (hay navegación prev/next manual) | 🟢 `playerAutoplay` + precarga | Carencia nº2 |
| **Auto-marcar como visto** | 🔴 manual (combo + botón Guardar) | 🟢 `playerAutocomplete` al 85 % del episodio | Carencia nº3 |
| **Calendario de emisión** | ⬛ | 🟢 sub (AniChart) + **dub** (animeschedule.net) | |
| **Notificaciones de episodio nuevo** | ⬛ | 🟢 sistema + in-app, por lista (CURRENT/PLANNING) | |
| **Seguimiento de doblajes** | ⬛ | 🟢 `animedubs.js` + `preferDubs` | Poco relevante para ES; sí para EN |
| **Tracking AniList** | ⬛ | 🟢 nativo, es su provider principal | |
| **Tracking MAL** | 🟡 OAuth + estado/episodio/nota | 🟢 igual + cola de mutaciones offline | El nuestro funciona bien |
| **Skip opening/ending** | ⬛ | 🟢 AniSkip + capítulos embebidos del MKV | Barato de traer, muy visible |
| **Resolución de torrents** | 🟡 1 fuente (Torrentio) por `kitsu:<id>:<ep>` | 🟢 N fuentes en paralelo + mappings AniDB/TVDB + anitomyscript | Nuestro camino es más simple; el suyo es más resistente |
| **Selección de torrent** | 🔴 diálogo manual siempre | 🟢 auto por resolución/seeders, diálogo opcional | Carencia nº4 |
| **Salud del torrent antes de elegir** | 🔴 seeders que reporta Torrentio (a menudo obsoletos) | 🟢 scrape real a trackers antes de listar | |
| **Torrents batch (temporada completa)** | 🟡 `file_idx` de Torrentio | 🟢 detecta batch, mapea episodio→fichero con anitomyscript | |
| **Tiempo hasta primer frame** | 🔴 `_READY_TIMEOUT = 125 s`; el propio código documenta 45-65 s reales | 🟢 caché de metadatos + cliente caliente + seeding | El "muy rápida" del README es esto |
| **Motor de torrent** | 🟡 daemon WebTorrent, 1 torrent a la vez, sin DHT/PEX configurables | 🟢 WebTorrent con seeding persistente, límites, puertos, DHT/PEX/uTP, gestor de disco | |
| **Reproductor** | 🟢 **libVLC nativo**: cualquier códec, pistas de audio/sub, spu-delay | 🔴 `<video>` de Chromium: excluye HEVC/AC3/DTS/TrueHD del listado | **Nuestra mayor ventaja** |
| **Render de subtítulos ASS** | 🟢 VLC nativo | 🟡 JASSUB (WASM) — bueno, pero es reimplementar lo que VLC ya hace | |
| **Subtítulos externos** | 🟢 addon OpenSubtitles v3 por id Kitsu + retiming por FPS | 🟡 principalmente los embebidos en el MKV | Otra ventaja nuestra |
| **Descargas** | 🟢 gestor persistente con pausa/reanudar/abrir/borrar | 🟡 gestor de torrents orientado a seeding | |
| **Miniplayer / PiP** | 🟡 overlay flotante | 🟢 miniplayer + PiP de documento | |
| **Ver juntos (W2G)** | ⬛ | 🟢 P2P vía `p2pt` | Curiosidad, baja prioridad |
| **Perfiles múltiples** | ⬛ | 🟢 | Irrelevante para Jarvis |
| **Móvil / TV** | 🟡 WebUI responsive + `mobile-app/` | 🟢 APK nativa Android + Android TV | |
| **Integración con el asistente** | 🟢 mando, voz, WebUI, resto de paneles | ⬛ app aislada | **Nuestra razón de existir** |

### Recuento

- Jarvis gana claro en **6**: reproductor, subtítulos externos, retiming,
  descargas, exactitud del match vía Kitsu-id, integración.
- Shiru gana claro en **~15**, casi todas de *producto*, no de *motor*.
- Las carencias nuestras más caras en experiencia y más baratas de arreglar:
  **progreso/continuar viendo, auto-next, auto-marcar visto, auto-selección de
  torrent, skip de opening**.

---

## 7. Por qué Shiru "va rápido" (lo que realmente hay detrás)

No es magia ni Svelte. Son cuatro cosas concretas, todas portables:

1. **`cache.js` (1442 líneas).** Caché en disco con TTL por dominio de consulta.
   Al abrir la app no pide nada: pinta desde caché y refresca en segundo plano.
   Nosotros llamamos a Kitsu en cada `_load_trending`.
2. **`torrentcache.js`.** Guarda el `.torrent` (metadata) por infoHash. La
   segunda vez que tocas ese torrent no hay espera de metadata del swarm — que
   es justo el bloque de 45-65 s que documenta nuestro `vlc_player.py:26-28`.
3. **Cliente WebTorrent caliente + seeding persistente.** Nosotros ya tenemos
   el daemon caliente (buena decisión, está documentada en `daemon.mjs:1-31`),
   pero destruimos el torrent al cambiar y no sembramos, así que cada vuelta
   atrás vuelve a empezar de cero.
4. **Scrape de trackers antes de listar.** Evita que elijas un torrent muerto y
   te comas los 125 s de timeout para descubrirlo. Nuestro `_TorrentSelectDialog`
   te deja elegir un torrent con 0 peers reales sin avisar.

El punto 4 es, con diferencia, la mejor relación valor/esfuerzo de todo el
documento.

---

## 8. Qué NO traer

- **El motor de reproducción.** `<video>` + JASSUB es un downgrade respecto a
  libVLC. No tocar.
- **Svelte/Electron/Capacitor.** Ya tenemos WebUI Svelte y app Qt; no hay nada
  que ganar.
- **Perfiles múltiples, Discord RPC, W2G.** Fuera del propósito de Jarvis.
- **Copiar código.** Ver §2.
- **El sistema completo de extensiones remotas.** El contrato es bueno, pero
  cargar JS remoto arbitrario en un asistente que tiene acceso a Gmail, Drive,
  WhatsApp y control del ordenador es un riesgo desproporcionado. Sí traer la
  *idea* (múltiples fuentes en paralelo) implementada como módulos Python
  locales.

---

## 9. Hallazgos de la segunda ronda (sondas en vivo, 2026-09-03)

Tras cerrar las decisiones contigo sondeé dos supuestos que sostenían el plan.
Los dos cambiaron el plan, así que quedan aquí registrados.

### 9.1 El reproductor de la WebUI no tiene subtítulos. Ninguno.

El reproductor web entero es esta línea
([`StreamingBrowser.svelte:244`](webui/src/lib/StreamingBrowser.svelte:244)):

```svelte
<video src={streamUrl} controls autoplay class="video"></video>
```

`/api/movies/play` ([`webui_server.py:1181`](actions/webui_server.py:1181))
arranca el daemon WebTorrent y publica la URL HTTP por SSE; el navegador la
reproduce directamente. VLC **no interviene en ningún momento** en la ruta web.

Consecuencias, todas verificables leyendo esa línea:

- **Sin subtítulos.** No hay `<track>`, no hay selección de pistas, y un
  `<video>` no renderiza ASS/SSA embebido bajo ningún concepto. Para anime,
  donde el fansub es ASS, esto significa que **en la WebUI el anime se ve sin
  subtítulos** salvo que el release sea hardsub.
- **Sin pistas de audio**, sin retardo de subtítulos, sin reanudar.
- **Con el problema de códecs de Shiru**, que yo había dado por evitado en §6:
  Hi10P (H.264 10-bit, frecuentísimo en anime) no lo decodifica **ningún**
  navegador; HEVC solo con decodificador de plataforma; AC3/DTS/TrueHD tampoco.

Es decir: la ventaja de libVLC que celebraba §6 **solo existe en el panel Qt**.
Al priorizar la WebUI, heredamos exactamente la restricción por la que Shiru
tiene que excluir releases del listado
(`common/modules/extensions/handler.js:22-28`), y su solución (JASSUB + filtrado
por códec) pasa de "algo que no traer" a "el manual de referencia".

Esto es, con bastante probabilidad, buena parte de por qué notas que la ventana
de anime "podría ser mejor".

### 9.2 `api.ani.zip` da mucho más de lo que esperaba, y por cualquier id

```
GET https://api.ani.zip/mappings?anilist_id=154587   → 200
GET https://api.ani.zip/mappings?kitsu_id=46474      → 200
GET https://api.ani.zip/mappings?mal_id=52991        → 200
```

Una sola llamada devuelve:

- **`mappings`** — `kitsu_id`, `mal_id`, `anilist_id`, `anidb_id`,
  `thetvdb_id`, `imdb_id`, `themoviedb_id`, `livechart_id`, `anisearch_id`,
  `animeplanet_id`
- **`episodes`** — 53 entradas para Frieren, cada una con `seasonNumber`,
  `episodeNumber`, **`absoluteEpisodeNumber`**, `title` en 7 idiomas,
  `airDateUtc`, `runtime`, `overview`, `image` (still de TheTVDB) y `anidbEid`

Tres cosas que el plan original repartía en tres fases distintas salen de aquí
de golpe: metadatos de episodio mejores que los del addon Kitsu, el `mal_id`
que AniSkip necesita (y que Kitsu no da), y el mapeo temporada/episodio ↔
episodio absoluto — que es exactamente el problema que Shiru resuelve con las
701 líneas de `animeresolver.js`.

Y como acepta `kitsu_id`, se enchufa a nuestro catálogo actual sin tocar nada.
Por eso **AniList deja de ser un cimiento** y baja al final del plan: lo único
que aporta por encima es mejor descubrimiento.

### 9.3 Dependencias verificadas

| Dependencia | Versión | Licencia | Compatible con CC BY-NC |
|---|---|---|---|
| `anitopy` (PyPI) | 2.1.1 | MPL-2.0 | ✅ |
| `JASSUB` (npm) | 1.8.8 | MIT (envuelve libass, ISC) | ✅ |
| `ffmpeg` | del sistema, ya resuelto en [`audio_tags.py:88`](actions/audio_tags.py:88) | — | ✅ |

`anitopy` es el puerto Python de Anitomy, la misma familia que el
`anitomyscript` que usa Shiru — así que podemos tener su calidad de parseo de
nombres de release sin copiar una línea de código GPL.

---

## 10. Preguntas abiertas — **resueltas**

Contestadas por el usuario en la misma sesión. Las decisiones cerradas y su
justificación están en [`NOTE_anime_shiru_plan.md`](NOTE_anime_shiru_plan.md),
sección "Decisiones cerradas". Resumen:

1. **AniList** → sí, pero al final y solo como catálogo de lectura (§9.2 lo
   degradó de cimiento a mejora separable).
2. **Prioridad** → WebUI.
3. **Multi-fuente vs. producto** → producto primero, pero §9.1 las acopla:
   al filtrar por códec en la web, una sola fuente se queda corta.
4. **Castellano** → no es fuente ni criterio primario. Etiqueta y filtro
   opcional, sin bonificación en el ranking.
5. **Reproductor web** (pregunta nueva, surgida de §9.1) → las dos cosas por
   fases: primero VLC como motor en el PC, después reproductor web real para
   móvil/LAN.
