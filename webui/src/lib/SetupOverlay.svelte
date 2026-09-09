<script>
  import { completeSetup } from '../api.js'

  // The agent worker waits until this screen saves the first Gemini key.

  let { onDone } = $props()

  const detectedOs = navigator.platform?.toLowerCase().includes('win')
    ? 'windows'
    : navigator.platform?.toLowerCase().includes('mac')
      ? 'mac'
      : 'linux'

  let apiKey = $state('')
  let osSystem = $state(detectedOs)
  let saving = $state(false)
  let error = $state('')

  async function save() {
    if (!apiKey.trim()) {
      error = 'Falta la clave de Gemini.'
      return
    }
    saving = true
    error = ''
    try {
      await completeSetup(apiKey.trim(), osSystem)
      onDone?.()
    } catch (e) {
      error = e.message
    } finally {
      saving = false
    }
  }
</script>

<div class="overlay">
  <div class="card">
    <img src="/favicon.png" alt="" class="logo" />
    <h1>Initialisation Required</h1>
    <p class="sub">Configura J.A.R.V.I.S antes de arrancar.</p>

    <label>
      Gemini API key
      <input type="password" bind:value={apiKey} placeholder="AIza…" onkeydown={(e) => e.key === 'Enter' && save()} />
    </label>

    <label>
      Sistema operativo
      <select bind:value={osSystem}>
        <option value="windows">Windows</option>
        <option value="mac">macOS</option>
        <option value="linux">Linux</option>
      </select>
    </label>

    {#if error}<div class="error">{error}</div>{/if}

    <button class="primary" onclick={save} disabled={saving}>
      {saving ? 'Iniciando…' : 'Initialise Systems'}
    </button>
  </div>
</div>

<style>
  .overlay {
    position: fixed;
    inset: 0;
    background: var(--bg);
    display: flex;
    align-items: center;
    justify-content: center;
    z-index: 3000;
  }

  .card {
    width: min(380px, 90vw);
    background: rgba(15, 23, 42, 0.92);
    border: 1px solid rgba(255, 255, 255, 0.14);
    border-radius: 22px;
    padding: 30px;
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 10px;
    text-align: center;
  }

  .logo { width: 56px; height: 56px; border-radius: 50%; margin-bottom: 4px; }

  h1 { margin: 0; font-size: 17px; color: var(--pri); }
  .sub { margin: 0 0 8px; font-size: 12px; color: var(--pri-dim); }

  label {
    width: 100%;
    display: flex;
    flex-direction: column;
    gap: 4px;
    text-align: left;
    font-size: 10px;
    letter-spacing: 0.06em;
    text-transform: uppercase;
    color: var(--text-med);
  }

  input, select {
    background: var(--panel-2);
    border: 1px solid var(--border-a);
    border-radius: var(--radius-sm);
    color: var(--text);
    padding: 8px 10px;
    font-size: 13px;
  }

  .error { color: var(--red); font-size: 12px; }

  .primary {
    width: 100%;
    margin-top: 6px;
    background: var(--pri-gho);
    border: 1px solid var(--pri-dim);
    border-radius: var(--radius-sm);
    color: var(--pri);
    padding: 9px;
    font-size: 13px;
    font-weight: 600;
    cursor: pointer;
  }

  .primary:disabled { opacity: 0.6; cursor: not-allowed; }
</style>
