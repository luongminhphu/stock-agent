/**
 * engine-controls.js
 * Owner: modules/engine
 *
 * Responsibility: wire #engineRunBtn → POST /api/v1/core/engine/run.
 * On success: toast + reload dashboard.
 * On error: toast error, reset button.
 *
 * Rule: không import loadDashboard trực tiếp để tránh circular dep.
 * Dùng CustomEvent 'engine:run-complete' → app.js lắng nghe và gọi loadDashboard.
 */

import { coreApiBase, sendJson } from '../../api/client.js';
import { showToast }             from '../../utils/dom.js';

export function initEngineControls() {
  const btn = document.getElementById('engineRunBtn');
  if (!btn) return;

  btn.addEventListener('click', async () => {
    if (btn.disabled) return;
    _setLoading(btn, true);

    try {
      await sendJson(`${coreApiBase()}/engine/run`, 'POST', null);
      showToast('🚀 Engine đang chạy — đang tải lại dữ liệu…');
      document.dispatchEvent(new CustomEvent('engine:run-complete'));
    } catch (err) {
      showToast(`Lỗi chạy engine: ${err.message}`, 'error');
    } finally {
      _setLoading(btn, false);
    }
  });
}

function _setLoading(btn, loading) {
  btn.disabled = loading;
  btn.setAttribute('aria-busy', String(loading));
  if (loading) {
    btn.dataset.origText = btn.textContent.trim();
    btn.innerHTML = `
      <svg class="engine-run-spinner" viewBox="0 0 24 24" fill="none"
           stroke="currentColor" stroke-width="2.5" stroke-linecap="round"
           aria-hidden="true">
        <path d="M12 2v4M12 18v4M4.93 4.93l2.83 2.83M16.24 16.24l2.83 2.83M2 12h4M18 12h4M4.93 19.07l2.83-2.83M16.24 7.76l2.83-2.83"/>
      </svg>
      Đang chạy…`;
  } else {
    btn.textContent = btn.dataset.origText ?? 'Chạy Engine';
    delete btn.dataset.origText;
  }
}
