# Plan de implementación — Modo Videojuegos (FitGirl + SteamDB) en Jarvis (Mark-XXXIX)

> Documento de planificación para relevo a otro modelo. Autocontenido: incluye todo
> el contexto de código necesario para implementar sin re-explorar. Fecha: 2026-07-09.

---

## 0. Objetivo

Añadir un **modo "Juegos"** a la app, análogo a los modos Movies/Anime, con dos piezas:

1. **Catálogo/metadata** desde **Steam** (posters, capsule art, descripción, año,
   géneros, rating) — reemplaza a Cinemeta/TMDB del modo pelis.
2. **Búsqueda de descargas** vía **torlink → FitGirl Repacks** (RSS de WordPress),
   devolviendo magnets normalizados igual que el resto de fuentes torrent.

Segundo objetivo, transversal a los tres modos multimedia:

3. **Botón de descarga** en juegos (descargar el repack), **y también** en Películas y
   Anime **junto al botón Reproducir** (descargar el vídeo a disco en vez de sólo
   hacer streaming a VLC).

Fuera de alcance: instalar/lanzar el juego, gestión de biblioteca Steam real
(eso ya lo cubre `actions/game_updater.py`, que es otra cosa: actualizar juegos ya
instalados vía Steam/Epic). Este modo es **descubrir + descargar repacks**.

---

## 1. Estado actual del código (verificado)

### 1.1 Arquitectura de modos multimedia

- **Panel base**: `ui/panels/movies.py` → `class MoviesModePanel(QWidget)` (línea 1136).
  Contiene toda la maquinaria: grid de tarjetas, hero banner, vista detalle,
  reproductor VLC embebido, selector de torrents (`_TorrentSelectDialog`, línea 558),
  poller de posición, menús de audio/subs.
- **Panel anime**: `class AnimeModePanel(MoviesModePanel)` (línea 2714) — hereda de
  Movies y sólo override: chips de género, fila MAL/login, búsqueda vía Kitsu/MAL,
  lista de episodios, `_search_and_play(anime, episode)`.
- **Registro del panel**: `ui/panels/__init__.py` exporta `MoviesModePanel, AnimeModePanel`.
- **Instanciación + navegación**: `ui/__init__.py`:
  - `self._movies_panel` / `self._anime_panel` lazy-init en `_show_movies_mode()`
    (línea 576) y `_show_anime_mode()` (línea 585).
  - `_on_mode_change(mode)` (línea 621) enruta el string de modo al `_show_*`.
  - `nav_items` (línea 798) define el sidebar: cada item `{mode, icon, label,
    labelHasKeyword, hasBadge}`. Movies usa icon `film`, Anime `tv`, CardTrader `cards`.
  - `mode_copy` (línea ~519) mapea modo → (título, subtítulo) del header.
  - Lista blanca de modos con panel central en línea 495.
- **Iconos**: `ui/icons.py` → `_line_icon(name, color, size)` (línea 67), cadena de
  `if name == ...`. Ya existe `download` (línea 134). **No existe** icono `gamepad`/
  `controller` → hay que añadirlo.

### 1.2 Fuente de metadata pelis (patrón a imitar)

- `actions/cinemeta.py` → `Movie` dataclass + `search()` / `get_meta()`. Devuelve
  objetos con `title, poster_url, imdb_id, release_year, rating, overview, media_type`.
- `actions/movie_search.py` → alternativa TMDB con `Movie` (`poster_url`, `tmdb_id`),
  `search()`, `get_trending()`, `get_details()`, `get_imdb_id*()`.
- El panel Movies consume objetos con atributos: `.title`, `.poster_url`,
  `.release_year`, `.rating`, `.media_type`, `.overview`, `.imdb_id`, `.tmdb_id`,
  y opcional `.mal_id` (para ruta anime). Ver `_show_movie_detail()` (línea 1619).

### 1.3 Búsqueda torrent (patrón a imitar)

