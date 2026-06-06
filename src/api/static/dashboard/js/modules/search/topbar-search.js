/**
 * topbar-search.js
 * Owner: api/static (UI chrome — no business logic)
 * Responsibility: client-side filter cho thesis table rows và watchlist cards
 *                 dựa trên #topbarSearch input.
 *
 * Design constraints:
 *   - KHÔNG gọi API. KHÔNG đụng vào loader hay state.theses.
 *   - KHÔNG re-render. Chỉ show/hide DOM nodes đã được renderer tạo sẵn.
 *   - Bind SAU khi renderer chạy xong (event-driven qua custom events).
 *   - Safe to call rebind sau mỗi loadDashboard() / loadWatchlist() vì
 *     querySelector lại từ đầu mỗi lần.
 *
 * Search scope per query:
 *   thesis table  → <tr data-ticker> + title text (td:nth-child(3))
 *   watchlist     → <div.wl-card data-ticker> + note text (.wl-note)
 *
 * UX:
 *   - Debounce 180ms (dưới ngưỡng cảm nhận lag ~200ms)
 *   - Highlight match: wrap match trong <mark class="search-hl">
 *   - Result count badge bên phải input
 *   - Clear (×) button hiện khi có text
 *   - Kbd shortcut: '/' focus, Escape clear
 *   - Khi query rỗng: restore toàn bộ (remove hidden, remove marks)
 */

import { debounce } from '../../utils/debounce.js';

// ─── Constants ───────────────────────────────────────────────────────────────

const DEBOUNCE_MS = 180;

// ─── Highlight helpers ────────────────────────────────────────────────────────

/**
 * Wrap các ký tự match trong element text với <mark class="search-hl">.
 * Làm việc trực tiếp trên childNodes để không phá vỡ event listeners.
 *
 * @param {HTMLElement} el
 * @param {string}      query  - raw query (sẽ được escape trước khi dùng)
 */
function highlightNode(el, query) {
  if (!el || !query) return;
  // Restore về text gốc trước (nếu đã mark lần trước)
  clearHighlightNode(el);

  const escaped = query.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const re = new RegExp(`(${escaped})`, 'gi');

  // Chỉ process text nodes trực tiếp (1 cấp) để tránh phá nested elements
  const textNodes = [];
  el.childNodes.forEach(n => {
    if (n.nodeType === Node.TEXT_NODE && n.textContent.trim()) {
      textNodes.push(n);
    }
  });

  textNodes.forEach(textNode => {
    if (!re.test(textNode.textContent)) return;
    re.lastIndex = 0;
    const frag = document.createDocumentFragment();
    let lastIdx = 0;
    let m;
    while ((m = re.exec(textNode.textContent)) !== null) {
      if (m.index > lastIdx) {
        frag.appendChild(document.createTextNode(textNode.textContent.slice(lastIdx, m.index)));
      }
      const mark = document.createElement('mark');
      mark.className = 'search-hl';
      mark.textContent = m[1];
      frag.appendChild(mark);
      lastIdx = re.lastIndex;
    }
    if (lastIdx < textNode.textContent.length) {
      frag.appendChild(document.createTextNode(textNode.textContent.slice(lastIdx)));
    }
    textNode.parentNode.replaceChild(frag, textNode);
  });
}

function clearHighlightNode(el) {
  if (!el) return;
  el.querySelectorAll('mark.search-hl').forEach(mark => {
    mark.replaceWith(document.createTextNode(mark.textContent));
  });
  // Normalize để merge text nodes liền kề
  el.normalize();
}

// ─── Filter: thesis table ─────────────────────────────────────────────────────

/**
 * Filter rows trong #thesesTableWrap table.
 * Mỗi row có: data-ticker, data-thesis-id, và text title tại td:nth-child(3).
 *
 * @param {string} q  - normalized query (lowercase, trimmed)
 * @returns {{ visible: number, total: number }}
 */
