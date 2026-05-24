// modules/briefing/brief-generate.js — Wave G
// Owner: briefing segment
// Responsibility: wire [data-generate-brief] buttons → POST /api/v1/briefing/{phase}/generate
// Rule: không chứa render logic — chỉ trigger generate + toast feedback
//
// Events dispatched:
//   briefing:generated  — after generate succeeds → AttentionPanel refresh

import { briefingApiBase, sendJson } from '../../api/client.js';
import { showToast } from '../../utils/dom.js';

/**
 * Delegates click trên [data-generate-brief="morning|eod"]
 * Gọi POST /api/v1/briefing/{phase}/generate
 * Toast success/error — reload brief được handle bởi caller nếu cần
 */
export function bindGenerateBriefButtons() {
  document.addEventListener('click', async e => {
    const btn = e.target.closest('[data-generate-brief]');
    if (!btn) return;

    const phase = btn.dataset.generateBrief; // 'morning' | 'eod'
    if (!phase) return;

    btn.disabled = true;
    const orig = btn.innerHTML;
    btn.innerHTML = '<span style="opacity:.6">Đang tạo…</span>';

    try {
      await sendJson(`${briefingApiBase()}/${phase}/generate`, 'POST', {});
      showToast(`✅ ${phase === 'morning' ? 'Morning' : 'EOD'} Brief đã tạo xong`);

      // Wave 2 wire: brief generate xong → notify app → AttentionPanel refresh
      // Brief mới có thể chứa action items mới cho nhà đầu tư.
      document.dispatchEvent(new CustomEvent('briefing:generated', {
        detail: { phase },
      }));
    } catch (err) {
      showToast(`Lỗi tạo brief: ${err.message}`, 'error');
    } finally {
      btn.disabled = false;
      btn.innerHTML = orig;
    }
  });
}
