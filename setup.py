"""Punto de entrada legado. Usa el script de tu plataforma en su lugar:

    Windows : .\\setup_windows.ps1
    Linux   : ./setup_linux.sh

Esos scripts crean el .venv, instalan requirements.txt, los navegadores de
Playwright, las dependencias de sistema (tk en Linux, Node) y las
dependencias del puente de WhatsApp (whatsapp_bridge/npm install) — este
archivo por sí solo ya no basta.
"""
import platform
import sys

script = "setup_windows.ps1" if platform.system() == "Windows" else "setup_linux.sh"
print(__doc__)
print(f"Ejecuta: {script}")
sys.exit(1)
