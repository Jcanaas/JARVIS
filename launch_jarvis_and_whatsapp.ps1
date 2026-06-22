# Lanzador Jarvis. main.py administra internamente el bridge de WhatsApp.
# Coloca este script en la raíz del repo (Mark-XXXIX). Crea un acceso directo en el Escritorio al final.

try {
    $root = Split-Path -Parent $MyInvocation.MyCommand.Path

    # Rutas
    $logs = Join-Path $root "logs"
    if (-not (Test-Path $logs)) { New-Item -ItemType Directory -Path $logs | Out-Null }

    $venv_pythonw = Join-Path $root ".venv\Scripts\pythonw.exe"
    $venv_python = Join-Path $root ".venv\Scripts\python.exe"
    $pythonw = if (Test-Path $venv_pythonw) { $venv_pythonw } else { "pythonw" }
    $python = if (Test-Path $venv_python) { $venv_python } else { "python" }
    $main_py = Join-Path $root "main.py"

    # Iniciar Jarvis sin consola. main.py inicia y supervisa WhatsApp.
    Write-Output "Iniciando Jarvis (python) sin consola..."
    if (Test-Path $main_py) {
        $jarvis_running = $false
        try {
            $procs = Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe'" -ErrorAction SilentlyContinue
            foreach ($p in $procs) {
                if ($p.CommandLine -and $p.CommandLine -like "*main.py*") { $jarvis_running = $true; break }
            }
        } catch { }

        if (-not $jarvis_running) {
            $jarvis_out = Join-Path $logs "jarvis.log"
            $jarvis_err = Join-Path $logs "jarvis.err"
            try {
                Start-Process -FilePath $python -ArgumentList "-u", "`"$main_py`"" -WorkingDirectory $root -WindowStyle Hidden -RedirectStandardOutput $jarvis_out -RedirectStandardError $jarvis_err -PassThru -ErrorAction SilentlyContinue | Out-Null
            } catch {
                Write-Output "No se pudo iniciar Jarvis (revisa $jarvis_err)."
            }
        } else {
            Write-Output "Jarvis ya estaba en ejecución."
        }
    } else {
        Write-Output "No se encontró 'main.py' en $root. Asegúrate de que el script existe."
    }

    # Crear shortcut en Escritorio que apunta a este script (si no existe)
    $desktop = [Environment]::GetFolderPath("Desktop")
    $lnkPath = Join-Path $desktop "Jarvis (sin consola).lnk"
    if (-not (Test-Path $lnkPath)) {
        $shell = New-Object -ComObject WScript.Shell
        $shortcut = $shell.CreateShortcut($lnkPath)
        $powershell = (Get-Command powershell.exe).Source
        $shortcut.TargetPath = $powershell
        $shortcut.Arguments = "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$root\launch_jarvis_and_whatsapp.ps1`""
        $shortcut.WorkingDirectory = $root
        $shortcut.IconLocation = "$root\resources\icon.ico,0"
        $shortcut.Save()
        Write-Output "Acceso directo creado en el Escritorio: $lnkPath"
    } else {
        Write-Output "Acceso directo ya existe: $lnkPath"
    }

    Write-Output "Listo."
} catch {
    Write-Output "Error lanzador: $_"
}
