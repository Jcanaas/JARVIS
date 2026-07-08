from __future__ import annotations

import math
import platform
import random
import subprocess
import threading
import time

import psutil
from PyQt6.QtCore import (
    QEasingCurve, QPointF, QPropertyAnimation, QRectF, Qt, QTimer, pyqtProperty,
)
from PyQt6.QtGui import (
    QBrush, QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap, QRadialGradient,
)
from PyQt6.QtWidgets import QSizePolicy, QWidget

from ..theme import C, FONT_UI, qcol


def _set_windows_app_id() -> None:
    if platform.system() != "Windows":
        return
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Mark-XXXIX.JARVIS")
    except Exception:
        pass


_OS = platform.system()


class _SysMetrics:
    def __init__(self):
        self.cpu  = 0.0
        self.mem  = 0.0
        self.net  = 0.0
        self.gpu  = -1.0
        self.tmp  = -1.0
        self._lock = threading.Lock()
        self._last_net = psutil.net_io_counters()
        self._last_net_t = time.time()
        self._running = True
        t = threading.Thread(target=self._loop, daemon=True)
        t.start()

    def _loop(self):
        while self._running:
            try:
                self._update()
            except Exception:
                pass
            time.sleep(1.5)

    def _update(self):
        cpu = psutil.cpu_percent(interval=None)
        mem = psutil.virtual_memory().percent

        nc  = psutil.net_io_counters()
        now = time.time()
        dt  = now - self._last_net_t
        if dt > 0:
            sent = (nc.bytes_sent - self._last_net.bytes_sent) / dt
            recv = (nc.bytes_recv - self._last_net.bytes_recv) / dt
            net  = (sent + recv) / (1024 * 1024)
        else:
            net = 0.0
        self._last_net   = nc
        self._last_net_t = now

        gpu = self._get_gpu()

        tmp = self._get_temp()

        with self._lock:
            self.cpu = cpu
            self.mem = mem
            self.net = net
            self.gpu = gpu
            self.tmp = tmp

    def _get_gpu(self) -> float:
        # NVIDIA
        try:
            r = subprocess.run(
                ["nvidia-smi", "--query-gpu=utilization.gpu",
                 "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=2
            )
            if r.returncode == 0:
                vals = [float(v.strip()) for v in r.stdout.strip().split("\n") if v.strip()]
                if vals:
                    return sum(vals) / len(vals)
        except Exception:
            pass

        # AMD (Linux)
        if _OS == "Linux":
            try:
                r = subprocess.run(
                    ["rocm-smi", "--showuse", "--csv"],
                    capture_output=True, text=True, timeout=2
                )
                if r.returncode == 0:
                    for line in r.stdout.strip().split("\n"):
                        parts = line.split(",")
                        if len(parts) >= 2:
                            try:
                                return float(parts[1].strip().replace("%", ""))
                            except ValueError:
                                pass
            except Exception:
                pass

            # Intel GPU (Linux)
            try:
                r = subprocess.run(
                    ["intel_gpu_top", "-J", "-s", "500"],
                    capture_output=True, text=True, timeout=1
                )
                if r.returncode == 0 and "Render/3D" in r.stdout:
                    import re
                    m = re.search(r'"busy":\s*([\d.]+)', r.stdout)
                    if m:
                        return float(m.group(1))
            except Exception:
                pass

        # macOS — powermetrics (GPU Engine)
        if _OS == "Darwin":
            try:
                r = subprocess.run(
                    ["sudo", "-n", "powermetrics", "-n", "1", "-i", "500",
                     "--samplers", "gpu_power"],
                    capture_output=True, text=True, timeout=2
                )
                if r.returncode == 0 and "GPU" in r.stdout:
                    import re
                    m = re.search(r'GPU\s+Active:\s+([\d.]+)%', r.stdout)
                    if m:
                        return float(m.group(1))
            except Exception:
                pass

        return -1.0

    def _get_temp(self) -> float:
        try:
            temps = psutil.sensors_temperatures()
            candidates = ["coretemp", "k10temp", "cpu_thermal", "acpitz",
                          "cpu-thermal", "zenpower", "it8688"]
            for name in candidates:
                if name in temps:
                    entries = temps[name]
                    if entries:
                        return entries[0].current
            for entries in temps.values():
                if entries:
                    return entries[0].current
        except Exception:
            pass
        if _OS == "Darwin":
            try:
                r = subprocess.run(
                    ["osx-cpu-temp"], capture_output=True, text=True, timeout=2
                )
                if r.returncode == 0:
                    import re
                    m = re.search(r"([\d.]+)", r.stdout)
                    if m:
                        return float(m.group(1))
            except Exception:
                pass

        if _OS == "Windows":
            try:
                r = subprocess.run(
                    ["powershell", "-Command",
                     "(Get-WmiObject MSAcpi_ThermalZoneTemperature -Namespace root/wmi).CurrentTemperature"],
                    capture_output=True, text=True, timeout=3
                )
                if r.returncode == 0 and r.stdout.strip():
                    raw = float(r.stdout.strip().split("\n")[0])
                    return (raw / 10.0) - 273.15
            except Exception:
                pass

        return -1.0

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "cpu": self.cpu,
                "mem": self.mem,
                "net": self.net,
                "gpu": self.gpu,
                "tmp": self.tmp,
            }


