# Plan: mejoras WhatsApp — sync móvil, respuestas (quote), edición

Tres mejoras al modo WhatsApp de Jarvis. Este documento es autocontenido:
otro modelo puede implementarlo sin leer esta conversación.

## Arquitectura relevante

- **Bridge Node** ([whatsapp_bridge/index.js](../whatsapp_bridge/index.js)): envuelve
  `whatsapp-web.js`, expone HTTP en `127.0.0.1:3000`. Mantiene un buffer en memoria
  `messages` (persistido) que Python sondea vía `GET /messages?since=<ts_ms>`.
  Endpoints de escritura (`/send`, `/send_media`, `/reconnect`, …) requieren token
  (`requireToken`).
- **Manager Python** ([actions/whatsapp_manager.py](../actions/whatsapp_manager.py)):
  hilo que sondea `/messages` (`_loop`, línea ~75), deduplica por id (`self._seen`),
  avanza `self._last_ts` y notifica listeners (UI en vivo, auto-reply, transcripción).
- **UI PyQt** ([actions/whatsapp_ui.py](../actions/whatsapp_ui.py), ~3000 líneas):
  `WhatsAppWindow`. Render de burbujas en `_add_message_bubble` (línea ~2196),
  mensajes entrantes en vivo vía `_handle_incoming_message` (~1862) y
  `_append_incoming_message` (~1881). Carga de conversación: `load_conversation`
  (~1415) usando `GET /chat_messages`. Entrada de texto: clase con `keyPressEvent`
  (~194) y el compositor dentro de `__init__` de la ventana.

⚠️ **Timestamps**: el buffer del bridge y `since` trabajan en **milisegundos**
(`Date.now()`). `whatsapp-web.js` da `msg.timestamp` en **segundos**. Mezclarlos
rompe el orden y el polling (ya pasó antes). Al insertar entradas nuevas en el
buffer usar siempre ms.

---

## 1. Mensajes enviados desde el móvil no aparecen al instante

### Causa raíz (confirmada en código)

En `whatsapp-web.js` el evento `message` solo dispara para mensajes **entrantes**.
Los mensajes propios enviados desde el teléfono (u otro dispositivo) disparan solo
`message_create`. En [index.js:449-460](../whatsapp_bridge/index.js) el handler de
`message_create` únicamente pone a cero el contador de no-leídos — **nunca hace push
al buffer `messages`**. Resultado: el polling de Python no los ve; solo aparecen al
recargar la conversación entera (`/chat_messages`).

### Fix (solo bridge)

En el handler `message_create` de index.js:

1. Si `msg.fromMe` es falso, no hacer nada nuevo (el evento `message` ya lo cubre).
2. Si `msg.fromMe`:
   - **Dedupe por id**: `message_create` también dispara para mensajes enviados por
     Jarvis vía `/send` y `/send_media`, que ya hacen push de su propia entrada
     (index.js:637-659 y 691-712). Antes de insertar, comprobar que
     `messages` no contiene ya ese `id` (`msg.id._serialized`). Mantener un
     `Set` auxiliar de ids si el escaneo lineal preocupa (buffer acotado por
     `MAX_BUFFERED_MESSAGES`, el escaneo es aceptable).
   - Construir la entrada con la misma forma que el handler `message`
     (index.js:416-445): `chatId: msg.to`, `fromMe: true`, `direction: 'out'`,
     `body: messageBody(msg)`, media igual que allí, y
     **`timestamp: Date.now()`** (ms — NO `msg.timestamp`, que va en segundos).
   - `messages.push(entry)`, recortar a `MAX_BUFFERED_MESSAGES`, `persistState()`.
   - Conservar el reset de no-leídos que ya existe.

### Lado Python

Nada que tocar: `_loop` ya procesa entradas `fromMe` (comentario en
whatsapp_manager.py:95-96 y `from_me` en línea 106) y la UI ya pinta salientes.
Verificar solamente que `_append_incoming_message` en whatsapp_ui.py pinta la
burbuja a la derecha cuando `entry['fromMe']` es true; si hoy asume entrante,
ajustar.

### Prueba

Con Jarvis abierto y un chat en pantalla, enviar un mensaje desde el teléfono a ese
chat: debe aparecer en la UI en menos de un ciclo de polling, sin recargar. Enviar
también uno desde Jarvis y confirmar que no sale duplicado.

---

## 2. Ver a qué mensaje responde otro (quotes) + responder con clic derecho

### Estado actual

`serializeMessage` (index.js:335-364) y el handler `message` no exponen nada del
mensaje citado. `whatsapp-web.js` ofrece `msg.hasQuotedMsg` y
`await msg.getQuotedMessage()`.

### Bridge

1. En `serializeMessage` y en la entrada del handler `message` (y la nueva de
   `message_create`), añadir cuando `msg.hasQuotedMsg`:
   ```js
   quoted: {
     id: q.id ? q.id._serialized : null,
     body: messageBody(q).slice(0, 200),
     fromMe: !!q.fromMe,
     senderName: /* safeContactName(q.author || q.from) */,
     type: q.type || 'chat',
   }
   ```
   donde `q = await msg.getQuotedMessage()` con try/catch (puede fallar si el
   citado ya no está en memoria; en ese caso `quoted: null`). Ojo: el handler
   `message` hoy es síncrono — hacerlo `async` o resolver el quote en una promesa
   y actualizar la entrada después (más simple: handler `async`).
2. En `/send` (index.js:617): aceptar campo opcional `quotedMessageId` en el body
   y pasarlo a `client.sendMessage(to, body, { quotedMessageId })`. Incluir
   `quoted` en la entrada registrada en el buffer para que la burbuja propia pinte
   la cita al instante.

