"""Manage the local WhatsApp Node bridge as a child of Jarvis."""
from __future__ import annotations

import atexit
import os
import shutil
import subprocess
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen


from actions.paths import RESOURCE_DIR, LOGS_DIR, WHATSAPP_DIR

BRIDGE_URL = "http://127.0.0.1:3000/status"
BRIDGE_DIR = RESOURCE_DIR / "whatsapp_bridge"          # read-only JS bundle
BRIDGE_LOG = LOGS_DIR / "bridge.log"                   # writable log
BRIDGE_LOG_MAX_BYTES = 10 * 1024 * 1024  # 10 MB rotation threshold


def _locate_node() -> str | None:
    """Prefer the bundled Node runtime; fall back to a system install."""
    for candidate in (
        RESOURCE_DIR / "node" / "node.exe",
        RESOURCE_DIR / "node" / "bin" / "node",
    ):
        if candidate.is_file():
            return str(candidate)
    return shutil.which("node")

_process: subprocess.Popen | None = None
_job_handle = None


def _rotate_log_if_needed():
    """Rotate bridge.log if it exceeds max size."""
    if not BRIDGE_LOG.exists():
        return
    try:
        if BRIDGE_LOG.stat().st_size > BRIDGE_LOG_MAX_BYTES:
            backup = BRIDGE_LOG.with_suffix(".log.1")
            if backup.exists():
                backup.unlink()
            BRIDGE_LOG.rename(backup)
    except OSError:
        pass


def bridge_running(timeout: float = 0.6) -> bool:
    try:
        with urlopen(BRIDGE_URL, timeout=timeout) as response:
            return response.status == 200
    except (OSError, URLError):
        return False


def _attach_windows_job(process: subprocess.Popen) -> bool:
    """Ensure the bridge is killed even if the Python process crashes."""
    global _job_handle
    if os.name != "nt":
        return False
    try:
        import ctypes
        import ctypes.wintypes as wt

        kernel32 = ctypes.windll.kernel32
        kernel32.CreateJobObjectW.restype = wt.HANDLE
        kernel32.AssignProcessToJobObject.argtypes = [wt.HANDLE, wt.HANDLE]
        kernel32.SetInformationJobObject.argtypes = [
            wt.HANDLE, ctypes.c_int, ctypes.c_void_p, wt.DWORD,
        ]

        class BasicLimitInformation(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_longlong),
                ("PerJobUserTimeLimit", ctypes.c_longlong),
                ("LimitFlags", wt.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wt.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wt.DWORD),
                ("SchedulingClass", wt.DWORD),
            ]

        class IoCounters(ctypes.Structure):
            _fields_ = [
                ("ReadOperationCount", ctypes.c_ulonglong),
                ("WriteOperationCount", ctypes.c_ulonglong),
                ("OtherOperationCount", ctypes.c_ulonglong),
                ("ReadTransferCount", ctypes.c_ulonglong),
                ("WriteTransferCount", ctypes.c_ulonglong),
                ("OtherTransferCount", ctypes.c_ulonglong),
            ]

        class ExtendedLimitInformation(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", BasicLimitInformation),
                ("IoInfo", IoCounters),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        job = kernel32.CreateJobObjectW(None, None)
        if not job:
            return False
        info = ExtendedLimitInformation()
        info.BasicLimitInformation.LimitFlags = 0x00002000
        if not kernel32.SetInformationJobObject(
            job, 9, ctypes.byref(info), ctypes.sizeof(info),
        ):
            kernel32.CloseHandle(job)
            return False
        if not kernel32.AssignProcessToJobObject(job, wt.HANDLE(int(process._handle))):
            kernel32.CloseHandle(job)
            return False
        _job_handle = job
        return True
    except Exception:
        return False


def start_bridge(wait_seconds: float = 15.0) -> bool:
    """Start the bridge when needed and wait until its HTTP server responds."""
    global _process
    if bridge_running():
        return True
    if not BRIDGE_DIR.is_dir() or not (BRIDGE_DIR / "index.js").is_file():
        return False
    node = _locate_node()
    if not node:
        return False

    # Tell the Node bridge to persist its session/token in the writable data dir.
    try:
        WHATSAPP_DIR.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    env = os.environ.copy()
    env["JARVIS_WA_DATA"] = str(WHATSAPP_DIR)

    _rotate_log_if_needed()
    try:
        log_file = open(BRIDGE_LOG, "a", encoding="utf-8")
    except OSError:
        log_file = subprocess.DEVNULL

    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    _process = subprocess.Popen(
        [node, "index.js"],
        cwd=BRIDGE_DIR,
        stdin=subprocess.DEVNULL,
        stdout=log_file,
        stderr=subprocess.STDOUT,
        creationflags=creationflags,
        env=env,
    )
    _attach_windows_job(_process)

    deadline = time.monotonic() + max(0.1, wait_seconds)
    while time.monotonic() < deadline:
        if _process.poll() is not None:
            _process = None
            return False
        if bridge_running():
            return True
        time.sleep(0.2)
    return bridge_running()


def stop_bridge() -> None:
    """Stop only the bridge process created by this Jarvis instance."""
    global _process, _job_handle
    process = _process
    _process = None
    if process is not None and process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=4)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=2)
    if _job_handle and os.name == "nt":
        try:
            import ctypes
            ctypes.windll.kernel32.CloseHandle(_job_handle)
        except Exception:
            pass
    _job_handle = None


atexit.register(stop_bridge)
