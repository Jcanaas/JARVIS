<script>
  import { fade } from 'svelte/transition'
</script>

<div class="splash" out:fade={{ duration: 300 }}>
  <div class="ring-wrap">
    <img src="/favicon.png" alt="" class="logo" />
    <div class="pulse"></div>
  </div>
  <div class="label">Conectando con J.A.R.V.I.S…</div>
</div>

<style>
  .splash {
    position: fixed;
    inset: 0;
    background: var(--bg);
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 20px;
    z-index: 2000;
  }

  .ring-wrap {
    position: relative;
    width: 96px;
    height: 96px;
    display: flex;
    align-items: center;
    justify-content: center;
  }

  .logo {
    width: 96px;
    height: 96px;
    border-radius: 50%;
    animation: spin 3.5s linear infinite;
    position: relative;
    z-index: 1;
  }

  .pulse {
    position: absolute;
    inset: -12px;
    border-radius: 50%;
    border: 1px solid var(--pri-dim);
    animation: pulse 1.8s ease-out infinite;
  }

  .label {
    color: var(--text-med);
    font-size: 13px;
    letter-spacing: 0.02em;
  }

  @keyframes spin {
    from { transform: rotate(0deg); }
    to { transform: rotate(360deg); }
  }

  @keyframes pulse {
    0% { transform: scale(0.9); opacity: 0.8; }
    100% { transform: scale(1.35); opacity: 0; }
  }
</style>
