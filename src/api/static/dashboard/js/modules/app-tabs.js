/**
 * app-tabs.js — Điều hướng 5 tab chính, đồng bộ với URL hash.
 * Owner: dashboard shell
 *
 * Tab: #board (mặc định) · #derivatives · #paper · #thesis · #context
 * - Nút tab: role="tab", aria-selected, roving tabindex, phím ←/→/↑/↓/Home/End.
 *   Desktop: menu dọc bên trái · mobile: thanh tab ngang (aria-orientation đồng bộ theo màn hình).
 * - Hash cũ (#cluster-a..e) và hash trỏ vào phần tử bên trong tab vẫn mở đúng tab.
 * - Phần tử ở tab ẩn được "reveal" trước khi cuộn: [data-scroll-to] (capture),
 *   sự kiện navigate:thesis, và CustomEvent('tab:reveal', { detail: { tab | id } }).
 *
 * Exports:
 *   initAppTabs({ onActivate }) — onActivate(tabId, { first }) gọi mỗi lần tab hiện
 *   activateTab(tabId, opts)
 *   getActiveTab()
 */

const TABS = ['board', 'derivatives', 'paper', 'thesis', 'context'];
const DEFAULT_TAB = 'board';
const LEGACY = Object.freeze({
  'cluster-a': 'board',
  'cluster-b': 'thesis',
  'cluster-c': 'thesis',
  'cluster-d': 'thesis',
  'cluster-e': 'context',
});

let _active = null;
let _onActivate = null;
const _seen = new Set();

const _btn = (id) => document.getElementById(`tabbtn-${id}`);
const _panel = (id) => document.getElementById(`tab-${id}`);

function _tabFromHash(hash) {
  const h = String(hash || '').replace(/^#/, '');
  if (!h) return null;
  if (TABS.includes(h)) return h;
  if (LEGACY[h]) return LEGACY[h];
  let target = null;
  try { target = document.getElementById(decodeURIComponent(h)); } catch { target = null; }
  return target ? _tabOfElement(target) : null;
}

function _tabOfElement(node) {
  const panel = node?.closest?.('[data-app-tab]');
  return panel ? panel.dataset.appTab : null;
}

/**
 * @param {string} id
 * @param {{ focus?: boolean, push?: boolean, replace?: boolean }} [opts]
 */
export function activateTab(id, opts = {}) {
  if (!TABS.includes(id)) id = DEFAULT_TAB;
  const changed = id !== _active;
  _active = id;

  TABS.forEach((t) => {
    const b = _btn(t);
    const p = _panel(t);
    const on = t === id;
    if (b) {
      b.setAttribute('aria-selected', on ? 'true' : 'false');
      b.tabIndex = on ? 0 : -1;
      b.classList.toggle('active', on);
    }
    if (p) p.hidden = !on;
  });

  if (opts.focus) _btn(id)?.focus();

  const want = `#${id}`;
  if (opts.push && location.hash !== want) {
    history.pushState(null, '', want);
  } else if (opts.replace && location.hash !== want) {
    history.replaceState(null, '', want);
  }

  if (changed) {
    const first = !_seen.has(id);
    _seen.add(id);
    try {
      _onActivate?.(id, { first });
    } catch (err) {
      console.error('[tabs] onActivate failed:', err);
    }
    document.dispatchEvent(new CustomEvent('tab:changed', { detail: { tab: id, first } }));
  }
}

export function getActiveTab() {
  return _active;
}

/** Hiện tab chứa phần tử `id` (không đổi hash nếu đã đúng tab). */
function _revealElementId(id) {
  const node = id ? document.getElementById(id) : null;
  const tab = node ? _tabOfElement(node) : null;
  if (tab && tab !== _active) activateTab(tab, { push: true });
}

function _syncFromLocation() {
  const tab = _tabFromHash(location.hash) ?? DEFAULT_TAB;
  activateTab(tab);
  const h = location.hash.replace(/^#/, '');
  // Hash trỏ vào phần tử bên trong tab → cuộn tới sau khi tab hiện
  if (h && !TABS.includes(h)) {
    const node = document.getElementById(h);
    if (node) requestAnimationFrame(() => node.scrollIntoView({ block: 'start' }));
  }
}

export function initAppTabs({ onActivate } = {}) {
  _onActivate = onActivate ?? null;
  const list = document.getElementById('appTabs');
  if (!list) return;

  list.addEventListener('click', (e) => {
    const b = e.target.closest('[role="tab"][data-tab-target]');
    if (!b) return;
    activateTab(b.dataset.tabTarget, { push: true });
    window.scrollTo({ top: 0 });
  });

  list.addEventListener('keydown', (e) => {
    const b = e.target.closest('[role="tab"][data-tab-target]');
    if (!b) return;
    const i = TABS.indexOf(b.dataset.tabTarget);
    let next = null;
    if (e.key === 'ArrowRight' || e.key === 'ArrowDown') next = TABS[(i + 1) % TABS.length];
    else if (e.key === 'ArrowLeft' || e.key === 'ArrowUp') next = TABS[(i - 1 + TABS.length) % TABS.length];
    else if (e.key === 'Home') next = TABS[0];
    else if (e.key === 'End') next = TABS[TABS.length - 1];
    if (!next) return;
    e.preventDefault();
    activateTab(next, { focus: true, push: true });
  });

  // Menu dọc (desktop) / thanh ngang (≤ 900px) — báo đúng hướng cho trình đọc màn hình.
  const mq = window.matchMedia('(max-width: 900px)');
  const syncOrientation = () => list.setAttribute('aria-orientation', mq.matches ? 'horizontal' : 'vertical');
  syncOrientation();
  mq.addEventListener?.('change', syncOrientation);

  window.addEventListener('popstate', _syncFromLocation);
  window.addEventListener('hashchange', _syncFromLocation);

  // Nút "cuộn tới" trong các module cũ: mở đúng tab trước khi handler của chúng cuộn.
  document.addEventListener('click', (e) => {
    const s = e.target.closest?.('[data-scroll-to]');
    if (s) _revealElementId(s.dataset.scrollTo);
    const a = e.target.closest?.('a[href^="#"]');
    if (a && !a.closest('#appTabs')) {
      const h = a.getAttribute('href').slice(1);
      if (h && !TABS.includes(h) && !LEGACY[h]) _revealElementId(h);
    }
  }, true);

  document.addEventListener('navigate:thesis', () => {
    if (_active !== 'thesis') activateTab('thesis', { push: true });
  });

  document.addEventListener('tab:reveal', (e) => {
    const { tab, id } = e.detail ?? {};
    if (tab && TABS.includes(tab)) {
      if (tab !== _active) activateTab(tab, { push: true });
    } else if (id) {
      _revealElementId(id);
    }
  });

  _syncFromLocation();
}
