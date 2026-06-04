/**
 * brief-feedback.js
 * Owner: modules/briefing
 * Responsibility:
 *   1. Submit brief feedback (acted / watching / skipped) via event delegation.
 *   2. Load brief feedback summary → KPI card #briefActedRate.
 */

import { briefingApiBase } from '../../api/client.js';

// Session-scoped availability flag.
// null  = not probed yet
// true  = endpoint exists, safe to fetch
// false = endpoint returned 404, skip all subsequent calls this session
let _summaryAvailable = null;

/**
 * _probeSummaryEndpoint()
 * HEAD probe on first call. Sets _summaryAvailable.
 * Suppresses 404 noise: if backend hasn't deployed the endpoint yet,
 * we get exactly ONE 404 in the network log instead of one per load cycle.
 */
async function _probeSummaryEndpoint() {
  if (_summaryAvailable !== null) return _summaryAvailable;
  try {
    const res = await fetch('/api/v1/dashboard/brief/feedback-summary', { method: 'HEAD' });
    _summaryAvailable = res.status !== 404;
  } catch {
    _summaryAvailable = false;
  }
  return _summaryAvailable;
}

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
      // Refresh KPI after successful feedback submission
      loadBriefFeedbackSummary({ silent: true });
    } catch (err) {
      // Rollback optimistic UI on failure
      bar.querySelectorAll('.fb-btn').forEach(b => { b.disabled = false; });
      btn.classList.remove('fb-selected');
      console.error('[brief-feedback] submit failed', err);
    }
  });
}

/**
 * loadBriefFeedbackSummary()
 * Fetches GET /api/v1/dashboard/brief/feedback-summary and populates
 * #briefActedRate + #briefActedSub in the KPI strip.
 *
 * Expected response shape:
 *   { acted: number, total: number, acted_rate: number,
 *     top_theme?: string, lookback_days?: number }
 *
 * Graceful degradation:
 * - Probes endpoint once via HEAD before first GET.
 * - If 404: sets session flag, skips all future calls — no repeated network log.
 * - If other error: warns once (unless silent=true).
 */
export async function loadBriefFeedbackSummary({ silent = false } = {}) {
  const rateEl = document.getElementById('briefActedRate');
  const subEl  = document.getElementById('briefActedSub');
  if (!rateEl && !subEl) return;

  // Probe once; if endpoint not available, bail silently for the session
  const available = await _probeSummaryEndpoint();
  if (!available) return;

  try {
    const res = await fetch('/api/v1/dashboard/brief/feedback-summary');
    if (res.status === 404) {
      _summaryAvailable = false;
      return;
    }
    if (!res.ok) throw new Error(`HTTP ${res.status}`);

    const data = await res.json();
    const { acted = 0, total = 0, acted_rate, top_theme, lookback_days = 7 } = data ?? {};

    if (rateEl) {
      const rate = acted_rate != null
        ? `${Math.round(acted_rate * 100)}%`
        : (total > 0 ? `${Math.round(acted / total * 100)}%` : '—');
      rateEl.textContent = rate;
      rateEl.classList.add('updated');
      setTimeout(() => rateEl.classList.remove('updated'), 900);
    }

    if (subEl) {
      const parts = [`${acted}/${total} acted`];
      if (top_theme) parts.push(top_theme.slice(0, 20));
      else parts.push(`${lookback_days}d`);
      subEl.textContent = parts.join(' · ');
    }
  } catch (err) {
    if (!silent) console.warn('[brief-feedback] loadBriefFeedbackSummary failed:', err.message);
  }
}