function filterThesisTable(q) {
  const wrap = document.getElementById('thesesTableWrap');
  if (!wrap) return { visible: 0, total: 0 };

  const rows = wrap.querySelectorAll('tbody tr[data-ticker]');
  let visible = 0;

  rows.forEach(row => {
    const ticker    = (row.dataset.ticker ?? '').toLowerCase();
    const titleCell = row.querySelector('td:nth-child(3)');
    const title     = (titleCell?.textContent ?? '').toLowerCase();

    const match = !q || ticker.includes(q) || title.includes(q);
    row.classList.toggle('search-hidden', !match);

    if (match) {
      visible++;
      if (q) {
        const tickerCell = row.querySelector('td:first-child strong');
        if (tickerCell) highlightNode(tickerCell, q);
        if (titleCell)  highlightNode(titleCell, q);
      }
    } else {
      // Clear marks trên rows bị ẩn để không leak nếu sau này hiện lại
      const tickerCell = row.querySelector('td:first-child strong');
      if (tickerCell) clearHighlightNode(tickerCell);
      if (titleCell)  clearHighlightNode(titleCell);
    }
  });

  const total = rows.length;

  // Empty state: nếu có query mà không match gì → hiện message
  let noResultEl = wrap.querySelector('.search-no-result--thesis');
  if (!q) {
    noResultEl?.remove();
  } else if (visible === 0) {
    if (!noResultEl) {
      noResultEl = document.createElement('p');
      noResultEl.className = 'empty-state search-no-result--thesis';
      noResultEl.textContent = `Không tìm thấy thesis nào khớp "${q}"`;
      wrap.appendChild(noResultEl);
    } else {
      noResultEl.textContent = `Không tìm thấy thesis nào khớp "${q}"`;
    }
  } else {
    noResultEl?.remove();
  }

  return { visible, total };
}

// ─── Filter: watchlist cards ──────────────────────────────────────────────────

/**
 * Filter cards trong #watchlistSection .wl-grid.
 * Mỗi card có: data-ticker, .wl-ticker text, .wl-note text.
 *
 * @param {string} q
 * @returns {{ visible: number, total: number }}
 */
function filterWatchlist(q) {
  const section = document.getElementById('watchlistSection');
  if (!section) return { visible: 0, total: 0 };

  const cards = section.querySelectorAll('.wl-card[data-ticker]');
  let visible = 0;

  cards.forEach(card => {
    const ticker    = (card.dataset.ticker ?? '').toLowerCase();
    const noteEl    = card.querySelector('.wl-note');
    const noteText  = (noteEl?.textContent ?? '').toLowerCase();
    const tickerEl  = card.querySelector('.wl-ticker');

    const match = !q || ticker.includes(q) || noteText.includes(q);
    card.classList.toggle('search-hidden', !match);

    if (match) {
      visible++;
      if (q) {
        if (tickerEl) highlightNode(tickerEl, q);
        if (noteEl && noteText.includes(q)) highlightNode(noteEl, q);
      }
    } else {
      if (tickerEl) clearHighlightNode(tickerEl);
      if (noteEl)   clearHighlightNode(noteEl);
    }
  });

  const total = cards.length;

  // Empty state
  const grid = section.querySelector('.wl-grid');
  let noResultEl = section.querySelector('.search-no-result--watchlist');
  if (!q) {
    noResultEl?.remove();
  } else if (visible === 0 && grid) {
    if (!noResultEl) {
      noResultEl = document.createElement('p');
      noResultEl.className = 'empty-state search-no-result--watchlist';
      noResultEl.textContent = `Không tìm thấy mã nào khớp "${q}"`;
      grid.insertAdjacentElement('afterend', noResultEl);
    } else {
      noResultEl.textContent = `Không tìm thấy mã nào khớp "${q}"`;
    }
  } else {
    noResultEl?.remove();
  }

  return { visible, total };
}

// ─── Result badge ─────────────────────────────────────────────────────────────

function updateResultBadge(badge, thesisResult, watchlistResult, q) {
  if (!badge) return;
  if (!q) {
    badge.textContent = '';
    badge.classList.add('search-badge--hidden');
    return;
  }

  const total   = thesisResult.visible + watchlistResult.visible;
  const outOf   = thesisResult.total   + watchlistResult.total;

  badge.classList.remove('search-badge--hidden');

  if (total === 0) {
    badge.textContent = '0 kết quả';
    badge.classList.add('search-badge--empty');
  } else {
    badge.textContent = `${total} / ${outOf}`;
    badge.classList.remove('search-badge--empty');
  }
}

// ─── Clear all highlights + show all ─────────────────────────────────────────

