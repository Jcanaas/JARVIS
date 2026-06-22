# Empaquetado de Jarvis para producción

Genera un instalador `.exe` autocontenido (sin que el usuario instale Python ni
Node) para Windows x64.

## Requisitos en la máquina de build

- **Python** con el entorno del proyecto (`requirements.txt` instalado).
- **PyInstaller** (`build.ps1` lo instala si falta).
- **Node.js + npm** (para resolver las dependencias del bridge de WhatsApp).
- **Inno Setup 6** — https://jrsoftware.org/isdl.php (para el paso final del `.exe`).
  Si no está instalado, el resto del build se completa y solo se omite el `.exe`.

## Construir todo

```powershell
pwsh -ExecutionPolicy Bypass -File build/build.ps1
```

Pasos que ejecuta:

1. Instala PyInstaller si falta.
2. `npm install --omit=dev` en `whatsapp_bridge/` si no hay `node_modules`.
3. Descarga un **Node.js portable** a `node/` (se empaqueta con la app).
4. Congela la app con `build/jarvis.spec` → `dist/Jarvis/`.
5. Copia el Node portable dentro de `dist/Jarvis/_internal/node/`.
6. Compila el instalador con Inno Setup → `dist/installer/Jarvis-Setup-*.exe`.

Flags útiles: `-SkipNode`, `-SkipInstaller`, `-NodeVersion 20.18.1`.

## Artefactos

- `dist/Jarvis/Jarvis.exe` — app congelada (one-folder).
- `dist/installer/Jarvis-Setup-<versión>.exe` — instalador.

## Datos de usuario (importante)

La app **nunca** escribe en su carpeta de instalación. En tiempo de ejecución
crea y usa `%LOCALAPPDATA%\Jarvis`:

- `config/` — `api_keys.json`, credenciales y tokens de Google, etc.
- `memory/` — memoria a largo plazo, historial, cachés.
- `whatsapp_bridge/` — sesión de WhatsApp (`.wwebjs_auth`), `bridge_token`.
- `logs/` — logs del bridge.

Ver `actions/paths.py` (`RESOURCE_DIR` vs `DATA_DIR`). Por eso el instalador
puede ir a Program Files sin problemas de permisos, y al desinstalar **no** se
borran los datos del usuario.

## Onboarding (primer arranque)

Si no hay cuenta de Google conectada, sale un diálogo que guía al usuario para
crear sus credenciales OAuth (Calendar + Gmail + Drive + YouTube en un solo
inicio de sesión), importarlas y autenticarse. WhatsApp muestra el QR de
vinculación dentro de su propio panel.

## Notas / problemas conocidos

- **Python 3.14**: el soporte de PyInstaller es reciente; si algún paquete falla
  al congelar, considera un entorno 3.12/3.13.
- **Playwright** (si se usa `browser_control`): sus navegadores no se empaquetan
  automáticamente; requieren `playwright install` o un bundle aparte.
- Tras congelar, prueba `dist/Jarvis/Jarvis.exe` antes de generar el `.exe`
  final para detectar imports que falten (`hiddenimports` en `jarvis.spec`).
