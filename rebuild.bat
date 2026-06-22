@echo off
REM ============================================================
REM  Jarvis - reconstruir app + instalador (doble clic o CLI)
REM
REM  Uso:
REM    rebuild.bat                 -> congela la app, empaqueta Node y
REM                                   compila dist\installer\Jarvis-Setup-*.exe
REM    rebuild.bat -SkipInstaller  -> solo congela la app (sin instalador)
REM    rebuild.bat -SkipNode       -> no vuelve a descargar Node portable
REM
REM  Requiere: el entorno del proyecto (.venv) y, para el instalador,
REM  Inno Setup 6 instalado.
REM ============================================================
setlocal
cd /d "%~dp0"
echo ============================================
echo   Reconstruyendo Jarvis...
echo ============================================

REM --- Usar el interprete del proyecto si hay un .venv ---
if exist ".venv\Scripts\activate.bat" (
    echo [*] Activando entorno virtual .venv
    call ".venv\Scripts\activate.bat"
)

REM --- Ejecutar el pipeline de build (PowerShell 7 si existe, si no Windows PowerShell) ---
where pwsh >nul 2>nul
if %errorlevel%==0 (
    pwsh -ExecutionPolicy Bypass -File "build\build.ps1" %*
) else (
    powershell -ExecutionPolicy Bypass -File "build\build.ps1" %*
)

if errorlevel 1 (
    echo.
    echo [X] El build ha FALLADO. Revisa los mensajes de arriba.
    pause
    exit /b 1
)

echo.
echo [OK] Listo.
echo     App:        dist\Jarvis\Jarvis.exe
echo     Instalador: dist\installer\Jarvis-Setup-1.0.0.exe
echo.
pause
