# Plan de implementación — Integración CardTrader en Jarvis (Mark-XXXIX)

> **Documento autocontenido.** Está pensado para que cualquier modelo/desarrollador pueda ejecutar la implementación sin acceso a esta conversación. Contiene: contrato de la API verificado contra la documentación oficial, arquitectura objetivo, especificación fichero a fichero, algoritmos, fases con criterios de aceptación, plan de pruebas y riesgos.

---

## 0. Objetivo

Integrar la API v2 de CardTrader (`https://api.cardtrader.com/api/v2`) como conjunto de herramientas de Jarvis para Magic: The Gathering, con estas capacidades de cara al usuario:

1. **Buscar una carta por nombre** y listar sus versiones (printings) con el mejor precio de cada una, priorizando ofertas **CardTrader Zero** (envío consolidado por el hub) y, cuando sea detectable, **CT Zero 1 day** (stock ya en el almacén de CardTrader).
2. **Mejor versión de una carta**: entre todas las ediciones/printings, cuál tiene el mejor precio cumpliendo filtros (CT Zero, condición mínima, idioma, foil/no foil).
3. **Presupuestar un mazo de Moxfield en texto plano**: pegar la lista, resolver cada carta, elegir la mejor oferta por carta y devolver total + desglose + cartas no encontradas.
4. **Gestionar el carrito**: añadir las ofertas elegidas (individuales o el mazo entero) vía CT Zero, ver el carrito con fees y shipping, quitar productos.
5. **Compra**: NUNCA automática. `POST /cart/purchase` queda fuera del alcance de las tools expuestas al LLM (ver §9, decisión D5).

El token de API lo aporta el usuario y se guarda en `config/api_keys.json` (clave `cardtrader_jwt`). No hay flujo OAuth: es un JWT estático que se obtiene de la web de CardTrader (perfil → Full API App).

---

## 1. Contrato de la API (verificado contra la referencia oficial)

Base: `https://api.cardtrader.com/api/v2` — Auth: header `Authorization: Bearer <JWT>` en todas las llamadas.

### 1.1 Rate limits (críticos para el diseño)

| Ámbito | Límite |
|---|---|
| Global (todos los endpoints) | **200 requests / 10 segundos** |
| `GET /marketplace/products` | la doc menciona en un punto "10 requests per second" y en otro "1 call per second". **Asumir 1 req/s como techo seguro y hacerlo configurable**; verificar empíricamente en Fase 0 |
| `GET /jobs/:uuid` | 1 req/s (no lo usamos en la ruta de compra) |

Respuesta al exceder: `{"error": "Too many requests: max 200 requests per 10 seconds"}` — tratar como retryable con backoff.

### 1.2 Endpoints que usamos

| Endpoint | Uso en Jarvis |
|---|---|
| `GET /info` | Validar token al arrancar / diagnóstico |
| `GET /games` | Obtener `game_id` de MTG (esperado: 1). No hardcodear sin verificar |
| `GET /expansions` | Catálogo completo de expansiones: `{id, game_id, code, name}`. Se cachea |
| `GET /blueprints/export?expansion_id=X` | Blueprints (= printing concreto de una carta) de una expansión. Campos clave: `id, name, version, game_id, category_id, expansion_id, scryfall_id, card_market_ids, tcg_player_id, editable_properties`. **404 si `expansion_id` inválido o ausente.** Se cachea en índice local |
| `GET /marketplace/products?blueprint_id=X` | Ofertas a la venta de un blueprint. Devuelve objeto `{blueprint_id: [25 productos más baratos]}`. Filtros opcionales: `foil` (boolean), `language` (locale de 2 letras). Endpoint "lightly cached": el precio real definitivo se ve en el carrito |
| `GET /cart` | Estado del carrito |
| `POST /cart/add` | Body: `{product_id, quantity, via_cardtrader_zero: true\|false}`. Devuelve carrito actualizado. Si un producto del carrito deja de estar disponible, la API lo elimina sola de la respuesta |
| `POST /cart/remove` | Body: `{product_id, quantity}` |
| `POST /cart/purchase` | Finaliza la compra (requiere método de pago configurado en la web). **No se expone como tool** |

### 1.3 Estructura del producto de marketplace (campos que usa el optimizador)