- `actions/torrent_search.py` → `search(query, kind, limit, spanish)`, `Torrent`
  dataclass (`title, magnet, seeders, leechers, size, spanish, provider, file_idx`).
  Lanza `actions/vendor/torlink/search.mjs` como subproceso Node, parsea JSON stdout.
- `search.mjs` (vendored, adaptado de baairon/torlink): fuentes YTS/TPB/1337x/Nyaa/
  SubsPlease. CLI: `node search.mjs search "<q>" --kind movie|tv|anime --limit N [--spanish]`.
  Salida: array JSON `{name, magnet, seeders, leechers, size, source, spanish}`.
  Incluye **DoH (DNS-over-HTTPS)** para saltar bloqueos DNS de ISP españoles — crítico.
- El panel llama a `_search_and_play(movie)` (línea 1713) que agrega Peerflix +
  Torrentio + torlink, dedup por infohash, muestra `_TorrentSelectDialog`.

### 1.4 Streaming / descarga actual

- `actions/vlc_player.py` → `start_streaming(magnet, title, file_index)` lanza
  `actions/vendor/webtorrent-stream/stream.mjs`, que **hace streaming HTTP** (no guarda
  a disco). Devuelve URL para VLC. `stop_streaming()`. Sólo streaming, **no hay
  descarga a disco** todavía.

### 1.5 torlink upstream — cómo hace juegos (verificado en repo)

Repo real `baairon/torlink` (TypeScript, `src/sources/`):
- `fitgirl.ts`: source id `fitgirl`, group `Games`, homepage `https://fitgirl-repacks.site`,
  `reportsHealth: false` (RSS WordPress no trae seeders → siempre `seeders: 0`).
  Delega en `fetchWordpressRss(HOME, "fitgirl", query, opts)`.
- `rss.ts` → `fetchWordpressRss(base, source, query, opts)`:
  - URL feed: con query `${base}/?s=<q>&feed=rss2`; sin query `${base}/feed/`.
    Paginación `&paged=N` (profundidad 3 páginas, 10 items/página).
  - Parseo: split por `<item>`, extrae **magnet** de `href="magnet:?xt=urn:btih:..."`
    dentro del cuerpo (los repacks FitGirl embeben magnet en el post), `<title>`,
    `<pubDate>`. `sizeBytes: 0`, `seeders: 0` (RSS no trae swarm).
  - Dedup por infohash, retorna `TorrentResult[]`.
- Categoría **Games = sólo FitGirl** por decisión de seguridad upstream (los juegos
  ejecutan código; sólo un repacker con reputación larga). Mantener esa restricción.

**Nota importante**: FitGirl publica **magnets** en cada post (además de hosts de
descarga directa). El magnet de un repack apunta a **carpeta con instalador**, no a un
vídeo — el flujo de "reproducir" no aplica; sólo "descargar".

### 1.6 SteamDB — restricción crítica de diseño

**SteamDB (steamdb.info) NO tiene API pública y está tras Cloudflare con anti-bot
agresivo.** Scrapearlo desde un subproceso Node fallará (challenge JS). Decisión:

- **Metadata → Steam Storefront API** (no oficial pero estable, sin key, sin Cloudflare):
  - Búsqueda: `https://store.steampowered.com/api/storesearch/?term=<q>&cc=es&l=spanish`
    → lista `{id (appid), name, tiny_image, price, ...}`.
  - Detalle: `https://store.steampowered.com/api/appdetails?appids=<appid>&cc=es&l=spanish`
    → `{name, short_description, header_image, release_date, genres, metacritic,
    screenshots, developers}`.
  - Posters: `header_image` (460×215 capsule) y
    `https://cdn.cloudflare.steamstatic.com/steam/apps/<appid>/library_600x900.jpg`
    (poster vertical estilo pelis — encaja con el grid actual).
- Mantener el **nombre "SteamDB"** en la UI (lo que pidió el usuario) pero la fuente
  técnica es la Storefront API de Steam. Documentar esto. Si en el futuro se quiere
  dato específico de SteamDB (histórico de precios, etc.) se evalúa un scraper aparte.
