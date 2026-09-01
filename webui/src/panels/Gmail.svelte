<script>
  import { getEmails, readEmail, archiveEmail, trashEmail, sendEmail, aiDraftEmail } from '../api.js'
  import Icon from '../lib/Icon.svelte'

  // Mirrors ui/panels/gmail.py's GmailModePanel: folder chips (exact same
  // 9, ui/panels/gmail.py:396-406), a paginated list, a reader pane, and a
  // compose dialog with an AI-draft helper and attachments. No
  // reply/forward/star/labels buttons — the Qt panel doesn't have them
  // either.

  const FOLDERS = [
    { text: 'Entrada', label: 'INBOX', unread: false },
    { text: 'No leídos', label: 'INBOX', unread: true },
    { text: 'Destacados', label: 'STARRED', unread: false },
    { text: 'Importantes', label: 'IMPORTANT', unread: false },
    { text: 'Enviados', label: 'SENT', unread: false },
    { text: 'Borradores', label: 'DRAFT', unread: false },
    { text: 'Spam', label: 'SPAM', unread: false },
    { text: 'Papelera', label: 'TRASH', unread: false },
    { text: 'Todo', label: 'ALL', unread: false },
  ]

  let activeFolder = $state(FOLDERS[0])
  let query = $state('')
  let page = $state(1)
  let pages = $state(1)
  let total = $state(0)
  let emails = $state([])
  let loading = $state(false)
  let error = $state('')
  let needsAuth = $state(false)

  let selected = $state(null)
  let selectedId = $state('')
  let loadingEmail = $state(false)

  let composeOpen = $state(false)
  let composeTo = $state('')
  let composeSubject = $state('')
  let composeBody = $state('')
  let composeFiles = $state([])
  let composeAiInstruction = $state('')
  let composeAiLoading = $state(false)
  let composeError = $state('')
  let sending = $state(false)

  async function loadPage(p = 1) {
    page = p
    loading = true
    error = ''
    needsAuth = false
    try {
      const res = await getEmails({
        page, pageSize: 25, label: activeFolder.label,
        unreadOnly: activeFolder.unread, query,
      })
      emails = res.emails || []
      pages = res.pages || 1
      total = res.total || 0
    } catch (e) {
      needsAuth = /needs_auth|credentials|token/i.test(e.message)
      error = e.message
      emails = []
    } finally {
      loading = false
    }
  }

  function selectFolder(f) {
    activeFolder = f
    query = ''
    loadPage(1)
  }

  function onSearchKeydown(e) {
    if (e.key === 'Enter') loadPage(1)
  }

  async function openEmail(id) {
    selectedId = id
    loadingEmail = true
    try {
      selected = await readEmail(id)
      emails = emails.map((e) => (e.id === id ? { ...e, unread: false } : e))
    } catch (e) {
      error = e.message
    } finally {
      loadingEmail = false
    }
  }

  function backToList() {
    selectedId = ''
    selected = null
  }

  async function archiveCurrent() {
    if (!selectedId) return
    try {
      await archiveEmail(selectedId)
      emails = emails.filter((e) => e.id !== selectedId)
      backToList()
    } catch (e) {
      error = e.message
    }
  }

  async function trashCurrent() {
    if (!selectedId) return
    try {
      await trashEmail(selectedId)
      emails = emails.filter((e) => e.id !== selectedId)
      backToList()
    } catch (e) {
      error = e.message
    }
  }

  function openCompose() {
    composeTo = ''
    composeSubject = ''
    composeBody = ''
    composeFiles = []
    composeAiInstruction = ''
    composeError = ''
    composeOpen = true
  }

  function onFilesPicked(e) {
    composeFiles = [...composeFiles, ...Array.from(e.target.files || [])]
    e.target.value = ''
  }

  function onDrop(e) {
    e.preventDefault()
    if (e.dataTransfer?.files?.length) {
      composeFiles = [...composeFiles, ...Array.from(e.dataTransfer.files)]
    }
  }

  function removeFile(i) {
    composeFiles = composeFiles.filter((_, idx) => idx !== i)
  }

  async function runAiDraft() {
    if (!composeAiInstruction.trim()) return
    composeAiLoading = true
    try {
      const draft = await aiDraftEmail(composeAiInstruction, composeTo, composeSubject)
      if (draft.subject) composeSubject = draft.subject
      if (draft.body) composeBody = draft.body
    } catch (e) {
      composeError = e.message
    } finally {
      composeAiLoading = false
    }
  }

  async function send() {
    if (!composeTo.trim()) {
      composeError = 'Falta el destinatario.'
      return
    }
    sending = true
    composeError = ''
    try {
      await sendEmail({ to: composeTo, subject: composeSubject, body: composeBody, attachments: composeFiles })
      composeOpen = false
      if (activeFolder.label === 'SENT') loadPage(1)
    } catch (e) {
      composeError = e.message
    } finally {
      sending = false
    }
  }

  function fmtDate(raw) {
    if (!raw) return ''
    const d = new Date(raw)
    if (Number.isNaN(d.getTime())) return raw
    return d.toLocaleDateString('es-ES', { day: '2-digit', month: 'short' })
  }

  loadPage(1)