```json
{
  "id": 101862104,
  "blueprint_id": 10050,
  "name_en": "Dragon Fodder",
  "quantity": 1,
  "price": { "cents": 2, "currency": "USD" },
  "properties_hash": {
    "condition": "Moderately Played",
    "signed": false,
    "mtg_foil": false,
    "mtg_language": "en",
    "altered": false
  },
  "expansion": { "id": 92, "code": "ptkdf", "name_en": "..." },
  "user": {
    "id": 41687,
    "username": "...",
    "can_sell_via_hub": true,
    "country_code": "FI",
    "user_type": "normal",
    "max_sellable_in24h_quantity": null
  },
  "graded": false,
  "on_vacation": false,
  "bundle_size": 1
}
```

Interpretación operativa:

- **Elegible CT Zero** ⇔ `user.can_sell_via_hub == true`. Al añadir al carrito se pasa `via_cardtrader_zero: true`.
- **Descartar siempre**: `on_vacation == true`; respetar `bundle_size` (se compra en múltiplos) y `quantity` disponible.
- La moneda puede variar (`USD`/`EUR`) según cuenta/seller — **no asumir EUR**: agrupar totales por `currency` y verificar en Fase 0 qué devuelve la cuenta del usuario.
- Condiciones posibles (de `editable_properties`): `Near Mint, Slightly Played, Moderately Played, Played, Heavily Played, Poor`. Idiomas: `de, en, es, fr, it, jp, pt`.

### 1.4 Estructura del carrito

`GET /cart` devuelve `subcarts` agrupados por seller. Los ítems CT Zero aparecen en un subcart cuyo seller es **"CT Connect"**. Campos de coste a mostrar al usuario: `subtotal`, `shipping_cost` por subcart, `safeguard_fee_amount`, `ct_zero_fee_amount`, y fee del método de pago.

### 1.5 ⚠️ "CT Zero 1 day" — punto sin confirmar (VERIFICAR en Fase 0)

La web de CardTrader distingue entre CT Zero normal (el seller envía al hub y el hub consolida) y stock que **ya está en el almacén** ("ships in 1 day"). La referencia API pública **no documenta un campo explícito** en el producto de marketplace que lo indique. Hipótesis a comprobar con token real, por orden:

1. Algún campo no documentado en la respuesta real de `/marketplace/products` (volcar JSON crudo y comparar con la web).
2. Correlación con `user.username == "CT Connect"` o `user.user_type`.
3. Relación con `user.max_sellable_in24h_quantity`.

Si no existe señal por API, la funcionalidad degrada a "priorizar CT Zero" a secas, y se documenta la limitación. **El diseño del optimizador debe tratar `is_one_day` como campo opcional (tri-estado: true/false/unknown).**

### 1.6 Resolución nombre → blueprint (el problema central)

**La API no tiene búsqueda por nombre de carta.** El único camino es: expansión → blueprints. Por tanto hace falta un **índice local de blueprints**:

- MTG tiene ~600-800 expansiones en CardTrader ⇒ una llamada `blueprints/export` por expansión ⇒ sincronización inicial completa: ~700 llamadas ≈ 40-60 s respetando el rate limit global. Resultado estimado: 90k-120k blueprints.
- Cada blueprint trae `scryfall_id`, lo que permite cruzar con Scryfall si hiciera falta desambiguar. Para v1 basta con matching por nombre normalizado sobre el índice local; Scryfall queda como mejora opcional (fuzzy matching de nombres mal escritos vía `https://api.scryfall.com/cards/named?fuzzy=`).
- Persistencia: **SQLite** en `config/cardtrader_catalog.db` (ver §3.2). Refresco incremental: re-descargar solo expansiones nuevas (diff de `GET /expansions` contra tabla local) + comando de refresco completo manual.

Normalización de nombres para el matching (columna `name_norm` indexada): lowercase, sin tildes/diacríticos, colapsar espacios, y para cartas dobles ("A // B") indexar también cada cara por separado.

---

## 2. Formato de entrada: mazo Moxfield en texto plano

El export de texto de Moxfield produce líneas con estas variantes (el parser debe cubrir todas):

```
4 Lightning Bolt
1 Arcane Signet (CMR) 297
1 Sol Ring (C21) 263 *F*
1 Fire // Ice (MH2) 290
1 Malakir Rebirth // Malakir Mire (ZNR) 111
SIDEBOARD:
1 Alpine Moon (MH1) 235
```

Reglas del parser (`deck_parser.py`):

