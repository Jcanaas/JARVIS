<script>
  // ui/panels/emulators.py's EmulatorsModePanel + LibraryModePanel, minus the
  // Steam/FitGirl Juegos hub (ui/panels/games.py — a separate, unrelated
  // feature). The core (actions/libretro.py) is driven headless by
  // actions/emulator_session.py; this panel just talks to that over HTTP:
  // the video is a plain <img> pointed at the MJPEG stream, input is a POST
  // per press, no WebSocket anywhere. Only ROMs already in «Mi biblioteca»
  // are playable from here — browsing/downloading the catalogue is backend
  // -only for now (actions/rom_catalog.py's routes exist and are tested,
  // just not wired to a screen yet).
  import * as api from '../api.js'
  import Icon from '../lib/Icon.svelte'

  let { active = true } = $props()

  let loading = $state(false)
  let error = $state('')
  let entries = $state([])

  let playing = $state(null) // the LibraryEntry currently loaded, or null
  let status = $state(null) // last /api/emulator/status response
  let padLayout = $state({})
  let streamNonce = $state(0) // bumped on every start() so the <img> can't show a stale MJPEG connection from a previous game
  let actionError = $state('')

  let statusTimer = null
  let videoWrap = $state()

  async function loadLibrary() {
    loading = true
    error = ''
    try {
      entries = await api.getEmulatorLibrary()
    } catch (e) {
      error = e.message
      entries = []
    } finally {
      loading = false
    }
  }

  function pollStatus() {
    clearInterval(statusTimer)
    statusTimer = setInterval(async () => {
      try {
        status = await api.getEmulatorStatus()
        if (!status.running) stopWatchingStatus()
      } catch {
        // A transient fetch failure shouldn't drop the player — just skip this tick.
      }
    }, 1000)
  }

  function stopWatchingStatus() {
    clearInterval(statusTimer)
    statusTimer = null
  }

  async function play(entry) {
    actionError = ''
    try {
      const res = await api.startEmulator(entry.entry_id)
      playing = entry
      status = res.status
      streamNonce++
      padLayout = await api.getEmulatorPad(entry.console_id)
      pollStatus()
    } catch (e) {
      actionError = e.message
    }
  }

  async function stop() {
    stopWatchingStatus()
    try {
      await api.stopEmulator()
    } catch {
      // best-effort — the panel is leaving the player either way
    }
    playing = null
    status = null
  }

  async function togglePause() {
    if (!status) return
    try {
      await api.pauseEmulator(!status.paused)
      status = { ...status, paused: !status.paused }
    } catch (e) {
      actionError = e.message
    }
  }

  async function reset() {
    try {
      await api.resetEmulator()
    } catch (e) {
      actionError = e.message
    }
  }

  async function saveState() {
    try {
      await api.saveEmulatorState()
    } catch (e) {
      actionError = e.message
    }
  }

  async function loadState() {
    try {
      await api.loadEmulatorState()
    } catch (e) {
      actionError = e.message
    }
  }

  function toggleFullscreen() {
    if (document.fullscreenElement) {
      document.exitFullscreen()
    } else {
      videoWrap?.requestFullscreen?.()
    }
  }

  // -- Input: touch/mouse pad -------------------------------------------------
  // Fire-and-forget: a press/release is a single POST, no batching — human
  // input rates never come close to needing it, and the emulation loop never
  // waits on this either way (actions/emulator_session.py's thread is the
  // only thing that touches the core).
  function sendButton(name, pressed) {
    api.sendEmulatorInput({ buttons: [{ name, pressed }] }).catch(() => {})
  }

  function pressButton(name) {
    return (e) => {
      e.preventDefault()
      sendButton(name, true)
    }
  }

  function releaseButton(name) {
    return (e) => {
      e.preventDefault()
      sendButton(name, false)
    }
  }

  // -- Input: keyboard ----------------------------------------------------
  // Same default layout as actions/input_config.py's _BASE_KEYBOARD (arrow
  // keys, X/Space=A, Z=B, S=X, A=Y, Q=L, W=R, Enter=start, Backspace=select)
  // — familiar to anyone who's used the legacy desktop app, kept in sync by
  // hand rather than fetched, since the web MVP doesn't expose per-user
  // remapping (that's a whole settings screen the plan never asked for).
  const KEY_MAP = {
    ArrowUp: 'up', ArrowDown: 'down', ArrowLeft: 'left', ArrowRight: 'right',
    KeyX: 'a', Space: 'a',
    KeyZ: 'b',
    KeyS: 'x',
    KeyA: 'y',
    KeyQ: 'l',
    KeyW: 'r',
    Digit1: 'l2', Digit2: 'r2', Digit3: 'l3', Digit4: 'r3',
    Enter: 'start', NumpadEnter: 'start',
    Backspace: 'select', ShiftLeft: 'select', ShiftRight: 'select',
  }
  const heldKeys = new Set()

  function onKeydown(e) {
    if (!playing) return
    const button = KEY_MAP[e.code]
    if (!button || heldKeys.has(e.code)) return
    heldKeys.add(e.code)
    e.preventDefault()
    sendButton(button, true)
  }

  function onKeyup(e) {
    if (!playing) return
    const button = KEY_MAP[e.code]
    if (!button) return
    heldKeys.delete(e.code)
    e.preventDefault()
    sendButton(button, false)
  }

  // -- Input: analog stick (touch/mouse drag) ------------------------------
  // Only rendered when padLayout.sticks >= 1 (GBA/SNES have none — this path
  // is dormant until Fase 5/6 add N64/PS2, kept here so that work is just a
  // CoreSpec + this component already knowing what to do with it).
  let stickBase = $state()
  let stickActive = $state(false)
  let stickPos = $state({ x: 0, y: 0 })

  function stickMove(clientX, clientY) {
    const rect = stickBase.getBoundingClientRect()
    const cx = rect.left + rect.width / 2
    const cy = rect.top + rect.height / 2
    const r = rect.width / 2
    let dx = (clientX - cx) / r
    let dy = (clientY - cy) / r
    const mag = Math.hypot(dx, dy)
    if (mag > 1) {
      dx /= mag
      dy /= mag
    }
    stickPos = { x: dx * r * 0.5, y: dy * r * 0.5 }
    const value = (v) => Math.round(v * 32767)
    api.sendEmulatorInput({
      axes: [
        { index: 0, axis: 0, value: value(dx) },
        { index: 0, axis: 1, value: value(dy) },
      ],
    }).catch(() => {})
  }

  function stickStart(e) {
    stickActive = true
    stickMove(e.clientX, e.clientY)
    e.target.setPointerCapture?.(e.pointerId)
  }

  function stickDrag(e) {
    if (!stickActive) return
    stickMove(e.clientX, e.clientY)
  }

  function stickEnd() {
    if (!stickActive) return
    stickActive = false
    stickPos = { x: 0, y: 0 }
    api.sendEmulatorInput({
      axes: [{ index: 0, axis: 0, value: 0 }, { index: 0, axis: 1, value: 0 }],
    }).catch(() => {})
  }

  $effect(() => {
    if (active && !playing && !entries.length && !loading && !error) loadLibrary()
  })

  $effect(() => {
    return () => stopWatchingStatus()
  })
