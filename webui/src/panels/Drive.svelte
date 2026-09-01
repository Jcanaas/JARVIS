<script>
  import {
    getDriveFiles, searchDrive, getDrivePreview, drivePreviewRawUrl, driveDownloadUrl,
    uploadDriveFile, createDriveFolder, shareDriveFile, renameDriveFile, deleteDriveFile,
  } from '../api.js'
  import Icon from '../lib/Icon.svelte'

  // Mirrors ui/panels/drive.py's split layout: file list (left) + details/
  // preview pane (right) + a bottom action bar (Descargar/Compartir/
  // Renombrar/Borrar). create_folder is exposed here even though the Qt
  // panel has no button for it — the backend already fully supports it
  // (actions/gdrive.py:422) and the audit flagged it as a real gap, not a
  // feature to invent from scratch.

  let folderStack = $state([]) // [{id, name}] — id:null means root
  let folderId = $derived(folderStack.length ? folderStack[folderStack.length - 1].id : null)
  let breadcrumb = $derived(['Mi unidad', ...folderStack.map((f) => f.name)])

  let files = $state([])
  let loading = $state(false)
  let error = $state('')
  let needsAuth = $state(false)
  let query = $state('')
  let searching = $state(false)

  let selected = $state(null)
  let preview = $state(null)
  let previewLoading = $state(false)

  let showNewFolder = $state(false)
  let newFolderName = $state('')

  let showShare = $state(false)
  let shareEmail = $state('')
  let shareRole = $state('reader')
  let shareAnyone = $state(false)
  let shareBusy = $state(false)
  let shareResult = $state('')

  const FOLDER_MIME = 'application/vnd.google-apps.folder'

  async function loadFolder() {
    loading = true
    error = ''
    needsAuth = false
    searching = false
    query = ''
    selected = null
    preview = null
    try {
      files = await getDriveFiles(folderId)
    } catch (e) {
      needsAuth = /needs_auth|credentials|token/i.test(e.message)
      error = e.message
      files = []
    } finally {
      loading = false
    }
  }

  async function doSearch() {
    const q = query.trim()
    if (!q) return loadFolder()
    searching = true
    loading = true
    error = ''
    try {
      files = await searchDrive(q, folderId)
    } catch (e) {
      error = e.message
      files = []
    } finally {
      loading = false
    }
  }

  function goRoot() {
    folderStack = []
    loadFolder()
  }

  function goToCrumb(i) {
    // i=0 is "Mi unidad" (root); i=n means keep the first n folder entries.
    folderStack = folderStack.slice(0, i)
    loadFolder()
  }

  function openItem(f) {
    if (f.mimeType === FOLDER_MIME) {
      folderStack = [...folderStack, { id: f.id, name: f.name }]
      loadFolder()
      return
    }
    selectFile(f)
  }

  async function selectFile(f) {
    selected = f
    preview = null
    previewLoading = true
    try {
      preview = await getDrivePreview(f.id)
    } catch (e) {
      preview = { kind: 'unsupported', error: e.message }
    } finally {
      previewLoading = false
    }
  }

  async function onUploadPicked(e) {
    const file = e.target.files?.[0]
    e.target.value = ''
    if (!file) return
    error = ''
    try {
      await uploadDriveFile(file, folderId)
      await loadFolder()
    } catch (err) {
      error = err.message
    }
  }

  async function createFolder() {
    const name = newFolderName.trim()
    if (!name) return
    try {
      await createDriveFolder(name, folderId)
      showNewFolder = false
      newFolderName = ''
      await loadFolder()
    } catch (e) {
      error = e.message
    }
  }

  function downloadSelected() {
    if (!selected) return
    window.open(driveDownloadUrl(selected.id), '_blank')
  }

  function openShareDialog() {
    if (!selected) return
    shareEmail = ''
    shareRole = 'reader'
    shareAnyone = false
    shareResult = ''
    showShare = true
  }

  async function confirmShare() {
    shareBusy = true
    shareResult = ''
    try {
      const res = await shareDriveFile(selected.id, {
        email: shareEmail, role: shareRole, anyone: shareAnyone || !shareEmail,
      })
      shareResult = res.result?.link || 'Compartido.'
    } catch (e) {
      shareResult = e.message
    } finally {
      shareBusy = false
    }
  }

  async function renameSelected() {
    if (!selected) return
    const newName = prompt('Nuevo nombre:', selected.name)
    if (!newName || newName === selected.name) return
    try {
      await renameDriveFile(selected.id, newName)
      await loadFolder()
    } catch (e) {
      error = e.message
    }
  }

  async function deleteSelected() {
    if (!selected) return
    if (!confirm(`¿Enviar "${selected.name}" a la papelera?`)) return
    try {
      await deleteDriveFile(selected.id, false)
      selected = null
      preview = null
      await loadFolder()
    } catch (e) {
      error = e.message
    }
  }

  function fmtSize(bytes) {
    const n = Number(bytes)
    if (!n) return ''
    if (n < 1024) return `${n} B`
    if (n < 1024 ** 2) return `${(n / 1024).toFixed(1)} KB`
    if (n < 1024 ** 3) return `${(n / 1024 ** 2).toFixed(1)} MB`
    return `${(n / 1024 ** 3).toFixed(1)} GB`
  }

  loadFolder()