- Regex principal: `^(\d+)x?\s+(.+?)(?:\s+\(([A-Za-z0-9]{2,6})\)\s+([\w\-★]+))?(?:\s+\*(F|E)\*)?\s*$`
  - grupo 1: cantidad; grupo 2: nombre; grupo 3: código de set (opcional); grupo 4: collector number (opcional); grupo 5: foil `F` / etched `E` (opcional).
- Ignorar líneas vacías y cabeceras de sección (`SIDEBOARD:`, `Commander:`, `Deck`, `Considering`, etc.) — pero conservar la sección como metadato del ítem (`section`).
- Aceptar tanto `4 Lightning Bolt` como `4x Lightning Bolt`.
- El nombre puede contener `//` (cartas partidas/MDFC): no romper por ese token.
- Salida: lista de `DeckEntry {qty, name, set_code|None, collector_number|None, foil: bool, etched: bool, section, raw_line}` + lista de líneas no parseadas (se reportan, nunca se descartan en silencio).
- Si hay `set_code`, la resolución de blueprint se restringe a esa expansión (match por `expansions.code`, case-insensitive); si el usuario pidió "la más barata", el set del export se trata como preferencia, no como restricción (parámetro `respect_printings: bool`, default `false`).

---

## 3. Arquitectura objetivo

### 3.1 Ficheros nuevos

```
actions/
  cardtrader_api.py        # Cliente HTTP: auth, throttle, retries, errores tipados
  cardtrader_catalog.py    # Índice SQLite: expansiones + blueprints, sync y resolución nombre→blueprints
  deck_parser.py           # Parser Moxfield texto plano (sin dependencias de red)
  cardtrader_optimizer.py  # Selección de mejor oferta, comparador de versiones, presupuesto de mazo
  cardtrader.py            # Capa de tools: funciones expuestas a Jarvis (firma parameters/speak), formateo de respuestas habladas
config/
  cardtrader_catalog.db    # (generado en runtime, añadir a .gitignore)
docs/
  mode-cardtrader.md       # Documentación de uso, siguiendo el patrón de los mode-*.md existentes
tests/
  test_deck_parser.py
  test_cardtrader_optimizer.py
  test_cardtrader_catalog.py
```

### 3.2 Esquema SQLite (`cardtrader_catalog.db`)

```sql
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);            -- last_full_sync, game_id, schema_version
CREATE TABLE expansions (
  id INTEGER PRIMARY KEY, game_id INTEGER, code TEXT, name TEXT,
  synced_at TEXT                                                  -- NULL = blueprints aún no descargados
);
CREATE TABLE blueprints (
  id INTEGER PRIMARY KEY, name TEXT, name_norm TEXT, version TEXT,
  expansion_id INTEGER REFERENCES expansions(id),
  category_id INTEGER, scryfall_id TEXT, collector_number TEXT
);
CREATE INDEX idx_bp_name_norm ON blueprints(name_norm);
CREATE INDEX idx_bp_expansion ON blueprints(expansion_id);
```

Nota: el campo `collector_number` no viene garantizado en el export de blueprints; si la respuesta real no lo incluye (verificar en Fase 0 dentro de `fixed_properties`/campos extra), la desambiguación por collector number del export de Moxfield se hace vía `scryfall_id` o se omite.

### 3.3 `cardtrader_api.py` — cliente

```python
class CardTraderError(Exception): ...        # + subclases: AuthError, RateLimited, NotFound, ApiError

class CardTraderClient:
    def __init__(self, token: str | None = None): ...   # lee config/api_keys.json["cardtrader_jwt"] vía actions.paths.config_path
    def info(self) -> dict
    def games(self) -> list[dict]
    def expansions(self) -> list[dict]
    def blueprints_export(self, expansion_id: int) -> list[dict]
    def marketplace_by_blueprint(self, blueprint_id: int, foil: bool | None = None,
                                 language: str | None = None) -> list[dict]
    def cart(self) -> dict
    def cart_add(self, product_id: int, quantity: int, via_zero: bool = True) -> dict
    def cart_remove(self, product_id: int, quantity: int) -> dict
```

Requisitos no funcionales del cliente:

