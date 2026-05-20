// modules/memory/memory-api.js
// Owner: api segment (adapter only)
// Responsibility: fetch /api/v1/memory/* — no render logic here

import { memoryApiBase, getJson, sendJson } from '../../api/client.js';
import { showToast } from '../../utils/dom.js';

/**
 * Fetch latest MemorySnapshot + MemoryContext.
 * Returns null on 404 (no data yet).
 * @returns {Promise<MemorySnapshotDTO|null>}
 */
export async function fetchMemorySnapshot() {
  try {
    return await getJson(`${memoryApiBase()}/snapshot`);
  } catch (err) {
    if (err.message.startsWith('404')) return null;
    throw err;
  }
}

/**
 * Trigger on-demand pattern synthesis.
 * Returns {status:'ok', ...} or {status:'insufficient_data', detail}.
 * @returns {Promise<{status: string, [key: string]: any}>}
 */
export async function triggerMemoryRefresh() {
  return sendJson(`${memoryApiBase()}/refresh`, 'POST', {});
}

/**
 * Wire [data-memory-refresh] button → POST /api/v1/memory/refresh.
 * On success: re-render panel with new data via onSuccess(result) callback.
 * @param {(result: object) => void} onSuccess
 */
export function bindRefreshButton(onSuccess) {
  document.addEventListener('click', async e => {
    const btn = e.target.closest('[data-memory-refresh]');
    if (!btn) return;

    btn.disabled = true;
    const orig = btn.innerHTML;
    btn.innerHTML = '<span style="opacity:.6">Đang tổng hợp…</span>';

    try {
      const result = await triggerMemoryRefresh();
      if (result?.status === 'insufficient_data') {
        showToast(`⚠️ ${result.detail}`, 'warning');
      } else {
        showToast('✅ Bộ nhớ đã được cập nhật');
        onSuccess?.(result);
      }
    } catch (err) {
      showToast(`Lỗi refresh bộ nhớ: ${err.message}`, 'error');
    } finally {
      btn.disabled = false;
      btn.innerHTML = orig;
    }
  });
}
