<script>
  import { uploadFile } from '../api.js'

  // Mirrors ui/panels/music.py's FileDropZone -> _on_file_selected: drop or
  // pick a file and it becomes a [FILE_UPLOADED] message the assistant
  // reacts to, same as typing in the command box.

  let fileInput = $state()
  let dragOver = $state(false)
  let hint = $state('')
  let busy = $state(false)

  async function handleFile(file) {
    if (!file) return
    busy = true
    hint = `Subiendo ${file.name}…`
    try {
      const res = await uploadFile(file)
      hint = `${res.name} · ${res.size} · díselo a JARVIS`
    } catch (e) {
      hint = `Error: ${e.message}`
    } finally {
      busy = false
    }
  }

  function onDrop(e) {
    e.preventDefault()
    dragOver = false
    const file = e.dataTransfer?.files?.[0]
    if (file) handleFile(file)
  }

  function onPick(e) {
    const file = e.target.files?.[0]
    e.target.value = ''
    if (file) handleFile(file)
  }
</script>

<div
  class="drop-zone"
  class:drag={dragOver}
  ondrop={onDrop}
  ondragover={(e) => { e.preventDefault(); dragOver = true }}
  ondragleave={() => (dragOver = false)}
  onclick={() => fileInput.click()}
  role="button"
  tabindex="0"
  onkeydown={(e) => (e.key === 'Enter' || e.key === ' ') && fileInput.click()}
>
  <input type="file" bind:this={fileInput} onchange={onPick} hidden />
  {#if busy}
    <span>Subiendo…</span>
  {:else}
    <span>Arrastra un archivo o haz clic</span>
  {/if}
</div>
{#if hint}<div class="file-hint">{hint}</div>{/if}

<style>
  .drop-zone {
    border: 1px dashed var(--border-a);
    border-radius: var(--radius-sm);
    padding: 14px;
    text-align: center;
    font-size: 11px;
    color: var(--text-med);
    cursor: pointer;
  }

  .drop-zone.drag {
    border-color: var(--pri-dim);
    background: var(--glass);
    color: var(--pri);
  }

  .file-hint {
    font-size: 10px;
    color: var(--text-med);
    margin-top: 4px;
    overflow-wrap: anywhere;
  }
</style>
