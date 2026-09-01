<script>
  import { onMount } from 'svelte'
  import { downloads } from '../stores/sse.js'
  import { listDownloads, pauseDownload, resumeDownload, removeDownload, openDownloadFolder } from '../api.js'

  // Mirrors ui/widgets/downloads.py's DownloadsListWidget: a row per
  // torrent download (games/movies/anime) with a progress bar and a
  // Pausar/Reanudar/Abrir carpeta/Borrar menu.

  let openMenuId = $state('')

  onMount(async () => {
    try {
      downloads.set(await listDownloads())
    } catch { /* SSE will populate it once the manager next fires */ }
  })

  function fmtSize(bytes) {
    if (!bytes) return ''
    if (bytes < 1024 ** 2) return `${(bytes / 1024).toFixed(0)} KB`
    if (bytes < 1024 ** 3) return `${(bytes / 1024 ** 2).toFixed(1)} MB`
    return `${(bytes / 1024 ** 3).toFixed(2)} GB`
  }

  function toggleMenu(id) {
    openMenuId = openMenuId === id ? '' : id
  }

  async function act(fn, id) {
    openMenuId = ''
    try { await fn(id) } catch (e) { console.warn(e) }
  }
</script>

{#if $downloads.length}
  <div class="downloads">
    {#each $downloads as d (d.id)}
      <div class="dl-row">
        <div class="dl-info">
          <div class="dl-title">{d.title}</div>
          <div class="dl-meta">
            {#if d.status === 'downloading'}
              {(d.progress * 100).toFixed(0)}% · {fmtSize(d.speed)}/s
            {:else if d.status === 'done'}
              Completado · {fmtSize(d.total)}
            {:else if d.status === 'paused'}
              Pausado · {(d.progress * 100).toFixed(0)}%
            {:else if d.status === 'error'}
              Error{d.error ? `: ${d.error}` : ''}
            {:else}
              {d.status}
            {/if}
          </div>
          <div class="dl-bar"><div class="dl-fill" style="width: {Math.round((d.progress || 0) * 100)}%"></div></div>
        </div>
        <div class="dl-menu-wrap">
          <button class="dl-menu-btn" onclick={() => toggleMenu(d.id)}>⋮</button>
          {#if openMenuId === d.id}
            <div class="dl-menu">
              {#if d.status === 'downloading'}
                <button onclick={() => act(pauseDownload, d.id)}>Pausar</button>
              {:else if d.status === 'paused' || d.status === 'error'}
                <button onclick={() => act(resumeDownload, d.id)}>Reanudar</button>
              {/if}
              <button onclick={() => act(openDownloadFolder, d.id)}>Abrir carpeta</button>
              <button class="danger" onclick={() => act(removeDownload, d.id)}>Borrar</button>
            </div>
          {/if}
        </div>
      </div>
    {/each}
  </div>
{/if}

<style>
  .downloads {
    display: flex;
    flex-direction: column;
    gap: 6px;
    flex-shrink: 0;
    max-height: 160px;
    overflow-y: auto;
  }

  .dl-row {
    display: flex;
    align-items: center;
    gap: 6px;
    background: var(--panel-2);
    border-radius: var(--radius-sm);
    padding: 6px 8px;
  }

  .dl-info { flex: 1; min-width: 0; }
  .dl-title { font-size: 11px; color: var(--text); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .dl-meta { font-size: 10px; color: var(--text-med); margin: 2px 0; }
  .dl-bar { height: 3px; background: var(--bar-bg); border-radius: 2px; overflow: hidden; }
  .dl-fill { height: 100%; background: var(--pri); }

  .dl-menu-wrap { position: relative; flex-shrink: 0; }
  .dl-menu-btn { background: none; border: none; color: var(--text-med); cursor: pointer; font-size: 14px; }

  .dl-menu {
    position: absolute;
    right: 0;
    top: 20px;
    background: var(--panel);
    border: 1px solid var(--border-a);
    border-radius: var(--radius-sm);
    box-shadow: 0 4px 16px var(--shadow);
    display: flex;
    flex-direction: column;
    z-index: 10;
    min-width: 120px;
  }

  .dl-menu button {
    background: none;
    border: none;
    color: var(--text-dim);
    padding: 6px 12px;
    text-align: left;
    font-size: 11px;
    cursor: pointer;
  }
  .dl-menu button:hover { background: var(--glass); }
  .dl-menu button.danger { color: var(--red); }
</style>
