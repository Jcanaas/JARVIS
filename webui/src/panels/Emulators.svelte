<script>
  // ui/panels/emulators.py's EmulatorsModePanel + LibraryModePanel, minus the
  // Steam/FitGirl Juegos hub (ui/panels/games.py — a separate, unrelated
  // feature). The core (actions/libretro.py) is driven headless by
  // actions/emulator_session.py; this panel just talks to that over HTTP:
  // the video is a plain <img> pointed at the MJPEG stream, input is a POST
  // per press, no WebSocket anywhere. Two top-level views besides the
  // player: "Mi biblioteca" (ROMs already owned) and "Catálogo" (search/
  // download via actions/rom_catalog.py, or local import for consoles with
  // no catalog — PS2, 3DS).
  import { onDestroy } from 'svelte'
  import * as api from '../api.js'
  import { emulatorPlaying } from '../stores/emulator.js'
  import { events } from '../stores/sse.js'
  import Icon from '../lib/Icon.svelte'
  import ControlsDialog from '../lib/ControlsDialog.svelte'

  let { active = true } = $props()

  let loading = $state(false)
  let error = $state('')
  let entries = $state([])

  let playing = $state(null) // the LibraryEntry currently loaded, or null
  let status = $state(null) // last /api/emulator/status response
  let padLayout = $state({})
  let streamNonce = $state(0) // bumped on every start() so the <img> can't show a stale MJPEG connection from a previous game
  // 3DS-only: the combined top+bottom frame shows the bottom (touch) screen
  // on the PC too, which is redundant once it's mirrored to the phone via
  // actions/lan_dashboard.py's /api/gamepad/screen. Cropped server-side
  // (?crop=top on /api/emulator/stream, actions/webui_server.py) rather
  // than with CSS on the full frame — cropping first means the <img> keeps
  // its native resolution and the existing max-width/max-height "contain"
  // fit still works exactly as it always did; forcing the *displayed* box
  // into the cropped aspect ratio via CSS instead upscaled the same source
  // pixels across a bigger area than before, which read as a resolution/
  // bitrate drop that was never really there.
  let hideBottomScreen = $state(false)
  // Only set for consoles actions/emulator_runtime.py's RESOLUTION_CHOICES
  // actually has an option for (PS2, 3DS) — null means "this console has no
  // such setting", not an error. A libretro core only reads its options at
  // load time, so picking a new value here only takes effect the *next*
  // time this game starts, never on the session already running.
  let resolutionOption = $state(null) // {key, choices, current}
  let actionError = $state('')
  let libraryError = $state('') // start() failed before the player mounted (e.g. missing PS2 BIOS)
  let biosBusy = $state(false)

  // -- Catálogo (Fase C) ---------------------------------------------------
  let view = $state('library') // 'library' | 'catalog' | 'catalogDetail'
  let consoles = $state([])
  let catalogConsoleId = $state('gba')
  let catalogRegion = $state('')
  let catalogQuery = $state('')
  let catalogResults = $state([])
  let catalogLoading = $state(false)
  let catalogError = $state('')
  let catalogSearchTimer = null
  let catalogSearchToken = 0

  let detailRom = $state(null)
  let detailVersions = $state([])
  let detailLoading = $state(false)
  // stem -> {fraction, status: 'downloading'|'ready'|'error', error}
  let downloadState = $state({})

  let importFile = $state(null)
  let importTitle = $state('')
  let importBusy = $state(false)
  let importError = $state('')

  const catalogConsole = $derived(consoles.find((c) => c.id === catalogConsoleId))

  async function loadConsoles() {
    try {
      consoles = await api.getEmulatorConsoles()
      if (consoles.length && !consoles.some((c) => c.id === catalogConsoleId)) {
        catalogConsoleId = consoles[0].id
      }
    } catch (e) {
      catalogError = e.message
    }
  }

  async function openCatalog() {
    view = 'catalog'
    if (!consoles.length) await loadConsoles()
    if (catalogConsole?.has_catalog) runCatalogSearch()
  }

  function selectCatalogConsole(id) {
    catalogConsoleId = id
    catalogQuery = ''
    catalogResults = []
    catalogError = ''
    const console_ = consoles.find((c) => c.id === id)
    if (console_?.has_catalog) runCatalogSearch()
  }

  function queueCatalogSearch() {
    clearTimeout(catalogSearchTimer)
    catalogSearchTimer = setTimeout(runCatalogSearch, 280)
  }

  async function runCatalogSearch() {
    const token = ++catalogSearchToken
    catalogLoading = true
    catalogError = ''
    try {
      const results = catalogQuery.trim()
        ? await api.searchEmulatorCatalog(catalogConsoleId, catalogQuery.trim(), catalogRegion)
        : await api.getEmulatorCatalogPopular(catalogConsoleId)
      if (token !== catalogSearchToken) return // a newer search already landed
      catalogResults = results
    } catch (e) {
      if (token !== catalogSearchToken) return
      catalogError = e.message
      catalogResults = []
    } finally {
      if (token === catalogSearchToken) catalogLoading = false
    }
  }

  async function openDetail(rom) {
    detailRom = rom
    detailVersions = [rom]
    view = 'catalogDetail'
    detailLoading = true
    try {
      detailVersions = await api.getEmulatorCatalogVersions(rom.console_id, rom.stem)
    } catch {
      // A rom with no other known versions isn't an error — just itself.
    } finally {
      detailLoading = false
    }
  }

  function backToCatalog() {
    view = 'catalog'
    detailRom = null
  }

  async function startDownload(rom) {
    downloadState = { ...downloadState, [rom.stem]: { fraction: 0, status: 'downloading' } }
    try {
      await api.downloadEmulatorRom(rom.console_id, rom.stem)
    } catch (e) {
      downloadState = { ...downloadState, [rom.stem]: { status: 'error', error: e.message } }
    }
  }

  async function playFromCatalog(rom) {
    await play({ entry_id: `rom:${rom.stem}`, console_id: rom.console_id, title: rom.title })
  }

  async function doImport() {
    if (!importFile) return
    importBusy = true
    importError = ''
    try {
      await api.importEmulatorRom(importFile, catalogConsoleId, importTitle)
      importFile = null
      importTitle = ''
      view = 'library'
      loadLibrary()
    } catch (e) {
      importError = e.message
    } finally {
      importBusy = false
    }
  }

  const unsubscribeCatalogEvents = events.subscribe(({ type, data }) => {
    if (type === 'emulator_download_progress') {
      downloadState = {
        ...downloadState,
        [data.stem]: { fraction: data.fraction, status: 'downloading' },
      }
    } else if (type === 'emulator_download_ready') {
      downloadState = { ...downloadState, [data.stem]: { status: 'ready' } }
      catalogResults = catalogResults.map((r) =>
        r.stem === data.stem ? { ...r, downloaded: true } : r
      )
    } else if (type === 'emulator_download_error') {
      downloadState = { ...downloadState, [data.stem]: { status: 'error', error: data.error } }
    }
  })
  onDestroy(unsubscribeCatalogEvents)

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
    // Covers both stop() and pollStatus() noticing the core died/exited on
    // its own — either way panel shortcuts (P/U/etc.) should work again.
    emulatorPlaying.set(false)
  }

  async function play(entry) {
    actionError = ''
    libraryError = ''
    try {
      const res = await api.startEmulator(entry.entry_id)
      playing = entry
      status = res.status
      streamNonce++
      padLayout = await api.getEmulatorPad(entry.console_id)
      loadKeyMap(entry.console_id)
      loadResolutionOption(entry.console_id)
      pollStatus()
      emulatorPlaying.set(true)
    } catch (e) {
      // start() fails before the player ever mounts, so this can't reuse
      // actionError (only rendered inside the player view below).
      libraryError = e.message
    }
  }

  // 3DS's resolution choices are bare integers ("1".."6") — labelling them
  // "1x".."6x" instead reads as a scale factor rather than a mystery
  // number. PS2's own choices are already self-explanatory strings
  // ("2x Native (~720p)"), left untouched.
  function resolutionChoiceLabel(choice) {
    return /^\d+$/.test(choice) ? `${choice}x` : choice
  }

  async function loadResolutionOption(consoleId) {
    try {
      resolutionOption = await api.getEmulatorResolution(consoleId)
    } catch {
      resolutionOption = null // this console just doesn't have the setting
    }
  }

  async function changeResolution(value) {
    if (!playing || !resolutionOption) return
    const previous = resolutionOption.current
    resolutionOption = { ...resolutionOption, current: value }
    try {
      await api.setEmulatorResolution(playing.console_id, value)
    } catch (e) {
      resolutionOption = { ...resolutionOption, current: previous }
      actionError = e.message
    }
  }

  // BIOS import: web equivalent of ui/panels/emulators.py's _import_bios(),
  // which opens a native Qt file picker — browsers only have a file input.
  // Only PS2 needs one (actions/bios.py's own docstring explains why this
  // app never bundles one itself), so this only ever shows for that error.
  const NEEDS_BIOS = 'PS2 necesita una BIOS'
  async function importBios(e) {
    const file = e.target.files?.[0]
    if (!file) return
    biosBusy = true
    try {
      await api.importEmulatorBios(file)
      libraryError = ''
    } catch (err) {
      libraryError = err.message
    } finally {
      biosBusy = false
      e.target.value = ''
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
    hideBottomScreen = false
    resolutionOption = null
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

  // -- Input: buttons -------------------------------------------------------
  // Fire-and-forget: a press/release is a single POST, no batching — human
  // input rates never come close to needing it, and the emulation loop never
  // waits on this either way (actions/emulator_session.py's thread is the
  // only thing that touches the core). The desktop panel used to also draw
  // its own on-screen d-pad/face-button pad (mouse/touch clickable) — pulled
  // per user feedback: on a PC every digital button already has a keyboard
  // binding (default or remapped, see the controls dialog below), so a
  // second on-screen copy was pure clutter. Kept as sendButton() because
  // onKeydown/onKeyup below still need it; only the extra UI is gone.
  function sendButton(name, pressed) {
    api.sendEmulatorInput({ buttons: [{ name, pressed }] }).catch(() => {})
  }

  // -- Input: keyboard ----------------------------------------------------
  // Same default layout as actions/input_config.py's _BASE_KEYBOARD (arrow
  // keys, X/Space=A, Z=B, S=X, A=Y, Q=L, W=R, Enter=start, Backspace=select)
  // — used until /api/emulator/controls answers (or if it fails), and for
  // any button the user has never customized. The user's actual bindings
  // (shared with --legacy, see actions/input_config.py's
  // qt_key_from_js_code) load into activeKeyMap once a game starts, and
  // reload after every save in the controls dialog below.
  const DEFAULT_KEY_MAP = {
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
  let activeKeyMap = $state(DEFAULT_KEY_MAP)
  const heldKeys = new Set()

  async function loadKeyMap(consoleId) {
    try {
      const res = await api.getEmulatorControls(consoleId)
      const map = {}
      for (const [buttonId, binding] of Object.entries(res.bindings)) {
        if (binding.code) map[binding.code] = buttonId
      }
      activeKeyMap = Object.keys(map).length ? map : DEFAULT_KEY_MAP
    } catch {
      activeKeyMap = DEFAULT_KEY_MAP
    }
  }

  let controlsOpen = $state(false)
  let pausedForControls = $state(false)

  async function openControls() {
    pausedForControls = !status?.paused
    if (pausedForControls) {
      try { await api.pauseEmulator(true) } catch {}
      status = status ? { ...status, paused: true } : status
    }
    controlsOpen = true
  }

  async function closeControls() {
    controlsOpen = false
    if (pausedForControls) {
      try { await api.pauseEmulator(false) } catch {}
      status = status ? { ...status, paused: false } : status
    }
  }

  // -- Input: keyboard-driven analog stick (W/A/S/D) -----------------------
  // Ports ui/widgets/retro.py's RetroScreen._stick_keys_held/_STICK_KEYS
  // exactly: full deflection (not gradual), and completely independent of
  // the button remapping above — like legacy, it's checked only as a
  // *fallback* when the same key isn't already bound to a button (below),
  // which is what makes GBA's default R=W binding still win there while
  // n64/ps2/3ds (which free W/A/S/D from any button in
  // input_config.py's _profile_defaults specifically for this) get the
  // stick instead. This replaces the on-screen mouse-drag stick that used
  // to sit under the video — per user feedback, on a PC keyboard input
  // beats a mouse-dragged on-screen control.
  const STICK_KEYS = { KeyW: 'up', KeyS: 'down', KeyA: 'left', KeyD: 'right' }
  const stickKeysHeld = new Set()

  function updateKeyboardStick() {
    const held = stickKeysHeld
    const x = (held.has('right') ? 32767 : 0) - (held.has('left') ? 32767 : 0)
    const y = (held.has('down') ? 32767 : 0) - (held.has('up') ? 32767 : 0)
    api.sendEmulatorInput({
      axes: [{ index: 0, axis: 0, value: x }, { index: 0, axis: 1, value: y }],
    }).catch(() => {})
  }

  function onKeydown(e) {
    if (!playing || controlsOpen) return
    const button = activeKeyMap[e.code]
    if (button) {
      if (heldKeys.has(e.code)) return
      heldKeys.add(e.code)
      e.preventDefault()
      sendButton(button, true)
      return
    }
    const direction = STICK_KEYS[e.code]
    if (direction) {
      e.preventDefault()
      stickKeysHeld.add(direction)
      updateKeyboardStick()
    }
  }

  function onKeyup(e) {
    if (!playing || controlsOpen) return
    const button = activeKeyMap[e.code]
    if (button) {
      heldKeys.delete(e.code)
      e.preventDefault()
      sendButton(button, false)
      return
    }
    const direction = STICK_KEYS[e.code]
    if (direction) {
      e.preventDefault()
      stickKeysHeld.delete(direction)
      updateKeyboardStick()
    }
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
      <div class="tabs">
        <button class="tab" class:active={view === 'library'} onclick={() => (view = 'library')}>Mi biblioteca</button>
        <button class="tab" class:active={view !== 'library'} onclick={openCatalog}>Catálogo</button>
      </div>
      {#if view === 'library'}
        <button class="ghost" onclick={loadLibrary} title="Actualizar"><Icon name="refresh" size={16} /></button>
      {/if}
    </div>

    {#if libraryError}
      <div class="action-error">
        {libraryError}
        {#if libraryError.includes(NEEDS_BIOS)}
          <label class="bios-import">
            {biosBusy ? 'Importando…' : 'Importar BIOS de PS2'}
            <input type="file" accept=".bin,.rom" onchange={importBios} disabled={biosBusy} hidden />
          </label>
        {/if}
      </div>
    {/if}

    {#if view === 'library'}
      {#if loading}
        <div class="empty">Cargando…</div>
      {:else if error}
        <div class="empty error">{error}</div>
      {:else if !entries.length}
        <div class="empty">
          Tu biblioteca está vacía. Copia una ROM en <code>roms/&lt;consola&gt;/</code>, pulsa actualizar, o
          descarga una desde el <button class="linklike" onclick={openCatalog}>Catálogo</button>.
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
    {:else if view === 'catalog'}
      <div class="console-chips">
        {#each consoles as c (c.id)}
          <button class="chip" class:active={c.id === catalogConsoleId} onclick={() => selectCatalogConsole(c.id)}>
            {c.short}
          </button>
        {/each}
      </div>

      {#if catalogConsole && !catalogConsole.has_catalog}
        <div class="import-form">
          <p class="hint">
            No hay catálogo de descarga para {catalogConsole.name} — importa un archivo que ya tengas.
          </p>
          <input
            type="file"
            accept={catalogConsole.rom_exts.join(',')}
            onchange={(e) => (importFile = e.target.files?.[0] || null)}
          />
          <input type="text" placeholder="Título (opcional)" bind:value={importTitle} />
          {#if importError}<div class="action-error">{importError}</div>{/if}
          <button class="primary" disabled={!importFile || importBusy} onclick={doImport}>
            {importBusy ? 'Importando…' : 'Importar'}
          </button>
        </div>
      {:else}
        <div class="region-chips">
          {#each [['', 'Todas'], ['USA', 'USA'], ['Europe', 'Europa'], ['Japan', 'Japón'], ['Spain', 'España']] as [value, label] (value)}
            <button
              class="chip small"
              class:active={catalogRegion === value}
              onclick={() => { catalogRegion = value; runCatalogSearch() }}
            >{label}</button>
          {/each}
        </div>
        <input
          type="text"
          class="search-box"
          placeholder="Buscar…"
          bind:value={catalogQuery}
          oninput={queueCatalogSearch}
        />

        {#if catalogLoading}
          <div class="empty">Cargando…</div>
        {:else if catalogError}
          <div class="empty error">{catalogError}</div>
        {:else if !catalogResults.length}
          <div class="empty">Sin resultados.</div>
        {:else}
          <div class="grid">
            {#each catalogResults as rom (rom.stem)}
              <button class="card" onclick={() => openDetail(rom)}>
                <div class="thumb-wrap">
                  {#if rom.poster_url}
                    <img src={rom.poster_url} alt="" loading="lazy" class="thumb" />
                  {:else}
                    <div class="thumb thumb-placeholder"><Icon name="games" size={28} /></div>
                  {/if}
                  {#if rom.downloaded}<span class="console-badge on-disk">EN DISCO</span>{/if}
                </div>
                <div class="card-title">{rom.title}</div>
              </button>
            {/each}
          </div>
        {/if}
      {/if}
    {:else if view === 'catalogDetail' && detailRom}
      <div class="detail">
        <button class="ghost back-btn" onclick={backToCatalog}><Icon name="up" size={16} /> Volver</button>
        <div class="detail-body">
          {#if detailRom.poster_url}
            <img src={detailRom.poster_url} alt="" class="detail-art" />
          {:else}
            <div class="detail-art thumb-placeholder"><Icon name="games" size={40} /></div>
          {/if}
          <div class="detail-info">
            <h3>{detailRom.title}</h3>
            {#if detailVersions.length > 1}
              <select
                class="version-select"
                onchange={(e) => (detailRom = detailVersions.find((v) => v.stem === e.target.value) || detailRom)}
              >
                {#each detailVersions as v (v.stem)}
                  <option value={v.stem} selected={v.stem === detailRom.stem}>{v.version_label || v.region || v.stem}</option>
                {/each}
              </select>
            {/if}
            <p class="detail-meta">{detailRom.region || 'Región sin indicar'} · {detailRom.size_label}</p>
            {#if !detailRom.available}
              <p class="warn">{detailRom.unavailable_reason || 'Esta versión no está disponible para descargar.'}</p>
            {/if}

            {#if downloadState[detailRom.stem]?.status === 'downloading'}
              <div class="progress"><div class="progress-bar" style={`width:${(downloadState[detailRom.stem].fraction || 0) * 100}%`}></div></div>
              <button class="primary" disabled>Descargando…</button>
            {:else if downloadState[detailRom.stem]?.status === 'error'}
              <div class="action-error">{downloadState[detailRom.stem].error}</div>
              <button class="primary" onclick={() => startDownload(detailRom)}>Reintentar</button>
            {:else if detailRom.downloaded || downloadState[detailRom.stem]?.status === 'ready'}
              <button class="primary" onclick={() => playFromCatalog(detailRom)}>▶ Jugar</button>
            {:else if !detailRom.available}
              <button class="primary" disabled>No disponible</button>
            {:else}
              <button class="primary" onclick={() => startDownload(detailRom)}>⬇ Descargar ROM</button>
            {/if}
          </div>
        </div>
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
          <button class="ghost" onclick={openControls} title="Controles">🎮</button>
          {#if resolutionOption}
            <label class="res-field" title="Resolución interna del emulador — se aplica al reiniciar el juego, no ahora mismo">
              <span class="res-label">🖼️ Res</span>
              <select
                class="res-select"
                value={resolutionOption.current}
                onchange={(e) => changeResolution(e.target.value)}
              >
                {#each resolutionOption.choices as choice (choice)}
                  <option value={choice}>{resolutionChoiceLabel(choice)}</option>
                {/each}
              </select>
            </label>
          {/if}
          {#if playing.console_id === '3ds'}
            <button
              class="ghost"
              class:active={hideBottomScreen}
              onclick={() => (hideBottomScreen = !hideBottomScreen)}
              title={hideBottomScreen ? 'Mostrar pantalla táctil aquí también' : 'Ocultar pantalla táctil (ya la tienes en el móvil)'}
            >
              {hideBottomScreen ? '🖥️' : '📱🖥️'}
            </button>
          {/if}
          <button class="ghost" onclick={toggleFullscreen} title="Pantalla completa">⛶</button>
        </div>
      </div>

      {#if actionError}<div class="action-error">{actionError}</div>{/if}

      {#if controlsOpen}
        <ControlsDialog
          consoleId={playing.console_id}
          onClose={closeControls}
          onSaved={() => loadKeyMap(playing.console_id)}
        />
      {/if}

      <div class="video-wrap" bind:this={videoWrap}>
        {#key streamNonce}
          <img
            src={`/api/emulator/stream?_=${streamNonce}${hideBottomScreen ? '&crop=top' : ''}`}
            alt={playing.title}
            class="stream"
          />
        {/key}
      </div>

      {#if padLayout.sticks >= 1}
        <span class="stick-hint">Stick analógico: W A S D</span>
      {/if}
    </div>
  {/if}
</div>

<style>
  .panel { height: 100%; min-height: 0; display: flex; flex-direction: column; padding: 16px; gap: 12px; }

  .header-row { display: flex; align-items: center; justify-content: space-between; flex-shrink: 0; }

  .tabs { display: flex; gap: 4px; }
  .tab {
    background: transparent; border: none; color: var(--text-med); font-size: 14px; font-weight: 600;
    padding: 6px 4px; cursor: pointer; border-bottom: 2px solid transparent;
  }
  .tab.active { color: var(--text); border-bottom-color: var(--pri); }

  .linklike { background: none; border: none; color: var(--pri); cursor: pointer; padding: 0; font: inherit; text-decoration: underline; }

  .console-chips, .region-chips { display: flex; gap: 6px; flex-wrap: wrap; flex-shrink: 0; }
  .chip {
    background: var(--panel-2); border: 1px solid var(--border-a); border-radius: 999px;
    color: var(--text-med); font-size: 12px; padding: 6px 12px; cursor: pointer;
  }
  .chip.small { font-size: 11px; padding: 4px 10px; }
  .chip.active { background: var(--pri-gho); border-color: var(--pri); color: var(--pri); }

  .search-box {
    background: var(--panel-2); border: 1px solid var(--border-a); border-radius: var(--radius-sm);
    color: var(--text); padding: 8px 12px; font-size: 13px; flex-shrink: 0;
  }

  .on-disk { background: rgba(50, 180, 100, 0.85); }

  .import-form { display: flex; flex-direction: column; gap: 10px; max-width: 340px; }
  .import-form input[type="text"] {
    background: var(--panel-2); border: 1px solid var(--border-a); border-radius: var(--radius-sm);
    color: var(--text); padding: 8px 12px; font-size: 13px;
  }
  .hint { color: var(--text-med); font-size: 12.5px; margin: 0; }

  .primary {
    background: var(--pri); border: none; border-radius: var(--radius-sm);
    color: #08101F; font-weight: 700; font-size: 13px; padding: 9px 16px; cursor: pointer;
    align-self: flex-start;
  }
  .primary:disabled { opacity: 0.6; cursor: default; }

  .detail { flex: 1; min-height: 0; overflow-y: auto; display: flex; flex-direction: column; gap: 14px; }
  .back-btn { width: auto; padding: 0 12px; gap: 6px; align-self: flex-start; }
  .detail-body { display: flex; gap: 18px; }
  .detail-art { width: 160px; height: 160px; border-radius: var(--radius-sm); object-fit: cover; background: var(--panel-2); flex-shrink: 0; }
  .detail-info { display: flex; flex-direction: column; gap: 8px; min-width: 0; }
  .detail-info h3 { margin: 0; font-size: 16px; }
  .detail-meta { color: var(--text-med); font-size: 12.5px; margin: 0; }
  .version-select {
    background: var(--panel-2); border: 1px solid var(--border-a); border-radius: var(--radius-sm);
    color: var(--text); padding: 6px 10px; font-size: 12.5px; align-self: flex-start;
  }
  .warn { color: #d9a441; font-size: 12.5px; margin: 0; }
  .progress { width: 100%; max-width: 260px; height: 6px; background: var(--panel-2); border-radius: 3px; overflow: hidden; }
  .progress-bar { height: 100%; background: var(--pri); transition: width 0.2s; }

  .ghost {
    background: var(--panel-2); border: 1px solid var(--border-a); border-radius: var(--radius-sm);
    color: var(--text); width: 32px; height: 32px; cursor: pointer;
    display: flex; align-items: center; justify-content: center; flex-shrink: 0;
  }
  .ghost.active { background: var(--pri); border-color: var(--pri); }
  .res-field {
    display: flex; align-items: center; gap: 5px; height: 32px;
    background: var(--panel-2); border: 1px solid var(--border-a); border-radius: var(--radius-sm);
    padding: 0 8px 0 10px; flex-shrink: 0;
  }
  .res-label { color: var(--text-med); font-size: 11px; font-weight: 600; white-space: nowrap; }
  .res-select {
    background: transparent; border: none; color: var(--text); font-size: 11px;
    padding: 0; cursor: pointer; max-width: 90px;
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

  .action-error { color: var(--red); font-size: 12px; flex-shrink: 0; display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
  .bios-import {
    background: var(--panel-2); border: 1px solid var(--border-a); border-radius: var(--radius-sm);
    color: var(--pri); font-size: 12px; padding: 6px 12px; cursor: pointer;
  }

  .video-wrap {
    flex: 1; min-height: 0; border-radius: var(--radius); overflow: hidden; background: #000;
    display: flex; align-items: center; justify-content: center;
  }
  /* max-width/max-height alone only ever shrinks an <img> that's bigger
     than its box — it never grows one that's smaller, which an emulator's
     native frame (e.g. 3DS's ~400x480) always is next to a real window.
     The video sat tiny in a sea of black until width/height:100% +
     object-fit actually stretch it (up or down) to fill .video-wrap,
     still preserving aspect ratio. */
  .stream { width: 100%; height: 100%; object-fit: contain; image-rendering: pixelated; }

  .stick-hint { flex-shrink: 0; color: var(--text-dim); font-size: 11.5px; padding: 4px; }
</style>