function clearAll() {
  // Thesis table
  const thesisWrap = document.getElementById('thesesTableWrap');
  if (thesisWrap) {
    thesisWrap.querySelectorAll('tbody tr.search-hidden').forEach(r => r.classList.remove('search-hidden'));
    thesisWrap.querySelectorAll('mark.search-hl').forEach(m => m.replaceWith(document.createTextNode(m.textContent)));
    thesisWrap.querySelector('.search-no-result--thesis')?.remove();
    thesisWrap.normalize();
  }
  // Watchlist
  const wlSection = document.getElementById('watchlistSection');
  if (wlSection) {
    wlSection.querySelectorAll('.wl-card.search-hidden').forEach(c => c.classList.remove('search-hidden'));
    wlSection.querySelectorAll('mark.search-hl').forEach(m => m.replaceWith(document.createTextNode(m.textContent)));
    wlSection.querySelector('.search-no-result--watchlist')?.remove();
    wlSection.normalize();
  }
}

// ─── Bootstrap: bind tất cả logic vào #topbarSearch ──────────────────────────

let _bound = false;

/**
 * initTopbarSearch()
 * Gọi 1 lần từ app.js sau DOMContentLoaded.
 * Safe to call multiple times (guard _bound).
 */
export function initTopbarSearch() {
  if (_bound) return;
  _bound = true;

  const input = document.getElementById('topbarSearch');
  if (!input) return;

  // ── Inject badge + clear button vào .topbar-search wrapper ──────────────
  const wrapper = input.closest('.topbar-search');
  if (!wrapper) return;

  const badge = document.createElement('span');
  badge.className = 'search-result-badge search-badge--hidden';
  badge.setAttribute('aria-live', 'polite');
  badge.setAttribute('aria-atomic', 'true');
  wrapper.appendChild(badge);

  const clearBtn = document.createElement('button');
  clearBtn.type = 'button';
  clearBtn.className = 'search-clear-btn search-clear-btn--hidden';
  clearBtn.setAttribute('aria-label', 'Xóa tìm kiếm');
  clearBtn.innerHTML = `<svg width="10" height="10" viewBox="0 0 24 24" fill="none"
    stroke="currentColor" stroke-width="2.5" stroke-linecap="round" aria-hidden="true">
    <line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>
  </svg>`;
  wrapper.appendChild(clearBtn);

  // ── Core filter function ──────────────────────────────────────────────────
  const runFilter = debounce(() => {
    const q = input.value.trim().toLowerCase();

    if (!q) {
      clearAll();
      updateResultBadge(badge, { visible: 0, total: 0 }, { visible: 0, total: 0 }, '');
      clearBtn.classList.add('search-clear-btn--hidden');
      return;
    }

    clearBtn.classList.remove('search-clear-btn--hidden');

    const thesisResult    = filterThesisTable(q);
    const watchlistResult = filterWatchlist(q);
    updateResultBadge(badge, thesisResult, watchlistResult, q);

    // Scroll to first visible thesis section nếu có match
    if (thesisResult.visible > 0) {
      const firstRow = document.querySelector('#thesesTableWrap tbody tr[data-ticker]:not(.search-hidden)');
      firstRow?.closest('section, .panel')?.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    }
  }, DEBOUNCE_MS);

  // ── Events ────────────────────────────────────────────────────────────────
  input.addEventListener('input', runFilter);

  clearBtn.addEventListener('click', () => {
    input.value = '';
    clearAll();
    updateResultBadge(badge, { visible: 0, total: 0 }, { visible: 0, total: 0 }, '');
    clearBtn.classList.add('search-clear-btn--hidden');
    input.focus();
  });

  // ── Keyboard shortcuts ────────────────────────────────────────────────────
  document.addEventListener('keydown', (e) => {
    // '/' → focus search (chỉ khi không đang trong input/textarea)
    if (e.key === '/' && !['INPUT', 'TEXTAREA', 'SELECT'].includes(document.activeElement?.tagName)) {
      e.preventDefault();
      input.focus();
      input.select();
    }
    // Escape → clear + blur
    if (e.key === 'Escape' && document.activeElement === input) {
      input.value = '';
      clearAll();
      updateResultBadge(badge, { visible: 0, total: 0 }, { visible: 0, total: 0 }, '');
      clearBtn.classList.add('search-clear-btn--hidden');
      input.blur();
    }
  });
}

/**
 * reapplySearch()
 * Gọi sau mỗi loadDashboard() hoặc loadWatchlist() để re-apply query hiện tại
 * lên DOM mới được render.
 * Nếu query rỗng: no-op.
 */
export function reapplySearch() {
  const input = document.getElementById('topbarSearch');
  if (!input) return;
  const q = input.value.trim().toLowerCase();
  if (!q) return;

  const badge = document.querySelector('.search-result-badge');
  const thesisResult    = filterThesisTable(q);
  const watchlistResult = filterWatchlist(q);
  updateResultBadge(badge, thesisResult, watchlistResult, q);
}