- **Throttle**: token bucket global (límite configurable, default 15 req/s ≪ 200/10s) + límite específico para `/marketplace/products` (default **1 req/s**, configurable tras verificar en Fase 0).
- **Retries**: en 429 y 5xx, backoff exponencial con jitter, máx. 4 intentos. En 401 → `AuthError` inmediato con mensaje accionable ("token inválido o caducado; renuévalo en cardtrader.com → Full API App").
- **Timeouts**: connect 10 s, read 60 s.
- Usar `requests` (ya está en el stack del proyecto) con `Session` reutilizada.
- Logging vía `event_bus.log("CardTrader", ...)` como el resto de actions.
- **Caché en memoria de proceso** para `marketplace_by_blueprint` con TTL 120 s (evita repetir llamadas durante un presupuesto de mazo y su posterior "añádelo al carrito").

### 3.4 `cardtrader_catalog.py`

```python
def ensure_catalog(progress_cb=None) -> dict      # sync inicial si BD vacía; devuelve stats {expansions, blueprints, seconds}
def refresh_new_expansions(progress_cb=None) -> dict   # diff GET /expansions vs BD, descarga solo lo nuevo
def full_resync(progress_cb=None) -> dict
def find_blueprints(name: str, set_code: str | None = None) -> list[Blueprint]
    # 1) match exacto por name_norm; 2) match por cara de carta doble;
    # 3) LIKE prefix; 4) (opcional v2) fuzzy Scryfall → scryfall_id → blueprint
def catalog_status() -> dict                      # última sync, contadores, expansiones pendientes
```

La sync inicial (~40-60 s) **no debe bloquear el arranque de Jarvis**: se lanza bajo demanda la primera vez que se usa una tool de CardTrader, con mensaje hablado de "estoy descargando el catálogo, dame un minuto" y `progress_cb` enganchado a `event_bus`.

### 3.5 `cardtrader_optimizer.py` — lógica de selección

```python
@dataclass
class OfferFilters:
    zero_only: bool = True            # solo sellers can_sell_via_hub
    min_condition: str = "Moderately Played"   # orden NM > SP > MP > PL > HP > PO
    languages: list[str] | None = None         # None = cualquiera; típico ["en","es"]
    foil: bool | None = None          # None = indiferente (gana el más barato)
    prefer_one_day: bool = True       # si is_one_day es detectable, priorizar

def best_offer(blueprint_ids: list[int], qty: int, filters: OfferFilters) -> OfferResult
    # Une los productos de todos los blueprints, filtra, ordena por
    # (prefer_one_day desc, price.cents asc, condición desc) y cubre qty
    # combinando productos si un seller no tiene suficientes copias.
    # Devuelve también runner-ups (top 3) para que el LLM pueda ofrecer alternativas.

def compare_versions(name: str, filters: OfferFilters) -> list[VersionSummary]
    # Por cada blueprint (printing) del nombre: mejor oferta individual.
    # Ordenado por precio asc. Incluye expansión, condición, idioma, foil, seller, CT Zero.

def quote_deck(entries: list[DeckEntry], filters: OfferFilters,
               respect_printings: bool = False, progress_cb=None) -> DeckQuote
    # Para cada entry: find_blueprints → best_offer.
    # DeckQuote: items resueltos (con product_ids elegidos), total por moneda,
    #            no_encontradas, sin_stock, avisos (p.ej. solo quedaban HP).
```

Presupuesto de coste en llamadas: un mazo Commander (~80 nombres únicos) con `respect_printings=false` puede tocar muchos printings por carta. **Regla v1**: consultar como máximo los **N=5 blueprints** más recientes por nombre (configurable) salvo que el usuario pida explícitamente "todas las versiones" de UNA carta (`compare_versions` sin límite). Con N=5 y 1 req/s sobre marketplace: ~400 llamadas ≈ 6-7 min en el peor caso; con el límite real de 10 req/s: <1 min. Por eso Fase 0 debe medir el límite real. Mientras dure la consulta, feedback de progreso hablado/log cada ~20 cartas.

El resultado de `quote_deck` se **guarda como "última cotización"** en memoria de módulo (y JSON en `config/cardtrader_last_quote.json`) para que "añádelo al carrito" no repita la búsqueda.

### 3.6 `cardtrader.py` — tools expuestas a Jarvis

Siguen la convención del proyecto: `def tool(parameters: dict | None = None, speak=None) -> str`, síncronas (el dispatcher de `main.py` ya las envuelve en `run_in_executor`). Devuelven texto en español listo para que el modelo lo lea/resuma.