</script>

<svelte:window onkeydown={onKeydown} onkeyup={onKeyup} />

<div class="panel">
  {#if !playing}
    <div class="header-row">
      <h2>Emuladores</h2>
      <button class="ghost" onclick={loadLibrary} title="Actualizar"><Icon name="refresh" size={16} /></button>
    </div>

    {#if loading}
      <div class="empty">Cargando…</div>
    {:else if error}
      <div class="empty error">{error}</div>
    {:else if !entries.length}
      <div class="empty">
        Tu biblioteca está vacía. Copia una ROM en <code>roms/&lt;consola&gt;/</code> y pulsa actualizar.
      </div>
    {:else}
      <div class="grid">
        {#each entries as entry (entry.entry_id)}
          <button class="card" onclick={() => play(entry)}>
            <div class="thumb-wrap">
              {#if entry.poster_url}
                <img src={entry.poster_url} alt="" loading="lazy" class="thumb" />
              {:else}
                <div class="thumb thumb-placeholder"><Icon name="games" size={28} /></div>
              {/if}
              <span class="console-badge">{entry.platform}</span>
            </div>
            <div class="card-title">{entry.title}</div>
          </button>
        {/each}
      </div>
    {/if}
  {:else}
    <div class="player">
      <div class="player-toolbar">
        <button class="ghost" onclick={stop} title="Volver a la biblioteca"><Icon name="up" size={16} /></button>
        <div class="player-title">
          <span class="title">{playing.title}</span>
          {#if status}<span class="fps">{status.fps.toFixed(0)} fps</span>{/if}
        </div>
        <div class="player-tools">
          <button class="ghost" onclick={togglePause} title={status?.paused ? 'Reanudar' : 'Pausa'}>
            <Icon name={status?.paused ? 'play' : 'pause'} size={16} />
          </button>
          <button class="ghost" onclick={reset} title="Reiniciar"><Icon name="refresh" size={16} /></button>
          <button class="ghost" onclick={saveState} title="Guardar estado">💾</button>
          <button class="ghost" onclick={loadState} title="Cargar estado">📂</button>
          <button class="ghost" onclick={toggleFullscreen} title="Pantalla completa">⛶</button>
        </div>
      </div>

      {#if actionError}<div class="action-error">{actionError}</div>{/if}

      <div class="video-wrap" bind:this={videoWrap}>
        {#key streamNonce}
          <img src={`/api/emulator/stream?_=${streamNonce}`} alt={playing.title} class="stream" />
        {/key}
      </div>

      <div class="pad">
        <div class="pad-left">
          <div class="dpad">
            <button class="dp dp-up" onpointerdown={pressButton('up')} onpointerup={releaseButton('up')} onpointerleave={releaseButton('up')}>▲</button>
            <button class="dp dp-left" onpointerdown={pressButton('left')} onpointerup={releaseButton('left')} onpointerleave={releaseButton('left')}>◀</button>
            <button class="dp dp-right" onpointerdown={pressButton('right')} onpointerup={releaseButton('right')} onpointerleave={releaseButton('right')}>▶</button>
            <button class="dp dp-down" onpointerdown={pressButton('down')} onpointerup={releaseButton('down')} onpointerleave={releaseButton('down')}>▼</button>
          </div>
          {#if padLayout.sticks >= 1}
            <div
              class="stick-base"
              bind:this={stickBase}
              onpointerdown={stickStart}
              onpointermove={stickDrag}
              onpointerup={stickEnd}
              onpointercancel={stickEnd}
            >
              <div class="stick-knob" style={`transform: translate(${stickPos.x}px, ${stickPos.y}px)`}></div>
            </div>
          {/if}
        </div>

        <div class="pad-mid">
          <button class="mini" onpointerdown={pressButton('select')} onpointerup={releaseButton('select')} onpointerleave={releaseButton('select')}>Select</button>
          <button class="mini" onpointerdown={pressButton('start')} onpointerup={releaseButton('start')} onpointerleave={releaseButton('start')}>Start</button>
        </div>

        <div class="pad-right">
          <div class="face">
            <button class="fb fb-y" onpointerdown={pressButton('y')} onpointerup={releaseButton('y')} onpointerleave={releaseButton('y')}>Y</button>
            <button class="fb fb-x" onpointerdown={pressButton('x')} onpointerup={releaseButton('x')} onpointerleave={releaseButton('x')}>X</button>
            <button class="fb fb-a" onpointerdown={pressButton('a')} onpointerup={releaseButton('a')} onpointerleave={releaseButton('a')}>A</button>
            <button class="fb fb-b" onpointerdown={pressButton('b')} onpointerup={releaseButton('b')} onpointerleave={releaseButton('b')}>B</button>
          </div>
        </div>
      </div>

      {#if padLayout.shoulders || padLayout.triggers}
        <div class="shoulders">
          {#if padLayout.shoulders}
            <button class="mini" onpointerdown={pressButton('l')} onpointerup={releaseButton('l')} onpointerleave={releaseButton('l')}>L</button>
          {/if}
          {#if padLayout.triggers}
            <button class="mini" onpointerdown={pressButton('l2')} onpointerup={releaseButton('l2')} onpointerleave={releaseButton('l2')}>L2</button>
            <button class="mini" onpointerdown={pressButton('r2')} onpointerup={releaseButton('r2')} onpointerleave={releaseButton('r2')}>R2</button>
          {/if}
          {#if padLayout.shoulders}
            <button class="mini" onpointerdown={pressButton('r')} onpointerup={releaseButton('r')} onpointerleave={releaseButton('r')}>R</button>
          {/if}
        </div>
      {/if}
    </div>
  {/if}
</div>

<style>
  .panel { height: 100%; min-height: 0; display: flex; flex-direction: column; padding: 16px; gap: 12px; }

  .header-row { display: flex; align-items: center; justify-content: space-between; flex-shrink: 0; }
  .header-row h2 { margin: 0; font-size: 16px; }

  .ghost {
    background: var(--panel-2); border: 1px solid var(--border-a); border-radius: var(--radius-sm);
    color: var(--text); width: 32px; height: 32px; cursor: pointer;
    display: flex; align-items: center; justify-content: center; flex-shrink: 0;
  }

  .empty { color: var(--text-med); font-size: 13px; padding: 24px; text-align: center; }
  .empty.error { color: var(--red); }
  .empty code { font-family: var(--font-mono); background: var(--panel-2); padding: 2px 6px; border-radius: 4px; }

  .grid {
    flex: 1; min-height: 0; overflow-y: auto;
    display: grid; grid-template-columns: repeat(auto-fill, minmax(160px, 1fr)); gap: 14px;
  }
  .card { background: transparent; border: none; cursor: pointer; text-align: left; color: var(--text); display: flex; flex-direction: column; gap: 4px; padding: 0; }
  .thumb-wrap { position: relative; }
  .thumb { width: 100%; aspect-ratio: 3/4; object-fit: cover; border-radius: var(--radius-sm); background: var(--panel-2); }
  .thumb-placeholder { display: flex; align-items: center; justify-content: center; color: var(--text-dim); }
  .console-badge {
    position: absolute; top: 6px; left: 6px;
    background: rgba(0,0,0,0.75); color: #fff; font-size: 10px; padding: 2px 6px; border-radius: 4px;
  }
  .card-title { font-size: 12.5px; font-weight: 600; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }

  .player { flex: 1; min-height: 0; display: flex; flex-direction: column; gap: 10px; }

  .player-toolbar { display: flex; align-items: center; gap: 10px; flex-shrink: 0; }
  .player-title { flex: 1; min-width: 0; display: flex; align-items: baseline; gap: 8px; }
  .player-title .title { font-size: 14px; font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .player-title .fps { font-size: 11px; color: var(--text-med); font-family: var(--font-mono); flex-shrink: 0; }
  .player-tools { display: flex; gap: 6px; flex-shrink: 0; }
  .player-tools .ghost { font-size: 14px; }

  .action-error { color: var(--red); font-size: 12px; flex-shrink: 0; }

  .video-wrap {
    flex: 1; min-height: 0; border-radius: var(--radius); overflow: hidden; background: #000;
    display: flex; align-items: center; justify-content: center;
  }
  .stream { max-width: 100%; max-height: 100%; image-rendering: pixelated; }

  .pad {
    flex-shrink: 0; display: flex; align-items: center; justify-content: space-between;
    padding: 8px 4px; gap: 12px;
  }
  .pad-left, .pad-right { display: flex; align-items: center; gap: 16px; }
  .pad-mid { display: flex; gap: 8px; align-self: flex-end; padding-bottom: 6px; }

  .dpad {
    position: relative; width: 108px; height: 108px; flex-shrink: 0;
    touch-action: none;
  }
  .dp {
    position: absolute; width: 36px; height: 36px;
    background: var(--panel-2); border: 1px solid var(--border-a); border-radius: 6px;
    color: var(--text); font-size: 14px; cursor: pointer; user-select: none;
  }
  .dp-up { top: 0; left: 36px; }
  .dp-down { bottom: 0; left: 36px; }
  .dp-left { top: 36px; left: 0; }
  .dp-right { top: 36px; right: 0; }

  .stick-base {
    width: 90px; height: 90px; border-radius: 50%;
    background: var(--panel-2); border: 1px solid var(--border-a);
    position: relative; touch-action: none; flex-shrink: 0;
  }
  .stick-knob {
    position: absolute; top: 50%; left: 50%; width: 40px; height: 40px; margin: -20px;
    border-radius: 50%; background: var(--pri-gho); border: 1px solid var(--pri);
    pointer-events: none;
  }

  .face {
    position: relative; width: 108px; height: 108px; flex-shrink: 0;
    touch-action: none;
  }
  .fb {
    position: absolute; width: 36px; height: 36px; border-radius: 50%;
    background: var(--panel-2); border: 1px solid var(--border-a);
    color: var(--text); font-size: 13px; font-weight: 700; cursor: pointer; user-select: none;
  }
  .fb-y { top: 0; left: 36px; }
  .fb-a { bottom: 0; left: 36px; }
  .fb-x { top: 36px; left: 0; }
  .fb-b { top: 36px; right: 0; }

  .mini {
    background: var(--panel-2); border: 1px solid var(--border-a); border-radius: var(--radius-sm);
    color: var(--text-med); font-size: 11px; padding: 6px 12px; cursor: pointer; user-select: none;
  }

  .shoulders { flex-shrink: 0; display: flex; justify-content: space-between; padding: 0 4px 4px; }
</style>
