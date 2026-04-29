import { scoreClass, fmtScore, pct } from '../../utils/format.js';

/**
 * Render score breakdown bar chart cho thesis detail.
 * @param {object|null} breakdown
 * @returns {string} HTML string
 */
export function renderScoreBreakdown(breakdown) {
  if (!breakdown) return '';

  const rows = [
    { key: 'assumption_health',  label: 'Assumptions',        value: breakdown.assumption_health,  max: 40 },
    { key: 'catalyst_progress',  label: 'Catalysts',          value: breakdown.catalyst_progress,  max: 30 },
    { key: 'risk_reward',        label: 'Risk / Reward',      value: breakdown.risk_reward,        max: 20 },
    { key: 'review_confidence',  label: 'Review confidence',  value: breakdown.review_confidence,  max: 10 },
  ];

  return `
    <div class="detail-section">
      <div class="detail-section-header">
        <h3>Score breakdown</h3>
        <span style="color:var(--muted);font-size:.82rem;">4 thành phần đóng góp vào health score</span>
      </div>
      <div class="detail-list">
        ${rows.map(r => `
          <div class="detail-item">
            <div class="detail-item-row">
              <span style="font-weight:600;font-size:.9rem;">${r.label}</span>
              <span class="${scoreClass((Number(r.value || 0) / r.max) * 100)}" style="font-weight:700;">${fmtScore(r.value)}/${r.max}</span>
            </div>
            <div style="margin-top:8px;height:8px;border-radius:999px;background:rgba(255,255,255,.08);overflow:hidden;">
              <div class="${scoreClass((Number(r.value || 0) / r.max) * 100)}" style="height:100%;width:${pct(r.value, r.max)}%;border-radius:999px;background:currentColor;"></div>
            </div>
          </div>
        `).join('')}
      </div>
    </div>`;
}
