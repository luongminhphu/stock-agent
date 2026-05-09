/**
 * leaderboard-loader.js
 * Owner: modules/dashboard
 * Responsibility: fetch /readmodel/leaderboard và render top-5 thesis cards.
 *
 * Exports:
 *   loadLeaderboard()  — fetch + render, idempotent
 */

import { getJson } from '../../api/client.js';
import { esc } from '../../utils/format.js';

const SCORE_CLS = score =>
  score >= 75 ? 'lb-score-high'
  : score >= 50 ? 'lb-score-mid'
  : 'lb-score-low';

const VERDICT_ICON = {
  BULLISH:      '🟢',
  BEARISH:      '🔴',
  NEUTRAL:      '⚪',
  WATCH:        '👀',
  ACCUMULATE:   '📈',
  REDUCE:       '📉',
  HOLD:         '🤝',
};

function renderLeaderboardCards(wrap, items) {
  wrap.innerHTML = '';

  if (!items.length) {
    wrap.innerHTML = `
      <div class="lb-empty">
        <strong>Chưa có dữ liệu leaderboard</strong>
        <span>Leaderboard xuất hiện sau khi có AI review cho ít nhất 1 thesis.</span>
      </div>`;
    return;
  }

  const grid = document.createElement('div');
  grid.className = 'lb-grid';

  items.slice(0, 5).forEach((t, idx) => {
    const score   = t.composite_score ?? t.score ?? null;
    const verdict = t.latest_verdict  ?? null;
    const vicon   = verdict ? (VERDICT_ICON[verdict.toUpperCase()] ?? '•') : '•';
    const scoreCls = score != null ? SCORE_CLS(score) : '';
    const pnl      = t.avg_pnl_pct ?? null;
    const pnlHtml  = pnl != null
      ? `<span class="lb-pnl ${pnl >= 0 ? 'up' : 'down'}">${pnl >= 0 ? '+' : ''}${Number(pnl).toFixed(1)}%</span>`
      : '';

    const card = document.createElement('div');
    card.className = 'lb-card';
    card.innerHTML = `
      <div class="lb-rank">#${idx + 1}</div>
      <div class="lb-body">
        <div class="lb-top">
          <span class="lb-ticker">${esc(t.ticker ?? '')}</span>
          ${verdict ? `<span class="lb-verdict">${vicon} ${esc(verdict)}</span>` : ''}
          ${pnlHtml}
        </div>
        <div class="lb-title">${esc(t.title ?? t.ticker ?? '')}</div>
        ${t.thesis_health_label ? `<div class="lb-health">${esc(t.thesis_health_label)}</div>` : ''}
      </div>
      ${score != null
        ? `<div class="lb-score-wrap"><span class="lb-score ${scoreCls}">${Math.round(score)}</span><span class="lb-score-label">score</span></div>`
        : ''}`;
    grid.appendChild(card);
  });

  wrap.appendChild(grid);
}

export async function loadLeaderboard() {
  const wrap = document.getElementById('leaderboardWrap');
  if (!wrap) return;

  wrap.innerHTML = '<p class="loading-text">Đang tải leaderboard…</p>';

  try {
    const res   = await getJson('/api/v1/readmodel/leaderboard?sort_by=score&limit=5');
    const items = Array.isArray(res) ? res : (res?.items ?? []);
    renderLeaderboardCards(wrap, items);
  } catch (err) {
    wrap.innerHTML = `<p class="error-text">Lỗi tải leaderboard: ${err.message}</p>`;
  }
}