- **Sin dependencia de IMDb id**: los juegos se emparejan FitGirl↔Steam por **nombre**
  (título normalizado), no por id externo. La búsqueda de repack usa el título del juego.

---

## 2. Arquitectura objetivo

### 2.1 Ficheros nuevos

```
actions/steam_catalog.py          # cliente Steam Storefront (metadata) — análogo a cinemeta.py
actions/game_search.py            # búsqueda de repacks FitGirl vía torlink — análogo a torrent_search.py
ui/panels/games.py                # GamesModePanel — análogo a movies.py (o subclase, ver §2.4)
docs/mode-games.md                # doc de modo (análogo a docs/mode-cardtrader.md)
tests/test_game_search.py
tests/test_steam_catalog.py
```

### 2.2 Cambios en `actions/vendor/torlink/search.mjs`

Añadir la fuente FitGirl + kind `game`:

- Nueva función `searchFitgirl(query)`:
  - Feed: `https://fitgirl-repacks.site/?s=<q>&feed=rss2` (+ `&paged=2..3`).
  - Parseo RSS igual que Nyaa/rss.ts upstream: split `<item>`, regex
    `href="(magnet:\?xt=urn:btih:[^"]+)"` para el magnet, `<title>`, `<pubDate>`.
  - **Filtrar antes de extraer magnet**: descartar items con `<category>...Updates
    Digest...</category>` o `<title>` que empiece por `Updates Digest for` (verificado
    en Fase 0: son resúmenes diarios sin descarga, no repacks reales).
  - `source: "fitgirl"`, `seeders: 0`, `leechers: 0`, `sizeBytes: 0`.
  - Reusa `buildMagnet` sólo si el post no trae magnet completo; normalmente sí lo trae
    (verificado en Fase 0 contra un repack real vía DoH).
- En `parseArgs`, aceptar `--kind game`.
- En `main()`: `if (kind === "game") sources = [searchFitgirl(query)];`
- **DoH ya cubre** el nuevo host automáticamente (dispatcher global).
- Ranking: FitGirl no trae seeders, así que ordenar por `added` (pubDate) desc cuando
  `kind === "game"` en vez de por seeders.

### 2.3 `actions/steam_catalog.py`

```python
@dataclass
class Game:
    appid: int
    title: str
    poster_url: str = ""      # library_600x900 vertical (grid)
    header_url: str = ""      # 460x215 capsule (hero/detalle)
    release_year: str = ""
    rating: float = 0.0       # metacritic/100 → 0-10 para reutilizar UI de pelis
    overview: str = ""
    genres: list[str] = field(default_factory=list)
    media_type: str = "game"  # para que la UI compartida no lo trate como movie/serie

def search(query, limit=20) -> list[Game]      # storesearch
def get_trending(limit=20) -> list[Game]        # featuredcategories / top sellers
def get_details(appid) -> Game | None           # appdetails
```

- `media_type="game"` permite que el panel/heredado sepa que **no hay reproducción**,
  sólo descarga.
- Cache en memoria de appdetails (rate-limit Steam ~200 req/5min por IP; ir con calma).
- Usa `urllib`/`requests` como los otros clientes (ver `movie_search.py`).

### 2.4 `ui/panels/games.py` — dos opciones (decisión abajo)

**Opción A (recomendada): subclasear `MoviesModePanel`.**
Igual que `AnimeModePanel` reusa toda la UI. Override:
- `_do_search`, `_load_trending`, `_load_recent` → llaman a `steam_catalog`.
- `_chip_defs` → chips por género de juego (Acción, RPG, Estrategia, Indie…).
- `_show_movie_detail(game)` → reetiquetar ("Año"/"Géneros"/"Metacritic"), y **cambiar
  el botón**: en vez de "▶ Reproducir" → "⬇ Descargar" (ver §3).
- `_search_and_play` → renombrado conceptual: para juegos es
  `_search_and_download(game)`, que llama `game_search.search(game.title, kind="game")`
  y muestra `_TorrentSelectDialog`, luego **descarga** (no VLC).
