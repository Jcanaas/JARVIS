# Setup del entorno de desarrollo de JARVIS en Windows.
#
# Crea (o reutiliza) .venv, instala requirements.txt y los navegadores de
# Playwright.
#
# Uso (PowerShell):
#   .\setup_windows.ps1

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

Write-Host "==> Comprobando Python"
$pythonCmd = Get-Command python -ErrorAction SilentlyContinue
if (-not $pythonCmd) {
    Write-Error "No se encontró 'python' en el PATH. Instala Python 3.11+ desde https://www.python.org/downloads/ (marca 'Add to PATH') y vuelve a ejecutar este script."
    exit 1
}

$pyVersion = & python -c "import sys; print('%d.%d' % sys.version_info[:2])"
Write-Host "    Python detectado: $pyVersion"

Write-Host "==> Creando entorno virtual (.venv)"
if (-not (Test-Path ".venv")) {
    python -m venv .venv
}

$venvPy = ".\.venv\Scripts\python.exe"

Write-Host "==> Actualizando pip"
& $venvPy -m pip install --upgrade pip | Out-Null

Write-Host "==> Instalando dependencias de requirements.txt"
& $venvPy -m pip install -r requirements.txt

Write-Host "==> Instalando navegador de Playwright (chromium)"
& $venvPy -m playwright install chromium

Write-Host "==> Instalando dependencias Node del puente de WhatsApp"
$npmCmd = Get-Command npm -ErrorAction SilentlyContinue
if ($npmCmd) {
    Push-Location "whatsapp_bridge"
    npm install
    # npm 11+ bloquea por defecto el postinstall de puppeteer (descarga de
    # Chrome); sin aprobarlo, whatsapp-web.js no tiene navegador que usar.
    npm install-scripts approve puppeteer 2>$null
    npm rebuild puppeteer
    Pop-Location
} else {
    Write-Host "    npm no encontrado. Instala Node.js (https://nodejs.org/) y vuelve a ejecutar este script,"
    Write-Host "    o instala manualmente: cd whatsapp_bridge; npm install"
}

# Si whatsapp_bridge\.wwebjs_auth se copió desde otra máquina/SO, Chrome deja
# locks apuntando a un proceso inexistente y whatsapp-web.js falla con
# "The browser is already running for ...".
$sessionDir = Join-Path $env:LOCALAPPDATA "Jarvis\whatsapp_bridge\.wwebjs_auth\session"
if (Test-Path $sessionDir) {
    Remove-Item -Force -ErrorAction SilentlyContinue `
        (Join-Path $sessionDir "SingletonLock"), `
        (Join-Path $sessionDir "SingletonSocket"), `
        (Join-Path $sessionDir "SingletonCookie")
}

Write-Host ""
Write-Host "Nota: mpv (reproducción de audio/vídeo) va empaquetado junto a la app en"
Write-Host "los builds de Windows. Si ejecutas desde código fuente y falla con"
Write-Host "'mpv no disponible', descarga mpv.exe y déjalo en la raíz del proyecto"
Write-Host "o en tools\mpv\ (ver actions\youtube_player.py), o instálalo en el PATH."

Write-Host ""
Write-Host "Nota: si vas a usar reproducción de vídeo vía VLC (actions/vlc_runtime.py"
Write-Host "espera un VLC portable en RESOURCE_DIR\vlc, o el VLC instalado del sistema),"
Write-Host "instala VLC de forma normal: https://www.videolan.org/vlc/"

Write-Host ""
Write-Host "Setup completo. Arranca JARVIS con:"
Write-Host "   .\.venv\Scripts\python.exe main.py"
