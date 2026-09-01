#!/usr/bin/env bash
# Setup del entorno de desarrollo de JARVIS en Linux.
#
# Crea (o reutiliza) .venv, instala requirements.txt, los navegadores de
# Playwright y avisa/instala las dependencias de sistema que pip no puede
# resolver (tk para pyautogui/mouseinfo, libvlc si se quiere reproducción VLC).
#
# Uso:
#   ./setup_linux.sh
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

PYTHON_BIN="${PYTHON_BIN:-python3}"

echo "==> Comprobando dependencias de sistema"

missing_system_pkgs=()

# tk: pyautogui -> mouseinfo hace `import tkinter` incondicionalmente y aborta
# el proceso (sys.exit) si falta, aunque no lo uses nunca activamente.
if ! "$PYTHON_BIN" -c "import tkinter" >/dev/null 2>&1; then
    missing_system_pkgs+=("tk")
fi

# node: el puente de WhatsApp (actions/whatsapp_bridge_process.py, JS con
# whatsapp-web.js) necesita un runtime Node; no hay uno empaquetado para Linux
# así que actions/paths.py find_node() cae al `node` del sistema.
if ! command -v node >/dev/null 2>&1; then
    missing_system_pkgs+=("nodejs" "npm")
fi

# mpv: backend de reproducción de audio/vídeo (actions/audio_tags.py,
# actions/youtube_player.py, actions/ytmusic*.py). En Windows va empaquetado
# junto a la app; en Linux no hay binario bundleado, así que hace falta el
# del sistema (se busca con shutil.which("mpv")).
if ! command -v mpv >/dev/null 2>&1; then
    missing_system_pkgs+=("mpv")
fi

# unzip: usado abajo como fallback si la extracción de Chrome de Puppeteer
# (vía npm postinstall) queda incompleta — un bug visto con Node muy reciente.
if ! command -v unzip >/dev/null 2>&1; then
    missing_system_pkgs+=("unzip")
fi

# webkit2gtk + PyGObject: backend de pywebview (actions/webui_host.py) para
# la ventana de la nueva UI web en Linux. Son paquetes de sistema, no
# pip-instalables de forma fiable (se compilan contra las libs GTK del
# sistema) — de ahí que el venv de abajo necesite --system-site-packages
# para verlos. Nombres de paquete apt (Debian/Ubuntu recientes); ajusta si
# tu distro usa otros (p. ej. gir1.2-webkit2-4.0 en versiones más viejas).
apt_extra_pkgs=()
if ! "$PYTHON_BIN" -c "import gi; gi.require_version('WebKit2', '4.1'); from gi.repository import WebKit2" >/dev/null 2>&1; then
    missing_system_pkgs+=("webkit2gtk-4.1" "python-gobject" "gtk3")
    apt_extra_pkgs+=("gir1.2-webkit2-4.1" "python3-gi" "gir1.2-gtk-3.0")
fi

if [ "${#missing_system_pkgs[@]}" -gt 0 ]; then
    echo "    Faltan paquetes de sistema: ${missing_system_pkgs[*]}"
    if command -v pacman >/dev/null 2>&1; then
        echo "    Instalando con pacman (puede pedir tu contraseña)..."
        sudo pacman -S --needed --noconfirm "${missing_system_pkgs[@]}"
    elif command -v apt-get >/dev/null 2>&1; then
        echo "    Instalando con apt (puede pedir tu contraseña)..."
        sudo apt-get update && sudo apt-get install -y python3-tk "${apt_extra_pkgs[@]}"
    else
        echo "    Gestor de paquetes no reconocido; instala manualmente: ${missing_system_pkgs[*]}"
    fi
else
    echo "    OK"
fi

if ! command -v vlc >/dev/null 2>&1; then
    echo "    Nota: VLC no está instalado (opcional, solo si usas reproducción vía libvlc)."
    echo "          Instálalo con: sudo pacman -S vlc   (o el equivalente de tu distro)"
fi

