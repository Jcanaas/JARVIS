# Fetch the baseline x64 builds, without requiring AVX2/x86-64-v3 CPUs.
$ErrorActionPreference = "Stop"
$PSNativeCommandUseErrorActionPreference = $true
$Root = Split-Path -Parent $PSScriptRoot
$release = gh api repos/shinchiro/mpv-winbuild-cmake/releases/latest | ConvertFrom-Json
$temp = Join-Path $env:RUNNER_TEMP "jarvis-media"
New-Item -ItemType Directory -Force $temp | Out-Null
foreach ($pattern in @('^mpv-x86_64-\d.*\.7z$')) {
    $asset = @($release.assets | Where-Object { $_.name -match $pattern })
    if ($asset.Count -ne 1) { throw "Expected one media asset matching $pattern" }
    $archive = Join-Path $temp $asset[0].name
    Invoke-WebRequest $asset[0].browser_download_url -OutFile $archive
    if ($asset[0].digest -match '^sha256:(.+)$') {
        if ((Get-FileHash $archive -Algorithm SHA256).Hash -ne $Matches[1]) {
            throw "Media archive checksum mismatch"
        }
    }
    & 7z x $archive "-o$temp/extracted" -y | Out-Null
}
$ffmpegUrl = 'https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.7z'
$ffmpegArchive = Join-Path $temp 'ffmpeg.7z'
Invoke-WebRequest $ffmpegUrl -OutFile $ffmpegArchive
$checksum = ((Invoke-RestMethod "$ffmpegUrl.sha256").Trim() -split '\s+')[0]
if ((Get-FileHash $ffmpegArchive -Algorithm SHA256).Hash -ne $checksum) {
    throw 'FFmpeg archive checksum mismatch'
}
& 7z x $ffmpegArchive "-o$temp/extracted" -y | Out-Null
foreach ($name in @("mpv.exe", "ffmpeg.exe", "ffprobe.exe")) {
    $file = Get-ChildItem "$temp/extracted" -Recurse -Filter $name | Select-Object -First 1
    if (-not $file) { throw "Missing media runtime: $name" }
    Copy-Item $file.FullName (Join-Path $Root $name) -Force
}