</script>

<div class="panel">
  {#if !selectedId}
    <aside class="inbox">
      <div class="inbox-head">
        <div class="inbox-title">Gmail</div>
        <span class="count">{total}</span>
        <button class="compose-btn" onclick={openCompose}>Redactar</button>
      </div>
      <input
        class="search"
        type="text" placeholder="Buscar correo…"
        bind:value={query} onkeydown={onSearchKeydown}
      />
      <div class="folders">
        {#each FOLDERS as f (f.text)}
          <button
            class="chip"
            class:active={activeFolder.text === f.text}
            onclick={() => selectFolder(f)}
          >{f.text}</button>
        {/each}
      </div>

      {#if needsAuth}
        <div class="auth-hint">Necesitas iniciar sesión con Google para usar Gmail.</div>
      {:else if error}
        <div class="error">{error}</div>
      {/if}

      <div class="email-list">
        {#if loading}
          <div class="empty">Cargando…</div>
        {:else if !emails.length}
          <div class="empty">Sin correos aquí.</div>
        {/if}
        {#each emails as e (e.id)}
          <button class="email-row" class:unread={e.unread} onclick={() => openEmail(e.id)}>
            <div class="email-from">{e.from}</div>
            <div class="email-subject">{e.subject || '(sin asunto)'}</div>
            <div class="email-snippet">{e.snippet}</div>
            <div class="email-date">{fmtDate(e.date)}</div>
          </button>
        {/each}
      </div>

      {#if pages > 1}
        <div class="pagination">
          <button disabled={page <= 1} onclick={() => loadPage(page - 1)}>‹</button>
          <span>{page} / {pages}</span>
          <button disabled={page >= pages} onclick={() => loadPage(page + 1)}>›</button>
        </div>
      {/if}
    </aside>
  {:else}
    <section class="reader">
      <div class="reader-toolbar">
        <button class="back" onclick={backToList} title="Volver">←</button>
        <button class="tb-btn" onclick={archiveCurrent} title="Archivar">Archivar</button>
        <button class="tb-btn danger" onclick={trashCurrent} title="Papelera">Papelera</button>
      </div>
      {#if loadingEmail}
        <div class="empty">Cargando…</div>
      {:else if selected}
        <div class="reader-body">
          <h2 class="subject">{selected.subject || '(sin asunto)'}</h2>
          <div class="sender-row">
            <div class="avatar">{(selected.from || '?').slice(0, 1).toUpperCase()}</div>
            <div>
              <div class="sender-name">{selected.from}</div>
              <div class="sender-to">Para: {selected.to || ''}</div>
            </div>
            <div class="sender-date">{fmtDate(selected.date)}</div>
          </div>
          <div class="divider"></div>
          {#if selected.html}
            <div class="html-body">{@html selected.html}</div>
          {:else}
            <div class="html-body plain">{selected.body || ''}</div>
          {/if}
        </div>
      {/if}
    </section>
  {/if}
</div>

{#if composeOpen}
  <div class="modal-backdrop" onclick={() => (composeOpen = false)}>
    <div class="modal" onclick={(e) => e.stopPropagation()} ondrop={onDrop} ondragover={(e) => e.preventDefault()}>
      <h3>Redactar</h3>
      <label>Para<input type="email" bind:value={composeTo} placeholder="destinatario@ejemplo.com" /></label>
      <label>Asunto<input type="text" bind:value={composeSubject} /></label>

      <div class="ai-row">
        <input type="text" placeholder="Instrucción para redactar con IA…" bind:value={composeAiInstruction} />
        <button onclick={runAiDraft} disabled={composeAiLoading}>{composeAiLoading ? '…' : 'Autocompletar'}</button>
      </div>

      <label>Mensaje<textarea bind:value={composeBody} rows="8" placeholder="Escribe tu mensaje, o arrastra archivos aquí"></textarea></label>

      <div class="attach-row">
        <label class="attach-btn">
          Adjuntar
          <input type="file" multiple onchange={onFilesPicked} hidden />
        </label>
        {#if composeFiles.length}
          <span class="attach-list">
            {#each composeFiles as f, i}
              <span class="attach-chip">{f.name} <button onclick={() => removeFile(i)}>×</button></span>
            {/each}
          </span>
        {/if}
      </div>

      {#if composeError}<div class="error">{composeError}</div>{/if}

      <div class="modal-actions">
        <div class="spacer"></div>
        <button class="ghost" onclick={() => (composeOpen = false)}>Cancelar</button>
        <button class="primary" onclick={send} disabled={sending}>{sending ? 'Enviando…' : 'Enviar'}</button>
      </div>
    </div>
  </div>
{/if}

<style>
  .panel { height: 100%; min-height: 0; display: flex; }

  .inbox {
    width: 100%;
    min-height: 0;
    display: flex;
    flex-direction: column;
    padding: 16px;
    gap: 10px;
  }

  .inbox-head {
    display: flex;
    align-items: center;
    gap: 8px;
    flex-shrink: 0;
  }

  .inbox-title { font-size: 16px; font-weight: 700; color: var(--text); }
  .count { font-size: 11px; color: var(--text-med); }

  .compose-btn {
    margin-left: auto;
    background: var(--pri-gho);
    border: 1px solid var(--border-a);
    border-radius: var(--radius-sm);
    color: var(--pri);
    padding: 6px 14px;
    font-size: 12px;
    cursor: pointer;
  }

  .search {
    flex-shrink: 0;
    background: var(--panel-2);
    border: 1px solid var(--border-a);
    border-radius: var(--radius-sm);
    color: var(--text);
    padding: 7px 12px;
    font-size: 13px;
  }

  .folders {
    display: flex;
    gap: 6px;
    flex-wrap: wrap;
    flex-shrink: 0;
  }

  .chip {
    background: transparent;
    border: 1px solid var(--border-a);
    border-radius: 999px;
    color: var(--text-med);
    padding: 4px 12px;
    font-size: 11px;
    cursor: pointer;
  }

  .chip.active {
    background: var(--pri-gho);
    border-color: var(--pri-dim);
    color: var(--pri);
  }

  .auth-hint { color: var(--acc2); font-size: 12px; }
  .error { color: var(--red); font-size: 12px; }

  .email-list {
    flex: 1;
    min-height: 0;
    overflow-y: auto;
    display: flex;
    flex-direction: column;
    gap: 2px;
  }

  .empty { color: var(--text-med); font-size: 12px; padding: 16px; text-align: center; }

  .email-row {
    display: grid;
    grid-template-columns: 140px 1fr auto;
    grid-template-rows: auto auto;
    column-gap: 10px;
    background: transparent;
    border: 1px solid transparent;
    border-bottom: 1px solid var(--border-a);
    border-radius: var(--radius-sm);
    padding: 8px 10px;
    cursor: pointer;
    text-align: left;
    color: var(--text-dim);
  }

  .email-row:hover { background: var(--glass); }
  .email-row.unread { color: var(--text); font-weight: 600; }

  .email-from {
    font-size: 12px;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  .email-date {
    font-size: 11px;
    color: var(--text-med);
    grid-row: 1;
    grid-column: 3;
  }

  .email-subject {
    font-size: 12px;
    grid-column: 1 / 3;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  .email-snippet {
    font-size: 11px;
    color: var(--text-med);
    font-weight: 400;
    grid-column: 1 / 3;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  .pagination {
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 12px;
    flex-shrink: 0;
    font-size: 12px;
    color: var(--text-med);
  }

  .pagination button {
    background: var(--panel-2);
    border: 1px solid var(--border-a);
    border-radius: var(--radius-sm);
    color: var(--text-dim);
    width: 26px;
    height: 26px;
    cursor: pointer;
  }

  .pagination button:disabled { opacity: 0.4; cursor: not-allowed; }

  .reader {
    width: 100%;
    min-height: 0;
    display: flex;
    flex-direction: column;
  }

  .reader-toolbar {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 12px 16px;
    border-bottom: 1px solid var(--border-a);
    flex-shrink: 0;
  }

  .back, .tb-btn {
    background: var(--panel-2);
    border: 1px solid var(--border-a);
    border-radius: var(--radius-sm);
    color: var(--text-dim);
    padding: 6px 12px;
    cursor: pointer;
    font-size: 12px;
  }

  .tb-btn.danger:hover { color: var(--red); border-color: rgba(255,94,130,0.3); }

  .reader-body {
    flex: 1;
    min-height: 0;
    overflow-y: auto;
    padding: 20px;
  }

  .subject { margin: 0 0 12px; color: var(--text); font-size: 18px; }

  .sender-row {
    display: flex;
    align-items: center;
    gap: 10px;
  }

  .avatar {
    width: 36px;
    height: 36px;
    border-radius: 50%;
    background: var(--pri-gho);
    color: var(--pri);
    display: flex;
    align-items: center;
    justify-content: center;
    font-weight: 700;
    flex-shrink: 0;
  }

  .sender-name { font-size: 13px; color: var(--text); }
  .sender-to { font-size: 11px; color: var(--text-med); }
  .sender-date { margin-left: auto; font-size: 11px; color: var(--text-med); }

  .divider { height: 1px; background: var(--border-a); margin: 14px 0; }

  .html-body {
    font-size: 13px;
    color: var(--text-dim);
    line-height: 1.5;
    overflow-wrap: anywhere;
  }

  .html-body :global(img) { max-width: 100%; }
  .html-body :global(a) { color: var(--pri); }
  .html-body.plain { white-space: pre-wrap; font-family: inherit; }

  /* Modal (shared visual language with Calendar.svelte's dialog) */
  .modal-backdrop {
    position: fixed;
    inset: 0;
    background: rgba(0, 0, 0, 0.5);
    display: flex;
    align-items: center;
    justify-content: center;
    z-index: 1000;
  }

  .modal {
    width: min(560px, 90vw);
    max-height: 85vh;
    overflow-y: auto;
    background: var(--panel);
    border: 1px solid var(--border-a);
    border-radius: var(--radius);
    padding: 20px;
    display: flex;
    flex-direction: column;
    gap: 10px;
  }

  .modal h3 { margin: 0 0 4px; color: var(--text); }

  .modal label {
    display: flex;
    flex-direction: column;
    gap: 4px;
    font-size: 11px;
    color: var(--text-med);
  }

  .modal input, .modal textarea {
    background: var(--panel-2);
    border: 1px solid var(--border-a);
    border-radius: var(--radius-sm);
    color: var(--text);
    padding: 7px 10px;
    font-size: 13px;
    font-family: inherit;
  }

  .ai-row { display: flex; gap: 6px; }
  .ai-row input { flex: 1; min-width: 0; }
  .ai-row button {
    background: var(--pri-gho);
    border: 1px solid var(--border-a);
    border-radius: var(--radius-sm);
    color: var(--pri);
    padding: 0 12px;
    font-size: 12px;
    cursor: pointer;
    white-space: nowrap;
  }

  .attach-row { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }

  .attach-btn {
    background: var(--panel-2);
    border: 1px solid var(--border-a);
    border-radius: var(--radius-sm);
    color: var(--text-dim);
    padding: 6px 12px;
    font-size: 12px;
    cursor: pointer;
  }

  .attach-list { display: flex; gap: 6px; flex-wrap: wrap; }

  .attach-chip {
    background: var(--panel-2);
    border-radius: 999px;
    padding: 3px 8px;
    font-size: 11px;
    color: var(--text-dim);
  }

  .attach-chip button {
    background: none;
    border: none;
    color: inherit;
    cursor: pointer;
    margin-left: 3px;
  }

  .modal-actions { display: flex; align-items: center; gap: 8px; margin-top: 8px; }
  .spacer { flex: 1; }
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
