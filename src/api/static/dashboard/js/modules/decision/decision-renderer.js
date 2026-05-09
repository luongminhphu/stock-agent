/**
 * decision-renderer.js
 * Owner: modules/decision
 * Responsibility: pure DOM render — decision table, lesson cards, replay panel.
 * Rule: không gọi API, không có side-effects ngoài DOM.
 *       nhận callbacks từ loader thông qua options object.
 */

import { esc, fmt, fmtDate, badge } from '../../utils/format.js';

// ── Decision type display map ─────────────────────────────────────────────────
const TYPE_LABEL = {
  BUY:  { label: 'MUA',  cls: 'dec-type-buy'  },
  SELL: { label: 'BÁN',  cls: 'dec-type-sell' },
  HOLD: { label: 'GIỮ',  cls: 'dec-type-hold' },
  SKIP: { label: 'BỎ',   cls: 'dec-type-skip' },
};

const VERDICT_CLS = {
  CORRECT:   'dec-verdict-correct',
  INCORRECT: 'dec-verdict-incorrect',
  MIXED:     'dec-verdict-mixed',
};

// ── renderDecisionsTable ──────────────────────────────────────────────────────
/**
 * Render toàn bộ decisions table vào container.
 * @param {HTMLElement} container
 * @param {Array} decisions
 * @param {{ onEvaluate, onReplay }} options
 */
export function renderDecisionsTable(container, decisions, { onEvaluate, onReplay } = {}) {
  container.innerHTML = '';

  if (!decisions.length) {
    container.innerHTML = `
      <div class="dec-empty">
        <strong>Chưa có decision nào</strong>
        <span>Nhấn "+ Log Decision" để ghi lại quyết định giao dịch đầu tiên.</span>
      </div>
    `;
    return;
  }

  const table = document.createElement('table');
  table.className = 'dec-table';
  table.innerHTML = `
    <thead>
      <tr>
        <th>Mã CK</th>
        <th>Type</th>
        <th>Giá QĐ</th>
        <th>PnL%</th>
        <th>Verdict</th>
        <th>Rationale</th>
        <th>Ngày</th>
        <th>Actions</th>
      </tr>
    </thead>
    <tbody id="decTableBody"></tbody>
  `;

  const tbody = table.querySelector('#decTableBody');

  for (const d of decisions) {
    const row = buildDecisionRow(d, onEvaluate, onReplay);
    tbody.appendChild(row);
  }

  container.appendChild(table);
}

// ── Build one decision row ────────────────────────────────────────────────────
function buildDecisionRow(d, onEvaluate, onReplay) {
  const typeInfo = TYPE_LABEL[d.decision_type] ?? { label: d.decision_type, cls: '' };
  const verdictCls = VERDICT_CLS[d.outcome_verdict] ?? '';

  const pnlHtml = d.outcome_pnl_pct != null
    ? `<span class="dec-pnl ${d.outcome_pnl_pct >= 0 ? 'up' : 'down'}">${d.outcome_pnl_pct >= 0 ? '+' : ''}${Number(d.outcome_pnl_pct).toFixed(2)}%</span>`
    : '<span class="dec-pnl muted">—</span>';

  const verdictHtml = d.outcome_verdict
    ? `<span class="dec-verdict ${verdictCls}">${esc(d.outcome_verdict)}</span>`
    : '<span class="dec-verdict muted">Chưa đánh giá</span>';

  const tr = document.createElement('tr');
  tr.className = 'dec-row';
  tr.dataset.id = d.id;
  tr.innerHTML = `
    <td><span class="dec-ticker">${esc(d.ticker)}</span></td>
    <td><span class="dec-type ${typeInfo.cls}">${typeInfo.label}</span></td>
    <td class="dec-price tabular">${fmt(d.price_at_decision)}</td>
    <td class="tabular">${pnlHtml}</td>
    <td>${verdictHtml}</td>
    <td class="dec-rationale">${d.rationale ? `<span title="${esc(d.rationale)}">${esc(d.rationale.slice(0, 60))}${d.rationale.length > 60 ? '…' : ''}</span>` : '<span class="muted">—</span>'}</td>
    <td class="dec-date">${fmtDate(d.decision_at)}</td>
    <td class="dec-actions">
      ${!d.outcome_evaluated_at ? `<button class="dec-btn-evaluate ghost-btn" title="Tính PnL">Evaluate</button>` : ''}
      ${d.outcome_evaluated_at ? `<button class="dec-btn-replay ghost-btn" title="AI phân tích">🧠 Replay</button>` : ''}
    </td>
  `;

  // Replay expand panel (hidden initially)
  const replayRow = document.createElement('tr');
  replayRow.className = 'dec-replay-row hidden';
  replayRow.innerHTML = `<td colspan="8"><div class="dec-replay-wrap"></div></td>`;
  const replayWrap = replayRow.querySelector('.dec-replay-wrap');

  // Wire evaluate
  const evalBtn = tr.querySelector('.dec-btn-evaluate');
  if (evalBtn && onEvaluate) {
    evalBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      onEvaluate(d.id, evalBtn);
    });
  }

  // Wire replay
  const replayBtn = tr.querySelector('.dec-btn-replay');
  if (replayBtn && onReplay) {
    replayBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      const isOpen = !replayRow.classList.contains('hidden');
      if (isOpen) {
        replayRow.classList.add('hidden');
        return;
      }
      replayRow.classList.remove('hidden');
      if (!replayWrap.hasChildNodes()) {
        onReplay(d.id, replayWrap, replayBtn);
      }
    });
  }

  // Return as DocumentFragment to keep rows paired
  const frag = document.createDocumentFragment();
  frag.appendChild(tr);
  frag.appendChild(replayRow);
  return frag;
}

