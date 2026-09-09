$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$installer = Get-ChildItem "$Root/dist/installer/Jarvis-Setup-*.exe" | Select-Object -First 1
$installDir = Join-Path $env:RUNNER_TEMP "Jarvis-installed"
$log = Join-Path $Root "build/smoke-install.log"
$process = Start-Process $installer.FullName -ArgumentList @('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/SP-', "/DIR=`"$installDir`"", "/LOG=`"$log`"") -PassThru -Wait
if ($process.ExitCode -ne 0) { throw "Installer failed: $($process.ExitCode)" }
foreach ($file in @('Jarvis.exe', '_internal/node/node.exe', '_internal/mpv.exe', '_internal/ffmpeg.exe', '_internal/vlc/libvlc.dll', '_internal/webui/dist/index.html')) {
    if (-not (Test-Path (Join-Path $installDir $file))) { throw "Installed file missing: $file" }
}
if (Test-Path "$installDir/_internal/config/api_keys.json") { throw "API keys must not be packaged" }
$app = Start-Process "$installDir/Jarvis.exe" -WorkingDirectory $installDir -PassThru
try {
    $ready = $false
    for ($i = 0; $i -lt 60; $i++) {
        Start-Sleep -Seconds 2
        if ($app.HasExited) { throw "Jarvis exited during first launch: $($app.ExitCode)" }
        try {
            $response = Invoke-RestMethod 'http://127.0.0.1:8877/api/setup/status' -TimeoutSec 2
            if ($response.ready -eq $false) {
                $page = Invoke-WebRequest 'http://127.0.0.1:8877/' -TimeoutSec 2
                if ($page.Content -match '<script') { $ready = $true; break }
            }
        } catch { Write-Host "Waiting for first-run UI..." }
    }
    if (-not $ready) { throw "First-run setup did not become available" }
    Start-Sleep -Seconds 10
    if ($app.HasExited) { throw "Jarvis exited after startup" }
    Write-Host 'PASS: installer, bundled runtimes, app process and first-run HTTP UI.'
} finally {
    if (-not $app.HasExited) { Stop-Process -Id $app.Id -Force }
    $crash = Join-Path $env:LOCALAPPDATA 'Jarvis/logs/crash.log'
    if (Test-Path $crash) { Copy-Item $crash "$Root/build/smoke-crash.log" }
}
$uninstall = Start-Process "$installDir/unins000.exe" -ArgumentList '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART' -PassThru -Wait
if ($uninstall.ExitCode -ne 0) { throw "Uninstall failed: $($uninstall.ExitCode)" }
