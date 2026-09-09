<#
    build/build.ps1 — End-to-end packaging for Jarvis (Mark-XXXIX)

    Steps:
      1. Ensure PyInstaller is installed in the current Python environment.
      2. Ensure the WhatsApp bridge has its production node_modules.
      3. Download a portable Node.js runtime (bundled with the app).
      3b. Download the VLC runtime (libvlc + plugins) for video playback.
      3d. Build the web UI frontend (webui/dist), bundled by jarvis.spec.
      4. Freeze the app with PyInstaller (build/jarvis.spec) -> dist/Jarvis.
      5. Copy the portable Node and VLC runtimes into the dist.
      6. Compile the Inno Setup installer (if ISCC is available).

    Run from anywhere; it cd's to the project root.
        pwsh -ExecutionPolicy Bypass -File build/build.ps1
#>
param(
    [string]$NodeVersion = "22.22.0",
    [string]$VlcVersion = "3.0.21",
    [switch]$SkipNode,
    [switch]$SkipVlc,
    [switch]$SkipYtdlp,
    [switch]$SkipInstaller
)

$ErrorActionPreference = "Stop"
# PowerShell otherwise continues after failed npm/python/ISCC commands.
$PSNativeCommandUseErrorActionPreference = $true
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
Write-Host "==> Project root: $Root" -ForegroundColor Cyan

# --- 1. PyInstaller -----------------------------------------------------------
Write-Host "==> Checking PyInstaller..." -ForegroundColor Cyan
if (-not (python -c "import importlib.util; print(bool(importlib.util.find_spec('PyInstaller')))" | Select-String '^True$')) {
    Write-Host "    Installing PyInstaller..." -ForegroundColor Yellow
    python -m pip install --upgrade pyinstaller
}

# --- 2. WhatsApp bridge deps --------------------------------------------------
$Bridge = Join-Path $Root "whatsapp_bridge"
if (-not (Test-Path (Join-Path $Bridge "node_modules"))) {
    Write-Host "==> Installing WhatsApp bridge dependencies (npm)..." -ForegroundColor Cyan
    Push-Location $Bridge
    # postinstall applies the whatsapp-web.js patch using patch-package.
    npm ci --include=dev
    npm prune --omit=dev --ignore-scripts
    Pop-Location
}

foreach ($vendor in @("actions/vendor/webtorrent-stream", "actions/vendor/torlink")) {
    Push-Location (Join-Path $Root $vendor)
    npm install --omit=dev
    Pop-Location
}

# --- 3. Portable Node runtime -------------------------------------------------
$NodeDir = Join-Path $Root "node"
if (-not $SkipNode -and -not (Test-Path (Join-Path $NodeDir "node.exe"))) {
    Write-Host "==> Downloading portable Node.js v$NodeVersion..." -ForegroundColor Cyan
    $zipName = "node-v$NodeVersion-win-x64"
    $url = "https://nodejs.org/dist/v$NodeVersion/$zipName.zip"
    $tmpZip = Join-Path $env:TEMP "$zipName.zip"
    Invoke-WebRequest -Uri $url -OutFile $tmpZip
    $tmpDir = Join-Path $env:TEMP "jarvis_node"
    if (Test-Path $tmpDir) { Remove-Item -Recurse -Force $tmpDir }
    Expand-Archive -Path $tmpZip -DestinationPath $tmpDir
    New-Item -ItemType Directory -Force -Path $NodeDir | Out-Null
    Copy-Item -Recurse -Force (Join-Path $tmpDir "$zipName\*") $NodeDir
    Remove-Item -Force $tmpZip
    Remove-Item -Recurse -Force $tmpDir
}

# --- 3b. Portable VLC runtime (libvlc for python-vlc playback) ----------------
$VlcDir = Join-Path $Root "vlc"
if (-not $SkipVlc -and -not (Test-Path (Join-Path $VlcDir "libvlc.dll"))) {
    Write-Host "==> Downloading VLC v$VlcVersion (libvlc runtime)..." -ForegroundColor Cyan
    $vlcZipName = "vlc-$VlcVersion-win64"
    $vlcUrl = "https://download.videolan.org/pub/videolan/vlc/$VlcVersion/win64/$vlcZipName.zip"
    $vlcTmpZip = Join-Path $env:TEMP "$vlcZipName.zip"
    if (-not (Test-Path $vlcTmpZip)) { Invoke-WebRequest -Uri $vlcUrl -OutFile $vlcTmpZip }
    $vlcTmpDir = Join-Path $env:TEMP "jarvis_vlc"
    if (Test-Path $vlcTmpDir) { Remove-Item -Recurse -Force $vlcTmpDir }
    Expand-Archive -Path $vlcTmpZip -DestinationPath $vlcTmpDir
    # The zip's top-level folder is "vlc-<version>" (no -win64 suffix); locate
    # libvlc.dll instead of assuming the layout.
    $vlcLib = Get-ChildItem -Path $vlcTmpDir -Recurse -Filter "libvlc.dll" | Select-Object -First 1
    if (-not $vlcLib) { throw "libvlc.dll not found inside $vlcTmpZip" }
    $vlcSrc = $vlcLib.DirectoryName
    New-Item -ItemType Directory -Force -Path $VlcDir | Out-Null
    # Only what libvlc needs at runtime: the two DLLs + the plugins tree.
    Copy-Item -Force (Join-Path $vlcSrc "libvlc.dll") $VlcDir
    Copy-Item -Force (Join-Path $vlcSrc "libvlccore.dll") $VlcDir
    Copy-Item -Recurse -Force (Join-Path $vlcSrc "plugins") (Join-Path $VlcDir "plugins")
    Remove-Item -Force $vlcTmpZip
    Remove-Item -Recurse -Force $vlcTmpDir
}

