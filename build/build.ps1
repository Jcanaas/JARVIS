<#
    build/build.ps1 — End-to-end packaging for Jarvis (Mark-XXXIX)

    Steps:
      1. Ensure PyInstaller is installed in the current Python environment.
      2. Ensure the WhatsApp bridge has its production node_modules.
      3. Download a portable Node.js runtime (bundled with the app).
      4. Freeze the app with PyInstaller (build/jarvis.spec) -> dist/Jarvis.
      5. Copy the portable Node runtime into the dist.
      6. Compile the Inno Setup installer (if ISCC is available).

    Run from anywhere; it cd's to the project root.
        pwsh -ExecutionPolicy Bypass -File build/build.ps1
#>
param(
    [string]$NodeVersion = "20.18.1",
    [switch]$SkipNode,
    [switch]$SkipInstaller
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
Write-Host "==> Project root: $Root" -ForegroundColor Cyan

# --- 1. PyInstaller -----------------------------------------------------------
Write-Host "==> Checking PyInstaller..." -ForegroundColor Cyan
python -c "import PyInstaller" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "    Installing PyInstaller..." -ForegroundColor Yellow
    python -m pip install --upgrade pyinstaller
}

# --- 2. WhatsApp bridge deps --------------------------------------------------
$Bridge = Join-Path $Root "whatsapp_bridge"
if (-not (Test-Path (Join-Path $Bridge "node_modules"))) {
    Write-Host "==> Installing WhatsApp bridge dependencies (npm)..." -ForegroundColor Cyan
    Push-Location $Bridge
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
        Write-Host "==> Installer written to dist\installer\" -ForegroundColor Green
    } else {
        Write-Host "==> Inno Setup (ISCC) not found. Install it from https://jrsoftware.org/isdl.php" -ForegroundColor Yellow
        Write-Host "    Then run: ISCC.exe installer\jarvis.iss" -ForegroundColor Yellow
    }
}

Write-Host "==> Done. App: dist\Jarvis\Jarvis.exe" -ForegroundColor Green
