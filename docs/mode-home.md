<div align="center">

<img src="assets/mockup-home.svg" alt="Jarvis — Modo Normal" width="800"/>

# Modo Normal — Chat e IA General

**El modo base de Jarvis. Conversación libre con el asistente, control total del sistema y acceso a todas las integraciones.**

[← Volver al README](../README.md) · [Música](mode-music.md) · [YouTube](mode-youtube.md) · [WhatsApp](mode-whatsapp.md) · [Gmail](mode-gmail.md) · [Drive](mode-drive.md)

</div>

---

## Descripción

El modo Normal es la pantalla principal de Jarvis. Funciona como un chat de IA conversacional con memoria persistente, capacidad de visión (pantalla y webcam) y acceso a todas las herramientas del sistema. Es el punto de partida y el hub desde el que se activan el resto de modos.

La interfaz muestra el historial de conversación con burbujas diferenciadas para el usuario y Jarvis, una barra de entrada con soporte de voz y texto, e indicadores de estado en tiempo real.

---

## Interfaz

| Elemento | Descripción |
|----------|-------------|
| **Área de chat** | Historial de conversación con scroll · Burbujas de usuario (derecha) y Jarvis (izquierda) |
| **Barra de entrada** | Campo de texto + botón de micrófono · Pulsar micrófono activa escucha continua |
| **Indicador de estado** | Punto verde "EN ESPERA" / azul "ESCUCHANDO" / amarillo "PROCESANDO" |
| **Cambio de modo** | Jarvis cambia de modo automáticamente cuando detectas la intención ("pon música", "abre WhatsApp") |

---

## Acciones del asistente

### Conversación e IA

| Comando de ejemplo | Acción |
|--------------------|--------|
| *"Qué puedes hacer?"* | Lista todas las capacidades disponibles |
| *"Recuerda que trabajo en [empresa]"* | Guarda un dato en la memoria persistente |
| *"Qué recuerdas de mí?"* | Resume la memoria a largo plazo |
| *"Traduce esto al inglés: [texto]"* | Traduce con el LLM |
| *"Explícame qué hace este código"* | Analiza código pegado en el chat |
| *"Resume [texto largo]"* | Resume cualquier texto |

### Control del sistema

| Comando de ejemplo | Acción |
|--------------------|--------|
| *"Abre Spotify"* | Lanza la aplicación en el SO |
| *"Cierra el navegador"* | Cierra el proceso activo |
| *"Abre el explorador de archivos en Descargas"* | Abre carpeta específica |
| *"Ejecuta ipconfig en la terminal"* | Ejecuta un comando de terminal |
| *"Sube el volumen al 80%"* | Ajusta el volumen del sistema |
| *"Pon el modo oscuro"* | Cambia el tema del SO |
| *"Haz una captura de pantalla"* | Guarda screenshot |
| *"Bloquea el ordenador"* | Bloquea la sesión de Windows |

### Visión e imagen

| Comando de ejemplo | Acción |
|--------------------|--------|
| *"Qué hay en mi pantalla?"* | Analiza el contenido de la pantalla con visión IA |
| *"Lee el texto de esta imagen"* | OCR sobre la pantalla o una imagen adjunta |
| *"Enciende la cámara y dime qué ves"* | Activa la webcam y analiza el frame |
| *"Hay algo interesante en el vídeo que estoy viendo?"* | Analiza el contenido del reproductor activo |

### Google Calendar

| Comando de ejemplo | Acción |
|--------------------|--------|
| *"Qué tengo mañana?"* | Lista eventos del día siguiente |
| *"Crea una reunión el viernes a las 10"* | Crea evento en Google Calendar |
| *"Cancela la reunión de las 15:00"* | Elimina un evento |
| *"Muéstrame los eventos de esta semana"* | Vista semanal |
| *"Añade '[descripción]' a mi calendario"* | Crea evento con descripción libre |

### Recordatorios

| Comando de ejemplo | Acción |
|--------------------|--------|
| *"Ponme un recordatorio en 20 minutos"* | Alerta local con notificación |
| *"Recuérdame comprar leche a las 18:00"* | Recordatorio con texto personalizado |
| *"Cancela el recordatorio de las 18:00"* | Elimina recordatorio pendiente |
| *"Qué recordatorios tengo?"* | Lista recordatorios activos |

### Clima y tiempo