### Python

1. `whatsapp_manager.py` `_loop`: copiar `quoted` del mensaje del bridge a la
   entrada (junto a `mentions`, `mediaUrl`, etc.).
2. Función de envío (`send_whatsapp` en [actions/whatsapp.py](../actions/whatsapp.py)):
   parámetro opcional `quoted_message_id` que se añade al POST `/send`.

### UI (whatsapp_ui.py)

1. **Render de cita**: en `_add_message_bubble`, si `msg.get('quoted')`, insertar
   arriba del cuerpo un bloque compacto (barra lateral de color + nombre del autor
   + primeras ~2 líneas del texto citado), estilo WhatsApp. Clic en el bloque:
   scroll hasta la burbuja original si está renderizada (mantener dict
   `id → widget de burbuja` al renderizar; ya se rastrea algo similar para acks en
   `_apply_message_acks`).
2. **Menú contextual**: las burbujas necesitan
   `setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)` y un QMenu con:
   - «Responder» (todas las burbujas)
   - «Editar» (solo propias — ver sección 3)
   - «Copiar texto»
3. **Estado de respuesta**: «Responder» guarda `self._reply_to = msg` y muestra una
   banda encima del compositor (autor + extracto + botón ✕ para cancelar, Esc
   también cancela). Al enviar, pasar `quoted_message_id` y limpiar el estado.
4. Los mensajes históricos de `/chat_messages` pasan por `serializeMessage`, así
   que las citas llegan también al recargar conversación.

### Prueba

Responder desde el teléfono a un mensaje y comprobar que la burbuja muestra la
cita. Responder desde Jarvis con clic derecho → «Responder» y verificar en el
teléfono que llega como respuesta real (con quote), no como mensaje suelto.

---

## 3. Editar mensajes propios

### API disponible

`whatsapp-web.js`: `message.edit(nuevoTexto)` — solo mensajes propios y dentro de
la ventana de edición de WhatsApp (~15 min). Evento `message_edit` dispara cuando
cualquier mensaje se edita (propio o ajeno).

### Bridge

1. Endpoint nuevo `POST /edit` (con `requireToken`):
   ```js
   { id: '<messageId serializado>', body: 'texto nuevo' }
   ```
   - `const msg = await client.getMessageById(id)`; 404 si no existe.
   - 403 si `!msg.fromMe`.
   - `await msg.edit(body)`; si devuelve null/lanza, responder 409 con mensaje
     claro («fuera de la ventana de edición» es el fallo típico).
   - Actualizar la entrada correspondiente del buffer `messages` (buscar por id,
     reemplazar `body`, marcar `edited: true`) y `persistState()`.
2. Handler `client.on('message_edit', (msg, newBody) => …)`: actualizar el buffer
   igual (body + `edited: true`) y **hacer push de una entrada ligera de evento**
   `{ type: 'edit', id, chatId, body, timestamp: Date.now() }` para que el polling
   de Python se entere sin recargar. Alternativa más simple si se prefiere:
   solo actualizar el buffer y que la UI lo recoja al recargar conversación —
   peor UX, decisión del implementador.

### Python

1. `actions/whatsapp.py`: función `edit_whatsapp_message(message_id, new_body)`
   que hace POST `/edit` con token.
2. `whatsapp_manager.py`: si se optó por la entrada de evento `type: 'edit'`, no
   tratarla como mensaje nuevo (ni auto-reply ni notificación); enrutar a un
   listener de ediciones.

### UI

1. Menú contextual «Editar» solo en burbujas con `fromMe`. Al elegirlo: cargar el
   texto actual en el compositor en «modo edición» (banda encima del input igual
   que la de respuesta, pero indicando edición; Esc cancela).
2. Al confirmar: llamar a `edit_whatsapp_message`; si OK, actualizar el QLabel de
   la burbuja in situ y añadir marca «editado» junto a la hora (el metadato de hora
   se construye en `_outgoing_meta_text`, ~2124). Si 409, mostrar el error (toast o
   texto en la banda) sin perder el texto escrito.
3. Ediciones remotas (evento del bridge): buscar la burbuja por id en el dict
   `id → widget` y actualizar body + marca «editado».

### Prueba

Editar un mensaje propio recién enviado desde Jarvis y verificar el cambio en el
teléfono. Editar desde el teléfono y ver el cambio reflejado en la UI. Intentar
editar un mensaje viejo (>15 min) y comprobar que el error es claro y no rompe nada.

---

## Orden recomendado de implementación

1. **Sync móvil** (sección 1): cambio pequeño y aislado en el bridge, valor
   inmediato, y las otras dos features se apoyan en que el buffer esté completo.
2. **Quotes** (sección 2): bridge primero (serialización + `/send`), después
   manager, después UI.
3. **Edición** (sección 3): reutiliza la banda del compositor y el dict
   `id → burbuja` creados en la sección 2.

## Notas transversales

- Reiniciar el bridge tras tocar index.js (lo gestiona
  [actions/whatsapp_bridge_process.py](../actions/whatsapp_bridge_process.py); basta
  cerrar y abrir Jarvis, o matar el proceso node y dejar que lo relance).
- `persistState()` guarda el buffer: las entradas nuevas (`quoted`, `edited`)
  deben ser JSON-serializables (ya lo son, solo strings/bools).
- No romper la forma de las entradas existentes: consumidores = `_loop` del
  manager, auto-reply, transcripción de audio, UI. Campos nuevos siempre
  opcionales con default `None`/ausente.
- Grupos: en quotes, `senderName` del citado importa sobre todo en grupos
  (usar `safeContactName` como ya hace `serializeMessage` con `authorName`).
