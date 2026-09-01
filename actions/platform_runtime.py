"""Capacidades que dependen del sistema operativo, detectadas en cada arranque.

Un solo sitio decide qué implementación usa el resto del código (IPC de mpv,
por ahora), en vez de repartir ``if os.name == "nt"`` por todos los módulos.

**La detección manda; el ajuste guardado es solo informativo.** El valor se
escribe en ``app_settings`` en cada arranque para poder inspeccionarlo desde
Ajustes/logs, pero NADIE debe leerlo para decidir comportamiento: ese archivo
(``config/app_settings.json``) se copia entre máquinas — la migración de
Windows a Linux de este mismo usuario lo hizo — y un valor heredado haría que
Linux intentara abrir named pipes de Windows. Por eso ``audio_ipc_backend()``
recalcula siempre y ``refresh()`` sobreescribe lo que hubiera guardado.
"""
from __future__ import annotations

import os
import sys

# Backends de IPC con mpv. mpv expone --input-ipc-server como named pipe en
# Windows y como socket de dominio Unix en el resto de plataformas, así que el
# transporte (Win32 API vs socket.AF_UNIX) no es intercambiable.
AUDIO_IPC_WINPIPE = "winpipe"
AUDIO_IPC_UNIXSOCKET = "unixsocket"

IS_WINDOWS = os.name == "nt"


def _os_name() -> str:
    if IS_WINDOWS:
        return "windows"
    return "macos" if sys.platform == "darwin" else "linux"


def audio_ipc_backend() -> str:
    """Transporte de IPC con mpv para este sistema. Recalculado, nunca leído."""
    return AUDIO_IPC_WINPIPE if IS_WINDOWS else AUDIO_IPC_UNIXSOCKET


def force_xcb_on_wayland() -> None:
    """En Linux, fuerza el backend XCB (XWayland) de Qt en vez del nativo Wayland.

    Los toasts flotantes (HUD, aviso de WhatsApp) son QWidget frameless que se
    posicionan con ``move()`` a coordenadas absolutas y dependen de
    ``WA_TranslucentBackground`` para el fondo redondeado semitransparente.
    Wayland no garantiza ninguna de las dos cosas a un cliente normal (cada
    compositor decide dónde colocar la ventana, y muchos no componen la
    transparencia de toplevels), lo que en la práctica se ve como una
    notificación en el sitio equivocado, con una altura absurda o con fondo
    opaco. XWayland sí respeta ese modelo de ventana X11, así que forzamos
    ``xcb`` antes de crear la ``QApplication``. Debe llamarse desde ``main()``
    antes de instanciar ``JarvisUI`` (ahí es cuando Qt lee la variable).
    """
    if IS_WINDOWS or sys.platform == "darwin":
        return
    if os.environ.get("WAYLAND_DISPLAY") or os.environ.get("XDG_SESSION_TYPE") == "wayland":
        os.environ["QT_QPA_PLATFORM"] = "xcb"


def sweep_stale_mpv_sockets() -> int:
    """Borra sockets de mpv de procesos que ya no existen. Devuelve cuántos.

    El nombre lleva el PID de la sesión que lo creó, y el atexit que los limpia
    no llega a ejecutarse si la app muere por SIGKILL o corte de luz. Sin este
    barrido, XDG_RUNTIME_DIR va acumulando sockets muertos entre arranques.
    """
    if IS_WINDOWS:
        return 0  # el SO libera los named pipes al morir el proceso
    import glob
    import re

    removed = 0
    sock_dir = os.environ.get("XDG_RUNTIME_DIR") or "/tmp"
    for path in glob.glob(os.path.join(sock_dir, "jarvis_mpv*.sock")):
        match = re.search(r"_(\d+)\.sock$", path)
        if not match:
            continue
        pid = int(match.group(1))
        if pid == os.getpid():
            continue
        try:
            os.kill(pid, 0)  # solo comprueba existencia, no envía señal
            continue         # el proceso sigue vivo: su socket es suyo
        except ProcessLookupError:
            pass
        except PermissionError:
            continue         # existe pero es de otro usuario: no tocar
        try:
            os.unlink(path)
            removed += 1
        except OSError:
            pass
    return removed


# Plan de rendimiento: cuántos recursos del sistema le deja usar Jarvis al
# proceso. "performance" sube la prioridad del proceso y el nº de workers de
# SharedThreadPool (más miniaturas/descargas en paralelo); "saver" hace lo
# contrario para dejar CPU libre a otras apps. El tamaño del pool solo se lee
# una vez, al crear el singleton (actions/perf_helpers.py), así que ese efecto
# concreto pide reiniciar; la prioridad del proceso se puede aplicar en caliente.
PERFORMANCE_PLANS = ("saver", "balanced", "performance")

# nº de workers de SharedThreadPool por plan.
THREAD_POOL_SIZES = {"saver": 2, "balanced": 4, "performance": 8}

# psutil.*_PRIORITY_CLASS solo existen en Windows; en POSIX psutil.Process.nice()
# toma directamente un valor de niceness (-20 a 19, negativo = más prioridad).
_POSIX_NICE = {"saver": 8, "balanced": 0, "performance": -8}


def thread_pool_size(plan: str | None = None) -> int:
    """Nº de workers para SharedThreadPool según el plan activo."""
    if plan is None:
        try:
            from actions import app_settings
            plan = app_settings.get("performance_plan", "balanced")
        except Exception:
            plan = "balanced"
    return THREAD_POOL_SIZES.get(str(plan), THREAD_POOL_SIZES["balanced"])


def apply_performance_plan(plan: str) -> bool:
    """Aplica la prioridad de proceso del plan dado. True si se pudo aplicar.

    Best-effort a propósito: subir prioridad (niceness negativo en Linux/macOS)
    normalmente requiere privilegios que un usuario normal no tiene, y eso no
    debe convertirse en un error visible — el resto del plan (más workers en el
    próximo arranque) sigue aplicándose igual.
    """
    if plan not in PERFORMANCE_PLANS:
        return False
    try:
        import psutil
        proc = psutil.Process(os.getpid())
        if IS_WINDOWS:
            cls = {
                "saver": psutil.BELOW_NORMAL_PRIORITY_CLASS,
                "balanced": psutil.NORMAL_PRIORITY_CLASS,
                "performance": psutil.HIGH_PRIORITY_CLASS,
            }[plan]
            proc.nice(cls)
        else:
            proc.nice(_POSIX_NICE[plan])
        return True
    except Exception as e:
        print(f"[platform_runtime] no se pudo ajustar la prioridad del proceso: {e}")
        return False


def refresh() -> dict:
    """Redetecta y persiste el estado de plataforma. Llamar en cada arranque.

    Devuelve lo detectado. Si la escritura falla (sesión no real, permisos),
    no pasa nada: el valor guardado es solo para diagnóstico.
    """
    stale = sweep_stale_mpv_sockets()
    if stale:
        print(f"[platform_runtime] limpiados {stale} sockets de mpv huérfanos")

    detected = {
        "platform_os": _os_name(),
        "platform_audio_ipc": audio_ipc_backend(),
    }
    try:
        from actions import app_settings
        for key, value in detected.items():
            if app_settings.get(key) != value:
                app_settings.set(key, value)
    except Exception as e:
        print(f"[platform_runtime] no se pudo persistir el estado de plataforma: {e}")
    return detected
