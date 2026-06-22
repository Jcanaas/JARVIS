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
- `GET /messages?since=<timestamp_ms>` — obtiene mensajes recibidos desde timestamp
- `POST /send` — enviar mensaje con JSON { "to": "5511999999999@c.us", "body": "Hola" }

Notas
- Primero deberás escanear el QR mostrado en consola la primera vez.
- El formato de destinatario para números es `country+number@c.us`, por ejemplo `5511999999999@c.us`.
