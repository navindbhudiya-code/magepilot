# Hyvä + Alpine.js — correct APIs (no jQuery / Knockout)

## Events between components — native CustomEvents, NOT "Alpine.on"
There is **no `Alpine.on()`**. Alpine reacts to DOM/window events. To communicate between components:
- **Dispatch:** `$dispatch('my-event', { id: 1 })` (Alpine magic, bubbles) or
  `window.dispatchEvent(new CustomEvent('my-event', { detail: { id: 1 } }))`.
- **Listen in markup:** `@my-event.window="handler($event.detail)"`:
  ```html
  <div x-data="{ open: false }" @open-modal.window="open = true">…</div>
  ```
- **Listen in `init()`:** `window.addEventListener('open-modal', () => this.open = true)` — NOT `Alpine.on`.

## x-data lifecycle and state
- `init()` is the Alpine lifecycle hook (runs once when the component initialises).
- **Declare every reactive property in `x-data`** (e.g. `product`, `loading`) before binding it with
  `x-text`/`x-show`/`x-model`, or it is undefined.
- Reusable components (CSP-friendly): register with `Alpine.data('name', () => ({ ... }))` inside an
  `alpine:init` listener, then `x-data="name()"`.
- Shared cross-component state: `Alpine.store('name', { ... })`, read via `$store.name`.

## Plugins bundled in Hyvä (use these, not custom JS)
- `x-cloak` — hide until Alpine initialises (add the CSS rule `[x-cloak]{display:none}`).
- `x-transition` — enter/leave transitions.
- `x-teleport="body"` — move an element (e.g. a modal) out of overflow/stacking contexts.
- `x-collapse` — animate height (accordions).
- `x-trap` (focus plugin) — trap focus / lock scroll, e.g. `x-trap.noscroll="open"` for modals.
- `x-intersect` — run when scrolled into view (lazy-load).
- `$persist` — sync a property to localStorage.

A modal pattern: `x-data="{ open:false }"`, `x-teleport="body"`, `x-show="open" x-cloak`,
`x-trap.noscroll="open"`, a backdrop that closes on `@click="open=false"`. Open it from elsewhere with
`window.dispatchEvent(new CustomEvent('open-modal'))` and `@open-modal.window="open=true"`.

## Escaping in templates (Hyvä uses the same `$escaper`)
- `escapeHtml($v)` — text content (between tags). **Button label text uses this**, not `escapeHtmlAttr`.
- `escapeHtmlAttr($v)` — values inside an attribute (`title="…"`, `data-…`).
- `escapeUrl($v)` — `href`/`src` (also strips `javascript:`/`data:`).
- `escapeJs($v)` — string literals inside a `<script>`.
Mark already-safe HTML (`$block->getChildHtml(...)`, `json_encode` output) with `@noEscape`.
