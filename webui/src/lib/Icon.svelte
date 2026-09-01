<script>
  // Small hand-authored icon set (24x24, stroke-based, currentColor) so the
  // nav rail/transport controls don't rely on emoji or external icon fonts —
  // keeps the bundle dependency-free and matches the HUD line-art look of
  // installer/jarvis-icon.ico.
  let { name = '', size = 20 } = $props()
</script>

{#if name === 'home'}
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
    <path d="M4 11 12 4l8 7" />
    <path d="M6 10v10h12V10" />
    <rect x="10" y="14" width="4" height="6" />
  </svg>
{:else if name === 'whatsapp'}
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
    <rect x="3" y="4" width="18" height="14" rx="4" ry="4" />
    <path d="M8 18 7 21l5-3" />
  </svg>
{:else if name === 'music'}
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
    <path d="M10 6 19 4" />
    <path d="M10 6v11" />
    <path d="M19 4v11" />
    <ellipse cx="8" cy="17.5" rx="2" ry="1.5" />
    <ellipse cx="17" cy="15.5" rx="2" ry="1.5" />
  </svg>
{:else if name === 'gmail'}
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
    <rect x="3" y="5" width="18" height="14" rx="2" />
    <path d="M4 7 12 13 20 7" />
  </svg>
{:else if name === 'youtube'}
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
    <path d="M2 8a4 4 0 0 1 4 -4h12a4 4 0 0 1 4 4v8a4 4 0 0 1 -4 4h-12a4 4 0 0 1 -4 -4v-8z" />
    <path d="M10 9l5 3l-5 3z" />
  </svg>
{:else if name === 'calendar'}
  <svg width={size} height={size} viewBox="0 0 448 512" fill="currentColor">
    <path d="M120 0c13.3 0 24 10.7 24 24l0 40 160 0 0-40c0-13.3 10.7-24 24-24s24 10.7 24 24l0 40 32 0c35.3 0 64 28.7 64 64l0 288c0 35.3-28.7 64-64 64L64 480c-35.3 0-64-28.7-64-64L0 128C0 92.7 28.7 64 64 64l32 0 0-40c0-13.3 10.7-24 24-24zM384 432c8.8 0 16-7.2 16-16l0-64-88 0 0 80 72 0zm16-128l0-80-88 0 0 80 88 0zm-136 0l0-80-80 0 0 80 80 0zm-128 0l0-80-88 0 0 80 88 0zM48 352l0 64c0 8.8 7.2 16 16 16l72 0 0-80-88 0zm136 0l0 80 80 0 0-80-80 0zM120 112l-56 0c-8.8 0-16 7.2-16 16l0 48 352 0 0-48c0-8.8-7.2-16-16-16l-264 0z" />
  </svg>
{:else if name === 'drive'}
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
    <path d="M12 10l-6 10l-3 -5l6 -10z" />
    <path d="M9 15h12l-3 5h-12" />
    <path d="M15 15l-6 -10h6l6 10z" />
  </svg>
{:else if name === 'games'}
  <svg width={size} height={size} viewBox="0 0 512 512" fill="none" stroke="currentColor" stroke-width="1.4">
    <path d="M467.51,248.83c-18.4-83.18-45.69-136.24-89.43-149.17A91.5,91.5,0,0,0,352,96c-26.89,0-48.11,16-96,16s-69.15-16-96-16a99.09,99.09,0,0,0-27.2,3.66C89,112.59,61.94,165.7,43.33,248.83c-19,84.91-15.56,152,21.58,164.88c26,9,49.25-9.61,71.27-37c25-31.2,55.79-40.8,119.82-40.8s93.62,9.6,118.66,40.8c22,27.41,46.11,45.79,71.42,37.16C487.1,399.86,486.52,334.74,467.51,248.83Z" stroke-miterlimit="10" stroke-width="34" />
    <circle cx="292" cy="224" r="20" fill="currentColor" stroke="none" />
    <path d="M336,288a20,20,0,1,1,20-19.95A20,20,0,0,1,336,288Z" fill="currentColor" stroke="none" />
    <circle cx="336" cy="180" r="20" fill="currentColor" stroke="none" />
    <circle cx="380" cy="224" r="20" fill="currentColor" stroke="none" />
    <line x1="160" y1="176" x2="160" y2="272" stroke-linecap="round" stroke-linejoin="round" stroke-width="34" />
    <line x1="208" y1="224" x2="112" y2="224" stroke-linecap="round" stroke-linejoin="round" stroke-width="34" />
  </svg>
{:else if name === 'cardtrader'}
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
    <rect x="3" y="6" width="12" height="15" rx="2" transform="rotate(-8 9 13.5)" />
    <rect x="9" y="4" width="12" height="15" rx="2" />
  </svg>
{:else if name === 'movies'}
  <svg width={size} height={size} viewBox="0 0 16 16" fill="currentColor">
    <path d="M6 3a3 3 0 1 1-6 0 3 3 0 0 1 6 0M1 3a2 2 0 1 0 4 0 2 2 0 0 0-4 0" />
    <path d="M9 6h.5a2 2 0 0 1 1.983 1.738l3.11-1.382A1 1 0 0 1 16 7.269v7.462a1 1 0 0 1-1.406.913l-3.111-1.382A2 2 0 0 1 9.5 16H2a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2zm6 8.73V7.27l-3.5 1.555v4.35zM1 8v6a1 1 0 0 0 1 1h7.5a1 1 0 0 0 1-1V8a1 1 0 0 0-1-1H2a1 1 0 0 0-1 1" />
    <path d="M9 6a3 3 0 1 0 0-6 3 3 0 0 0 0 6M7 3a2 2 0 1 1 4 0 2 2 0 0 1-4 0" />
  </svg>
{:else if name === 'anime'}
  <svg width={size} height={size} viewBox="0 0 692 712" fill="currentColor">
    <path d="M 456 201 L 450 197 L 434 192 L 418 192 L 402 195 L 383 202 L 280 251 L 237 269 L 234 268 L 235 257 L 250 183 L 252 165 L 205 217 L 130 305 L 82 368 L 59 404 L 51 420 L 45 439 L 46 451 L 51 457 L 56 459 L 69 458 L 79 455 L 114 440 L 252 369 L 285 356 L 299 353 L 314 356 L 320 361 L 323 370 L 323 377 L 299 528 L 290 530 L 67 534 L 36 592 L 17 640 L 12 665 L 12 679 L 15 691 L 20 698 L 38 699 L 60 695 L 317 633 L 372 623 L 381 619 L 393 608 L 408 584 L 414 565 L 419 445 L 425 391 L 434 341 L 451 282 L 467 244 L 468 229 L 466 217 L 463 209 Z M 613 35 L 550 19 L 492 12 L 451 47 L 416 84 L 393 113 L 379 134 L 368 157 L 445 178 L 487 135 L 554 74 L 595 43 Z M 679 93 L 651 73 L 635 65 L 621 62 L 580 90 L 522 136 L 481 175 L 478 182 L 500 205 L 519 220 L 532 227 L 539 227 L 575 205 L 620 168 L 661 124 L 674 105 Z" />
  </svg>
{:else if name === 'tv'}
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
    <rect x="3" y="5" width="18" height="13" rx="2" />
    <path d="M8 18h8" />
    <path d="M12 18v3" />
    <path d="M7 9 10 12 7 15" />
    <circle cx="14.5" cy="11.5" r="1.5" fill="currentColor" stroke="none" />
    <circle cx="13.75" cy="14.75" r="0.75" fill="currentColor" stroke="none" />
  </svg>
{:else if name === 'settings'}
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
    <path d="M11.42 15.17 17.25 21A2.652 2.652 0 0 0 21 17.25l-5.877-5.877M11.42 15.17l2.496-3.03c.317-.384.74-.626 1.208-.766M11.42 15.17l-4.655 5.653a2.548 2.548 0 1 1-3.586-3.586l6.837-5.63m5.108-.233c.55-.164 1.163-.188 1.743-.14a4.5 4.5 0 0 0 4.486-6.336l-3.276 3.277a3.004 3.004 0 0 1-2.25-2.25l3.276-3.276a4.5 4.5 0 0 0-6.336 4.486c.091 1.076-.071 2.264-.904 2.95l-.102.085m-1.745 1.437L5.909 7.5H4.5L2.25 3.75l1.5-1.5L7.5 4.5v1.409l4.26 4.26m-1.745 1.437 1.745-1.437m6.615 8.206L15.75 15.75M4.867 19.125h.008v.008h-.008v-.008Z" />
  </svg>
{:else if name === 'play'}
  <svg width={size} height={size} viewBox="0 0 24 24" fill="currentColor"><path d="M8 5v14l11-7Z" /></svg>
{:else if name === 'pause'}
  <svg width={size} height={size} viewBox="0 0 24 24" fill="currentColor"><rect x="6" y="5" width="4" height="14" rx="1" /><rect x="14" y="5" width="4" height="14" rx="1" /></svg>
{:else if name === 'next'}
  <svg width={size} height={size} viewBox="0 0 24 24" fill="currentColor"><path d="M6 5v14l9-7Z" /><rect x="16" y="5" width="2.5" height="14" rx="1" /></svg>
{:else if name === 'previous'}
  <svg width={size} height={size} viewBox="0 0 24 24" fill="currentColor"><path d="M18 5v14L9 12Z" /><rect x="5.5" y="5" width="2.5" height="14" rx="1" /></svg>
{:else if name === 'heart'}
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round">
    <path d="M12 20 4.5 12.6a4.6 4.6 0 0 1 6.9-6.1L12 7l.6-.5a4.6 4.6 0 0 1 6.9 6.1Z" />
  </svg>
{:else if name === 'heart-filled'}
  <svg width={size} height={size} viewBox="0 0 24 24" fill="currentColor">
    <path d="M12 20 4.5 12.6a4.6 4.6 0 0 1 6.9-6.1L12 7l.6-.5a4.6 4.6 0 0 1 6.9 6.1Z" />
  </svg>
{:else if name === 'send'}
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
    <path d="M4 12 20 4l-6 16-3-7-7-1Z" />
  </svg>
{:else if name === 'volume'}
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
    <path d="M4 9.5h3L11 6v12l-4-3.5H4Z" />
    <path d="M15.5 9.5a3.5 3.5 0 0 1 0 5M18 7a7 7 0 0 1 0 10" />
  </svg>
{:else if name === 'volume-muted'}
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
    <path d="M4 9.5h3L11 6v12l-4-3.5H4Z" />
    <path d="m15.5 10 4 4M19.5 10l-4 4" />
  </svg>
{:else if name === 'karaoke'}
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
    <rect x="9.5" y="3" width="5" height="10" rx="2.5" />
    <path d="M6 11a6 6 0 0 0 12 0M12 17v4M9 21h6" />
  </svg>
{:else if name === 'search'}
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
    <circle cx="11" cy="11" r="6.5" />
    <path d="m20 20-4.3-4.3" />
  </svg>
{:else if name === 'mic'}
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
    <rect x="9" y="3" width="6" height="11" rx="3" />
    <path d="M6 11a6 6 0 0 0 12 0" />
    <path d="M12 17v3M9 21h6" />
  </svg>
{:else if name === 'stop'}
  <svg width={size} height={size} viewBox="0 0 24 24" fill="currentColor"><rect x="6" y="6" width="12" height="12" rx="2" /></svg>
{:else if name === 'attach'}
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
    <g transform="rotate(45 12 12)">
      <rect x="9" y="2.5" width="6" height="19" rx="3" />
      <line x1="12" y1="6.5" x2="12" y2="16" />
    </g>
  </svg>
{:else if name === 'reply'}
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
    <path d="M10 8 4 13l6 5" />
    <path d="M4 13h9a6 6 0 0 1 6 6v1" />
  </svg>
{:else if name === 'edit'}
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
    <path d="M5 19l3-5 8-8 3 3-8 8-3 5-3 -3Z" />
    <path d="m14 8 3 3" />
  </svg>
{:else if name === 'refresh'}
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
    <path d="M4 12a8 8 0 0 1 13.66-5.66M20 12a8 8 0 0 1-13.66 5.66" />
    <path d="M17 4v3.5h-3.5M7 20v-3.5h3.5" />
  </svg>
{:else if name === 'copy'}
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
    <rect x="9" y="9" width="11" height="11" rx="2" />
    <path d="M5 15V5a2 2 0 0 1 2-2h10" />
  </svg>
{:else if name === 'trash'}
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
    <rect x="7" y="7" width="10" height="13" rx="1" />
    <path d="M5 7h14M9 4h6M10 10v7M14 10v7" />
  </svg>
{:else if name === 'up'}
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
    <path d="M6 15l6-6 6 6" />
  </svg>
{:else if name === 'down'}
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
    <path d="M6 9l6 6 6-6" />
  </svg>
{:else if name === 'eye'}
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
    <path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7-10-7-10-7Z" />
    <circle cx="12" cy="12" r="3" />
  </svg>
{:else if name === 'close'}
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
    <path d="M6 6l12 12M18 6 6 18" />
  </svg>
{:else if name === 'check'}
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
    <path d="M5 12.5 9.5 17 19 7" />
  </svg>
{/if}
