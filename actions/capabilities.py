from __future__ import annotations


CAPABILITY_SECTIONS = [
    (
        "Musica y YouTube",
        [
            "Reproducir canciones, artistas, albumes y playlists.",
            "Pausar, reanudar, saltar, retroceder, cambiar volumen y consultar la cancion actual.",
            "Activar/desactivar autoplay, ver cola y reproducir canciones guardadas.",
            "Buscar canciones, artistas, albumes, letras, historial y canciones con like.",
            "Listar playlists y sacar todas las canciones de una playlist.",
            "Extraer solo los nombres de canciones desde un array de tracks.",
            "Descargar audio de playlists, liked songs o rangos concretos.",
            "Descargar videos de YouTube por URL o busqueda.",
            "Preguntar calidad antes de descargar audio/video.",
            "Ver progreso de descargas, cancelar, pausar, reanudar y reintentar fallidas.",
            "Previsualizar playlists antes de descargarlas.",
            "Abrir carpetas de audio/video descargado y limpiar temporales.",
        ],
    ),
    (
        "Memoria, notas y portapapeles",
        [
            "Guardar memoria personal importante de forma silenciosa.",
            "Listar recuerdos guardados por categoria.",
            "Buscar recuerdos por texto.",
            "Borrar recuerdos concretos por key, categoria o coincidencia.",
            "Guardar notas rapidas con titulo y tags.",
            "Listar notas recientes.",
            "Buscar notas por texto.",
            "Leer el portapapeles.",
            "Copiar texto al portapapeles.",
            "Ver historial local de portapapeles.",
        ],
    ),
    (
        "Sistema, apps y archivos",
        [
            "Abrir aplicaciones por nombre: Chrome, VS Code, WhatsApp, Spotify, Explorer, etc.",
            "Traer ventanas al frente por titulo.",
            "Ver estado del PC: sistema operativo, CPU, RAM, disco, bateria y procesos pesados.",
            "Controlar volumen, brillo, WiFi, ventanas, atajos, pantalla completa y energia.",
            "Escribir texto, pegar, pulsar teclas, hacer hotkeys, clicar, mover y arrastrar el raton.",
            "Buscar elementos en pantalla con vision y hacer click sobre ellos.",
            "Hacer capturas de pantalla.",
            "Listar, crear, leer, escribir, mover, copiar, renombrar y borrar archivos de forma segura.",
            "Buscar archivos por nombre o extension.",
            "Ver archivos recientes.",
            "Abrir/revelar un archivo o carpeta en Explorer.",
            "Ver archivos grandes, uso de disco y organizar escritorio.",
        ],
    ),
    (
        "Mensajeria y comunicacion",
        [
            "Enviar mensajes por WhatsApp usando contactos por nombre.",
            "Enviar archivos por WhatsApp (imagenes, PDF, videos, documentos) a un contacto.",
            "Leer conversaciones de WhatsApp.",
            "Listar mensajes pendientes/no leidos de WhatsApp.",
            "Buscar texto en mensajes recientes o conversaciones de WhatsApp.",
            "Abrir un modo WhatsApp con un chat especifico y ver la conversacion ahi.",
            "Sugerir respuestas de WhatsApp en el cuadro de texto sin enviarlas.",
            "Responder automaticamente a un contacto de WhatsApp durante un tiempo limitado.",
            "Preparar y enviar respuestas a mensajes pendientes.",
            "Enviar mensajes por otras plataformas no-WhatsApp.",
            "Anunciar mensajes entrantes sin responder automaticamente.",
        ],
    ),
    (
        "Google Calendar, Gmail y Drive",
        [
            "Listar proximos eventos.",
            "Ver eventos de hoy.",
            "Crear eventos con fecha en lenguaje natural.",
            "Buscar eventos.",
            "Borrar eventos por ID.",
            "Consultar disponibilidad/free-busy.",
            "Listar emails recientes o no leidos.",
            "Buscar emails con sintaxis Gmail.",
            "Leer emails completos por ID.",
            "Enviar emails.",
            "Hacer resumen rapido del inbox.",
            "Listar y buscar archivos en Google Drive.",
            "Subir y descargar archivos de Google Drive.",
            "Compartir archivos de Drive con permiso lector, comentarista o editor.",
            "Crear carpetas, renombrar archivos, reemplazar contenido y borrar archivos de Drive.",
        ],
    ),
    (
        "Web, navegador y busqueda",
        [
            "Buscar informacion en la web.",
            "Comparar productos, specs, precios o reviews.",
            "Hacer una busqueda web resumida.",
            "Abrir webs en navegador.",
            "Buscar en Google/Bing/DuckDuckGo/Yandex.",
            "Controlar pestanas, recargar, volver, avanzar y cambiar de navegador.",
            "Clicar elementos, rellenar formularios y escribir en paginas.",
            "Leer texto visible de una pagina.",
            "Hacer capturas del navegador.",
        ],
    ),
    (
        "Archivos y procesamiento multimedia",
        [
            "Analizar imagenes, describirlas y hacer OCR.",
            "Redimensionar, convertir, comprimir y recortar imagenes.",
            "Resumir PDFs, extraer texto, extraer paginas y convertir a Word.",
            "Resumir, corregir, reformatear y contar palabras en DOCX/TXT/MD.",
            "Analizar CSV/Excel, filtrar, ordenar, convertir y sacar estadisticas.",
            "Validar, formatear, extraer y convertir JSON/XML.",
            "Explicar, revisar, arreglar, ejecutar, documentar y testear codigo.",
            "Transcribir, recortar, convertir y sacar info de audios.",
            "Recortar videos, extraer audio, extraer frames, comprimir y transcribir.",
            "Listar y extraer archivos ZIP/archives.",
            "Resumir presentaciones y extraer texto.",
        ],
    ),
    (
        "Vision, escritorio y automatizacion",
        [
            "Analizar pantalla o camara cuando preguntas que se ve.",
            "Controlar el escritorio: wallpaper, organizar, limpiar, listar y estadisticas.",
            "Ejecutar tareas multi-paso con agent_task cuando una accion simple no basta.",
            "Crear, editar, explicar, ejecutar o construir codigo.",
            "Crear proyectos multi-archivo con dev_agent.",
            "Programar recordatorios.",
            "Buscar vuelos y leer opciones.",
            "Consultar el tiempo.",
            "Actualizar, instalar y listar juegos de Steam/Epic.",
            "Programar actualizaciones de juegos y apagar el PC al terminar.",
            "Cerrar Jarvis cuando se pide explicitamente.",
        ],
    ),
]


def capabilities_catalog(parameters: dict | None = None, player=None, speak=None) -> str:
    params = parameters or {}
    compact = str(params.get("format", "")).lower().strip() == "compact"

    if player:
        player.write_log("[Capabilities] catalog")

    lines = [
        "Puedo hacer bastante mas que reproducir musica. Lista amplia de capacidades:",
        "",
    ]
    for title, items in CAPABILITY_SECTIONS:
        lines.append(f"{title}:")
        selected = items[:6] if compact else items
        for item in selected:
            lines.append(f"- {item}")
        lines.append("")

    lines.append("Tambien puedo combinar varias de estas acciones si me das un objetivo claro.")
    return "\n".join(lines).strip()
