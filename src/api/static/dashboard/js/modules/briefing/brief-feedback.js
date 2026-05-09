/**
 * brief-feedback.js
 * Owner: modules/briefing
 * Responsibility: submit brief feedback (acted / watching / skipped) via
 *                 event delegation on document — works with dynamically
 *                 rendered brief cards.
 */

import { briefingApiBase } from '../../api/client.js';

/**
 * bindFeedbackEvents()
 * Wire once at bootstrap. Listens for clicks on .fb-btn inside
 * .brief-feedback-bar[data-brief-id] and POSTs to /api/v1/briefing/{id}/feedback.
 */
export function bindFeedbackEvents() {
  document.addEventListener('click', async e => {
    const btn = e.target.closest('.fb-btn[data-outcome]');
    if (!btn) return;
    const bar = btn.closest('.brief-feedback-bar[data-brief-id]');
    if (!bar) return;

    const briefId = bar.dataset.briefId;
    const outcome = btn.dataset.outcome;
    if (!briefId || !outcome) return;

    // Optimistic UI — disable all buttons immediately
    bar.querySelectorAll('.fb-btn').forEach(b => { b.disabled = true; });
    btn.classList.add('fb-selected');

    try {
      const res = await fetch(`${briefingApiBase()}/${briefId}/feedback`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ outcome }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      // Replace bar with confirmation message
      bar.innerHTML = `<span class="fb-confirmed">✓ Đã ghi nhận: ${btn.textContent.trim()}</span>`;
    } catch (err) {
      // Rollback optimistic UI on failure
      bar.querySelectorAll('.fb-btn').forEach(b => { b.disabled = false; });
      btn.classList.remove('fb-selected');
      console.error('[brief-feedback] submit failed', err);
    }
  });
}