- Deshabilitar todo lo de reproductor/episodios/subtítulos (no aplica a juegos).

**Opción B: panel independiente desde cero.** Más limpio conceptualmente (juego ≠
vídeo) pero duplica ~400 líneas de grid/hero/tarjetas. Sólo si la reutilización se
vuelve confusa.

→ **Decisión: Opción A**, porque el grid + hero + tarjetas + detalle son idénticos y
ya probados; el único bloque que no aplica (reproductor) simplemente no se activa
cuando `media_type == "game"`.

### 2.5 Descarga a disco (nuevo — compartido por los 3 modos)

Nuevo `actions/torrent_download.py`:
```python
def start_download(magnet, dest_dir=None, file_index=-1, progress_hook=None) -> DownloadHandle
def cancel_download(handle)
```
- Reusa el motor WebTorrent ya vendored. **Opción rápida**: crear
  `actions/vendor/webtorrent-stream/download.mjs` (o flag `--download <dir>` en
  `stream.mjs`) que en vez de servir HTTP, **escriba los ficheros a `dest_dir`** y
  emita líneas JSON de progreso (`{progress, downloadSpeed, done}`) a stdout.
- El Python lee stdout en hilo (como `vlc_player.start_streaming`) y llama
  `progress_hook(pct, speed)`. Ya existe patrón: `_download_sig` en `ui/__init__.py`
  (Drive/YouTube usan `progress_hook=self._download_sig.emit`).
- Destino por defecto: carpeta Descargas del usuario; para juegos, subcarpeta `Games/`.

---

## 3. Botón de descarga en Movies / Anime (junto a Reproducir)

En `ui/panels/movies.py`, `_show_movie_detail()` (línea 1694-1707) hoy sólo añade
`play_btn` ("▶ Reproducir"). Cambio:

- Envolver en un `QHBoxLayout` con **dos botones**:
  - `▶ Reproducir` (existente) → `_search_and_play(movie)` (streaming VLC).
  - `⬇ Descargar` (nuevo) → `_search_and_download(movie)`:
    - Reusa exactamente la misma agregación de torrents de `_search_and_play`
      (refactor: extraer `_gather_torrents(movie) -> list[Torrent]` compartido).
    - Muestra `_TorrentSelectDialog`.
    - En vez de `vlc_player.start_streaming`, llama `torrent_download.start_download`
      con `progress_hook` → status bar / notificación al terminar.
- Para juegos (`media_type == "game"`): **sólo** el botón Descargar (sin Reproducir).
- Refactor sugerido: extraer método `_selected_torrent_then(callback)` que hace la
  búsqueda + diálogo y llama `callback(torrent)`, para no duplicar entre play/download.

Estilo del botón descargar: mismo `QPushButton` de 42px, color secundario
(`C.DARK`/borde) para distinguir del primario de Reproducir. Icono `download` ya existe
en `ui/icons.py`.

---

## 4. Integración en el shell de la app (`ui/__init__.py`)

1. `nav_items` (línea 798): añadir
   `{"mode": "Games", "icon": "gamepad", "label": "Juegos", "labelHasKeyword": ["U"],
   "hasBadge": False}` (elegir tecla libre; I,W,C,D,M,Y,P,N,A,G,J ya usadas → usar `U`
   de jUegos o `V` de Videojuegos; verificar colisión en `_mode_shortcuts`).
2. `_show_games_mode()` análogo a `_show_movies_mode()` con `self._games_panel`.
3. `self._games_panel: QWidget | None = None` en `__init__` (junto a línea 164).
4. `_on_mode_change`: rama `elif mode == "Games": self._show_games_mode()`.
5. `mode_copy` (línea ~519): `"Games": ("Juegos", "SteamDB · FitGirl Repacks")`.
6. Lista blanca de modos con panel central (línea 495): añadir `"Games"`.
7. `ui/panels/__init__.py`: exportar `GamesModePanel`.
8. `ui/icons.py`: añadir rama `elif name == "gamepad":` con path SVG de mando
   (2 sticks + d-pad, o silueta simple de gamepad).