| Tool | Parámetros | Hace |
|---|---|---|
| `cardtrader_search_card` | `name` (req), `set_code`, `foil`, `language`, `zero_only` (default true), `all_versions` (bool) | Si `all_versions`: `compare_versions`. Si no: mejor oferta global. Formato: "Lightning Bolt — más barata: 0,35 € (2X2, NM, en, CT Zero, seller X). Otras versiones: ..." |
| `cardtrader_quote_deck` | `deck_text` (req), `zero_only`, `min_condition`, `language`, `respect_printings` | Parsea + cotiza. Resumen hablado: total, nº cartas resueltas, no encontradas, las 3 más caras. Detalle completo a log/archivo |
| `cardtrader_add_to_cart` | `scope`: `"last_quote"` \| `"product"`, `product_id`, `quantity`, `card_name` (para añadir una sola carta de la última cotización) | Añade con `via_cardtrader_zero=true` cuando el seller es elegible. Reporta qué entró y qué falló (producto agotado → reintenta con runner-up y avisa) |
| `cardtrader_cart` | `action`: `"view"` \| `"remove"` \| `"clear"`, `product_id`, `quantity` | Estado del carrito con subtotal, shipping por subcart, `ct_zero_fee_amount`, `safeguard_fee_amount`. `clear` = remove iterado |
| `cardtrader_catalog` | `action`: `"status"` \| `"sync"` \| `"full_resync"` | Gestión del índice local |

**No existe tool de purchase.** Si el usuario pide comprar, el modelo responde que la compra final se confirma en la web/app de CardTrader (el carrito ya queda preparado).

### 3.7 Cambios en ficheros existentes

1. **`config/api_keys.json`**: añadir clave `"cardtrader_jwt": "<token>"` (la pone el usuario a mano; el código falla con mensaje claro si falta).
2. **`main.py`** (3 puntos, siguiendo exactamente el patrón de `flight_finder`):
   - Import junto al resto: `from actions.cardtrader import cardtrader_search_card, cardtrader_quote_deck, cardtrader_add_to_cart, cardtrader_cart, cardtrader_catalog`
   - Añadir a `FUNCTION_DECLARATIONS` (tipos en mayúsculas estilo Gemini: `OBJECT/STRING/INTEGER/BOOLEAN`). Declaraciones listas para pegar en §7.
   - Dispatch en la cadena `elif name == ...` (~línea 1408): `r = await loop.run_in_executor(None, lambda: cardtrader_search_card(parameters=args))` etc.
3. **`actions/capabilities.py`**: nueva sección `"Cartas Magic (CardTrader)"` con 5-6 líneas de capacidades.
4. **`.gitignore`**: `config/cardtrader_catalog.db`, `config/cardtrader_last_quote.json`.
5. **`config/prompt.txt`** (opcional, revisar al final): una línea indicando que para precios/compra de cartas Magic use las tools `cardtrader_*` y que la compra final nunca se ejecuta automáticamente.

---

## 4. Fases de trabajo

### Fase 0 — Descubrimiento con token real (½ día) 🔑 bloqueante

Pequeño script desechable (`tests/manual_ct_probe.py`, no commitear resultados con datos personales):

1. `GET /info` → validar token, anotar qué devuelve (¿moneda de la cuenta?).
2. `GET /games` → confirmar `game_id` de MTG.
3. `GET /expansions` → contar expansiones MTG reales; medir tamaño de respuesta.
4. `GET /blueprints/export` de 2-3 expansiones → **volcar JSON crudo completo** y confirmar: presencia de `scryfall_id`, `collector_number` o equivalente en `fixed_properties`, cartas dobles (formato del `name`).
5. `GET /marketplace/products?blueprint_id=` de una carta popular (p.ej. Sol Ring C21) → **volcar JSON crudo** y compararlo con la web con el filtro "CT Zero 1 day" activado: identificar la señal `is_one_day` (§1.5). Probar params `foil`/`language`.
6. Medir empíricamente el rate limit real de `/marketplace/products` (ráfaga de 15 llamadas espaciadas).
7. Ciclo carrito completo con una carta de céntimos: `cart_add` (via_zero true y false), `GET /cart` (ver fees), `cart_remove`. **NO llamar a `/cart/purchase`.**

**Entregable**: sección "Resultados Fase 0" añadida al final de este documento con los JSON de ejemplo y las decisiones sobre §1.5 y el rate limit. Las fases siguientes no arrancan sin esto.

### Fase 1 — Cliente API (½ día)

`cardtrader_api.py` completo (§3.3). Criterios de aceptación:
- `CardTraderClient().info()` funciona con el token de `api_keys.json`.
- Throttle verificable en test (mock de reloj) y retry en 429 con backoff.
- Errores tipados; 401 produce mensaje accionable.

