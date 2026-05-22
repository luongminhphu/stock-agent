/**
 * watchlist-loader.js
 * Owner: modules/watchlist
 * Responsibility: fetch /watchlist list + enrich mỗi item với market quote
 *                 → delegate render sang watchlist-renderer.js
 * Rule: KHÔNG chứa DOM manipulation trực tiếp (ngoài error fallback).
 *       KHÔNG chứa business logic. Chỉ fetch → enrich → render.
 */

import { el, showToast }         from '../../utils/dom.js';
import { getJson, sendJson }     from '../../api/client.js';
import { renderWatchlist }       from './watchlist-renderer.js';

const WATCHLIST_BASE = '/api/v1/watchlist';
const MARKET_BASE    = '/api/v1/market';

/**
 * Module-scope cache: populated after a manual scan, cleared on page reload.
 * Shape: { [ticker]: SignalReport[] }
 */
let _signalsMap = {};

/**
 * Fetch toàn bộ watchlist của current user,
 * enrich mỗi item với quote từ market segment,
 * sau đó render.
 */
export async function loadWatchlist() {
  const wrap = el('watchlistSection');
  if (!wrap) return;

  wrap.innerHTML = '<p class="muted" style="padding:16px">Đang tải watchlist…</p>';

  try {
    const data = await getJson(WATCHLIST_BASE);
    const items = data?.items ?? [];

    // Enrich song song — Promise.allSettled để 1 ticker lỗi không block cả list
    const enriched = await Promise.allSettled(
      items.map(async (item) => {
        try {
          const quote = await getJson(`${MARKET_BASE}/quote/${item.ticker}`);
          return { ...item, quote };
        } catch {
          return { ...item, quote: null };
        }
      })
    );

    const resolved = enriched.map(r =>
      r.status === 'fulfilled' ? r.value : { ...(r.reason ?? {}), quote: null }
    );

    renderWatchlist(wrap, resolved, {
      onRemove:   handleRemove,
      onScan:     handleScan,
      onAdd:      handleAddTicker,
      onEditNote: handleEditNote,
      signalsMap: _signalsMap,
    });
  } catch (err) {
    wrap.innerHTML = `<p class="empty-state">Lỗi tải watchlist: ${err.message}</p>`;
    console.error('[watchlist-loader] loadWatchlist error:', err);
  }
}

/**
 * Thêm ticker vào watchlist, reload sau khi thành công.
 */
export async function handleAddTicker(ticker, note = '') {
  if (!ticker) return;
  try {
    await sendJson(WATCHLIST_BASE, 'POST', {
      ticker: ticker.toUpperCase().trim(),
      note,
    });
    showToast(`✅ Đã thêm ${ticker.toUpperCase()} vào watchlist`);
    await loadWatchlist();
  } catch (err) {
    const msg = err.message.includes('409')
      ? `${ticker.toUpperCase()} đã có trong watchlist`
      : `Lỗi thêm ticker: ${err.message}`;
    showToast(msg, 'error');
  }
}

/**
 * Xóa ticker khỏi watchlist, reload sau khi thành công.
 */
async function handleRemove(ticker) {
  try {
    await sendJson(`${WATCHLIST_BASE}/${encodeURIComponent(ticker)}`, 'DELETE');
    showToast(`🗑 Đã xóa ${ticker} khỏi watchlist`);
    await loadWatchlist();
  } catch (err) {
    showToast(`Lỗi xóa: ${err.message}`, 'error');
  }
}

/**
 * Sửa ghi chú cho một watchlist item.
 * Dùng window.prompt để nhận input — lightweight, không cần modal mới.
 * Sau khi PATCH thành công: cập nhật DOM tại chỗ (không reload toàn bộ watchlist).
 *
 * @param {string}      ticker
 * @param {string}      currentNote  - giá trị hiện tại để pre-fill prompt
 * @param {HTMLElement} cardEl       - card DOM node để patch note in-place
 */
async function handleEditNote(ticker, currentNote, cardEl) {
  const newNote = window.prompt(`Ghi chú cho ${ticker}:`, currentNote ?? '');

  // null = user bấm Cancel
  if (newNote === null) return;

  try {
    const updated = await sendJson(
      `${WATCHLIST_BASE}/${encodeURIComponent(ticker)}/note`,
      'PATCH',
      { note: newNote },
    );

    // Patch note in-place — avoid full reload
    const noteEl = cardEl?.querySelector('[data-note]');
    if (noteEl) {
      noteEl.textContent = updated.note ?? '';
      noteEl.classList.toggle('wl-note--empty', !updated.note);
    }

    showToast(`✏️ Đã cập nhật ghi chú ${ticker}`);
  } catch (err) {
    showToast(`Lỗi cập nhật ghi chú: ${err.message}`, 'error');
  }
}

/**
 * Trigger manual scan, hiện kết quả trong banner.
 * Sau khi scan xong: cache _signalsMap rồi reload watchlist để tags hiện lên card.
 */
async function handleScan(resultEl, btnEl) {
  btnEl.classList.add('scanning');
  btnEl.textContent = '⏳ Đang scan…';
  resultEl.classList.add('hidden');

  try {
    const res = await sendJson(`${WATCHLIST_BASE}/scan`, 'POST');

    const scanItems = res.signals?.items ?? [];
    _signalsMap = Object.fromEntries(
      scanItems.map(i => [i.ticker, i.signal_reports ?? []])
    );

    resultEl.textContent =
      `Scan xong: ${res.scanned_tickers} tickers, ${res.triggered} tín hiệu. ${res.summary ?? ''}`;
    resultEl.classList.remove('hidden');

    await loadWatchlist();
  } catch (err) {
    resultEl.textContent = `Lỗi scan: ${err.message}`;
    resultEl.classList.remove('hidden');
  } finally {
    btnEl.classList.remove('scanning');
    btnEl.innerHTML = `
      <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <circle cx="11" cy="11" r="8"/>
        <path d="m21 21-4.35-4.35"/>
      </svg>
      Scan now`;
  }
}