---

## 5. Herramienta de voz/agente (opcional, `main.py`)

Análogo a `torrent_search` (main.py línea 103) y cardtrader. Añadir declaración de
función `game_search` y handler:
```python
{
  "name": "game_search",
  "description": "Busca un videojuego en Steam y sus repacks FitGirl para descargar.",
  "parameters": {"query": {"type": "STRING"}}
}
```
`actions/game_search.py::search_action(parameters)` devuelve texto con top N repacks.
Baja prioridad — el modo UI es lo pedido; la tool de voz es extra.

---

## 6. Fases de trabajo

### Fase 0 — Verificación de fuentes (✅ EJECUTADA 2026-07-09, resultados abajo)

**Steam Storefront**: confirmado 100% funcional sin key. `storesearch?term=elden+ring&cc=es&l=spanish`
devolvió `id, name, tiny_image, metascore` reales; `appdetails?appids=1245620` devolvió
`header_image`, `release_date`, `genres` (en español), `metacritic.score`. Sin fricción.

**FitGirl RSS**: `fitgirl-repacks.site` está **bloqueado a nivel DNS/IP en España**
(sirve página de la Comisión de Propiedad Intelectual — mismo bloqueo que YTS/1337x/TPB
ya documentado en `search.mjs`). Confirmado que el **DoH de Cloudflare ya usado en
search.mjs resuelve el bloqueo sin cambios** (resolví el host vía `1.1.1.1/dns-query`,
conecté a la IP con SNI/Host reales → contenido real servido, 200 OK).

Dos hallazgos que **corrigen el diseño original de §2.2**:

1. **Filtrar posts "Updates Digest"**: el feed de búsqueda mezcla posts de repack real
   (p.ej. "ELDEN RING NIGHTREIGN: Deluxe Edition, v1.03…") con posts-resumen diarios
   sin contenido descargable ("Updates Digest for July 3, 2026"). Estos últimos no
   traen magnet y ensucian resultados. **Fix**: excluir items cuya `<category>` incluya
   `Updates Digest`, o cuyo `<title>` empiece por `Updates Digest for`.
2. **El magnet SÍ está embebido**, pero dentro de `<content:encoded><![CDATA[...]]>`,
   no en `<description>` — el regex `href="(magnet:\?xt=urn:btih:[^"]+)"` de rss.ts
   sigue sirviendo porque busca en todo el bloque `<item>...</item>`, sólo hay que
   asegurarse de no cortar el item antes de `content:encoded` al parsear. El magnet trae
   entidades HTML sin escapar (`&#038;` en vez de `&`) — `unescapeEntities()` (ya
   existe en `search.mjs`) ya cubre `&#0?38;` → `&`, no requiere cambios.
   Ejemplo real verificado: `magnet:?xt=urn:btih:DDC2E96C8654141A9C9161DF7EA0AB77125F0F93&dn=ELDEN+RING+NIGHTREIGN...`.

Confirmado también en la página de repack individual (no sólo RSS): trae magnet +
enlaces 1337x/rutor como alternativa, útil como fallback futuro si el RSS falla.

No se pudo verificar rate-limit real de Steam en esta sesión (una sola query) —
mantener cache/backoff conservador en Fase 1 como medida preventiva.

### Fase 1 — Cliente Steam (`steam_catalog.py`) (½ día)
- `search`, `get_details`, `get_trending`, dataclass `Game`, cache, tests.

### Fase 2 — FitGirl en torlink (½ día)
- `searchFitgirl` + kind `game` en `search.mjs`; `game_search.py` wrapper Python
  (copiar estructura de `torrent_search.py`); test que parsea salida real.

### Fase 3 — Panel Juegos (`games.py`) (1 día)
- Subclase de `MoviesModePanel`; override búsqueda/chips/detalle; botón Descargar;
  desactivar reproductor. Registrar en `ui/__init__.py` + icono + sidebar.