# --- 3c. Standalone yt-dlp.exe (mpv's ytdl_hook needs a real executable; the
# bundled `yt_dlp` Python package is only importable in-process, mpv can't
# spawn it) --------------------------------------------------------------------
$YtdlpExe = Join-Path $Root "yt-dlp.exe"
if (-not $SkipYtdlp -and -not (Test-Path $YtdlpExe)) {
    Write-Host "==> Downloading yt-dlp.exe (standalone binary for mpv)..." -ForegroundColor Cyan
    Invoke-WebRequest -Uri "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe" -OutFile $YtdlpExe
}

# --- 3d. Build the web UI frontend (webui/dist) --------------------------------
# jarvis.spec bundles webui/dist into the frozen app (actions/webui_server.py
# serves it at runtime) — it must exist and be up to date before PyInstaller
# runs, since the spec reads the directory at analysis time.
$WebUi = Join-Path $Root "webui"
Write-Host "==> Building web UI frontend (webui/)..." -ForegroundColor Cyan
if (-not (Test-Path (Join-Path $WebUi "node_modules"))) {
    Push-Location $WebUi
    npm ci
    Pop-Location
}
Push-Location $WebUi
npm run build
Pop-Location
if ($LASTEXITCODE -ne 0) { throw "webui frontend build failed." }

# --- 4. PyInstaller freeze ----------------------------------------------------
Write-Host "==> Freezing app with PyInstaller..." -ForegroundColor Cyan
python -m PyInstaller build/jarvis.spec --noconfirm --distpath dist --workpath build/work
if ($LASTEXITCODE -ne 0) { throw "PyInstaller build failed." }

# --- 5. Bundle portable Node into the dist -----------------------------------
$Internal = Join-Path $Root "dist\Jarvis\_internal"
if (-not (Test-Path $Internal)) { $Internal = Join-Path $Root "dist\Jarvis" }  # older layouts
if ((Test-Path $NodeDir) -and (Test-Path (Join-Path $NodeDir "node.exe"))) {
    Write-Host "==> Copying portable Node runtime into dist..." -ForegroundColor Cyan
    $destNode = Join-Path $Internal "node"
    New-Item -ItemType Directory -Force -Path $destNode | Out-Null
    Copy-Item -Recurse -Force (Join-Path $NodeDir "*") $destNode
} else {
    Write-Host "    (No portable Node found; the app will fall back to a system Node.)" -ForegroundColor Yellow
}

# --- 5b. Bundle the VLC runtime into the dist ----------------------------------
if (Test-Path (Join-Path $VlcDir "libvlc.dll")) {
    Write-Host "==> Copying VLC runtime into dist..." -ForegroundColor Cyan
    $destVlc = Join-Path $Internal "vlc"
    New-Item -ItemType Directory -Force -Path $destVlc | Out-Null
    Copy-Item -Recurse -Force (Join-Path $VlcDir "*") $destVlc
} else {
    Write-Host "    (No VLC runtime found; playback will need a system VLC install.)" -ForegroundColor Yellow
}

# --- 6. Inno Setup installer --------------------------------------------------
if (-not $SkipInstaller) {
    $iscc = (Get-Command iscc -ErrorAction SilentlyContinue).Source
    if (-not $iscc) {
        foreach ($p in @("C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
                         "C:\Program Files\Inno Setup 6\ISCC.exe",
                         "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe")) {
            if (Test-Path $p) { $iscc = $p; break }
        }
    }
    if ($iscc) {
        Write-Host "==> Compiling installer with Inno Setup..." -ForegroundColor Cyan
        & $iscc "installer\jarvis.iss"
        if ($LASTEXITCODE -ne 0) { throw "Inno Setup failed: $LASTEXITCODE" }
        if (-not (Get-ChildItem "dist/installer/Jarvis-Setup-*.exe" -ErrorAction SilentlyContinue)) {
            throw "Inno Setup did not produce an installer."
        }
        Write-Host "==> Installer written to dist\installer\" -ForegroundColor Green
    } else {
        throw "Inno Setup (ISCC) not found. Install Inno Setup 6 or use -SkipInstaller."
    }
}

Write-Host "==> Done. App: dist\Jarvis\Jarvis.exe" -ForegroundColor Green
