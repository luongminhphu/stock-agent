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
        <span>Log một decision đầu tiên để bắt đầu theo dõi.</span>
      </div>
    `;
    return;
  }

  const table = document.createElement('table');
  table.className = 'dec-table';
  table.innerHTML = `
    <thead>
      <tr>
        <th>Ticker</th>
        <th>Loại</th>
        <th>Ngày</th>
        <th>Giá</th>
        <th>PnL</th>
        <th>Verdict</th>
        <th>Thesis</th>
        <th></th>
      </tr>
    </thead>
  `;

  const tbody = document.createElement('tbody');

  for (const d of decisions) {
    const typeInfo   = TYPE_LABEL[d.decision_type] ?? { label: d.decision_type, cls: '' };
    const verdictCls = VERDICT_CLS[d.outcome_verdict] ?? '';
    const pnl        = d.outcome_pnl_pct != null
      ? `<span class="dec-pnl ${d.outcome_pnl_pct >= 0 ? 'up' : 'down'}">${d.outcome_pnl_pct >= 0 ? '+' : ''}${Number(d.outcome_pnl_pct).toFixed(2)}%</span>`
      : '—';
    const evaluated  = !!d.outcome_evaluated_at;
    const due        = !evaluated
      ? new Date(d.decision_at).getTime() + (d.review_horizon_days ?? 30) * 86_400_000 < Date.now()
      : false;

    const row = document.createElement('tr');
    row.dataset.id = d.id;
    if (due) row.classList.add('dec-row--overdue');

    row.innerHTML = `
      <td class="dec-ticker">${esc(d.ticker)}</td>
      <td><span class="dec-type ${typeInfo.cls}">${typeInfo.label}</span></td>
      <td class="dec-date">${fmtDate(d.decision_at)}</td>
      <td class="dec-price">${d.entry_price != null ? fmt(d.entry_price, { decimals: 1 }) : '—'}</td>
      <td>${pnl}</td>
      <td>${d.outcome_verdict ? `<span class="dec-verdict ${verdictCls}">${esc(d.outcome_verdict)}</span>` : '<span class="muted">chưa</span>'}</td>
      <td class="dec-thesis-col">${d.thesis_title ? `<span class="dec-thesis-link" title="${esc(d.thesis_title)}">${esc(d.thesis_title.slice(0, 24))}${d.thesis_title.length > 24 ? '…' : ''}</span>` : '<span class="muted">—</span>'}</td>
      <td class="dec-actions">
        ${!evaluated ? `<button class="btn-sm btn-evaluate" data-id="${d.id}" title="Evaluate outcome">Eval</button>` : ''}
        <button class="btn-sm btn-replay" data-id="${d.id}" title="AI Replay">Replay</button>
      </td>
    `;

    // Inject replay panel placeholder below row
    const replayRow = document.createElement('tr');
    replayRow.className = 'dec-replay-row hidden';
    replayRow.dataset.replayFor = d.id;
    replayRow.innerHTML = `<td colspan="8"><div class="dec-replay-wrap"></div></td>`;

    tbody.appendChild(row);
    tbody.appendChild(replayRow);

    // Wire evaluate
    const evalBtn = row.querySelector('.btn-evaluate');
    if (evalBtn && onEvaluate) {
      evalBtn.addEventListener('click', () => onEvaluate(d.id, row));
    }

    // Wire replay
    const replayBtn = row.querySelector('.btn-replay');
    if (replayBtn && onReplay) {
      replayBtn.addEventListener('click', () => onReplay(d.id, replayRow));
    }
  }

  table.appendChild(tbody);
  container.appendChild(table);
}

// ── renderLessonsCards ────────────────────────────────────────────────────────
/**
 * Render AI lesson cards vào container.
 * Fields rendered: key_lesson, pattern_detected, suggested_adjustment,
 *   confidence, outcome_pnl_pct, what_went_right, what_went_wrong.
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
    const typeInfo   = TYPE_LABEL[l.decision_type] ?? { label: l.decision_type, cls: '' };
    const verdictCls = VERDICT_CLS[l.outcome_verdict] ?? '';
    const confidence = l.confidence != null ? `${Math.round(l.confidence * 100)}%` : null;
    const pnl        = l.outcome_pnl_pct != null
      ? `<span class="dec-pnl ${l.outcome_pnl_pct >= 0 ? 'up' : 'down'}">${l.outcome_pnl_pct >= 0 ? '+' : ''}${Number(l.outcome_pnl_pct).toFixed(1)}%</span>`
      : '';

    const wentRight = (l.what_went_right ?? []).map(s => `<li>${esc(s)}</li>`).join('');
    const wentWrong = (l.what_went_wrong ?? []).map(s => `<li>${esc(s)}</li>`).join('');

    const card = document.createElement('div');
    card.className = 'lesson-card';
    card.innerHTML = `
      <div class="lesson-card-head">
        <span class="dec-ticker">${esc(l.ticker)}</span>
        <span class="dec-type ${typeInfo.cls}">${typeInfo.label}</span>
        ${l.outcome_verdict ? `<span class="dec-verdict ${verdictCls}">${esc(l.outcome_verdict)}</span>` : ''}
        ${pnl}
        ${confidence ? `<span class="lesson-conf muted">${confidence}</span>` : ''}
        <span class="lesson-date">${fmtDate(l.decision_at)}</span>
      </div>
      <p class="lesson-text">${esc(l.key_lesson ?? '—')}</p>
      ${l.pattern_detected
        ? `<div class="lesson-pattern">📌 <em>${esc(l.pattern_detected)}</em></div>`
        : ''}
      ${(wentRight || wentWrong) ? `
        <div class="lesson-columns">
          ${wentRight ? `<div class="lesson-col lesson-col--right"><span class="lesson-col-label">✅ Đúng</span><ul>${wentRight}</ul></div>` : ''}
          ${wentWrong ? `<div class="lesson-col lesson-col--wrong"><span class="lesson-col-label">❌ Sai</span><ul>${wentWrong}</ul></div>` : ''}
        </div>` : ''}
      ${l.suggested_adjustment
        ? `<div class="lesson-adjust">🔧 <strong>Điều chỉnh:</strong> ${esc(l.suggested_adjustment)}</div>`
        : ''}
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
