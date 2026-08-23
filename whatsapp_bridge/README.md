WhatsApp Bridge
================

Pequeño puente HTTP que usa `whatsapp-web.js` para exponer una API local para enviar y leer mensajes.

Instalación
```
cd whatsapp_bridge
npm install
```

Ejecución
```
npm start
```

Uso
- `GET /status` — estado del cliente
- `GET /messages?since=<timestamp_ms>` — obtiene mensajes y eventos (edición, borrado y reacción) desde el cursor inclusivo
- `GET /unread_counts` — devuelve los no leídos reconciliados con WhatsApp y otros dispositivos vinculados
- `POST /send` — enviar mensaje con JSON { "to": "5511999999999@c.us", "body": "Hola", "clientRequestId": "local-uuid" }
- `POST /mark_read` — marca un chat como leído con JSON { "chatId": "5511999999999@c.us" }

Notas
- Primero deberás escanear el QR mostrado en consola la primera vez.
- El formato de destinatario para números es `country+number@c.us`, por ejemplo `5511999999999@c.us`.
- `clientRequestId` es opcional pero recomendado: hace idempotentes los reintentos de texto y multimedia y evita duplicados tras un timeout.
