/**
 * decision-loader.js — Decision Log & Lessons tab logic
 * Owner: dashboard (static adapter)
 *
 * Exports:
 *   loadDecisions()             — fetch + render decisions table + update KPI strip
 *   loadLessons(force?)         — lazy fetch + render lessons cards
 *   bindDecisionTabs()          — wire Decisions | Lessons tab toggle
 *   bindLogDecisionModal()      — wire Log Decision modal (open/submit)
 *   bindDecisionFormEvents()    — alias for bindLogDecisionModal (app.js compat)
 *   evaluateDecision(id, row)   — POST evaluate, reload table
 *   replayDecision(id, row)     — GET replay, show inline panel
 *
 * Loop closure:
 *   After a successful replay that produces key_lesson, dispatches
 *   CustomEvent('decision:lesson-persisted', { ticker, thesis_id })
 *   so thesis-service.js can mark the relevant thesis row for review
 *   without any direct import dependency between the two modules.
 */

import { getJson, sendJson, thesisApiBase } from '../../api/client.js';
import {
  renderDecisionsTable,
  renderLessonsCards,
  renderReplayPanel,
} from './decision-renderer.js';

let lessonsLoaded = false;

// ---------------------------------------------------------------------------
// Private: update Wave B KPI strip (client-side aggregate)
// ---------------------------------------------------------------------------

function setKpi(id, value, sub, alert = false) {
  const card  = document.getElementById(id);
  if (!card) return;
  const valEl = card.querySelector('.dec-kpi-value');
  const subEl = card.querySelector('.dec-kpi-sub');
  if (valEl) {
    valEl.textContent = value ?? '—';
    valEl.classList.add('updated');          // flash animation
    setTimeout(() => valEl.classList.remove('updated'), 900);
  }
  if (subEl && sub !== undefined) subEl.textContent = sub;
  if (card.classList.contains('dec-kpi-card--alert')) {
    card.dataset.zero = alert ? 'false' : 'true';
  }
}

function updateDecisionKpis(items) {
  const total     = items.length;
  const evaluated = items.filter(d => d.outcome_evaluated_at);
  const wins      = evaluated.filter(d => d.outcome_verdict === 'CORRECT').length;
  const winRate   = evaluated.length
    ? Math.round(wins / evaluated.length * 100)
    : null;
  const buyCount  = items.filter(d => d.decision_type === 'BUY').length;
  const sellCount = items.filter(d => d.decision_type === 'SELL').length;
  const now       = Date.now();
  const pending   = items.filter(d => {
    if (d.outcome_evaluated_at) return false;
    const due = new Date(d.decision_at).getTime()
      + (d.review_horizon_days ?? 30) * 86_400_000;
    return due < now;
  }).length;

  setKpi('dkpiTotal',    total,    undefined);
  setKpi('dkpiWinRate',  winRate !== null ? `${winRate}%` : '—', `${evaluated.length} evaluated`);
  setKpi('dkpiBuyCount', `${buyCount} / ${sellCount}`);
  setKpi('dkpiPending',  pending,  'quá hạn review', pending > 0);

  // win-rate colour tier
  const rateEl = document.querySelector('#dkpiWinRate .dec-kpi-value');
  if (rateEl && winRate !== null) {
    rateEl.classList.remove('rate-high', 'rate-mid', 'rate-low');
    rateEl.classList.add(
      winRate >= 60 ? 'rate-high' : winRate >= 40 ? 'rate-mid' : 'rate-low'
    );
  }
}

// ---------------------------------------------------------------------------
// Public: load & render decisions + update KPI strip
// Fetch 20 most-recent decisions (newest first) for the table.
// KPI strip aggregates the returned slice — enough for a working signal.
// ---------------------------------------------------------------------------

export async function loadDecisions() {
  const wrap = document.getElementById('decisionsTableWrap');
  if (!wrap) return;
  wrap.innerHTML = '<p class="loading-text">Đang tải decisions…</p>';
  try {
    const data = await getJson('/api/v1/decisions?limit=20&order=desc');
    const items = Array.isArray(data) ? data : (data.items ?? []);
    renderDecisionsTable(wrap, items, {
      onEvaluate: evaluateDecision,
      onReplay:   replayDecision,
    });
    updateDecisionKpis(items);
  } catch (err) {
    wrap.innerHTML = `<p class="error-text">Lỗi tải decisions: ${err.message}</p>`;
  }
}

// ---------------------------------------------------------------------------
// Public: load & render lessons (lazy)
// Fetch 20 most-recent lessons, newest on top.
// ---------------------------------------------------------------------------

export async function loadLessons(force = false) {
  if (lessonsLoaded && !force) return;
  const wrap = document.getElementById('lessonsListWrap');
  if (!wrap) return;
  wrap.innerHTML = '<p class="loading-text">Đang tải lessons…</p>';
  try {
    const data = await getJson('/api/v1/lessons?limit=20&lookback_days=180&sort_by=created_at&order=desc');
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

    // Close the lesson → thesis review UI loop:
    // Dispatch a CustomEvent so thesis-service.js can badge the thesis row
    // and show a toast — without any direct import between the two modules.
    if (result.key_lesson && result.thesis_id) {
      document.dispatchEvent(new CustomEvent('decision:lesson-persisted', {
        detail: { ticker: result.ticker, thesis_id: String(result.thesis_id) },
      }));
    }
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
