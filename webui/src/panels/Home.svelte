<script>
  import { onMount, onDestroy } from 'svelte'
  import { events, voiceState, musicPlaying } from '../stores/sse.js'

  // PanelHost now keeps this component mounted (display:none) rather than
  // destroying it when you leave Inicio, so state would survive either way
  // here — but the RAF loop must not keep redrawing an invisible canvas 60
  // times a second forever. `active` gates it instead of onMount/onDestroy.
  let { active = true } = $props()

  // Faithful-in-spirit port of ui/widgets/hud.py's HudCanvas.paintEvent:
  // gradient background, halo rings, three counter-rotating segmented arc
  // rings, two scanner arcs, radar tick marks, a crosshair, corner
  // brackets, a central audio-reactive orb with a bright core, a 32-bar
  // equalizer, a frequency-shaped waveform, drifting particles while
  // speaking, and a status line with a blinking glyph.

  const COL = {
    bg: '#0A0C16', pri: '#B6C4FF', priDim: '#5E82FF', priGho: '#131C46',
    acc: '#A7AFFF', acc2: '#8FA8FF', mutedC: '#9AA3C0', white: '#FFFFFF',
  }

  // withAlpha runs 100+ times per drawn frame (every stroke/fill in the
  // HUD) — precomputing the 0-255 hex suffixes once avoids a toString+
  // padStart allocation per call, which otherwise adds real GC pressure at
  // 60fps on WebKitGTK's software rasterizer.
  const HEX2 = Array.from({ length: 256 }, (_, n) => n.toString(16).padStart(2, '0'))
  function withAlpha(hex, a) {
    const n = a < 0 ? 0 : a > 255 ? 255 : (a + 0.5) | 0
    return hex + HEX2[n]
  }

  let canvas
  let ctx
  let raf
  let last = performance.now()
  let resizeObserver

  // Canvas geometry in CSS pixels. The backing store is scaled by
  // devicePixelRatio (otherwise the HUD renders at 1x and gets upscaled by
  // the compositor, which is what made it look pixelated), and the drawing
  // is inset from the box so the corner brackets, radar ticks and status
  // line can't be clipped at the edges the way they were.
  const view = { w: 0, h: 0 }
  const INSET = 0.86

  function resizeCanvas() {
    if (!canvas) return
    const rect = canvas.getBoundingClientRect()
    if (!rect.width || !rect.height) return
    if (!ctx) {
      // alpha:false lets WebKit/Chromium skip per-pixel alpha compositing
      // against the page behind the canvas — every frame paints the whole
      // surface opaquely anyway (clearRect + full-canvas bg gradient), so
      // there's nothing to composite. desynchronized:true drops a vsync
      // hop where supported. Both are no-ops on unsupported engines.
      ctx = canvas.getContext('2d', { alpha: false, desynchronized: true })
    }
    const dpr = window.devicePixelRatio || 1
    view.w = rect.width
    view.h = rect.height
    canvas.width = Math.round(rect.width * dpr)
    canvas.height = Math.round(rect.height * dpr)
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
  }

  const audio = { level: 0, bass: 0, mid: 0, treble: 0, bins: new Array(64).fill(0) }
  const unsubscribe = events.subscribe(({ type, data }) => {
    if (type === 'audio') audio.level = data.level ?? 0
    else if (type === 'audio_bands') {
      audio.bass = data.bass ?? 0
      audio.mid = data.mid ?? 0
      audio.treble = data.treble ?? 0
    } else if (type === 'fft' && Array.isArray(data.bins)) {
      for (let i = 0; i < audio.bins.length && i < data.bins.length; i++) {
        if (data.bins[i] > audio.bins[i]) audio.bins[i] = data.bins[i]
      }
    }
  })

  // Animated state, mutated every frame outside Svelte's reactivity.
  const s = {
    tick: 0, scale: 1, halo: 55,
    rings: [0, 120, 240], scan: 0, scan2: 180,
    pulses: [0, 50, 100], particles: [],
    blink: true, blinkTick: 0, lastAudioT: 0,
  }

  function step(dtMs) {
    const dt = dtMs / 1000
    const k = Math.max(0.05, Math.min(3, dt / 0.033))
    const f = (a) => 1 - (1 - a) ** k
    s.tick += k

    const state = $voiceState
    const speaking = state === 'SPEAKING'
    const isMusic = $musicPlaying
    const active = speaking || isMusic
    const al = Math.max(audio.bass, audio.mid, audio.treble, audio.level)
    const now = performance.now()

    if (al > 0.02) {
      s.scale = 1 + al * 0.32
      s.halo = 58 + al * 180
      s.lastAudioT = now
    } else if (now - s.lastAudioT < 180) {
      s.scale += (1 - s.scale) * f(0.22)
      s.halo += (55 - s.halo) * f(0.22)
    } else if (active) {
      s.scale += (1.015 - s.scale) * f(0.14)
      s.halo += (70 - s.halo) * f(0.14)
    } else {
      const breath = Math.sin(s.tick * 0.012) * 0.003
      s.scale = 1.001 + breath
      s.halo = 52 + breath * 1200
    }

    const speeds = speaking ? [1.3, -0.9, 2.0] : isMusic ? [0.9, -0.65, 1.4] : [0.55, -0.35, 0.9]
    for (let i = 0; i < 3; i++) s.rings[i] = (s.rings[i] + speeds[i] * k) % 360
    s.scan = (s.scan + (speaking ? 3.0 : isMusic ? 2.0 : 1.3) * k) % 360
    s.scan2 = (s.scan2 + (speaking ? -2.0 : isMusic ? -1.4 : -0.75) * k) % 360

    for (let i = 0; i < audio.bins.length; i++) audio.bins[i] *= 0.84 ** k

    if (view.w && view.h) {
      const fw = Math.min(view.w, view.h) * INSET
      const lim = fw * 0.74
      const spd = (speaking ? 4.2 : isMusic ? 3.2 : 2.0) * k
      s.pulses = s.pulses.map((r) => r + spd).filter((r) => r < lim)
      const emit = audio.bass > 0.35 ? 0.12 : speaking ? 0.07 : isMusic ? 0.05 : 0.025
      if (s.pulses.length < 5 && Math.random() < emit * k) s.pulses.push(0)

      if (active && Math.random() < (speaking ? 0.28 : 0.1) * k) {
        const cx = view.w / 2
        const cy = view.h / 2
        const ang = Math.random() * Math.PI * 2
        const rr = fw * 0.28
        s.particles.push([
          cx + Math.cos(ang) * rr, cy + Math.sin(ang) * rr,
          Math.cos(ang) * (0.9 + Math.random() * 1.5), Math.sin(ang) * (0.9 + Math.random() * 1.5) - 0.4, 1,
        ])
      }
      const damp = 0.97 ** k
      s.particles = s.particles
        .map((p) => [p[0] + p[2] * k, p[1] + p[3] * k, p[2] * damp, p[3] * damp, p[4] - 0.028 * k])
        .filter((p) => p[4] > 0)
    }

    s.blinkTick += k
    if (s.blinkTick >= 38) { s.blink = !s.blink; s.blinkTick = 0 }
  }

  function draw() {
    const W = view.w
    const H = view.h
    if (!W || !H) return
    const cx = W / 2
    const cy = H / 2
    const fw = Math.min(W, H) * INSET
    const state = $voiceState
    const speaking = state === 'SPEAKING'
    const al = audio.level

    ctx.clearRect(0, 0, W, H)

    const bg = ctx.createLinearGradient(0, 0, W, H)
    bg.addColorStop(0, '#101A45')
    bg.addColorStop(0.45, '#0A0C16')
    bg.addColorStop(1, '#0A0F33')
    ctx.fillStyle = bg
    ctx.fillRect(0, 0, W, H)

    const glow = ctx.createRadialGradient(W * 0.42, H * 0.34, 0, W * 0.42, H * 0.34, fw * 0.78)
    glow.addColorStop(0, withAlpha(COL.priDim, 52))
    glow.addColorStop(0.42, withAlpha(COL.priGho, 34))
    glow.addColorStop(1, withAlpha(COL.bg, 0))
    ctx.fillStyle = glow
    ctx.fillRect(0, 0, W, H)

    const rFace = fw * 0.262

    // halo rings
    for (let i = 0; i < 10; i++) {
      const r = rFace * (1.8 - i * 0.08)
      const frc = 1 - i / 10
      ctx.strokeStyle = withAlpha(COL.pri, s.halo * 0.085 * frc)
      ctx.lineWidth = 1.5
      ctx.beginPath()
      ctx.arc(cx, cy, r, 0, Math.PI * 2)
      ctx.stroke()
    }

    // pulse rings
    for (const pr of s.pulses) {
      ctx.strokeStyle = withAlpha(COL.pri, 230 * (1 - pr / (fw * 0.74)))
      ctx.lineWidth = 1.5
      ctx.beginPath()
      ctx.arc(cx, cy, pr, 0, Math.PI * 2)
      ctx.stroke()
    }

    // spinning segmented arc rings
    ;[[0.48, 3, 115, 78], [0.40, 2, 78, 55], [0.32, 1, 56, 40]].forEach(([rFrac, wR, arcL, gap], idx) => {
      const ringR = fw * rFrac
      const base = (s.rings[idx] * Math.PI) / 180
      const arcRad = (arcL * Math.PI) / 180
      const gapRad = (gap * Math.PI) / 180
      ctx.strokeStyle = withAlpha(COL.pri, s.halo * (1 - idx * 0.18))
      ctx.lineWidth = wR
      let angle = base
      while (angle < base + Math.PI * 2) {
        ctx.beginPath()
        ctx.arc(cx, cy, ringR, angle, angle + arcRad)
        ctx.stroke()
        angle += arcRad + gapRad
      }
    })

    // scanners
    const sr = fw * 0.5
    const sa = Math.min(255, s.halo * 1.5)
    const ex = ((speaking ? 75 : 44) * Math.PI) / 180
    ctx.strokeStyle = withAlpha(COL.pri, sa)
    ctx.lineWidth = 2.5
    const scanRad = (s.scan * Math.PI) / 180
    ctx.beginPath()
    ctx.arc(cx, cy, sr, scanRad, scanRad + ex)
    ctx.stroke()
    ctx.strokeStyle = withAlpha(COL.acc, sa / 2)
    ctx.lineWidth = 1.5
    const scan2Rad = (s.scan2 * Math.PI) / 180
    ctx.beginPath()
    ctx.arc(cx, cy, sr, scan2Rad, scan2Rad + ex)
    ctx.stroke()

    // tick marks
    const tOut = fw * 0.497
    const tIn = fw * 0.474
    ctx.strokeStyle = withAlpha(COL.pri, 140)
    ctx.lineWidth = 1
    for (let deg = 0; deg < 360; deg += 10) {
      const rad = (deg * Math.PI) / 180
      const inn = deg % 30 === 0 ? tIn : tIn + 6
      ctx.beginPath()
      ctx.moveTo(cx + tOut * Math.cos(rad), cy - tOut * Math.sin(rad))
      ctx.lineTo(cx + inn * Math.cos(rad), cy - inn * Math.sin(rad))
      ctx.stroke()
    }

    // crosshair
    const chR = fw * 0.51
    const gapH = fw * 0.16
    ctx.strokeStyle = withAlpha(COL.pri, s.halo * 0.5)
    ctx.lineWidth = 1
    ctx.beginPath()
    ctx.moveTo(cx - chR, cy); ctx.lineTo(cx - gapH, cy)
    ctx.moveTo(cx + gapH, cy); ctx.lineTo(cx + chR, cy)
    ctx.moveTo(cx, cy - chR); ctx.lineTo(cx, cy - gapH)
    ctx.moveTo(cx, cy + gapH); ctx.lineTo(cx, cy + chR)
    ctx.stroke()

    // corner brackets
    const bl = 24
    const hl = cx - fw / 2, hr = cx + fw / 2, ht = cy - fw / 2, hb = cy + fw / 2
    ctx.strokeStyle = withAlpha(COL.pri, 210)
    ctx.lineWidth = 2
    for (const [bx, by, dx, dy] of [[hl, ht, 1, 1], [hr, ht, -1, 1], [hl, hb, 1, -1], [hr, hb, -1, -1]]) {
      ctx.beginPath()
      ctx.moveTo(bx, by); ctx.lineTo(bx + dx * bl, by)
      ctx.moveTo(bx, by); ctx.lineTo(bx, by + dy * bl)
      ctx.stroke()
    }

    // central reactive orb
    const rOrbBase = fw * 0.262
    const orbR = rOrbBase * s.scale
    const grad = ctx.createRadialGradient(cx, cy, 0, cx, cy, orbR)
    if (speaking) {
      const lv = Math.min(1, al * 1.4 + 0.3)
      grad.addColorStop(0, withAlpha(COL.white, Math.min(255, 90 + 165 * lv)))
      grad.addColorStop(0.22, withAlpha(COL.acc, Math.min(255, 140 + 115 * lv)))
      grad.addColorStop(0.62, withAlpha(COL.priDim, Math.min(200, 75 + 125 * lv)))
      grad.addColorStop(0.9, withAlpha(COL.priGho, 35))
      grad.addColorStop(1, withAlpha(COL.bg, 0))
    } else {
      grad.addColorStop(0, withAlpha(COL.acc, Math.min(200, 85 + 115 * al)))
      grad.addColorStop(0.4, withAlpha(COL.priDim, Math.min(180, 80 + 100 * al)))
      grad.addColorStop(0.78, withAlpha(COL.priGho, 40))
      grad.addColorStop(1, withAlpha(COL.bg, 0))
    }
    ctx.fillStyle = grad
    ctx.beginPath(); ctx.arc(cx, cy, orbR, 0, Math.PI * 2); ctx.fill()

    // outer ring
    ctx.strokeStyle = withAlpha(speaking ? COL.acc : COL.pri, Math.min(255, s.halo * 2))
    ctx.lineWidth = 2
    ctx.beginPath(); ctx.arc(cx, cy, orbR, 0, Math.PI * 2); ctx.stroke()

    // equalizer bars
    const nDisp = 32
    const nSrc = audio.bins.length
    const step2 = Math.max(1, Math.floor(nSrc / nDisp))
    const barAreaW = rOrbBase * 2.6
    const barSlotW = barAreaW / nDisp
    const barW = barSlotW * 0.62
    const barMaxH = fw * 0.17
    const baselineY = cy + rOrbBase * 1.18
    for (let i = 0; i < nDisp; i++) {
      const idx = Math.min(i * step2, nSrc - 1)
      let h = 0
      for (let j = idx; j < Math.min(idx + step2, nSrc); j++) h = Math.max(h, audio.bins[j])
      const idleH = 0.022 + 0.01 * Math.sin(s.tick * 0.016 + i * 0.25)
      const displayH = Math.max(h, idleH)
      const barH = displayH * barMaxH
      const bx = cx - barAreaW / 2 + i * barSlotW + (barSlotW - barW) / 2
      const bgrad = ctx.createLinearGradient(bx, baselineY, bx, baselineY - barH)
      bgrad.addColorStop(0, withAlpha(COL.pri, 55))
      bgrad.addColorStop(0.55, withAlpha(COL.pri, Math.min(210, 90 + 120 * displayH)))
      bgrad.addColorStop(1, h > 0.55 ? withAlpha(COL.white, Math.min(255, 170 + 85 * h)) : withAlpha(COL.acc, Math.min(255, 110 + 145 * displayH)))
      ctx.fillStyle = bgrad
      ctx.beginPath()
      ctx.roundRect(bx, baselineY - barH, barW, barH, 2)
      ctx.fill()
    }

    // waveform
    const waveAmp = orbR * Math.max(0.05, Math.min(0.7, al * 0.78 + 0.05))
    const tot = Math.max(0.001, audio.bass + audio.mid + audio.treble)
    const bR = audio.bass / tot, mR = audio.mid / tot, trR = audio.treble / tot
    const nCyc = Math.max(1.2, Math.min(8, bR * 1.5 + mR * 4.0 + trR * 8.0))
    const tSpd = Math.max(0.018, Math.min(0.22, bR * 0.025 + mR * 0.07 + trR * 0.18))
    const harm = Math.min(0.55, trR * 0.8)
    ctx.save()
    ctx.beginPath()
    ctx.arc(cx, cy, orbR * 0.9, 0, Math.PI * 2)
    ctx.clip()
    for (let wi = 0; wi < 2; wi++) {
      const ph = wi * Math.PI * 0.55
      const dim = 1 - wi * 0.38
      let wCol
      if (al > 0.03 || speaking) {
        if (bR > 0.5) wCol = withAlpha(COL.acc, Math.min(255, (150 + 105 * al) * dim))
        else if (trR > 0.5) wCol = withAlpha('#D9DEFF', Math.min(255, (135 + 120 * al) * dim))
        else wCol = withAlpha(COL.pri, Math.min(255, (140 + 115 * al) * dim))
      } else {
        wCol = withAlpha(COL.pri, 128 * dim)
      }
      ctx.strokeStyle = wCol
      ctx.lineWidth = 1.9 - wi * 0.7
      ctx.lineCap = 'round'
      ctx.lineJoin = 'round'
      ctx.beginPath()
      const nPts = 100
      for (let i = 0; i <= nPts; i++) {
        const frac = i / nPts
        const x = cx - orbR + 2 * orbR * frac
        const base = Math.sin(s.tick * tSpd + frac * nCyc * Math.PI * 2 + ph)
        const hrm = harm * Math.sin(s.tick * tSpd * 2.6 + frac * nCyc * 2.4 * Math.PI * 2 + ph)
        const y = cy + waveAmp * (base + hrm)
        if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y)
      }
      ctx.stroke()
    }
    ctx.restore()

    // inner core pulse
    const coreR = orbR * Math.max(0.18, Math.min(0.42, 0.22 + 0.2 * al))
    const cg = ctx.createRadialGradient(cx, cy, 0, cx, cy, coreR)
    if (speaking) {
      cg.addColorStop(0, withAlpha(COL.white, Math.min(255, 195 + 60 * al)))
      cg.addColorStop(0.45, withAlpha(COL.acc, Math.min(200, 115 + 85 * al)))
      cg.addColorStop(1, withAlpha(COL.pri, 0))
    } else {
      cg.addColorStop(0, withAlpha(COL.acc, Math.min(200, 125 + 75 * al)))
      cg.addColorStop(1, withAlpha(COL.pri, 0))
    }
    ctx.fillStyle = cg
    ctx.beginPath(); ctx.arc(cx, cy, coreR, 0, Math.PI * 2); ctx.fill()

    // particles
    for (const pt of s.particles) {
      ctx.fillStyle = withAlpha(speaking ? COL.acc : COL.pri, pt[4] * 255)
      ctx.beginPath()
      ctx.arc(pt[0], pt[1], 2.5, 0, Math.PI * 2)
      ctx.fill()
    }

    // status text
    const sy = cy + fw * 0.43
    let txt, col
    if (state === 'MUTED') { txt = '⊘  SILENCIADO'; col = COL.mutedC }
    else if (speaking) { txt = '●  HABLANDO'; col = COL.acc }
    else if (state === 'THINKING') { txt = `${s.blink ? '◈' : '◇'}  PENSANDO`; col = COL.acc2 }
    else if (state === 'PROCESSING') { txt = `${s.blink ? '▷' : '▶'}  PROCESANDO`; col = COL.acc2 }
    else if (state === 'LISTENING') { txt = `${s.blink ? '●' : '○'}  ESCUCHANDO`; col = COL.acc }
    else { txt = `${s.blink ? '●' : '○'}  ${state || ''}`; col = COL.pri }

    ctx.fillStyle = col
    ctx.font = '600 13px "Segoe UI Variable", "Segoe UI", sans-serif'
    ctx.textAlign = 'center'
    ctx.textBaseline = 'middle'
    ctx.fillText(txt, cx, sy + 13)
  }

  function frame(now) {
    step(now - last)
    last = now
    if (canvas) draw()
    raf = requestAnimationFrame(frame)
  }

  onMount(() => {
    resizeObserver = new ResizeObserver(resizeCanvas)
    resizeObserver.observe(canvas)
    // devicePixelRatio changes when the window moves between displays or
    // the user changes zoom; the backing store has to be rebuilt for it.
    window.addEventListener('resize', resizeCanvas)
  })

  onDestroy(() => {
    unsubscribe()
    if (resizeObserver) resizeObserver.disconnect()
    window.removeEventListener('resize', resizeCanvas)
  })

  $effect(() => {
    if (!active) return
    // The canvas reports 0x0 while display:none (hidden panel), so re-measure
    // on every reactivation rather than relying solely on the ResizeObserver.
    resizeCanvas()
    last = performance.now()
    raf = requestAnimationFrame(frame)
    return () => { if (raf) cancelAnimationFrame(raf) }
  })
</script>

<div class="home">
  <canvas bind:this={canvas}></canvas>
</div>

<style>
  .home {
    height: 100%;
    min-height: 0;
    display: flex;
    align-items: stretch;
    justify-content: stretch;
    overflow: hidden;
  }

  canvas {
    display: block;
    width: 100%;
    height: 100%;
  }
</style>
