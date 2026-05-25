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
 *   openDecisionModal(opts?)    — mở modal, populate thesis select, pre-fill từ opts
 *
 * opts shape (tất cả optional):
 *   { thesisId?: number, ticker?: string, action?: string, price?: number }
 *
 * Loop closure:
 *   After a successful replay that produces key_lesson, dispatches
 *   CustomEvent('decision:lesson-persisted', { ticker, thesis_id })
 *   so thesis-service.js can mark the relevant thesis row for review
 *   without any direct import dependency between the two modules.
 *
 * Events dispatched:
 *   decision:changed  { thesisId: number|null }
 *     — after form submit, evaluate, or replay succeeds.
 *     — thesisId is null when no thesis is linked; listeners should guard.
 */

import { getJson, sendJson, thesisApiBase } from '../../api/client.js';
import {
  renderDecisionsTable,
  renderLessonsCards,
  renderReplayPanel,
} from './decision-renderer.js';

let lessonsLoaded = false;

// ---------------------------------------------------------------------------
// Private helpers
// ---------------------------------------------------------------------------

function setKpi(id, value, sub, alert = false) {
  const card  = document.getElementById(id);
  if (!card) return;
  const valEl = card.querySelector('.dec-kpi-value');
  const subEl = card.querySelector('.dec-kpi-sub');
  if (valEl) {
    valEl.textContent = value ?? '—';
    valEl.classList.add('updated');
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

  const rateEl = document.querySelector('#dkpiWinRate .dec-kpi-value');
  if (rateEl && winRate !== null) {
    rateEl.classList.remove('rate-high', 'rate-mid', 'rate-low');
    rateEl.classList.add(
      winRate >= 60 ? 'rate-high' : winRate >= 40 ? 'rate-mid' : 'rate-low'
    );
  }
}

/**
 * Dispatch decision:changed với thesisId (number | null).
 * Listeners phải guard: chỉ react khi thesisId match.
 */
function dispatchDecisionChanged(thesisId) {
  document.dispatchEvent(new CustomEvent('decision:changed', {
    detail: { thesisId: thesisId ?? null },
  }));
}

// ---------------------------------------------------------------------------
// Public: load & render decisions + update KPI strip
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
      const thesisRaw = document.getElementById('decisionThesisSelect')?.value;
      const thesisId  = thesisRaw ? parseInt(thesisRaw, 10) : null;

      const payload = {
        ticker:              form.decTickerField?.value?.trim().toUpperCase() || null,
        thesis_id:           thesisId,
        decision_type:       form.decActionField.value,
        rationale:           form.decReasonField.value.trim(),
        review_horizon_days: 30,
      };

      const price   = parseFloat(form.decPriceField?.value);
      const qty     = parseInt(form.decQtyField?.value, 10);
      const emotion = form.decEmotionField?.value;
      if (!isNaN(price) && price > 0)   payload.price       = price;
      if (!isNaN(qty)   && qty   > 0)   payload.quantity    = qty;
      if (emotion)                       payload.emotion_tag = emotion;

      if (!payload.decision_type || !payload.rationale) {
        alert('Vui lòng điền đầy đủ Hành động và Lý do quyết định.');
        return;
      }

      await sendJson('/api/v1/decisions', 'POST', payload);
      modal.close();
      form.reset();
      const sel = document.getElementById('decisionThesisSelect');
      if (sel) sel.value = '';
      await loadDecisions();

      // Wave 3 wire: decision logged → notify app → loadThesisDetail if selected
      // thesisId có thể null nếu user không chọn thesis — listeners phải guard.
      dispatchDecisionChanged(thesisId);
    } catch (err) {
      alert(`Lỗi lưu decision: ${err.message}`);
    } finally {
      submitBtn.disabled = false;
      submitBtn.textContent = 'Lưu Decision';
    }
  });
}

/** Alias for backward compat with app.js */
export const bindDecisionFormEvents = bindLogDecisionModal;

// ---------------------------------------------------------------------------
// Public: evaluate a decision
// thesisId đọc từ data-thesis-id trên DOM row được renderer gắn.
// ---------------------------------------------------------------------------