### Fase 2 — Catálogo local (1 día)

`cardtrader_catalog.py` + esquema SQLite (§3.2, §3.4). Criterios:
- Sync completa de MTG termina sin exceder rate limits y persiste >50k blueprints.
- `find_blueprints("Lightning Bolt")` devuelve múltiples printings; `find_blueprints("Fire // Ice")` y `find_blueprints("Fire")` resuelven; con `set_code="mh2"` restringe.
- Sync reanudable: si se corta a medias, `ensure_catalog` continúa por las expansiones con `synced_at IS NULL`.

### Fase 3 — Parser Moxfield (½ día)

`deck_parser.py` (§2) con `tests/test_deck_parser.py` cubriendo todas las variantes de línea listadas + un export real completo de Moxfield como fixture. Sin red. Criterio: 100 % de las líneas del fixture parseadas o reportadas.

### Fase 4 — Optimizador (1 día)

`cardtrader_optimizer.py` (§3.5). Tests con fixtures JSON de marketplace (sin red): filtrado por condición/idioma/foil/CT Zero, cobertura de qty>1 combinando sellers, orden por precio, runner-ups, agrupación de totales por moneda. Criterio: `quote_deck` sobre un mazo fixture de 10 cartas produce el DeckQuote esperado determinísticamente.

### Fase 5 — Tools + integración en Jarvis (1 día)

`cardtrader.py` (§3.6) + cambios de §3.7. Criterios:
- Por voz/texto: "busca el precio de Sol Ring", "cuánto costaría este mazo: …", "añade la más barata al carrito", "qué hay en mi carrito de CardTrader" funcionan de extremo a extremo.
- La primera invocación dispara la sync de catálogo con aviso hablado, no un error.
- Respuestas habladas cortas; detalle largo va a `event_bus.log` y/o archivo.

### Fase 6 — Documentación y cierre (½ día)

- `docs/mode-cardtrader.md` (patrón de los `mode-*.md`: qué hace, frases de ejemplo, límites).
- Actualizar `capabilities.py` y, si procede, `config/prompt.txt`.
- Pasada final de pruebas manuales del flujo completo con el token real, incluida la verificación del carrito en la web de CardTrader.

**Estimación total: ~4,5 días efectivos.** Dependencias: F0 → F1 → F2 → (F3 ∥ F4) → F5 → F6. F3 y F4 son paralelizables.

---

## 5. Manejo de errores y UX

| Situación | Comportamiento |
|---|---|
| Token ausente/inválido | Mensaje hablado: cómo obtener el token y dónde ponerlo (`config/api_keys.json`, clave `cardtrader_jwt`) |
| Carta no encontrada en catálogo | Se lista en `no_encontradas` con la línea original; sugerir corrección si hay match parcial |
| Sin stock que cumpla filtros | Reportar el motivo dominante ("solo hay HP", "no hay CT Zero") y la mejor alternativa relajando filtros |
| Producto agotado al añadir al carrito | Reintentar con runner-up de la cotización y avisar del cambio de precio |
| 429 persistente | Pausar, avisar ("CardTrader me está limitando, esto tardará un poco más") y continuar |
| Mazo enorme (>150 líneas) | Avisar de la duración estimada antes de empezar y emitir progreso |

## 6. Riesgos

| Riesgo | Impacto | Mitigación |
|---|---|---|
| No hay señal API para "1 day" (§1.5) | Media: se pierde ese filtro fino | Tri-estado `is_one_day`; degradar a CT Zero; documentar |
| Rate limit real de marketplace = 1 req/s | Cotizaciones de mazo lentas (minutos) | Límite N printings/carta, caché TTL, progreso hablado, verificación F0 |
| Catálogo desactualizado (sets nuevos) | Cartas nuevas no resuelven | `refresh_new_expansions` automático si el último sync > 7 días al usar una tool |
| Doc contradictoria / campos no documentados | Retrabajo | Fase 0 obligatoria con volcados crudos antes de codificar el optimizador |
| Monedas mixtas en ofertas | Totales engañosos | Totalizar por moneda, nunca sumar monedas distintas |
| El LLM alucina `product_id` | Añadir al carrito algo erróneo | `cardtrader_add_to_cart` solo acepta product_ids presentes en la última cotización/búsqueda, salvo `product_id` explícito verificado contra marketplace |