_metrics = _SysMetrics()


class HudCanvas(QWidget):
    def __init__(self, face_path: str, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
        self.setMinimumSize(300, 300)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        self.muted    = False
        self.speaking = False
        self.state    = "INITIALISING"

        self._tick       = 0
        self._scale      = 1.0
        self._tgt_scale  = 1.0
        self._halo       = 55.0
        self._tgt_halo   = 55.0
        self._last_t     = time.time()
        self._scan       = 0.0
        self._scan2      = 180.0
        self._rings      = [0.0, 120.0, 240.0]
        self._pulses: list[float] = [0.0, 50.0, 100.0]
        self._blink      = True
        self._blink_tick = 0
        self._particles: list[list[float]] = []
        self._audio_level = 0.0          # 0.0-1.0, driven externally
        self._bass   = 0.0               # low-freq band level 0-1
        self._mid    = 0.0               # mid-freq band level 0-1
        self._treble = 0.0               # high-freq band level 0-1
        self._last_audio_t = 0.0         # timestamp del último audio detectado
        self.music_playing = False       # True while a track is playing
        _N = 64
        self._bar_heights = [0.0] * _N
        self._audio_data_lock = threading.Lock()
        self._pending_fft: list[float] | None = None
        self._pending_bands: tuple[float, float, float] | None = None
        self._bar_phases  = [random.uniform(0, 2 * math.pi) for _ in range(_N)]
        self._rot_angle   = 0.0

        self._tmr = QTimer(self)
        self._tmr.setTimerType(Qt.TimerType.PreciseTimer)
        self._tmr.timeout.connect(self._step)
        self._tmr.start(33)

    def burst(self):
        """Onda expansiva puntual — feedback al enviar una orden."""
        self._pulses.append(0.0)
        self._pulses.append(26.0)

    def _load_face(self, path: str):
        try:
            from PIL import Image, ImageDraw
            import io
            img = Image.open(path).convert("RGBA")
            sz  = min(img.size)
            img = img.resize((sz, sz), Image.LANCZOS)
            mk  = Image.new("L", (sz, sz), 0)
            ImageDraw.Draw(mk).ellipse((2, 2, sz - 2, sz - 2), fill=255)
            img.putalpha(mk)
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            px = QPixmap(); px.loadFromData(buf.getvalue())
            self._face_px = px
        except Exception:
            self._face_px = None

    def _step(self):
        self._tick += 1
        now = time.time()
        with self._audio_data_lock:
            pending_fft = self._pending_fft
            pending_bands = self._pending_bands
            self._pending_fft = None
            self._pending_bands = None

        if pending_bands is not None:
            self._bass, self._mid, self._treble = pending_bands
            self._audio_level = max(pending_bands)
        if pending_fft is not None:
            for i, value in enumerate(pending_fft[:len(self._bar_heights)]):
                if value > self._bar_heights[i]:
                    self._bar_heights[i] = value

        _al = max(self._bass, self._mid, self._treble, self._audio_level)
        _active = self.speaking or self.music_playing

        if _al > 0.02:
            # ataque directo: sin interpolación, reacción inmediata
            self._scale = 1.0 + _al * 0.32
            self._halo  = 58.0 + _al * 180.0
            self._last_t = now
            self._last_audio_t = now
        elif now - self._last_audio_t < 0.18:
            # 180ms de hold + decaimiento suave tras el audio
            self._scale += (1.0  - self._scale) * 0.22
            self._halo  += (55.0 - self._halo)  * 0.22
        elif _active:
            # hablando/música sin nivel detectable: pulso suave
            self._scale += (1.015 - self._scale) * 0.14
            self._halo  += (70.0  - self._halo)  * 0.14
        else:
            # reposo total: respiración mínima y estable
            breath = math.sin(self._tick * 0.012) * 0.003
            self._scale = 1.001 + breath
            self._halo  = 52.0  + breath * 1200

        speeds = [1.3, -0.9, 2.0] if self.speaking else ([0.9, -0.65, 1.4] if self.music_playing else [0.55, -0.35, 0.9])
        for i, spd in enumerate(speeds):
            self._rings[i] = (self._rings[i] + spd) % 360

        self._scan  = (self._scan  + (3.0 if self.speaking else (2.0 if self.music_playing else 1.3))) % 360
        self._scan2 = (self._scan2 + (-2.0 if self.speaking else (-1.4 if self.music_playing else -0.75))) % 360
        rot_spd = 1.8 if (self.speaking or self.music_playing) else (0.9 if _al > 0.02 else 0.25)
        self._rot_angle = (self._rot_angle + rot_spd) % 360
        # decay de barras FFT cada tick (en-place para no perder escrituras del hilo de audio)
        _decay = 0.84
        for _i in range(len(self._bar_heights)):
            self._bar_heights[_i] *= _decay

        fw  = min(self.width(), self.height())
        lim = fw * 0.74
        spd = 4.2 if self.speaking else (3.2 if self.music_playing else 2.0)
        self._pulses = [r + spd for r in self._pulses if r + spd < lim]
        # los golpes de bajo emiten pulsos extra
        _emit = 0.12 if self._bass > 0.35 else (0.07 if self.speaking else (0.05 if self.music_playing else 0.025))
        if len(self._pulses) < 5 and random.random() < _emit:
            self._pulses.append(0.0)

        if (self.speaking or self.music_playing) and random.random() < (0.28 if self.speaking else 0.1):
            cx, cy = self.width() / 2, self.height() / 2
            ang = random.uniform(0, 2 * math.pi)
            r_s = fw * 0.28
            self._particles.append([
                cx + math.cos(ang) * r_s, cy + math.sin(ang) * r_s,
                math.cos(ang) * random.uniform(0.9, 2.4),
                math.sin(ang) * random.uniform(0.9, 2.4) - 0.4, 1.0,
            ])
        self._particles = [
            [p[0]+p[2], p[1]+p[3], p[2]*0.97, p[3]*0.97, p[4]-0.028]
            for p in self._particles if p[4] > 0
        ]

        self._blink_tick += 1
        if self._blink_tick >= 38:
            self._blink = not self._blink
            self._blink_tick = 0



        self.update()

    def set_audio_level(self, level: float):
        """Set real-time audio amplitude (0.0–1.0) for the orb visualizer."""
        self._audio_level = max(0.0, min(1.0, float(level)))

    def set_audio_bands(self, bass: float, mid: float, treble: float):
        """Set per-band levels (0-1). Drives frequency-aware waveform shape."""
        values = (
            max(0.0, min(1.0, float(bass))),
            max(0.0, min(1.0, float(mid))),
            max(0.0, min(1.0, float(treble))),
        )
        with self._audio_data_lock:
            self._pending_bands = values

    def set_fft_bins(self, bins):
        """bins: lista de 64 floats 0-1 con amplitud por banda de frecuencia."""
        latest = [
            max(0.0, min(1.0, float(v)))
            for v in list(bins)[:len(self._bar_heights)]
        ]
        with self._audio_data_lock:
            self._pending_fft = latest

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        W, H = self.width(), self.height()
        cx, cy = W / 2, H / 2
        fw = min(W, H)

        bg = QLinearGradient(QPointF(0, 0), QPointF(W, H))
        bg.setColorAt(0.0, qcol("#101A45"))
        bg.setColorAt(0.45, qcol("#0A0C16"))
        bg.setColorAt(1.0, qcol("#0A0F33"))
        p.fillRect(self.rect(), QBrush(bg))

        glow = QRadialGradient(QPointF(W * 0.42, H * 0.34), fw * 0.78)
        glow.setColorAt(0.0, qcol(C.PRI_DIM, 52))
        glow.setColorAt(0.42, qcol(C.PRI_GHO, 34))
        glow.setColorAt(1.0, qcol(C.BG, 0))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(glow))
        p.drawEllipse(QRectF(W * 0.02, H * -0.10, W * 0.92, H * 0.92))

        glow2 = QRadialGradient(QPointF(W * 0.70, H * 0.68), fw * 0.55)
        glow2.setColorAt(0.0, qcol(C.ACC, 24))
        glow2.setColorAt(1.0, qcol(C.BG, 0))
        p.setBrush(QBrush(glow2))
        p.drawEllipse(QRectF(W * 0.40, H * 0.32, W * 0.72, H * 0.72))

        r_face = fw * 0.262

        # halo glow
        for i in range(10):
            r   = r_face * (1.8 - i * 0.08)
            frc = 1.0 - i / 10
            a   = max(0, min(255, int(self._halo * 0.085 * frc)))
            col = qcol(C.PRI, a)
            p.setPen(QPen(col, 1.5)); p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(QRectF(cx - r, cy - r, r * 2, r * 2))

        # pulse rings
        for pr in self._pulses:
            a   = max(0, int(230 * (1.0 - pr / (fw * 0.74))))
            col = qcol(C.PRI, a)
            p.setPen(QPen(col, 1.5)); p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(QRectF(cx - pr, cy - pr, pr * 2, pr * 2))

        # spinning arc rings
        for idx, (r_frac, w_r, arc_l, gap) in enumerate(
            [(0.48, 3, 115, 78), (0.40, 2, 78, 55), (0.32, 1, 56, 40)]
        ):
            ring_r = fw * r_frac
            base   = self._rings[idx]
            a_val  = max(0, min(255, int(self._halo * (1.0 - idx * 0.18))))
            col    = qcol(C.PRI, a_val)
            p.setPen(QPen(col, w_r)); p.setBrush(Qt.BrushStyle.NoBrush)
            angle = base
            rect  = QRectF(cx - ring_r, cy - ring_r, ring_r * 2, ring_r * 2)
            while angle < base + 360:
                p.drawArc(rect, int(angle * 16), int(arc_l * 16))
                angle += arc_l + gap

        # scanners
        sr = fw * 0.50
        sa = min(255, int(self._halo * 1.5))
        ex = 75 if self.speaking else 44
        p.setPen(QPen(qcol(C.PRI, sa), 2.5))
        p.setBrush(Qt.BrushStyle.NoBrush)
        srect = QRectF(cx - sr, cy - sr, sr * 2, sr * 2)
        p.drawArc(srect, int(self._scan * 16), int(ex * 16))
        p.setPen(QPen(qcol(C.ACC, sa // 2), 1.5))
        p.drawArc(srect, int(self._scan2 * 16), int(ex * 16))

        # tick marks
        t_out, t_in = fw * 0.497, fw * 0.474
        p.setPen(QPen(qcol(C.PRI, 140), 1))
        for deg in range(0, 360, 10):
            rad = math.radians(deg)
            inn = t_in if deg % 30 == 0 else t_in + 6
            p.drawLine(
                QPointF(cx + t_out * math.cos(rad), cy - t_out * math.sin(rad)),
                QPointF(cx + inn  * math.cos(rad), cy - inn  * math.sin(rad)),
            )

        # crosshair
        ch_r, gap_h = fw * 0.51, fw * 0.16
        p.setPen(QPen(qcol(C.PRI, int(self._halo * 0.5)), 1))
        p.drawLine(QPointF(cx - ch_r, cy), QPointF(cx - gap_h, cy))
        p.drawLine(QPointF(cx + gap_h, cy), QPointF(cx + ch_r, cy))
        p.drawLine(QPointF(cx, cy - ch_r), QPointF(cx, cy - gap_h))
        p.drawLine(QPointF(cx, cy + gap_h), QPointF(cx, cy + ch_r))

        # corner brackets
        bl = 24
        bc = qcol(C.PRI, 210)
        hl, hr = cx - fw // 2, cx + fw // 2
        ht, hb = cy - fw // 2, cy + fw // 2
        p.setPen(QPen(bc, 2))
        for bx, by, dx, dy in [(hl,ht,1,1),(hr,ht,-1,1),(hl,hb,1,-1),(hr,hb,-1,-1)]:
            p.drawLine(QPointF(bx, by), QPointF(bx + dx * bl, by))
            p.drawLine(QPointF(bx, by), QPointF(bx, by + dy * bl))

        # --- central reactive orb ---
        r_orb_base = fw * 0.262
        orb_r = r_orb_base * self._scale   # crece con el audio
        al = self._audio_level

        # fill gradient
        grad = QRadialGradient(QPointF(cx, cy), orb_r)
        if self.speaking:
            lv = min(1.0, al * 1.4 + 0.30)
            grad.setColorAt(0.0, qcol("#FFFFFF", min(255, int(90 + 165 * lv))))
            grad.setColorAt(0.22, qcol(C.ACC, min(255, int(140 + 115 * lv))))
            grad.setColorAt(0.62, qcol(C.PRI_DIM, min(200, int(75 + 125 * lv))))
            grad.setColorAt(0.90, qcol(C.PRI_GHO, 35))
            grad.setColorAt(1.0, qcol(C.BG, 0))
        else:
            grad.setColorAt(0.0, qcol(C.ACC, min(200, int(85 + 115 * al))))
            grad.setColorAt(0.40, qcol(C.PRI_DIM, min(180, int(80 + 100 * al))))
            grad.setColorAt(0.78, qcol(C.PRI_GHO, 40))
            grad.setColorAt(1.0, qcol(C.BG, 0))
        p.setBrush(QBrush(grad)); p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(QRectF(cx - orb_r, cy - orb_r, orb_r * 2, orb_r * 2))

        # outer glow ring
        ring_a = min(255, int(self._halo * 2.0))
        if self.speaking:
            ring_col = qcol(C.ACC, ring_a)
        else:
            ring_col = qcol(C.PRI, ring_a)
        p.setPen(QPen(ring_col, 2.0)); p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(QRectF(cx - orb_r, cy - orb_r, orb_r * 2, orb_r * 2))

        # --- equalizer vertical (estilo reproductor de música) ---
        if True:
            n_disp     = 32
            n_src      = len(self._bar_heights)
            step       = max(1, n_src // n_disp)
            bar_area_w = r_orb_base * 2.6
            bar_slot_w = bar_area_w / n_disp
            bar_w      = bar_slot_w * 0.62
            bar_max_h  = fw * 0.17
            baseline_y = cy + r_orb_base * 1.18
            for i in range(n_disp):
                idx   = min(i * step, n_src - 1)
                group = self._bar_heights[idx : idx + step]
                h     = max(group) if group else 0.0
                idle_h   = 0.022 + 0.010 * math.sin(self._tick * 0.016 + i * 0.25)
                display_h = max(h, idle_h)
                bar_h = display_h * bar_max_h
                bx    = cx - bar_area_w / 2 + i * bar_slot_w + (bar_slot_w - bar_w) / 2
                # gradiente: base oscura → punta cian brillante
                grad = QLinearGradient(QPointF(bx, baseline_y),
                                       QPointF(bx, baseline_y - bar_h))
                grad.setColorAt(0.0, qcol(C.PRI, 55))
                grad.setColorAt(0.55, qcol(C.PRI, min(210, int(90 + 120 * display_h))))
                if h > 0.55:
                    grad.setColorAt(1.0, qcol("#FFFFFF", min(255, int(170 + 85 * h))))
                else:
                    grad.setColorAt(1.0, qcol(C.ACC, min(255, int(110 + 145 * display_h))))
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QBrush(grad))
                p.drawRoundedRect(QRectF(bx, baseline_y - bar_h, bar_w, bar_h), 2.0, 2.0)
                # punto de peak
                if h > 0.12:
                    peak_col = qcol(C.ACC, min(255, int(190 + 65 * h)))
                    p.setBrush(QBrush(peak_col))
                    p.drawEllipse(QRectF(bx + bar_w * 0.1, baseline_y - bar_h - 4,
                                         bar_w * 0.8, 3.5))

        # --- waveform frecuencia-adaptativa ---
        t = self._tick
        wave_amp = orb_r * max(0.05, min(0.70, al * 0.78 + 0.05))
        _tb = self._bass; _tm = self._mid; _tt = self._treble
        _tot = max(0.001, _tb + _tm + _tt)
        b_r = _tb / _tot;  m_r = _tm / _tot;  tr_r = _tt / _tot
        # ciclos visibles: bajo=pocos lentos, agudos=muchos rápidos
        n_cyc = max(1.2, min(8.0, b_r * 1.5 + m_r * 4.0 + tr_r * 8.0))
        t_spd = max(0.018, min(0.22, b_r * 0.025 + m_r * 0.070 + tr_r * 0.18))
        # armónicos de agudos
        harm  = min(0.55, tr_r * 0.80)
        clip_path = QPainterPath()
        clip_path.addEllipse(QRectF(cx - orb_r * 0.90, cy - orb_r * 0.90,
                                     orb_r * 1.80, orb_r * 1.80))
        p.save()
        p.setClipPath(clip_path)
        n_pts = 100
        for wave_idx in range(2):
            ph   = wave_idx * math.pi * 0.55
            dim  = 1.0 - wave_idx * 0.38
            if al > 0.03 or self.speaking:
                # color según banda dominante
                if b_r > 0.50:
                    w_col = qcol(C.ACC, min(255, int((150 + 105 * al) * dim)))
                elif tr_r > 0.50:
                    w_col = qcol("#D9DEFF", min(255, int((135 + 120 * al) * dim)))
                else:
                    w_col = qcol(C.PRI, min(255, int((140 + 115 * al) * dim)))
            else:
                w_col = qcol(C.PRI, int(128 * dim))
            pen_w = 1.9 - wave_idx * 0.7
            wave_path = QPainterPath()
            for i in range(n_pts + 1):
                frac = i / n_pts
                x    = cx - orb_r + 2 * orb_r * frac
                base = math.sin(t * t_spd + frac * n_cyc * math.tau + ph)
                hrm  = harm * math.sin(t * t_spd * 2.6 + frac * n_cyc * 2.4 * math.tau + ph)
                y    = cy + wave_amp * (base + hrm)
                if i == 0:
                    wave_path.moveTo(QPointF(x, y))
                else:
                    wave_path.lineTo(QPointF(x, y))
            p.setPen(QPen(w_col, pen_w, Qt.PenStyle.SolidLine,
                          Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPath(wave_path)
        p.restore()

        # inner bright core pulse
        core_r = orb_r * max(0.18, min(0.42, 0.22 + 0.20 * al))
        cg = QRadialGradient(QPointF(cx, cy), core_r)
        if self.speaking:
            cg.setColorAt(0, qcol("#FFFFFF", min(255, int(195 + 60 * al))))
            cg.setColorAt(0.45, qcol(C.ACC, min(200, int(115 + 85 * al))))
            cg.setColorAt(1, qcol(C.PRI, 0))
        else:
            cg.setColorAt(0, qcol(C.ACC, min(200, int(125 + 75 * al))))
            cg.setColorAt(1, qcol(C.PRI, 0))
        p.setBrush(QBrush(cg)); p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(QRectF(cx - core_r, cy - core_r, core_r * 2, core_r * 2))

        # particles
        for pt in self._particles:
            a = max(0, min(255, int(pt[4] * 255)))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(qcol(C.ACC if self.speaking else C.PRI, a)))
            p.drawEllipse(QPointF(pt[0], pt[1]), 2.5, 2.5)

        # status text
        sy = cy + fw * 0.43
        if self.muted:
            txt, col = "⊘  SILENCIADO", qcol(C.MUTED_C)
        elif self.speaking:
            txt, col = "●  HABLANDO",   qcol(C.ACC)
        elif self.state == "THINKING":
            sym = "◈" if self._blink else "◇"
            txt, col = f"{sym}  PENSANDO",   qcol(C.ACC2)
        elif self.state == "PROCESSING":
            sym = "▷" if self._blink else "▶"
            txt, col = f"{sym}  PROCESANDO", qcol(C.ACC2)
        elif self.state == "LISTENING":
            sym = "●" if self._blink else "○"
            txt, col = f"{sym}  ESCUCHANDO",  qcol(C.ACC)
        else:
            sym = "●" if self._blink else "○"
            label = {"INITIALISING": "INICIANDO"}.get(self.state, self.state)
            txt, col = f"{sym}  {label}", qcol(C.PRI)

        p.setPen(QPen(col, 1))
        p.setFont(QFont(FONT_UI, 11, QFont.Weight.DemiBold))
        p.drawText(QRectF(0, sy, W, 26), Qt.AlignmentFlag.AlignCenter, txt)


class MetricBar(QWidget):

    def __init__(self, label: str, color: str = C.PRI, parent=None):
        super().__init__(parent)
        self._label = label
        self._color = color
        self._value = 0.0       # 0–100
        self._text  = "--"
        self.setFixedHeight(38)
        self.setMinimumWidth(80)

    def set_value(self, pct: float, text: str):
        target = max(0.0, min(100.0, pct))
        self._text = text
        # Transición suave hacia el nuevo valor (fluidez del design system).
        if abs(target - self._value) < 0.5 or not self.isVisible():
            self._value = target
            self.update()
            return
        anim = getattr(self, "_anim", None)
        if anim is None:
            anim = QPropertyAnimation(self, b"barValue", self)
            anim.setDuration(360)
            anim.setEasingCurve(QEasingCurve.Type.OutCubic)
            self._anim = anim
        anim.stop()
        anim.setStartValue(self._value)
        anim.setEndValue(target)
        anim.start()

    def _get_bar_value(self) -> float:
        return self._value

    def _set_bar_value(self, v: float):
        self._value = v
        self.update()

    barValue = pyqtProperty(float, _get_bar_value, _set_bar_value)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()

        card = QLinearGradient(QPointF(0, 0), QPointF(W, H))
        card.setColorAt(0, qcol("#FFFFFF", 24))
        card.setColorAt(1, qcol("#FFFFFF", 10))
        p.setBrush(QBrush(card))
        p.setPen(QPen(qcol(C.BORDER_B, 70), 1))
        p.drawRoundedRect(QRectF(1, 1, W - 2, H - 2), 10, 10)

        bar_h   = 4
        bar_y   = H - bar_h - 5
        bar_w   = W - 12
        bar_x   = 6
        fill_w  = int(bar_w * self._value / 100)

        p.setBrush(QBrush(qcol(C.BAR_BG)))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(QRectF(bar_x, bar_y, bar_w, bar_h), 2, 2)

        if self._value > 85:
            bar_col = qcol(C.RED)
        elif self._value > 65:
            bar_col = qcol(C.ACC)
        else:
            bar_col = qcol(self._color)

        if fill_w > 0:
            p.setBrush(QBrush(bar_col))
            p.drawRoundedRect(QRectF(bar_x, bar_y, fill_w, bar_h), 2, 2)

        p.setFont(QFont(FONT_UI, 7, QFont.Weight.DemiBold))
        p.setPen(QPen(qcol(C.TEXT_DIM), 1))
        p.drawText(QRectF(8, 5, 50, 14), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, self._label)

        p.setFont(QFont(FONT_UI, 9, QFont.Weight.DemiBold))
        p.setPen(QPen(bar_col if self._text != "--" else qcol(C.TEXT_DIM), 1))
        p.drawText(QRectF(0, 4, W - 6, 16), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, self._text)


__all__ = [
    '_set_windows_app_id',
    '_SysMetrics',
    '_metrics',
    'HudCanvas',
    'MetricBar',
]