export async function evaluateDecision(decisionId, btnEl) {
  // Đọc thesis_id từ DOM row trước khi reload bảng
  const row      = btnEl?.closest('tr[data-thesis-id]');
  const rawId    = row?.dataset?.thesisId;
  const thesisId = rawId ? parseInt(rawId, 10) : null;

  if (btnEl) { btnEl.disabled = true; btnEl.textContent = '…'; }
  try {
    await sendJson(`/api/v1/decisions/${decisionId}/evaluate`, 'POST', null);
    await loadDecisions();

    // Wave 3 wire: evaluate xong → notify nếu thesis_id xác định được
    dispatchDecisionChanged(thesisId);
  } catch (err) {
    alert(`Lỗi evaluate: ${err.message}`);
    if (btnEl) { btnEl.disabled = false; btnEl.textContent = 'Evaluate'; }
  }
}

// ---------------------------------------------------------------------------
// Public: replay a decision
// ---------------------------------------------------------------------------

export async function replayDecision(decisionId, replayWrap, btnEl) {
  if (btnEl) { btnEl.disabled = true; btnEl.textContent = '…'; }
  try {
    const result = await getJson(`/api/v1/decisions/${decisionId}/replay`);
    renderReplayPanel(replayWrap, result);
    lessonsLoaded = false;

    // Existing loop: close decision → thesis review badge
    if (result.key_lesson && result.thesis_id) {
      document.dispatchEvent(new CustomEvent('decision:lesson-persisted', {
        detail: { ticker: result.ticker, thesis_id: String(result.thesis_id) },
      }));
    }

    // Wave 3 wire: replay xong → notify thesis panel nếu thesis_id có
    // result.thesis_id là number|null từ backend
    const thesisId = result.thesis_id ? Number(result.thesis_id) : null;
    dispatchDecisionChanged(thesisId);
  } catch (err) {
    if (replayWrap) replayWrap.innerHTML = `<p class="error-text">Lỗi replay: ${err.message}</p>`;
  } finally {
    if (btnEl) { btnEl.disabled = false; btnEl.textContent = '🧠 Replay'; }
  }
}

// ---------------------------------------------------------------------------
// Private: populate thesis <select> inside the modal
// Trả về danh sách thesis đã load để caller có thể pre-select.
// ---------------------------------------------------------------------------

async function populateThesisSelect() {
  const sel = document.getElementById('decisionThesisSelect');
  if (!sel) return [];
  sel.innerHTML = '<option value="">Đang tải…</option>';
  try {
    const theses = await getJson(`${thesisApiBase()}?status=active&limit=50`);
    const list = Array.isArray(theses) ? theses : (theses.items ?? []);
    if (!list.length) {
      sel.innerHTML = '<option value="">Không có thesis active</option>';
      return [];
    }
    sel.innerHTML = '<option value="">— Chọn thesis (không bắt buộc) —</option>' +
      list.map(t =>
        `<option value="${t.id}">[${t.ticker}] ${t.title ?? t.ticker}</option>`
      ).join('');
    return list;
  } catch {
    sel.innerHTML = '<option value="">Lỗi tải thesis</option>';
    return [];
  }
}

// ---------------------------------------------------------------------------
// Public: open modal + populate thesis select + pre-fill từ opts
//
// opts (tất cả optional):
//   thesisId?: number  — pre-select thesis trong dropdown
//   ticker?:   string  — điền vào field decTickerField
//   action?:   string  — set decActionField  (BUY | SELL | HOLD | SKIP | WATCH)
//   price?:    number  — điền vào field decPriceField
// ---------------------------------------------------------------------------

export async function openDecisionModal(opts = {}) {
  const { thesisId, ticker, action, price } = opts ?? {};

  // Populate thesis dropdown trước, nhận lại list để có thể pre-select
  await populateThesisSelect();

  // Pre-select thesis nếu có
  if (thesisId) {
    const sel = document.getElementById('decisionThesisSelect');
    if (sel) sel.value = String(thesisId);
  }

  // Pre-fill ticker
  if (ticker) {
    const tickerField = document.getElementById('decTickerField')
      ?? document.querySelector('[name="decTickerField"]');
    if (tickerField) tickerField.value = ticker.toUpperCase();
  }

  // Pre-fill action (BUY / SELL / HOLD / …)
  if (action) {
    const actionField = document.getElementById('decActionField')
      ?? document.querySelector('[name="decActionField"]');
    if (actionField) actionField.value = action.toUpperCase();
  }

  // Pre-fill price
  if (price != null && !isNaN(price)) {
    const priceField = document.getElementById('decPriceField')
      ?? document.querySelector('[name="decPriceField"]');
    if (priceField) priceField.value = price;
  }

  document.getElementById('decisionModal')?.showModal();
}