## 7. Declaraciones de función listas para `main.py`

```python
{
    "name": "cardtrader_search_card",
    "description": (
        "Busca una carta de Magic en CardTrader y devuelve las mejores ofertas. "
        "Con all_versions=true compara todas las ediciones/printings de la carta por precio. "
        "Prioriza ofertas CardTrader Zero (envío consolidado)."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "name":         {"type": "STRING",  "description": "Nombre de la carta (inglés preferido)"},
            "set_code":     {"type": "STRING",  "description": "Código de expansión para restringir, ej: cmr, 2x2"},
            "all_versions": {"type": "BOOLEAN", "description": "true = comparar todas las ediciones por precio"},
            "foil":         {"type": "BOOLEAN", "description": "true solo foil, false solo no-foil, omitir = indiferente"},
            "language":     {"type": "STRING",  "description": "Idioma 2 letras: en, es, de, fr, it, jp, pt"},
            "zero_only":    {"type": "BOOLEAN", "description": "Solo ofertas CardTrader Zero (default true)"},
        },
        "required": ["name"]
    }
},
{
    "name": "cardtrader_quote_deck",
    "description": (
        "Presupuesta un mazo pegado en texto plano formato Moxfield (lineas '4 Lightning Bolt' o "
        "'1 Sol Ring (C21) 263'). Busca la mejor oferta de cada carta en CardTrader (CT Zero) "
        "y devuelve total, desglose y cartas no encontradas. Guarda la cotizacion para poder "
        "añadirla al carrito despues."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "deck_text":         {"type": "STRING",  "description": "Lista del mazo en texto plano"},
            "min_condition":     {"type": "STRING",  "description": "Near Mint | Slightly Played | Moderately Played | Played | Heavily Played | Poor"},
            "language":          {"type": "STRING",  "description": "Idioma preferido 2 letras"},
            "zero_only":         {"type": "BOOLEAN", "description": "Solo CT Zero (default true)"},
            "respect_printings": {"type": "BOOLEAN", "description": "true = respetar la edicion exacta del export; false = la mas barata (default)"},
        },
        "required": ["deck_text"]
    }
},
{
    "name": "cardtrader_add_to_cart",
    "description": (
        "Añade al carrito de CardTrader la ultima cotizacion de mazo completa, una carta concreta "
        "de esa cotizacion, o un product_id concreto de una busqueda previa. Usa CardTrader Zero. "
        "NUNCA finaliza la compra."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "scope":      {"type": "STRING",  "description": "last_quote (todo el mazo cotizado) | product (uno concreto)"},
            "card_name":  {"type": "STRING",  "description": "Para añadir solo una carta de la ultima cotizacion"},
            "product_id": {"type": "INTEGER", "description": "ID de producto concreto (de una busqueda previa)"},
            "quantity":   {"type": "INTEGER", "description": "Cantidad (default 1)"},
        },
        "required": ["scope"]
    }
},
{
    "name": "cardtrader_cart",
    "description": "Consulta o modifica el carrito de CardTrader: ver contenido con costes y fees, quitar productos o vaciarlo.",
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action":     {"type": "STRING",  "description": "view | remove | clear"},
            "product_id": {"type": "INTEGER", "description": "Producto a quitar (para remove)"},
            "quantity":   {"type": "INTEGER", "description": "Cantidad a quitar (default 1)"},
        },
        "required": ["action"]
    }
},
{
    "name": "cardtrader_catalog",
    "description": "Gestiona el catalogo local de cartas de CardTrader: estado, sincronizar sets nuevos o resincronizar todo.",
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {"type": "STRING", "description": "status | sync | full_resync"},
        },
        "required": ["action"]
    }
},
```

## 8. Plan de pruebas

- **Unitarias (sin red)**: parser (F3), optimizador con fixtures (F4), throttle/retry con mocks (F1), normalización de nombres y matching del catálogo con una BD sembrada (F2).
- **Integración (token real, manuales, guiadas por checklist)**: sync de catálogo, búsqueda de 5 cartas conocidas comparando contra la web, cotización de un mazo real de Moxfield (~60 cartas), ciclo carrito add/view/remove con cartas de céntimos.
- **Regresión Jarvis**: arrancar Jarvis y comprobar que las tools nuevas no rompen el registro de funciones existente (el array `FUNCTION_DECLARATIONS` es sensible a errores de sintaxis) ni el arranque en frío sin token.

