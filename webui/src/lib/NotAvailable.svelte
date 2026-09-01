<script>
  let { label = '' } = $props()
</script>

<div class="wrap">
  <div class="card">
    <h2>{label}</h2>
    <p>Todavía no está disponible en el modo web.</p>
    <p class="hint">Ciérralo y arranca con <code>--legacy</code> para usar la interfaz clásica mientras tanto.</p>
  </div>
</div>

<style>
  .wrap {
    height: 100%;
    display: flex;
    align-items: center;
    justify-content: center;
  }

  .card {
    text-align: center;
    color: var(--text-med);
    max-width: 360px;
  }

  h2 {
    color: var(--text);
    margin: 0 0 8px;
  }

  .hint {
    font-size: 13px;
    margin-top: 12px;
  }

  code {
    font-family: var(--font-mono);
    background: var(--panel-2);
    padding: 2px 6px;
    border-radius: 4px;
  }
</style>