</script>

<div class="panel">
  <header class="drive-header">
    <div class="titles">
      <div class="title">Drive</div>
      <div class="subtitle">Archivos y documentos de tu cuenta</div>
    </div>
    <div class="spacer"></div>
    <input
      class="search" type="text" placeholder="Buscar en Drive…"
      bind:value={query} onkeydown={(e) => e.key === 'Enter' && doSearch()}
    />
    <button class="tb-btn" onclick={goRoot} title="Mi unidad"><Icon name="drive" size={15} /></button>
    <button class="tb-btn" onclick={() => (showNewFolder = true)}>Nueva carpeta</button>
    <label class="tb-btn primary">
      Subir
      <input type="file" onchange={onUploadPicked} hidden />
    </label>
  </header>

  <div class="breadcrumb">
    {#each breadcrumb as crumb, i}
      {#if i > 0}<span class="sep">/</span>{/if}
      <button class="crumb" onclick={() => goToCrumb(i)}>{crumb}</button>
    {/each}
  </div>

  {#if needsAuth}
    <div class="auth-hint">Necesitas iniciar sesión con Google para usar Drive.</div>
  {:else if error}
    <div class="error">{error}</div>
  {/if}

  <div class="body">
    <div class="file-panel">
      <div class="panel-heading">ARCHIVOS</div>
      <div class="file-list">
        {#if loading}
          <div class="empty">Cargando…</div>
        {:else if !files.length}
          <div class="empty">Carpeta vacía.</div>
        {/if}
        {#each files as f (f.id)}
          <button
            class="file-row"
            class:active={selected?.id === f.id}
            onclick={() => openItem(f)}
          >
            <Icon name={f.mimeType === FOLDER_MIME ? 'drive' : 'send'} size={16} />
            <span class="file-name">{f.name}</span>
            {#if f.size}<span class="file-size">{fmtSize(f.size)}</span>{/if}
          </button>
        {/each}
      </div>
    </div>

    <div class="details-panel">
      <div class="panel-heading">DETALLES</div>
      <div class="preview-area">
        {#if !selected}
          <div class="empty">Selecciona un archivo para ver detalles.</div>
        {:else if previewLoading}
          <div class="empty">Cargando vista previa…</div>
        {:else if preview}
          {#if preview.kind === 'image'}
            <img class="preview-img" src={drivePreviewRawUrl(selected.id)} alt={preview.name} />
          {:else if preview.kind === 'pdf'}
            <iframe class="preview-frame" title={preview.name} src={drivePreviewRawUrl(selected.id)}></iframe>
          {:else if preview.kind === 'audio'}
            <audio class="preview-audio" controls src={drivePreviewRawUrl(selected.id)}></audio>
          {:else if preview.kind === 'text'}
            <pre class="preview-text">{preview.text}</pre>
          {:else if preview.kind === 'folder'}
            <div class="empty">Carpeta — ábrela con doble clic en la lista.</div>
          {:else if preview.kind === 'too_large'}
            <div class="empty">Archivo demasiado grande para previsualizar ({fmtSize(preview.size)}).</div>
          {:else}
            <div class="empty">No hay vista previa disponible para este archivo.</div>
          {/if}
        {/if}
      </div>
      {#if selected}
        <div class="meta-box">
          <div class="meta-row"><span>Nombre</span><span>{selected.name}</span></div>
          <div class="meta-row"><span>Tipo</span><span>{selected.mimeType}</span></div>
          {#if selected.size}<div class="meta-row"><span>Tamaño</span><span>{fmtSize(selected.size)}</span></div>{/if}
          {#if selected.modifiedTime}<div class="meta-row"><span>Modificado</span><span>{new Date(selected.modifiedTime).toLocaleString('es-ES')}</span></div>{/if}
        </div>
      {/if}
    </div>
  </div>

  <div class="action-bar">
    <button disabled={!selected} onclick={downloadSelected}>Descargar</button>
    <button disabled={!selected} onclick={openShareDialog}>Compartir</button>
    <button disabled={!selected} onclick={renameSelected}>Renombrar</button>
    <button class="danger" disabled={!selected} onclick={deleteSelected}>Borrar</button>
  </div>
</div>

{#if showNewFolder}
  <div class="modal-backdrop" onclick={() => (showNewFolder = false)}>
    <div class="modal" onclick={(e) => e.stopPropagation()}>
      <h3>Nueva carpeta</h3>
      <input type="text" bind:value={newFolderName} placeholder="Nombre de la carpeta" />
      <div class="modal-actions">
        <div class="spacer"></div>
        <button class="ghost" onclick={() => (showNewFolder = false)}>Cancelar</button>
        <button class="primary" onclick={createFolder}>Crear</button>
      </div>
    </div>
  </div>
{/if}

{#if showShare}
  <div class="modal-backdrop" onclick={() => (showShare = false)}>
    <div class="modal" onclick={(e) => e.stopPropagation()}>
      <h3>Compartir "{selected?.name}"</h3>
      <label>Email (o vacío para enlace público)<input type="email" bind:value={shareEmail} placeholder="alguien@ejemplo.com" /></label>
      <label>Permiso
        <select bind:value={shareRole}>
          <option value="reader">Lector</option>
          <option value="commenter">Comentarista</option>
          <option value="writer">Editor</option>
        </select>
      </label>
      <label class="checkbox-row"><input type="checkbox" bind:checked={shareAnyone} /> Cualquiera con el enlace</label>
      {#if shareResult}<div class="share-result">{shareResult}</div>{/if}
      <div class="modal-actions">
        <div class="spacer"></div>
        <button class="ghost" onclick={() => (showShare = false)}>Cerrar</button>
        <button class="primary" onclick={confirmShare} disabled={shareBusy}>{shareBusy ? '…' : 'Compartir'}</button>
      </div>
    </div>
  </div>
{/if}

<style>
  .panel { height: 100%; min-height: 0; display: flex; flex-direction: column; padding: 16px; gap: 10px; }

  .drive-header { display: flex; align-items: center; gap: 8px; flex-shrink: 0; }
  .titles { display: flex; flex-direction: column; }
  .title { font-size: 16px; font-weight: 700; color: var(--text); }
  .subtitle { font-size: 11px; color: var(--text-med); }
  .spacer { flex: 1; }

  .search {
    background: var(--panel-2);
    border: 1px solid var(--border-a);
    border-radius: var(--radius-sm);
    color: var(--text);
    padding: 6px 12px;
    font-size: 12px;
    width: 180px;
  }

  .tb-btn {
    display: flex;
    align-items: center;
    gap: 4px;
    background: var(--panel-2);
    border: 1px solid var(--border-a);
    border-radius: var(--radius-sm);
    color: var(--text-dim);
    padding: 6px 12px;
    font-size: 12px;
    cursor: pointer;
    white-space: nowrap;
  }

  .tb-btn.primary { background: var(--pri-gho); border-color: var(--pri-dim); color: var(--pri); }

  .breadcrumb { display: flex; align-items: center; gap: 4px; flex-shrink: 0; font-size: 12px; }
  .crumb { background: none; border: none; color: var(--text-med); cursor: pointer; padding: 2px 4px; }
  .crumb:hover { color: var(--pri); }
  .sep { color: var(--text-med); }

  .auth-hint { color: var(--acc2); font-size: 12px; flex-shrink: 0; }
  .error { color: var(--red); font-size: 12px; flex-shrink: 0; }

  .body { flex: 1; min-height: 0; display: flex; gap: 12px; }

  .file-panel { flex: 7; min-width: 0; display: flex; flex-direction: column; }
  .details-panel {
    flex: 5;
    min-width: 0;
    display: flex;
    flex-direction: column;
    background: var(--panel);
    border: 1px solid var(--border-a);
    border-radius: var(--radius);
    padding: 12px;
    gap: 8px;
  }

  .panel-heading { font-size: 10px; letter-spacing: 0.08em; color: var(--text-med); flex-shrink: 0; }

  .file-list { flex: 1; min-height: 0; overflow-y: auto; display: flex; flex-direction: column; gap: 2px; }
  .empty { color: var(--text-med); font-size: 12px; padding: 16px; text-align: center; }

  .file-row {
    display: flex;
    align-items: center;
    gap: 10px;
    background: transparent;
    border: 1px solid transparent;
    border-radius: var(--radius-sm);
    padding: 7px 10px;
    cursor: pointer;
    text-align: left;
    color: var(--text-dim);
  }

  .file-row:hover { background: var(--glass); border-color: var(--border-a); }
  .file-row.active { background: var(--pri-gho); border-color: var(--pri-dim); color: var(--pri); }

  .file-name { flex: 1; min-width: 0; font-size: 12px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .file-size { font-size: 11px; color: var(--text-med); }

  .preview-area {
    flex: 4;
    min-height: 0;
    display: flex;
    align-items: center;
    justify-content: center;
    overflow: auto;
    background: var(--panel-2);
    border-radius: var(--radius-sm);
  }

  .preview-img { max-width: 100%; max-height: 100%; object-fit: contain; }
  .preview-frame { width: 100%; height: 100%; border: none; }
  .preview-audio { width: 90%; }
  .preview-text {
    width: 100%;
    height: 100%;
    margin: 0;
    padding: 10px;
    font-size: 11px;
    color: var(--text-dim);
    overflow: auto;
    white-space: pre-wrap;
  }

  .meta-box { flex-shrink: 0; max-height: 150px; overflow-y: auto; font-size: 11px; }
  .meta-row { display: flex; justify-content: space-between; gap: 8px; padding: 3px 0; color: var(--text-med); }
  .meta-row span:last-child { color: var(--text-dim); text-align: right; overflow-wrap: anywhere; }

  .action-bar { display: flex; gap: 8px; flex-shrink: 0; }
  .action-bar button {
    background: var(--panel-2);
    border: 1px solid var(--border-a);
    border-radius: var(--radius-sm);
    color: var(--text-dim);
    padding: 8px 16px;
    font-size: 12px;
    cursor: pointer;
  }
  .action-bar button:disabled { opacity: 0.4; cursor: not-allowed; }
  .action-bar button.danger:hover:not(:disabled) { color: var(--red); border-color: rgba(255,94,130,0.3); }

  /* Modals share the same visual language as Calendar.svelte/Gmail.svelte */
  .modal-backdrop {
    position: fixed; inset: 0; background: rgba(0,0,0,0.5);
    display: flex; align-items: center; justify-content: center; z-index: 1000;
  }
  .modal {
    width: min(420px, 90vw);
    background: var(--panel);
    border: 1px solid var(--border-a);
    border-radius: var(--radius);
    padding: 20px;
    display: flex;
    flex-direction: column;
    gap: 10px;
  }
  .modal h3 { margin: 0 0 4px; color: var(--text); }
  .modal label { display: flex; flex-direction: column; gap: 4px; font-size: 11px; color: var(--text-med); }
  .modal input, .modal select {
    background: var(--panel-2);
    border: 1px solid var(--border-a);
    border-radius: var(--radius-sm);
    color: var(--text);
    padding: 7px 10px;
    font-size: 13px;
  }
  .checkbox-row { flex-direction: row !important; align-items: center; gap: 6px !important; }
  .share-result { font-size: 12px; color: var(--pri); overflow-wrap: anywhere; }
  .modal-actions { display: flex; align-items: center; gap: 8px; margin-top: 8px; }
  .modal-actions button {
    border-radius: var(--radius-sm);
    padding: 7px 14px;
    font-size: 12px;
    cursor: pointer;
    border: 1px solid var(--border-a);
  }
  .modal-actions .ghost { background: transparent; color: var(--text-dim); }
  .modal-actions .primary { background: var(--pri-gho); border-color: var(--pri-dim); color: var(--pri); }
</style>
