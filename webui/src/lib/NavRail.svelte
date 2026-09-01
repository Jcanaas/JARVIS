<script>
  import { PANELS, activePanel } from '../stores/nav.js'
  import { whatsappUnread } from '../stores/sse.js'
  import Icon from './Icon.svelte'
</script>

<nav class="rail">
  {#each PANELS as panel (panel.id)}
    <button
      class="item"
      class:active={$activePanel === panel.id}
      class:disabled={!panel.available}
      disabled={!panel.available}
      title={panel.available ? panel.label : `${panel.label} — no disponible en modo web todavía, usa --legacy`}
      onclick={() => panel.available && activePanel.set(panel.id)}
    >
      <Icon name={panel.icon} />
      <span class="label">{panel.label}</span>
      {#if panel.id === 'whatsapp' && $whatsappUnread > 0}
        <span class="badge">{$whatsappUnread}</span>
      {/if}
    </button>
  {/each}
</nav>

<style>
  .rail {
    width: var(--nav-w);
    flex: 0 0 var(--nav-w);
    background: var(--panel);
    border-right: 1px solid var(--border-a);
    display: flex;
    flex-direction: column;
    align-items: stretch;
    gap: 4px;
    padding: 12px 6px;
    overflow-y: auto;
  }

  .item {
    position: relative;
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 6px;
    background: transparent;
    border: 1px solid transparent;
    border-radius: var(--radius-sm);
    color: var(--text-med);
    padding: 10px 4px;
    cursor: pointer;
    transition: background 0.15s ease, color 0.15s ease, border-color 0.15s ease;
  }

  .item:hover:not(.disabled) {
    background: var(--glass);
    color: var(--text);
  }

  .item.active {
    background: var(--pri-gho);
    border-color: var(--pri-dim);
    color: var(--pri);
  }

  .item.disabled {
    opacity: 0.4;
    cursor: not-allowed;
  }

  .label {
    font-size: 10px;
    text-align: center;
    line-height: 1.2;
  }

  .badge {
    position: absolute;
    top: 4px;
    right: 8px;
    min-width: 16px;
    height: 16px;
    padding: 0 4px;
    border-radius: 8px;
    background: var(--red);
    color: var(--white);
    font-size: 10px;
    font-weight: 600;
    display: flex;
    align-items: center;
    justify-content: center;
  }
</style>