// ── renderLessonsCards ────────────────────────────────────────────────────────
/**
 * Render AI lesson cards vào container.
 * @param {HTMLElement} container
 * @param {Array} lessons
 */
export function renderLessonsCards(container, lessons) {
  container.innerHTML = '';

  if (!lessons.length) {
    container.innerHTML = `
      <div class="dec-empty">
        <strong>Chưa có lesson nào</strong>
        <span>Lesson xuất hiện sau khi chạy AI Replay cho một decision đã evaluate.</span>
      </div>
    `;
    return;
  }

  const grid = document.createElement('div');
  grid.className = 'lesson-grid';

  for (const l of lessons) {
    const typeInfo = TYPE_LABEL[l.decision_type] ?? { label: l.decision_type, cls: '' };
    const verdictCls = VERDICT_CLS[l.outcome_verdict] ?? '';

    const card = document.createElement('div');
    card.className = 'lesson-card';
    card.innerHTML = `
      <div class="lesson-card-head">
        <span class="dec-ticker">${esc(l.ticker)}</span>
        <span class="dec-type ${typeInfo.cls}">${typeInfo.label}</span>
        ${l.outcome_verdict ? `<span class="dec-verdict ${verdictCls}">${esc(l.outcome_verdict)}</span>` : ''}
        <span class="lesson-date">${fmtDate(l.decision_at)}</span>
      </div>
      <p class="lesson-text">${esc(l.key_lesson)}</p>
      ${l.pattern_detected ? `<div class="lesson-pattern">📌 Pattern: <em>${esc(l.pattern_detected)}</em></div>` : ''}
    `;
    grid.appendChild(card);
  }

  container.appendChild(grid);
}

// ── renderReplayPanel ─────────────────────────────────────────────────────────
/**
 * Render AI replay result vào inline expand panel.
 * @param {HTMLElement} wrap
 * @param {object} result  — ReplayResponse
 */
export function renderReplayPanel(wrap, result) {
  const wentRight = (result.what_went_right ?? []).map(s => `<li>${esc(s)}</li>`).join('');
  const wentWrong = (result.what_went_wrong ?? []).map(s => `<li>${esc(s)}</li>`).join('');

  const verdictCls = VERDICT_CLS[result.outcome_verdict] ?? '';
  const confidence = result.confidence != null ? `${Math.round(result.confidence * 100)}%` : '—';

  wrap.innerHTML = `
    <div class="replay-panel">
      <div class="replay-header">
        <strong>🧠 AI Replay Analysis</strong>
        ${result.outcome_verdict ? `<span class="dec-verdict ${verdictCls}">${esc(result.outcome_verdict)}</span>` : ''}
        <span class="replay-confidence">Confidence: ${confidence}</span>
        ${result.outcome_pnl_pct != null
          ? `<span class="dec-pnl ${result.outcome_pnl_pct >= 0 ? 'up' : 'down'}">${result.outcome_pnl_pct >= 0 ? '+' : ''}${Number(result.outcome_pnl_pct).toFixed(2)}%</span>`
          : ''}
      </div>
      <div class="replay-columns">
        ${wentRight ? `
          <div class="replay-col">
            <h4>✅ Đúng ở đâu</h4>
            <ul>${wentRight}</ul>
          </div>` : ''}
        ${wentWrong ? `
          <div class="replay-col">
            <h4>❌ Sai ở đâu</h4>
            <ul>${wentWrong}</ul>
          </div>` : ''}
      </div>
      ${result.key_lesson ? `<div class="replay-lesson"><strong>💡 Key Lesson:</strong> ${esc(result.key_lesson)}</div>` : ''}
      ${result.pattern_detected ? `<div class="replay-pattern">📌 Pattern: <em>${esc(result.pattern_detected)}</em></div>` : ''}
      ${result.suggested_adjustment ? `<div class="replay-adjust">🔧 Suggested adjustment: ${esc(result.suggested_adjustment)}</div>` : ''}
    </div>
  `;
  wrap.classList.remove('hidden');
}