### Fase 4 — Descarga a disco (1 día)
- `download.mjs` (o flag en stream.mjs) + `torrent_download.py` + progress hook.
- Botón Descargar en Movies/Anime junto a Reproducir (refactor `_gather_torrents`).

### Fase 5 — Pulido, docs, tests (½ día)
- `docs/mode-games.md`, smoke test visual (patrón `grab()` sin `show()`, ver memoria
  "PyQt visual smoke test"), manejo de errores/UX.

**Total estimado: ~3.5–4 días.**

---

## 7. Manejo de errores y UX

- FitGirl sin magnet en algún post → saltar item (no romper búsqueda).
- Sin resultados FitGirl → mensaje "No encontré repack para «X»" (muchos juegos no
  tienen repack o el título Steam no coincide; ofrecer buscar con título alternativo).
- Steam appdetails puede devolver `{success: false}` para appids sin ficha regional →
  fallback a `cc=us&l=english`.
- Descarga: repacks son grandes (decenas de GB); mostrar tamaño estimado y confirmar
  antes de empezar. Progreso visible + cancelable.
- **Legal/seguridad**: FitGirl distribuye software pirata que ejecuta código. Mantener
  la restricción de fuente única (sólo FitGirl, como upstream) y no añadir otros
  repackers. El usuario asume el riesgo; la app no ejecuta el instalador, sólo descarga.

---

## 8. Riesgos

| Riesgo | Mitigación |
|---|---|
| SteamDB tras Cloudflare | Usar Steam Storefront API; mantener "SteamDB" sólo como etiqueta UI |
| Matching FitGirl↔Steam por nombre falla (ediciones, subtítulos) | Normalizar título (quitar ™, ®, "Deluxe Edition"); mostrar varios resultados |
| RSS FitGirl cambia formato | Parseo tolerante regex; test contra fixture real |
| Rate-limit Steam | Cache appdetails, backoff, no prefetch masivo del grid |
| Bloqueo DNS ISP (España) a fitgirl-repacks.site | Ya cubierto por DoH global en search.mjs |
| Descarga WebTorrent sin seeders (magnets FitGirl a veces flojos) | Timeout + mensaje; FitGirl también da hosts directos (fuera de alcance) |

---

## 9. Decisiones de diseño (para no re-discutir)

1. **Metadata = Steam Storefront API**, etiquetada como "SteamDB" en UI. SteamDB real
   no es scrapeable de forma fiable.
2. **Repacks = sólo FitGirl** vía RSS WordPress (igual que torlink upstream). Sin otros
   repackers por seguridad.
3. **Panel = subclase de `MoviesModePanel`** (Opción A), reutiliza grid/hero/detalle.
4. **Juegos no se reproducen**, sólo se descargan. `media_type="game"` gobierna la UI.
5. **Descarga a disco** es feature nueva compartida: nuevo `torrent_download.py` +
   `download.mjs`, reutilizado por Movies/Anime (botón junto a Reproducir) y Games.
6. **Emparejado por nombre**, no por id externo (no hay IMDb equivalente).
7. Mantener DoH de search.mjs — es lo que hace funcionar torrents en España sin VPN.

---

## Apéndice A — Referencias

- torlink upstream: https://github.com/baairon/torlink (MIT). Fuente games:
  `src/sources/fitgirl.ts` + `src/sources/rss.ts` (fetchWordpressRss).
- FitGirl: https://fitgirl-repacks.site (feed RSS `?s=<q>&feed=rss2`).
- Steam Storefront (no oficial): `store.steampowered.com/api/storesearch`,
  `/api/appdetails`. CDN posters: `cdn.cloudflare.steamstatic.com/steam/apps/<appid>/`.
- Código base a imitar: `actions/cinemeta.py`, `actions/torrent_search.py`,
  `actions/vlc_player.py`, `ui/panels/movies.py` (MoviesModePanel/AnimeModePanel),
  `ui/__init__.py` (registro de modos), `ui/icons.py` (`_line_icon`).