## 9. Decisiones de diseño (para no re-discutir)

- **D1**: SQLite local para el catálogo (no llamadas por nombre: la API no lo soporta; no JSON plano: 100k filas necesitan índice).
- **D2**: Scryfall solo como mejora opcional v2 para fuzzy matching; v1 funciona 100 % con CardTrader + normalización propia.
- **D3**: `zero_only=true` por defecto en todas las tools (es el caso de uso del usuario); relajable por parámetro.
- **D4**: precios siempre en céntimos internamente; formateo a € solo en la capa de presentación; nunca sumar monedas distintas.
- **D5**: sin tool de purchase. Riesgo económico real + el error de la API si falta método de pago demuestra que el flujo final pertenece a la web. El carrito preparado es el entregable.
- **D6**: tools síncronas estilo `flight_finder`, envueltas por el dispatcher en `run_in_executor` — no introducir asyncio en `actions/`.
- **D7**: `is_one_day` tri-estado hasta que Fase 0 confirme la señal.

---

## Apéndice A — Referencias

- Referencia API: https://www.cardtrader.com/es/docs/api/full/reference
- Colección Postman del usuario: `C:\Users\j.canadas\Downloads\card_trader_postman_collection.json` (host `api.cardtrader.com`, auth Bearer)
- Patrón de integración de tools en Jarvis: ver `actions/flight_finder.py` (tool simple con API key) y su registro en `main.py` (import ~L84, declaración ~L864, dispatch ~L1408)
- Scryfall (opcional v2): `GET https://api.scryfall.com/cards/named?fuzzy=<nombre>` y `GET /cards/search?q=!"<nombre>"&unique=prints`

## Apéndice B — Resultados Fase 0 (ejecutada con token real, 2026-07-09)

**Estado: COMPLETADA. Implementación ya realizada con estos hallazgos.**

1. **`GET /games`** devuelve `{"array": [...]}`, no una lista pelada (a diferencia de lo que sugería la doc). `cardtrader_api.py.games()` desenvuelve la clave. **MTG confirmado `game_id: 1`**, `name: "Magic"`, `display_name: "Magic: the Gathering"`. Total 775 expansiones de MTG (de 3763 expansiones de todos los juegos).

2. **Señal "CT Zero 1 day" encontrada**: `user.one_day_ready` (boolean) en la respuesta real de `/marketplace/products`, **no documentado en la referencia pública**. Se usa directamente en `OfferFilters.prefer_one_day` / `Offer.one_day_ready`. Decisión D7 resuelta: ya no es tri-estado, es un booleano real. Otros campos no documentados en `product.user`: `too_many_request_for_cancel_as_seller`, `can_sell_sealed_with_ct_zero`.

3. **`collector_number` confirmado** en `blueprint.fixed_properties.collector_number` (junto a `mtg_rarity`). En el producto de marketplace también aparece duplicado dentro de `properties_hash.collector_number`.

4. **Estructura real del producto de marketplace** difiere ligeramente del ejemplo de la doc: trae **tanto** `price_cents`/`price_currency` a nivel raíz **como** el objeto anidado `price: {cents, currency, currency_symbol, formatted}`. El código usa los campos planos `price_cents`/`price_currency` como fuente de verdad.

5. **Rate limit real de `/marketplace/products` medido: exactamente 1 req/s** (15 llamadas consecutivas tardaron 15.02 s total, ritmo constante ~1.00-1.02 s entre llamadas). Confirma la hipótesis conservadora del plan; el throttle de `cardtrader_api.py` (`_marketplace_bucket = _TokenBucket(rate=1, per=1.0)`) ya está calibrado a este valor real.

6. **Moneda de la cuenta del usuario: EUR** (no asumir, se confirma en `/cart`). El carrito real ya contenía artículos por 35,14 € vía "Ct connect" (seller CT Zero) — **el carrito preexistente del usuario no fue tocado** durante la sonda (no se llamó `cart_add`/`cart_remove`/`cart_purchase`).

7. **Condiciones reales devueltas por la API**: `Mint, Near Mint, Slightly Played, Moderately Played, Played, Poor` — nota: **no incluye "Heavily Played"** pese a que la doc sí lo listaba en otro punto. `_CONDITION_RANK` en el optimizador contempla ambos por seguridad, pero la API en la práctica no la usa.

**Fichero de sonda**: `tests/manual_ct_probe.py` (no commitear su salida cruda si contiene usernames/países reales).
