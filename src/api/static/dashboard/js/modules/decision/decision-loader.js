/**
 * decision-loader.js
 * Owner: modules/decision
 * Responsibility: fetch /api/v1/decisions + /api/v1/lessons,
 *                 handle log/evaluate/replay actions,
 *                 delegate render sang decision-renderer.js
 * Rule: KHÔNG chứa DOM manipulation trực tiếp (ngoài error fallback).
 *       KHÔNG chứa business logic. Chỉ fetch → action → render.
 */

import { el, showToast, openModal, closeModal } from '../../utils/dom.js';
import { getJson, sendJson } from '../../api/client.js';
import {
  renderDecisionsTable,
  renderLessonsCards,
  renderReplayPanel,
} from './decision-renderer.js';

const DECISIONS_BASE = '/api/v1/decisions';
const LESSONS_BASE   = '/api/v1/lessons';

// ── Public: load decisions tab ───────────────────────────────────────────────
export async function loadDecisions() {
  const wrap = el('decisionsTableWrap');
  if (!wrap) return;

  wrap.innerHTML = '<p class="muted" style="padding:16px">Đang tải decisions…</p>';

  try {
    const data = await getJson(`${DECISIONS_BASE}?limit=100`);
    renderDecisionsTable(wrap, data ?? [], {
      onEvaluate:  handleEvaluate,
      onReplay:    handleReplay,
    });
  } catch (err) {
    wrap.innerHTML = `<p class="empty-state">Lỗi tải decisions: ${err.message}</p>`;
    console.error('[decision-loader] loadDecisions error:', err);
  }
}

// ── Public: load lessons tab ─────────────────────────────────────────────────
export async function loadLessons(ticker = null) {
  const wrap = el('lessonsListWrap');
  if (!wrap) return;

  wrap.innerHTML = '<p class="muted" style="padding:16px">Đang tải lessons…</p>';

  try {
    const q = ticker ? `?ticker=${encodeURIComponent(ticker)}&limit=30` : '?limit=30';
    const data = await getJson(`${LESSONS_BASE}${q}`);
    renderLessonsCards(wrap, data ?? []);
  } catch (err) {
    wrap.innerHTML = `<p class="empty-state">Lỗi tải lessons: ${err.message}</p>`;
    console.error('[decision-loader] loadLessons error:', err);
  }
}

// ── Public: bind decision modal form ─────────────────────────────────────────
export function bindDecisionFormEvents() {
  const form = el('decisionForm');
  if (!form) return;

  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const btn = el('decisionSubmitBtn');
    btn && (btn.disabled = true) && (btn.textContent = 'Đang lưu…');

    try {
      const payload = {
        ticker:                form.querySelector('#decTickerInput')?.value?.trim().toUpperCase(),
        decision_type:         form.querySelector('#decTypeSelect')?.value,
        price_at_decision:     parseFloat(form.querySelector('#decPriceInput')?.value),
        thesis_id:             parseInt(form.querySelector('#decThesisIdInput')?.value) || null,
        rationale:             form.querySelector('#decRationaleInput')?.value?.trim() || null,
        review_horizon_days:   parseInt(form.querySelector('#decHorizonInput')?.value) || 30,
      };

      if (!payload.ticker || !payload.price_at_decision) {
        showToast('Vui lòng nhập Mã CK và Giá quyết định.', 'error');
        return;
      }

      await sendJson(DECISIONS_BASE, 'POST', payload);
      closeModal('decisionModal');
      form.reset();
      showToast(`✅ Đã log decision ${payload.decision_type} ${payload.ticker}`);
      await loadDecisions();
    } catch (err) {
      showToast(`Lỗi log decision: ${err.message}`, 'error');
    } finally {
      if (btn) { btn.disabled = false; btn.textContent = 'Lưu Decision'; }
    }
  });
}

// ── Internal: evaluate outcome ────────────────────────────────────────────────
async function handleEvaluate(decisionId, btnEl) {
  if (btnEl) { btnEl.disabled = true; btnEl.textContent = '⏳…'; }
  try {
    await sendJson(`${DECISIONS_BASE}/${decisionId}/evaluate`, 'POST');
    showToast('✅ Đã evaluate outcome');
    await loadDecisions();
  } catch (err) {
    showToast(`Lỗi evaluate: ${err.message}`, 'error');
  } finally {
    if (btnEl) { btnEl.disabled = false; btnEl.textContent = 'Evaluate'; }
  }
}

// ── Internal: AI replay ────────────────────────────────────────────────────────
async function handleReplay(decisionId, replayWrap, btnEl) {
  if (btnEl) { btnEl.disabled = true; btnEl.textContent = '⏳ AI…'; }
  try {
    const result = await getJson(`${DECISIONS_BASE}/${decisionId}/replay`);
    renderReplayPanel(replayWrap, result);
    showToast('🧠 AI Replay hoàn tất');
    // Also refresh lessons tab in background
    loadLessons();
  } catch (err) {
    replayWrap.innerHTML = `<p class="dec-replay-error">Lỗi replay: ${err.message}</p>`;
    replayWrap.classList.remove('hidden');
    showToast(`Lỗi replay: ${err.message}`, 'error');
  } finally {
    if (btnEl) { btnEl.disabled = false; btnEl.textContent = '🧠 Replay'; }
  }
}
