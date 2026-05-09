/**
 * decision-loader.js — Decision Log & Lessons tab logic
 * Owner: dashboard (static adapter)
 *
 * Exports:
 *   loadDecisions()             — fetch + render decisions table
 *   loadLessons(force?)         — lazy fetch + render lessons cards
 *   bindDecisionTabs()          — wire Decisions | Lessons tab toggle
 *   bindLogDecisionModal()      — wire Log Decision modal (open/submit)
 *   bindDecisionFormEvents()    — alias for bindLogDecisionModal (app.js compat)
 *   evaluateDecision(id, row)   — POST evaluate, reload table
 *   replayDecision(id, row)     — GET replay, show inline panel
 */

import { getJson, sendJson, thesisApiBase } from '../../api/client.js';
import {
  renderDecisionsTable,
  renderLessonsCards,
  renderReplayPanel,
} from './decision-renderer.js';

let lessonsLoaded = false;

// ---------------------------------------------------------------------------
// Public: load & render decisions
// ---------------------------------------------------------------------------

export async function loadDecisions() {
  const wrap = document.getElementById('decisionsTableWrap');
  if (!wrap) return;
  wrap.innerHTML = '<p class="loading-text">Đang tải decisions…</p>';
  try {
    const data = await getJson('/api/v1/decisions?limit=50');
    renderDecisionsTable(wrap, Array.isArray(data) ? data : [], {
      onEvaluate: evaluateDecision,
      onReplay:   replayDecision,
    });
  } catch (err) {
    wrap.innerHTML = `<p class="error-text">Lỗi tải decisions: ${err.message}</p>`;
  }
}

// ---------------------------------------------------------------------------
// Public: load & render lessons (lazy)
// ---------------------------------------------------------------------------

export async function loadLessons(force = false) {
  if (lessonsLoaded && !force) return;
  const wrap = document.getElementById('lessonsListWrap');
  if (!wrap) return;
  wrap.innerHTML = '<p class="loading-text">Đang tải lessons…</p>';
  try {
    const data = await getJson('/api/v1/lessons?limit=20&lookback_days=180');
    renderLessonsCards(wrap, Array.isArray(data) ? data : []);
    lessonsLoaded = true;
  } catch (err) {
    wrap.innerHTML = `<p class="error-text">Lỗi tải lessons: ${err.message}</p>`;
  }
}

// ---------------------------------------------------------------------------
// Public: wire decision tabs
// ---------------------------------------------------------------------------

export function bindDecisionTabs() {
  const tabs = document.querySelectorAll('[data-decision-tab]');
  const panes = {
    decisions: document.getElementById('decisionsPane'),
    lessons: document.getElementById('lessonsPane'),
  };

  tabs.forEach(tab => {
    tab.addEventListener('click', () => {
      tabs.forEach(t => t.classList.remove('active'));
      tab.classList.add('active');
      const target = tab.dataset.decisionTab;
      Object.entries(panes).forEach(([key, pane]) => {
        if (!pane) return;
        pane.classList.toggle('hidden', key !== target);
      });
      if (target === 'lessons') loadLessons();
    });
  });
}

// ---------------------------------------------------------------------------
// Public: wire Log Decision modal
// ---------------------------------------------------------------------------

export function bindLogDecisionModal() {
  const modal    = document.getElementById('decisionModal');
  const form     = document.getElementById('decisionForm');
  const closeBtn = modal?.querySelector('[data-close-modal]');

  if (!modal || !form) return;

  closeBtn?.addEventListener('click', () => modal.close());
  modal.addEventListener('click', e => { if (e.target === modal) modal.close(); });

  form.addEventListener('submit', async e => {
    e.preventDefault();
    const submitBtn = form.querySelector('[type="submit"]');
    submitBtn.disabled = true;
    submitBtn.textContent = 'Đang lưu…';

    try {
      const payload = {
        thesis_id:           parseInt(form.thesis_id.value, 10),
        decision_type:       form.decision_type.value,
        rationale:           form.rationale.value.trim(),
        brief_summary:       form.brief_summary?.value?.trim() || null,
        review_horizon_days: parseInt(form.review_horizon_days.value, 10) || 30,
      };

      if (!payload.thesis_id || !payload.decision_type || !payload.rationale) {
        alert('Vui lòng điền đầy đủ Thesis, Loại quyết định và Rationale.');
        return;
      }

      await sendJson('/api/v1/decisions', 'POST', payload);
      modal.close();
      form.reset();
      await loadDecisions();
    } catch (err) {
      alert(`Lỗi lưu decision: ${err.message}`);
    } finally {
      submitBtn.disabled = false;
      submitBtn.textContent = 'Lưu Decision';
    }
  });
}

/**
 * Alias for bindLogDecisionModal — kept for backward compat with app.js
 * which imports { bindDecisionFormEvents }.
 */
export const bindDecisionFormEvents = bindLogDecisionModal;

// ---------------------------------------------------------------------------
// Public: evaluate a decision (called from renderer callback)
// ---------------------------------------------------------------------------

export async function evaluateDecision(decisionId, btnEl) {
  if (btnEl) { btnEl.disabled = true; btnEl.textContent = '…'; }
  try {
    await sendJson(`/api/v1/decisions/${decisionId}/evaluate`, 'POST', null);
    await loadDecisions();
  } catch (err) {
    alert(`Lỗi evaluate: ${err.message}`);
    if (btnEl) { btnEl.disabled = false; btnEl.textContent = 'Evaluate'; }
  }
}

// ---------------------------------------------------------------------------
// Public: replay a decision (called from renderer callback)
// ---------------------------------------------------------------------------

export async function replayDecision(decisionId, replayWrap, btnEl) {
  if (btnEl) { btnEl.disabled = true; btnEl.textContent = '…'; }
  try {
    const result = await getJson(`/api/v1/decisions/${decisionId}/replay`);
    renderReplayPanel(replayWrap, result);
    lessonsLoaded = false;
  } catch (err) {
    if (replayWrap) replayWrap.innerHTML = `<p class="error-text">Lỗi replay: ${err.message}</p>`;
  } finally {
    if (btnEl) { btnEl.disabled = false; btnEl.textContent = '🧠 Replay'; }
  }
}

// ---------------------------------------------------------------------------
// Private: populate thesis <select> inside the modal
// ---------------------------------------------------------------------------

async function populateThesisSelect() {
  const sel = document.getElementById('decisionThesisSelect');
  if (!sel) return;
  sel.innerHTML = '<option value="">Đang tải…</option>';
  try {
    const theses = await getJson(`${thesisApiBase()}?status=active&limit=50`);
    const list = Array.isArray(theses) ? theses : (theses.items ?? []);
    if (!list.length) {
      sel.innerHTML = '<option value="">Không có thesis active</option>';
      return;
    }
    sel.innerHTML = '<option value="">-- Chọn Thesis --</option>' +
      list.map(t =>
        `<option value="${t.id}">[${t.ticker}] ${t.title ?? t.ticker}</option>`
      ).join('');
  } catch {
    sel.innerHTML = '<option value="">Lỗi tải thesis</option>';
  }
}

// ---------------------------------------------------------------------------
// Public: open modal + populate thesis select
// ---------------------------------------------------------------------------

export async function openDecisionModal() {
  await populateThesisSelect();
  document.getElementById('decisionModal')?.showModal();
}
