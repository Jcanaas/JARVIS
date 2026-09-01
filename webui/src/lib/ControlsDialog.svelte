<script>
  // Web equivalent of ui/widgets/controls.py's ControlsDialog — keyboard
  // side only (gamepad capture belongs to the phone's controller, not this
  // desktop panel). Click a cell, press a key, done — same interaction as
  // legacy: Escape cancels, a still-capturing cell times out after 6s
  // (_CAPTURE_TIMEOUT_MS there), and it writes through
  // actions/input_config.py's real Bindings storage via
  // /api/emulator/controls/*, not a web-only preference.
  import * as api from '../api.js'

  let { consoleId, onClose, onSaved } = $props()

  let buttons = $state([])
  let bindings = $state({})
  let loading = $state(true)
  let error = $state('')
  let capturing = $state('') // button id currently waiting for a keypress, or ''
  let captureTimer = null

  async function load() {
    loading = true
    error = ''
    try {
      const res = await api.getEmulatorControls(consoleId)
      buttons = res.buttons
      bindings = res.bindings
    } catch (e) {
      error = e.message
    } finally {
      loading = false
    }
  }

  function startCapture(buttonId) {
    stopCapture()
    capturing = buttonId
    captureTimer = setTimeout(stopCapture, 6000) // matches _CAPTURE_TIMEOUT_MS
  }

  function stopCapture() {
    clearTimeout(captureTimer)
    captureTimer = null
    capturing = ''
  }

  async function onCaptureKey(e) {
    if (!capturing) return
    e.preventDefault()
    e.stopPropagation()
    const buttonId = capturing
    if (e.key === 'Escape') {
      stopCapture()
      return
    }
    stopCapture()
    try {
      const res = await api.bindEmulatorControl(consoleId, buttonId, e.code)
      bindings = { ...bindings, [buttonId]: { code: res.code, label: res.label } }
      onSaved?.()
    } catch (err) {
      error = err.message
    }
  }

  async function resetAll() {
    error = ''
    try {
      await api.resetEmulatorControls(consoleId)
      await load()
      onSaved?.()
    } catch (e) {
      error = e.message
    }
  }

  load()
</script>

<svelte:window onkeydown={onCaptureKey} />

<div class="backdrop" role="presentation" onclick={onClose}>
  <div class="dialog" role="dialog" aria-modal="true" tabindex="-1" onclick={(e) => e.stopPropagation()}>
    <div class="header">
      <h3>Controles — {consoleId.toUpperCase()}</h3>
      <button class="icon-btn" onclick={onClose} aria-label="Cerrar">✕</button>
    </div>

    {#if error}<div class="error">{error}</div>{/if}

    {#if loading}
      <div class="empty">Cargando…</div>
    {:else}
      <div class="grid">
        <div class="row head">
          <span>Botón</span>
          <span>Teclado</span>
        </div>
        {#each buttons as btn (btn.id)}
          <div class="row">
            <span class="label">{btn.label}</span>
            <button
              class="cell"
              class:capturing={capturing === btn.id}
              onclick={() => startCapture(btn.id)}
            >
              {capturing === btn.id ? 'Pulsa una tecla…' : (bindings[btn.id]?.label || '—')}
            </button>
          </div>
        {/each}
      </div>
      <div class="footer">
        <button class="ghost" onclick={resetAll}>Restaurar por defecto</button>
      </div>
    {/if}
  </div>
</div>

<style>
  .backdrop {
    position: fixed; inset: 0; background: rgba(0,0,0,0.6);
    display: flex; align-items: center; justify-content: center; z-index: 1000;
  }
  .dialog {
    background: var(--panel); border: 1px solid var(--border-a); border-radius: var(--radius);
    width: 380px; max-height: 80vh; overflow-y: auto; padding: 18px;
  }
  .header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 12px; }
  .header h3 { margin: 0; font-size: 15px; }
  .icon-btn {
    background: var(--panel-2); border: 1px solid var(--border-a); border-radius: var(--radius-sm);
    color: var(--text-med); width: 28px; height: 28px; cursor: pointer;
  }
  .error { color: var(--red); font-size: 12px; margin-bottom: 10px; }
  .empty { color: var(--text-med); font-size: 13px; padding: 16px; text-align: center; }

  .grid { display: flex; flex-direction: column; gap: 6px; }
  .row { display: grid; grid-template-columns: 1fr 1.4fr; align-items: center; gap: 10px; }
  .row.head span { color: var(--text-dim); font-size: 11px; text-transform: uppercase; letter-spacing: 0.04em; }
  .label { font-size: 13px; color: var(--text); }
  .cell {
    background: var(--panel-2); border: 1px solid var(--border-a); border-radius: var(--radius-sm);
    color: var(--text); font-size: 12.5px; padding: 7px 10px; cursor: pointer; text-align: left;
    font-family: var(--font-mono);
  }
  .cell.capturing { border-color: var(--pri); color: var(--pri); }

  .footer { margin-top: 14px; display: flex; justify-content: flex-end; }
  .ghost {
    background: var(--panel-2); border: 1px solid var(--border-a); border-radius: var(--radius-sm);
    color: var(--text-med); font-size: 12.5px; padding: 7px 14px; cursor: pointer;
  }
</style>
