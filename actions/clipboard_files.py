"""Extrae rutas de archivo desde un QMimeData (portapapeles o arrastrar-soltar).

Las imágenes pegadas (p. ej. capturas de pantalla) se guardan como PNG temporal y
se devuelve su ruta, de modo que puedan adjuntarse o enviarse como un archivo más.
"""
from __future__ import annotations

import tempfile
import time
from pathlib import Path


def mime_to_files(mime) -> list[str]:
    """Devuelve rutas locales presentes en `mime`. Imágenes -> PNG temporal."""
    paths: list[str] = []
    if mime is None:
        return paths

    try:
        if mime.hasUrls():
            for url in mime.urls():
                if url.isLocalFile():
                    local = url.toLocalFile()
                    if local:
                        paths.append(local)
    except Exception:
        pass

    if paths:
        return paths

    try:
        if mime.hasImage():
            from PyQt6.QtGui import QImage

            image = mime.imageData()
            if not isinstance(image, QImage):
                image = QImage(image) if image is not None else QImage()
            if not image.isNull():
                out = Path(tempfile.gettempdir()) / f"jarvis-paste-{int(time.time() * 1000)}.png"
                if image.save(str(out), "PNG"):
                    paths.append(str(out))
    except Exception:
        pass

    return paths