| Comando de ejemplo | Acción |
|--------------------|--------|
| *"Qué tiempo hace en Madrid?"* | Clima actual con temperatura, viento y humedad |
| *"Cómo estará mañana en Barcelona?"* | Previsión para el día siguiente |
| *"Previsión para la semana en Valencia"* | Forecast de 7 días |
| *"Va a llover esta tarde?"* | Respuesta directa sobre precipitaciones |

### Búsqueda web e información

| Comando de ejemplo | Acción |
|--------------------|--------|
| *"Busca noticias sobre [tema]"* | Búsqueda web con resumen IA |
| *"Qué es [término]?"* | Búsqueda + explicación sintetizada |
| *"Cuál es el precio del Bitcoin ahora?"* | Datos en tiempo real |
| *"Busca el mejor restaurante japonés en Madrid"* | Búsqueda con contexto local |

### Vuelos y viajes

| Comando de ejemplo | Acción |
|--------------------|--------|
| *"Busca vuelos de Madrid a Londres el 15 de julio"* | Búsqueda de vuelos con precios |
| *"Vuelos baratos a Ámsterdam para el fin de semana"* | Búsqueda flexible de fechas |
| *"Opciones de ida y vuelta París, semana que viene"* | Vuelos de ida y vuelta |

### Comunicación

| Plataforma | Comando de ejemplo | Acción |
|------------|--------------------|--------|
| **WhatsApp** | *"Manda un WhatsApp a Juan diciendo que llego tarde"* | Envía mensaje (puedes estar en cualquier modo) |
| **Telegram** | *"Envía un Telegram a [contacto]: [mensaje]"* | Envía por Telegram |
| **Discord** | *"Escribe en el canal #general de [servidor]: [mensaje]"* | Envía a Discord |
| **Instagram** | *"Manda un DM a [usuario] en Instagram"* | Envía DM de Instagram |
| **Signal** | *"Manda un Signal a [contacto]"* | Envía por Signal |

### Gestión de archivos

| Comando de ejemplo | Acción |
|--------------------|--------|
| *"Crea una carpeta 'Proyecto X' en el escritorio"* | Crea directorio |
| *"Mueve todos los PDFs de Descargas a Documentos"* | Mueve archivos |
| *"Borra los ficheros temporales de [carpeta]"* | Elimina archivos |
| *"Busca archivos .py en Documentos"* | Búsqueda de archivos |
| *"Comprime la carpeta Proyectos"* | Crea archivo ZIP |

### Productividad y código

| Comando de ejemplo | Acción |
|--------------------|--------|
| *"Escribe un script Python que [tarea]"* | Genera código |
| *"Corrige este código: [código]"* | Depura y sugiere mejoras |
| *"Crea un documento Word con [contenido]"* | Genera y guarda documento |
| *"Abre VS Code en la carpeta actual"* | Lanza IDE con proyecto |

### Juegos y entretenimiento

| Comando de ejemplo | Acción |
|--------------------|--------|
| *"Actualiza mis juegos de Steam"* | Lanza actualizaciones de Steam |
| *"Busca ofertas de [juego] en Epic Games"* | Consulta tienda de Epic |
| *"Abre [juego]"* | Lanza juego desde Steam o Epic |

---

## Cambio de modo

Jarvis detecta automáticamente cuándo quieres cambiar de pantalla:

| Intención detectada | Modo al que cambia |
|--------------------|--------------------|
| *"Pon música"*, *"Abre YouTube Music"* | Música |
| *"Busca un vídeo de..."*, *"Abre YouTube"* | YouTube Video |
| *"Lee mis mensajes de WhatsApp"* | WhatsApp |
| *"Abre mi Gmail"*, *"Cuántos emails tengo?"* | Gmail |
| *"Abre Drive"*, *"Sube este archivo a Drive"* | Google Drive |

> También puedes cambiar de modo desde cualquier pantalla: los comandos de voz funcionan independientemente del modo activo.

---

## Memoria persistente

Jarvis recuerda información entre sesiones:

- **Datos personales**: nombre, ciudad, preferencias, proyectos
- **Notas**: información guardada explícitamente con *"Recuerda que..."*
- **Historial de conversación**: las últimas N conversaciones para dar contexto
- **Portapapeles**: historial de copias recientes

Para borrar la memoria: *"Borra lo que recuerdas de mí"* o elimina los archivos en `memory/`.
