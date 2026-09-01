<script>
  import * as api from '../api.js'
  import Icon from '../lib/Icon.svelte'

  let { active = true } = $props()

  let query = $state('')
  let items = $state([])
  let loading = $state(false)
  let error = $state('')
  let playing = $state(null) // {id, title}

  function fmtDuration(s) {
    s = Number(s) || 0
    const m = Math.floor(s / 60)
    const sec = Math.floor(s % 60)
    return `${m}:${String(sec).padStart(2, '0')}`
  }

  async function loadRecommendations() {
    loading = true
    error = ''
    try {
      items = await api.getYoutubeRecommendations()
    } catch (e) {
      error = e.message
      items = []
    } finally {
      loading = false
    }
  }

  async function doSearch() {
    const q = query.trim()
    if (!q) return loadRecommendations()
    loading = true
    error = ''
    try {
      items = await api.searchYoutube(q)
    } catch (e) {
      error = e.message
      items = []
    } finally {
      loading = false
    }
  }

  function onSearchKeydown(e) {
    if (e.key === 'Enter') doSearch()
  }

  function play(video) {
    playing = video
  }

  function stopPlaying() {
    playing = null
  }

  $effect(() => {
    if (active && !items.length && !loading && !error) loadRecommendations()
  })
</script>

<div class="panel">
  {#if !playing}
    <div class="search-row">
      <input type="text" placeholder="Buscar en YouTube…" bind:value={query} onkeydown={onSearchKeydown} />
      <button onclick={doSearch}>Buscar</button>
    </div>

    {#if loading}
      <div class="empty">Cargando…</div>
    {:else if error}
      <div class="empty error">{error}</div>
    {:else if !items.length}
      <div class="empty">Sin resultados.</div>
    {:else}
      <div class="grid">
        {#each items as v (v.id)}
          <button class="card" onclick={() => play(v)}>
            <div class="thumb-wrap">
              <img src={`https://i.ytimg.com/vi/${v.id}/mqdefault.jpg`} alt="" loading="lazy" class="thumb" />
              {#if v.duration}<span class="duration">{fmtDuration(v.duration)}</span>{/if}
            </div>
            <div class="card-title">{v.title}</div>
            <div class="card-meta">{v.channel || ''}</div>
          </button>
        {/each}
      </div>
    {/if}
  {:else}
    <div class="player">
      <button class="back" onclick={stopPlaying} title="Volver"><Icon name="up" size={16} /></button>
      <div class="video-wrap">
        <iframe
          src={`https://www.youtube.com/embed/${playing.id}?autoplay=1`}
          title={playing.title}
          allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture"
          allowfullscreen
        ></iframe>
      </div>
      <div class="video-title">{playing.title}</div>
      <div class="video-channel">{playing.channel || ''}</div>
    </div>
  {/if}
</div>

<style>
  .panel { height: 100%; min-height: 0; display: flex; flex-direction: column; padding: 16px; gap: 12px; }

  .search-row { display: flex; gap: 8px; flex-shrink: 0; }
  .search-row input {
    flex: 1; min-width: 0;
    background: var(--panel-2); border: 1px solid var(--border-a); border-radius: var(--radius-sm);
    color: var(--text); padding: 8px 12px; font-size: 14px;
  }
  .search-row button {
    background: var(--pri-gho); border: 1px solid var(--border-a); border-radius: var(--radius-sm);
    color: var(--pri); padding: 8px 14px; cursor: pointer;
  }

  .empty { color: var(--text-med); font-size: 13px; padding: 24px; text-align: center; }
  .empty.error { color: var(--red); }

  .grid {
    flex: 1; min-height: 0; overflow-y: auto;
    display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 14px;
  }
  .card { background: transparent; border: none; cursor: pointer; text-align: left; color: var(--text); display: flex; flex-direction: column; gap: 4px; }
  .thumb-wrap { position: relative; }
  .thumb { width: 100%; aspect-ratio: 16/9; object-fit: cover; border-radius: var(--radius-sm); background: var(--panel-2); }
  .duration {
    position: absolute; bottom: 4px; right: 4px;
    background: rgba(0,0,0,0.75); color: #fff; font-size: 10px; padding: 1px 5px; border-radius: 4px;
  }
  .card-title { font-size: 12.5px; font-weight: 600; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
  .card-meta { font-size: 11px; color: var(--text-med); }

  .player { flex: 1; min-height: 0; display: flex; flex-direction: column; gap: 10px; }
  .back {
    align-self: flex-start;
    background: var(--panel-2); border: 1px solid var(--border-a); border-radius: var(--radius-sm);
    color: var(--text); width: 30px; height: 30px; cursor: pointer; transform: rotate(-90deg);
    display: flex; align-items: center; justify-content: center;
  }
  .video-wrap { flex: 1; min-height: 0; border-radius: var(--radius); overflow: hidden; background: #000; }
  .video-wrap iframe { width: 100%; height: 100%; border: none; }
  .video-title { font-size: 14px; font-weight: 600; flex-shrink: 0; }
  .video-channel { font-size: 12px; color: var(--text-med); flex-shrink: 0; }
</style>
