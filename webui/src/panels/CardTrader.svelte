<script>
  import { onMount } from 'svelte'
  import {
    cardtraderStatus, cardtraderSync, cardtraderSearch, cardtraderQuoteDeck,
    cardtraderCartAdd, cardtraderCart, cardtraderCartRemove,
  } from '../api.js'

  // Mirrors ui/panels/cardtrader.py's CardTraderModePanel: three pill tabs
  // (Buscar/Mazo/Carrito), same filter set, same condition/language/foil
  // options. Search/quote progress isn't streamed live (no progress_cb over
  // HTTP) — a spinner stands in for the Qt panel's per-card progress bar.

  const CONDITIONS = ['Poor', 'Heavily Played', 'Played', 'Moderately Played', 'Slightly Played', 'Near Mint', 'Mint']
  const LANGUAGES = ['Cualquiera', 'en', 'es', 'de', 'fr', 'it', 'jp', 'pt']

  let tab = $state('search')
  let catalogStatus = $state(null)
  let syncing = $state(false)

  // -- Search tab --
  let searchName = $state('')
  let zeroOnly = $state(true)
  let condition = $state('Moderately Played')
  let language = $state('Cualquiera')
  let foil = $state('Indiferente')
  let allVersions = $state(false)
  let fastMode = $state(false)
  let searching = $state(false)
  let searchResult = $state(null)
  let searchError = $state('')

  // -- Deck tab --
  let deckText = $state('')
  let deckZeroOnly = $state(true)
  let deckCondition = $state('Moderately Played')
  let deckLanguage = $state('Cualquiera')
  let respectPrintings = $state(false)
  let quoting = $state(false)
  let deckQuote = $state(null)
  let deckError = $state('')
  let addingToCart = $state(false)
  let addCartMsg = $state('')

  // -- Cart tab --
  let cart = $state(null)
  let cartLoading = $state(false)
  let cartError = $state('')

  function filtersPayload(zero, cond, lang, fast = false) {
    return { zero_only: zero, min_condition: cond, language: lang, fast }
  }

  async function loadStatus() {
    try {
      catalogStatus = await cardtraderStatus()
    } catch {
      catalogStatus = null
    }
  }

  async function syncCatalog() {
    syncing = true
    try {
      catalogStatus = await cardtraderSync()
    } catch (e) {
      searchError = e.message
    } finally {
      syncing = false
    }
  }

  async function doSearch() {
    const name = searchName.trim()
    if (!name) return
    searching = true
    searchError = ''
    searchResult = null
    try {
      const foilVal = foil === 'Indiferente' ? null : foil === 'Solo foil'
      searchResult = await cardtraderSearch({
        name,
        all_versions: allVersions,
        filters: { ...filtersPayload(zeroOnly, condition, language, fastMode), foil: foilVal },
      })
    } catch (e) {
      searchError = e.message
    } finally {
      searching = false
    }
  }

  function fmtPrice(cents, currency) {
    if (cents == null) return '—'
    return `${(cents / 100).toFixed(2)} ${currency || 'EUR'}`
  }

  // CardTrader's cart endpoint nests money as {cents, currency} (verified
  // live) rather than the flat price_cents/price_currency pair used
  // elsewhere in the API — this adapts either shape.
  function fmtMoney(m) {
    if (m == null) return '—'
    if (typeof m === 'object') return fmtPrice(m.cents, m.currency)
    return fmtPrice(m)
  }

  async function quoteDeck() {
    if (!deckText.trim()) return
    quoting = true
    deckError = ''
    deckQuote = null
    addCartMsg = ''
    try {
      deckQuote = await cardtraderQuoteDeck({
        deck_text: deckText,
        filters: filtersPayload(deckZeroOnly, deckCondition, deckLanguage),
        respect_printings: respectPrintings,
      })
    } catch (e) {
      deckError = e.message
    } finally {
      quoting = false
    }
  }

  async function addDeckToCart() {
    if (!deckQuote) return
    const items = []
    for (const item of deckQuote.items) {
      if (!item.result) continue
      for (const [offer, qty] of item.result.offers_used) {
        items.push({ product_id: offer.product_id, quantity: qty })
      }
    }
    if (!items.length) {
      addCartMsg = 'No hay ofertas para añadir.'
      return
    }
    addingToCart = true
    addCartMsg = ''
    try {
      const res = await cardtraderCartAdd(items)
      addCartMsg = res.failures?.length
        ? `Añadidas ${res.added}/${items.length} (${res.failures.length} fallaron).`
        : `Añadidas ${res.added} líneas al carrito.`
    } catch (e) {
      addCartMsg = e.message
    } finally {
      addingToCart = false
    }
  }

  async function loadCart() {
    cartLoading = true
    cartError = ''
    try {
      cart = await cardtraderCart()
    } catch (e) {
      cartError = e.message
      cart = null
    } finally {
      cartLoading = false
    }
  }

  let cartItems = $derived(
    (cart?.subcarts || []).flatMap((sc) => sc.cart_items || [])
  )

  async function removeCartItem(productId, quantity) {
    try {
      const res = await cardtraderCartRemove(productId, quantity)
      cart = res.cart
    } catch (e) {
      cartError = e.message
    }
  }

  async function clearCart() {
    if (!confirm('¿Vaciar todo el carrito?')) return
    for (const item of cartItems) {
      try {
        await cardtraderCartRemove(item.product.id, item.quantity)
      } catch {
        // continue removing the rest even if one item fails
      }
    }
    await loadCart()
  }

  function selectTab(t) {
    tab = t
    if (t === 'cart') loadCart()
  }

  onMount(loadStatus)
