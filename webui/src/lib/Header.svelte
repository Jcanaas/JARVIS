<script>
  import { onMount, onDestroy } from 'svelte'
  import { activePanel, PANELS } from '../stores/nav.js'
  import { voiceState } from '../stores/sse.js'
  import { rightPanelCollapsed } from '../stores/ui.js'

  const STATE_PALETTE = {
    SPEAKING: { label: 'HABLANDO', color: '#B6C4FF', bg: 'rgba(94, 130, 255, 0.11)' },
    THINKING: { label: 'PENSANDO', color: '#C4B5FD', bg: 'rgba(167, 139, 250, 0.11)' },
    PROCESSING: { label: 'PROCESANDO', color: '#FDE68A', bg: 'rgba(250, 204, 21, 0.09)' },
    MUTED: { label: 'SILENCIADO', color: '#FDA4AF', bg: 'rgba(244, 63, 94, 0.09)' },
    LISTENING: { label: 'ESCUCHANDO', color: '#4ADE80', bg: 'rgba(52, 211, 153, 0.09)' },
  }

  let statePill = $derived(STATE_PALETTE[$voiceState] ?? { label: $voiceState, color: '#B6C4FF', bg: 'rgba(94, 130, 255, 0.09)' })
  let currentPanel = $derived(PANELS.find((p) => p.id === $activePanel))

  let now = $state(new Date())
  let timer
  onMount(() => {
    timer = setInterval(() => { now = new Date() }, 1000)
  })
  onDestroy(() => clearInterval(timer))

  const timeFmt = new Intl.DateTimeFormat('es-ES', { hour: '2-digit', minute: '2-digit' })
  const dateFmt = new Intl.DateTimeFormat('es-ES', { day: '2-digit', month: 'short' })
</script>

<header class="header">
  <div class="brand">
    <img src="/favicon.png" alt="" class="logo" />
    <div class="brand-text">
      <div class="title">J.A.R.V.I.S</div>
      <div class="subtitle">MARK XXXIX</div>
    </div>
  </div>

  <div class="divider"></div>

  <div class="mode">
    <div class="mode-title">{currentPanel?.label ?? ''}</div>
    <div class="mode-sub">{currentPanel?.subtitle ?? ''}</div>
  </div>

  <div class="spacer"></div>

  <div class="state-pill" style="color: {statePill.color}; background: {statePill.bg}">
    <span class="dot" style="background: {statePill.color}"></span>
    {statePill.label}
  </div>

  <div class="clock">
    <div class="time">{timeFmt.format(now)}</div>
    <div class="date">{dateFmt.format(now)}</div>
  </div>

  <button
    class="collapse-btn"
    title={$rightPanelCollapsed ? 'Mostrar panel' : 'Ocultar panel'}
    onclick={() => rightPanelCollapsed.update((v) => !v)}
  >
    {$rightPanelCollapsed ? '⟨' : '⟩'}
  </button>
</header>

<style>
  .header {
    height: 66px;
    flex-shrink: 0;
    display: flex;
    align-items: center;
    gap: 16px;
    padding: 0 18px;
    background: var(--panel);
    border-bottom: 1px solid var(--border-a);
  }

  .brand {
    display: flex;
    align-items: center;
    gap: 10px;
  }

  .logo {
    width: 32px;
    height: 32px;
    border-radius: 50%;
  }

  .title {
    font-size: 14px;
    font-weight: 700;
    letter-spacing: 0.04em;
    color: var(--text);
  }

  .subtitle {
    font-size: 10px;
    color: var(--text-med);
    letter-spacing: 0.08em;
  }

  .divider {
    width: 1px;
    height: 28px;
    background: var(--border-a);
  }

  .mode-title {
    font-size: 13px;
    font-weight: 600;
    color: var(--text);
  }

  .mode-sub {
    font-size: 11px;
    color: var(--text-med);
  }

  .spacer {
    flex: 1;
  }

  .state-pill {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 6px 12px;
    border-radius: 8px;
    border: 1px solid rgba(255, 255, 255, 0.08);
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 0.04em;
  }

  .dot {
    width: 6px;
    height: 6px;
    border-radius: 50%;
  }

  .clock {
    text-align: right;
  }

  .time {
    font-size: 13px;
    font-weight: 600;
    color: var(--text);
    font-family: var(--font-mono);
  }

  .date {
    font-size: 10px;
    color: var(--text-med);
    text-transform: capitalize;
  }

  .collapse-btn {
    background: var(--panel-2);
    border: 1px solid var(--border-a);
    border-radius: var(--radius-sm);
    color: var(--text-med);
    width: 28px;
    height: 28px;
    cursor: pointer;
  }

  .collapse-btn:hover {
    color: var(--text);
    border-color: var(--pri-dim);
  }
</style>