echo "==> Creando entorno virtual (.venv)"
# --system-site-packages: PyGObject (el módulo "gi" que usa pywebview para su
# backend GTK/WebKit2GTK en Linux) no es pip-instalable de forma fiable —
# se compila contra las libs GTK del sistema — así que el venv necesita ver
# los paquetes de sistema en vez de aislarlos del todo.
if [ ! -d ".venv" ]; then
    "$PYTHON_BIN" -m venv --system-site-packages .venv
elif ! grep -q "^include-system-site-packages = true" ".venv/pyvenv.cfg" 2>/dev/null; then
    echo "    Habilitando --system-site-packages en el .venv existente (para pywebview/gi)"
    sed -i 's/^include-system-site-packages = false/include-system-site-packages = true/' ".venv/pyvenv.cfg"
fi

VENV_PY=".venv/bin/python"

echo "==> Asegurando pip"
"$VENV_PY" -m ensurepip --upgrade >/dev/null
"$VENV_PY" -m pip install --upgrade pip >/dev/null

echo "==> Instalando dependencias de requirements.txt"
"$VENV_PY" -m pip install -r requirements.txt

echo "==> Instalando navegador de Playwright (chromium)"
"$VENV_PY" -m playwright install chromium

echo "==> Instalando dependencias Node del puente de WhatsApp"
if command -v npm >/dev/null 2>&1; then
    (cd whatsapp_bridge && npm install)
    # npm bloquea por defecto el postinstall de puppeteer (descarga de Chrome)
    # tras npm 11+; sin aprobarlo, whatsapp-web.js no tiene navegador que usar.
    (cd whatsapp_bridge && npm install-scripts approve puppeteer 2>/dev/null; npm rebuild puppeteer)

    # Visto en la práctica: la extracción del zip de Chrome que hace el
    # postinstall de puppeteer a veces queda a medias (faltan los binarios
    # grandes) con versiones de Node muy recientes. Si el binario no existe,
    # re-extrae el zip descargado con `unzip` del sistema.
    chrome_cache="$HOME/.cache/puppeteer/chrome"
    if [ -d "$chrome_cache" ] && command -v unzip >/dev/null 2>&1; then
        for zip in "$chrome_cache"/*.zip; do
            [ -f "$zip" ] || continue
            dest_dir="$chrome_cache/$(basename "$zip" | sed -E 's/-chrome-linux64\.zip$//' | sed -E 's/^/linux-/')"
            if [ ! -x "$dest_dir/chrome-linux64/chrome" ]; then
                echo "    Extracción de Chrome (puppeteer) incompleta; reextrayendo con unzip..."
                rm -rf "$dest_dir"
                mkdir -p "$dest_dir"
                unzip -q "$zip" -d "$dest_dir"
                chmod +x "$dest_dir/chrome-linux64/chrome" "$dest_dir/chrome-linux64/chrome-wrapper" 2>/dev/null || true
            fi
        done
    fi
else
    echo "    npm no disponible; sáltate esto o instala nodejs/npm y repite."
fi

# Si la carpeta whatsapp_bridge/.wwebjs_auth se copió desde otra máquina/SO
# (p. ej. una migración desde Windows), Chrome deja un lock (SingletonLock/
# Socket/Cookie) apuntando a un proceso que ya no existe aquí; sin borrarlo
# whatsapp-web.js falla con "The browser is already running for ...".
session_dir="$HOME/.jarvis/whatsapp_bridge/.wwebjs_auth/session"
if [ -d "$session_dir" ]; then
    rm -f "$session_dir/SingletonLock" "$session_dir/SingletonSocket" "$session_dir/SingletonCookie"
fi

echo "==> Preparando ~/.Xauthority"
# python3-Xlib (usado por pyautogui/mouseinfo) exige que exista el archivo
# aunque esté vacío; sin él, el proceso muere sin traceback visible.
touch "$HOME/.Xauthority"

echo ""
echo "✅ Setup completo. Arranca JARVIS con:"
echo "   .venv/bin/python main.py"