</script>

<div class="panel">
  <header class="ct-header">
    <div class="titles">
      <div class="title">Cartas Magic</div>
      <div class="subtitle">Mercado CardTrader</div>
    </div>
    <div class="spacer"></div>
    {#if catalogStatus}
      <span class="catalog-label">
        {catalogStatus.blueprints ?? '?'} cartas en catálogo
      </span>
    {/if}
    <button class="sync-btn" onclick={syncCatalog} disabled={syncing}>
      {syncing ? 'Sincronizando…' : 'Sincronizar catálogo'}
    </button>
  </header>

  <div class="tabs">
    <button class:active={tab === 'search'} onclick={() => selectTab('search')}>Buscar</button>
    <button class:active={tab === 'deck'} onclick={() => selectTab('deck')}>Mazo</button>
    <button class:active={tab === 'cart'} onclick={() => selectTab('cart')}>Carrito</button>
  </div>

  {#if tab === 'search'}
    <div class="view">
      <div class="search-row">
        <input
          type="text" placeholder="Nombre de la carta…" bind:value={searchName}
          onkeydown={(e) => e.key === 'Enter' && doSearch()}
        />
        <button class="primary" onclick={doSearch} disabled={searching}>{searching ? 'Buscando…' : 'Buscar'}</button>
      </div>

      <div class="filters-row">
        <label class="toggle-row"><input type="checkbox" bind:checked={zeroOnly} /> Solo CT Zero</label>
        <label>Condición mínima
          <select bind:value={condition}>{#each CONDITIONS as c}<option value={c}>{c}</option>{/each}</select>
        </label>
        <label>Idioma
          <select bind:value={language}>{#each LANGUAGES as l}<option value={l}>{l}</option>{/each}</select>
        </label>
        <label>Foil
          <select bind:value={foil}>
            <option>Indiferente</option><option>Solo foil</option><option>Solo normal</option>
          </select>
        </label>
        <label class="toggle-row"><input type="checkbox" bind:checked={allVersions} /> Comparar todas las versiones</label>
        <label class="toggle-row"><input type="checkbox" bind:checked={fastMode} /> Modo rápido</label>
      </div>

      {#if searchError}<div class="error">{searchError}</div>{/if}

      <div class="results">
        {#if searching}
          <div class="empty">Buscando…</div>
        {:else if searchResult?.kind === 'not_found'}
          <div class="empty">No se encontró "{searchResult.data}" en el catálogo.</div>
        {:else if searchResult?.kind === 'offer'}
          {#if !searchResult.data}
            <div class="empty">Sin ofertas disponibles con esos filtros.</div>
          {:else}
            {#each searchResult.data.offers_used as [offer, qty]}
              <div class="offer-row">
                <div class="offer-main">
                  <div class="offer-name">{offer.name} <span class="offer-exp">{offer.expansion_code}</span></div>
                  <div class="offer-meta">{offer.condition} · {offer.language}{offer.foil ? ' · Foil' : ''} · {offer.seller_username}</div>
                </div>
                <div class="offer-qty">×{qty}</div>
                <div class="offer-price">{fmtPrice(offer.price_cents, offer.price_currency)}</div>
              </div>
            {/each}
            {#if searchResult.data.warning}<div class="warning">{searchResult.data.warning}</div>{/if}
          {/if}
        {:else if searchResult?.kind === 'versions'}
          {#each searchResult.data as v}
            <div class="offer-row">
              <div class="offer-main">
                <div class="offer-name">{v.expansion_name} <span class="offer-exp">{v.expansion_code}</span></div>
                <div class="offer-meta">{v.offers_count} oferta(s)</div>
              </div>
              {#if v.best_offer}
                <div class="offer-price">{fmtPrice(v.best_offer.price_cents, v.best_offer.price_currency)}</div>
              {:else}
                <div class="offer-price dim">Sin ofertas</div>
              {/if}
            </div>
          {/each}
        {:else}
          <div class="empty">Busca una carta para empezar.</div>
        {/if}
      </div>
    </div>
  {:else if tab === 'deck'}
    <div class="view">
      <textarea class="deck-input" bind:value={deckText} rows="6" placeholder="Pega tu decklist aquí (formato Moxfield)…"></textarea>

      <div class="filters-row">
        <label class="toggle-row"><input type="checkbox" bind:checked={deckZeroOnly} /> Solo CT Zero</label>
        <label>Condición mínima
          <select bind:value={deckCondition}>{#each CONDITIONS as c}<option value={c}>{c}</option>{/each}</select>
        </label>
        <label>Idioma
          <select bind:value={deckLanguage}>{#each LANGUAGES as l}<option value={l}>{l}</option>{/each}</select>
        </label>
        <label class="toggle-row"><input type="checkbox" bind:checked={respectPrintings} /> Respetar edición exacta</label>
      </div>

      <div class="deck-actions">
        <button class="primary" onclick={quoteDeck} disabled={quoting}>{quoting ? 'Cotizando…' : 'Cotizar mazo'}</button>
        <button onclick={addDeckToCart} disabled={!deckQuote || addingToCart}>
          {addingToCart ? 'Añadiendo…' : 'Añadir mazo al carrito'}
        </button>
      </div>

      {#if deckError}<div class="error">{deckError}</div>{/if}
      {#if addCartMsg}<div class="hint">{addCartMsg}</div>{/if}

      {#if deckQuote}
        {#if deckQuote.not_found?.length}
          <div class="warning">No encontradas: {deckQuote.not_found.join(', ')}</div>
        {/if}
        <div class="deck-table">
          <div class="deck-table-head">
            <span>Carta</span><span>Cant.</span><span>Mejor precio</span><span>Edición</span>
          </div>
          <div class="deck-table-body">
            {#each deckQuote.items as item}
              <div class="deck-row">
                <span>{item.entry.name}</span>
                <span>{item.entry.qty}</span>
                <span>
                  {#if item.result}
                    {fmtPrice(
                      Object.values(item.result.total_by_currency || {})[0],
                      Object.keys(item.result.total_by_currency || {})[0]
                    )}
                  {:else}
                    {item.reason || 'Sin oferta'}
                  {/if}
                </span>
                <span>{item.result?.offers_used?.[0]?.[0]?.expansion_code || ''}</span>
              </div>
            {/each}
          </div>
        </div>
        <div class="deck-total">
          Total: {#each Object.entries(deckQuote.total_by_currency || {}) as [cur, cents], i}{i > 0 ? ' + ' : ''}{fmtPrice(cents, cur)}{/each}
        </div>
      {/if}
    </div>
  {:else}
    <div class="view">
      <div class="cart-toolbar">
        <button onclick={loadCart} disabled={cartLoading}>Actualizar</button>
        <button class="danger" onclick={clearCart} disabled={!cartItems.length}>Vaciar carrito</button>
      </div>

      {#if cartError}<div class="error">{cartError}</div>{/if}

      <div class="results">
        {#if cartLoading}
          <div class="empty">Cargando…</div>
        {:else if !cartItems.length}
          <div class="empty">El carrito está vacío.</div>
        {/if}
        {#each cartItems as item}
          <div class="cart-row">
            <div class="offer-main">
              <div class="offer-name">{item.product?.name_en || item.product?.name || 'Carta'}</div>
              <div class="offer-meta">×{item.quantity}</div>
            </div>
            <div class="offer-price">{fmtPrice(item.price_cents, item.price_currency)}</div>
            <button class="remove-btn" onclick={() => removeCartItem(item.product?.id, item.quantity)} title="Quitar">✕</button>
          </div>
        {/each}
      </div>

      {#if cart}
        <div class="cart-totals">
          {#if cart.subtotal != null}<div>Subtotal <span>{fmtMoney(cart.subtotal)}</span></div>{/if}
          {#if cart.shipping_cost != null}<div>Envío <span>{fmtMoney(cart.shipping_cost)}</span></div>{/if}
          {#if cart.ct_zero_fee_amount != null}<div>Tasa CT Zero <span>{fmtMoney(cart.ct_zero_fee_amount)}</span></div>{/if}
          {#if cart.total != null}<div class="grand-total">Total <span>{fmtMoney(cart.total)}</span></div>{/if}
        </div>
      {/if}
      <div class="note">La compra final se confirma en la web o app de CardTrader.</div>
    </div>
  {/if}
</div>

<style>
  .panel { height: 100%; min-height: 0; display: flex; flex-direction: column; padding: 16px; gap: 10px; }

  .ct-header { display: flex; align-items: center; gap: 10px; flex-shrink: 0; }
  .titles { display: flex; flex-direction: column; }
  .title { font-size: 16px; font-weight: 700; color: var(--text); }
  .subtitle { font-size: 11px; color: var(--text-med); }
  .spacer { flex: 1; }
  .catalog-label { font-size: 11px; color: var(--text-med); }

  .sync-btn {
    background: var(--panel-2);
    border: 1px solid var(--border-a);
    border-radius: var(--radius-sm);
    color: var(--text-dim);
    padding: 6px 12px;
    font-size: 12px;
    cursor: pointer;
  }

  .tabs { display: flex; gap: 6px; flex-shrink: 0; }
  .tabs button {
    background: transparent;
    border: 1px solid var(--border-a);
    border-radius: 999px;
    color: var(--text-med);
    padding: 5px 16px;
    font-size: 12px;
    cursor: pointer;
  }
  .tabs button.active { background: var(--pri-gho); border-color: var(--pri-dim); color: var(--pri); }

  .view { flex: 1; min-height: 0; display: flex; flex-direction: column; gap: 10px; overflow-y: auto; }

  .search-row { display: flex; gap: 8px; flex-shrink: 0; }
  .search-row input {
    flex: 1; min-width: 0;
    background: var(--panel-2);
    border: 1px solid var(--border-a);
    border-radius: var(--radius-sm);
    color: var(--text);
    padding: 8px 12px;
    font-size: 14px;
  }

  .primary {
    background: var(--pri-gho);
    border: 1px solid var(--pri-dim);
    border-radius: var(--radius-sm);
    color: var(--pri);
    padding: 8px 16px;
    font-size: 13px;
    cursor: pointer;
  }

  .filters-row {
    display: flex;
    flex-wrap: wrap;
    gap: 12px;
    align-items: flex-end;
    flex-shrink: 0;
    font-size: 11px;
    color: var(--text-med);
  }

  .filters-row label { display: flex; flex-direction: column; gap: 3px; }
  .filters-row select {
    background: var(--panel-2);
    border: 1px solid var(--border-a);
    border-radius: var(--radius-sm);
    color: var(--text);
    padding: 5px 8px;
    font-size: 12px;
  }

  .toggle-row { flex-direction: row !important; align-items: center; gap: 5px !important; }

  .error { color: var(--red); font-size: 12px; }
  .warning { color: var(--acc2); font-size: 12px; }
  .hint { color: var(--pri); font-size: 12px; }

  .results {
    flex: 1;
    min-height: 0;
    overflow-y: auto;
    display: flex;
    flex-direction: column;
    gap: 4px;
  }

  .empty { color: var(--text-med); font-size: 12px; padding: 20px; text-align: center; }

  .offer-row, .cart-row {
    display: flex;
    align-items: center;
    gap: 10px;
    background: var(--panel);
    border: 1px solid var(--border-a);
    border-radius: var(--radius-sm);
    padding: 8px 12px;
  }

  .offer-main { flex: 1; min-width: 0; }
  .offer-name { font-size: 13px; color: var(--text); }
  .offer-exp { font-size: 10px; color: var(--text-med); }
  .offer-meta { font-size: 11px; color: var(--text-med); }
  .offer-qty { font-size: 12px; color: var(--text-med); }
  .offer-price { font-size: 13px; font-weight: 600; color: var(--pri); }
  .offer-price.dim { color: var(--text-med); font-weight: 400; }

  .remove-btn { background: none; border: none; color: var(--text-med); cursor: pointer; }
  .remove-btn:hover { color: var(--red); }

  .deck-input {
    flex-shrink: 0;
    background: var(--panel-2);
    border: 1px solid var(--border-a);
    border-radius: var(--radius-sm);
    color: var(--text);
    padding: 10px;
    font-size: 12px;
    font-family: var(--font-mono);
    resize: vertical;
  }

  .deck-actions { display: flex; gap: 8px; flex-shrink: 0; }
  .deck-actions button:not(.primary) {
    background: var(--panel-2);
    border: 1px solid var(--border-a);
    border-radius: var(--radius-sm);
    color: var(--text-dim);
    padding: 8px 16px;
    font-size: 12px;
    cursor: pointer;
  }

  .deck-table { border: 1px solid var(--border-a); border-radius: var(--radius-sm); overflow: hidden; }
  .deck-table-head, .deck-row {
    display: grid;
    grid-template-columns: 2fr 0.5fr 1fr 0.8fr;
    gap: 8px;
    padding: 6px 10px;
    font-size: 11px;
  }
  .deck-table-head { color: var(--text-med); background: var(--panel-2); }
  .deck-row { color: var(--text-dim); border-top: 1px solid var(--border-a); }
  .deck-total { font-size: 13px; font-weight: 600; color: var(--pri); text-align: right; }

  .cart-toolbar { display: flex; gap: 8px; flex-shrink: 0; }
  .cart-toolbar button {
    background: var(--panel-2);
    border: 1px solid var(--border-a);
    border-radius: var(--radius-sm);
    color: var(--text-dim);
    padding: 6px 14px;
    font-size: 12px;
    cursor: pointer;
  }
  .cart-toolbar button.danger:hover:not(:disabled) { color: var(--red); border-color: rgba(255,94,130,0.3); }
  .cart-toolbar button:disabled { opacity: 0.4; cursor: not-allowed; }

  .cart-totals {
    flex-shrink: 0;
    border-top: 1px solid var(--border-a);
    padding-top: 8px;
    font-size: 12px;
    color: var(--text-med);
    display: flex;
    flex-direction: column;
    gap: 3px;
  }
  .cart-totals div { display: flex; justify-content: space-between; }
  .cart-totals .grand-total { color: var(--text); font-weight: 700; font-size: 13px; }

  .note { flex-shrink: 0; font-size: 10px; color: var(--text-med); text-align: center; }
</style>
