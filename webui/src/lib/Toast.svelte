<script>
  import { toasts } from '../stores/sse.js'
</script>

<div class="toast-stack">
  {#each $toasts as toast (toast.id)}
    <div class="toast">{toast.text}</div>
  {/each}
</div>

<style>
  .toast-stack {
    position: fixed;
    bottom: 24px;
    left: 50%;
    transform: translateX(-50%);
    display: flex;
    flex-direction: column;
    gap: 8px;
    z-index: 1000;
    pointer-events: none;
  }

  .toast {
    background: var(--glass-d);
    backdrop-filter: blur(8px);
    border: 1px solid var(--border-a);
    border-radius: var(--radius-sm);
    color: var(--text);
    padding: 10px 16px;
    font-size: 13px;
    box-shadow: 0 4px 16px var(--shadow);
    max-width: 480px;
    text-align: center;
  }
</style>
