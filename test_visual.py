"""
Test visual del visualizador — simula audio sin necesitar el app completo.
Ejecutar:  python test_visual.py
"""
import sys, math, random
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import QTimer
import ui

def main():
    app = QApplication(sys.argv)
    jarvis = ui.JarvisUI("")
    jarvis.set_state("LISTENING")

    tick = [0]

    def feed_audio():
        t = tick[0] * 0.05
        tick[0] += 1

        # simulación: beat de bajo cada ~0.8s + agudos constantes + voz en mid
        beat  = max(0.0, math.sin(t * 1.25 * math.pi) ** 6)          # kick
        mid_v = max(0.0, math.sin(t * 0.6 + 1.0)) * 0.55             # voz
        hi    = abs(math.sin(t * 3.7)) * 0.35                         # hi-hat

        # 64 bins: bajos fuertes al principio, agudos al final
        bins = []
        for i in range(64):
            frac = i / 63.0
            bass_contrib   = beat  * math.exp(-frac * 6.0)
            mid_contrib    = mid_v * math.exp(-((frac - 0.35) ** 2) * 30)
            treble_contrib = hi    * math.exp(-((frac - 0.80) ** 2) * 20)
            noise = random.uniform(0, 0.04)
            bins.append(min(1.0, bass_contrib + mid_contrib + treble_contrib + noise))

        jarvis.set_fft_bins(bins)
        jarvis.set_audio_bands(beat * 0.9, mid_v, hi)

        # alternar estado para ver diferencias
        if tick[0] == 80:
            jarvis.set_state("SPEAKING")
        if tick[0] == 160:
            jarvis.set_state("LISTENING")
        if tick[0] == 220:
            jarvis.muted = True
        if tick[0] == 280:
            jarvis.muted = False

    tmr = QTimer()
    tmr.timeout.connect(feed_audio)
    tmr.start(40)   # 25 fps de audio simulado

    sys.exit(app.exec())

if __name__ == "__main__":
    main()
