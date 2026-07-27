# Modo CardTrader — Cartas Magic

**Busca precios de cartas de Magic, presupuesta mazos de Moxfield y prepara el carrito en CardTrader, priorizando CardTrader Zero.**

[← README](../README.md) · [Normal](mode-home.md)

---

## Descripción

Jarvis se conecta a la API de [CardTrader](https://www.cardtrader.com) con tu token personal para buscar cartas de Magic: The Gathering, comparar precios entre todas sus ediciones, presupuestar un mazo completo pegado en texto plano (formato Moxfield) y preparar el carrito de compra vía **CardTrader Zero** (envío consolidado por el hub).

**La compra final nunca se ejecuta automáticamente.** Jarvis deja el carrito listo; confirmas el pago desde la web o la app de CardTrader.

La primera vez que uses una función de CardTrader, Jarvis descarga un catálogo local de todas las cartas de Magic disponibles (sincronización única de ~10-15 minutos, limitada por la API). Las veces siguientes es instantáneo.

---

## Configuración

1. Entra en [cardtrader.com](https://www.cardtrader.com) → tu perfil → **Full API App**.
2. Copia el token.
3. Pégalo en `cardtrader_jwt` dentro de `config/api_keys.json` (o `%LOCALAPPDATA%\Jarvis\config\api_keys.json` si usas la app instalada).

Sin token, Jarvis te lo pedirá con instrucciones cuando intentes usar la función.

---

## Acciones del asistente

| Comando de ejemplo | Acción |
|--------------------|--------|
| *"Busca el precio de Sol Ring"* | Mejor oferta CT Zero de esa carta |
| *"Compara todas las versiones de Lightning Bolt"* | Lista todas las ediciones ordenadas por precio |
| *"Cuánto costaría este mazo: [pega lista de Moxfield]"* | Presupuesto completo carta a carta, total y no encontradas |
| *"Añade el mazo al carrito"* | Añade la última cotización vía CT Zero |
| *"Añade solo el Sol Ring al carrito"* | Añade una carta concreta de la última cotización |
| *"Qué hay en mi carrito de CardTrader"* | Ver contenido, subtotal, envío y fees |
| *"Quita esa carta del carrito"* | Elimina un producto del carrito |

### Filtros disponibles (en búsqueda y presupuesto)

- **Idioma**: inglés, español, alemán, francés, italiano, japonés, portugués.
- **Condición mínima**: Near Mint, Slightly Played, Moderately Played, Played, Heavily Played, Poor.
- **Foil**: solo foil, solo no-foil, o indiferente.
- **CT Zero**: activado por defecto; se puede pedir "también vendedores directos" para desactivarlo.

---

## Límites conocidos

- **Sin búsqueda nativa por nombre en la API.** Jarvis resuelve el nombre contra un índice local sincronizado; cartas de sets muy recientes pueden tardar en aparecer hasta la siguiente sincronización (`cardtrader_catalog action=sync`).
- **Cotizar un mazo grande es lento**: la API limita las consultas de precio a ~1 por segundo. Un mazo Commander de 80 cartas puede tardar más de un minuto.
- **No hay tool de compra.** Es intencional: el carrito se prepara, la confirmación de pago se hace en la web/app oficial.
