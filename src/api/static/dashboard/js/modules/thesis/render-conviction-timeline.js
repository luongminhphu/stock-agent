/**
 * render-conviction-timeline.js
 * Owner: modules/thesis
 * Responsibility: fetch + render Conviction Score Timeline cho thesis detail.
 *
 * Flow:
 *   1. thesis-service.js gọi loadConvictionTimeline(thesisId) sau khi detail HTML đã vào DOM.
 *   2. Module này fetch GET /api/v1/thesis/:id/conviction-timeline.
 *   3. Render SVG sparkline + breakdown mini-grid + trend badge vào #convictionTimelineSlot.
 *
 * Không chứa business logic — chỉ render dựa trên ConvictionTimelineResponse shape.
 */

import { esc, fmtDate, fmtScore, scoreClass } from '../../utils/format.js';
import { thesisApiBase, getJson } from '../../api/client.js';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const VERDICT_CLS = {
  BULLISH:   'bullish',
  BEARISH:   'bearish',
  NEUTRAL:   'neutral',
  WATCHLIST: 'watchlist',
};

const TREND_META = {
  improving:        { icon: '↑', label: 'Cải thiện',     cls: 'score-high' },
  declining:        { icon: '↓', label: 'Suy giảm',      cls: 'score-low' },
  stable:           { icon: '→', label: 'Ổn định',       cls: 'score-mid' },
  insufficient_data:{ icon: '—', label: 'Chưa đủ data',  cls: '' },
};

/**
 * Trả về HTML skeleton slot (trước khi data về).
 * @param {string|number} thesisId
 */
export function convictionTimelineSlotHTML(thesisId) {
  return `<div id="convictionTimelineSlot-${thesisId}" data-thesis-id="${thesisId}"></div>`;
}

// ---------------------------------------------------------------------------
// SVG Sparkline
// ---------------------------------------------------------------------------

/**
 * Vẽ SVG sparkline từ mảng điểm số.
 * @param {number[]} scores  — mảng score 0-100, oldest first
 * @param {string}   trend
 */
function renderSparkline(scores, trend) {
  if (!scores.length) return '';

  const W = 320, H = 72, PAD = 8;
  const minS = Math.max(0,  Math.min(...scores) - 5);
  const maxS = Math.min(100, Math.max(...scores) + 5);
  const range = maxS - minS || 1;

  const xStep = scores.length > 1 ? (W - PAD * 2) / (scores.length - 1) : 0;
  const toX = i  => PAD + i * xStep;
  const toY = s  => H - PAD - ((s - minS) / range) * (H - PAD * 2);

  const pts = scores.map((s, i) => `${toX(i).toFixed(1)},${toY(s).toFixed(1)}`).join(' ');
  const areaBottom = `${toX(scores.length-1).toFixed(1)},${H - PAD} ${toX(0).toFixed(1)},${H - PAD}`;

  const strokeColor = trend === 'improving' ? 'var(--success, #6daa45)'
                    : trend === 'declining'  ? 'var(--danger,  #a12c7b)'
                    : 'var(--accent, #4f98a3)';

  const gradId = `cg-${Math.random().toString(36).slice(2, 7)}`;

  return `
    <svg viewBox="0 0 ${W} ${H}" width="100%" height="${H}"
         aria-hidden="true" style="display:block;overflow:visible;">
      <defs>
        <linearGradient id="${gradId}" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%"   stop-color="${strokeColor}" stop-opacity="0.25"/>
          <stop offset="100%" stop-color="${strokeColor}" stop-opacity="0"/>
        </linearGradient>
      </defs>
      <polygon
        points="${pts} ${areaBottom}"
        fill="url(#${gradId})"
        stroke="none"
      />
      <polyline
        points="${pts}"
        fill="none"
        stroke="${strokeColor}"
        stroke-width="2"
        stroke-linejoin="round"
        stroke-linecap="round"
      />
      ${scores.map((s, i) => i === scores.length - 1 ? `
        <circle cx="${toX(i).toFixed(1)}" cy="${toY(s).toFixed(1)}" r="3.5"
          fill="${strokeColor}" stroke="var(--surface, #1c1b19)" stroke-width="1.5"/>
      ` : '').join('')}
    </svg>`;
}

// ---------------------------------------------------------------------------
// Breakdown mini-grid
// ---------------------------------------------------------------------------

const BREAKDOWN_ROWS = [
  { key: 'assumption_health', label: 'Assumptions',  max: 40 },
  { key: 'catalyst_progress', label: 'Catalysts',    max: 30 },
  { key: 'risk_reward',       label: 'Risk/Reward',  max: 20 },
  { key: 'review_confidence', label: 'AI Confidence',max: 10 },
];

function renderBreakdownGrid(breakdown) {
  if (!breakdown) return '<p class="empty-state" style="font-size:.8rem;">Chưa có breakdown data.</p>';
  return BREAKDOWN_ROWS.map(r => {
    const val  = Number(breakdown[r.key] ?? 0);
    const pct  = Math.round((val / r.max) * 100);
    const cls  = scoreClass(pct);
    return `
      <div style="display:flex;align-items:center;gap:8px;margin-bottom:6px;">
        <span style="width:100px;font-size:.78rem;color:var(--muted);flex-shrink:0;">${r.label}</span>
        <div style="flex:1;height:6px;border-radius:999px;background:rgba(255,255,255,.08);overflow:hidden;">
          <div class="${cls}" style="height:100%;width:${pct}%;border-radius:999px;background:currentColor;transition:width .4s ease;"></div>
        </div>
        <span class="${cls}" style="width:40px;text-align:right;font-size:.78rem;font-weight:700;">${val}/${r.max}</span>
      </div>`;
  }).join('');
}

// ---------------------------------------------------------------------------
// Main render
// ---------------------------------------------------------------------------

/**
 * Render toàn bộ conviction timeline section.
 * @param {object} data  ConvictionTimelineResponse
 */
export function renderConvictionTimeline(data) {
  if (!data || !Array.isArray(data.points)) return '';

  const points = data.points;               // oldest → newest
  const latest = points[points.length - 1]; // newest point
  const trend  = data.trend ?? 'insufficient_data';
  const tm     = TREND_META[trend] ?? TREND_META.insufficient_data;

  if (!points.length) {
    return `
      <div class="detail-section">
        <div class="detail-section-header"><h3>Conviction Timeline</h3></div>
        <p class="empty-state">Chưa có snapshot nào. Trigger AI review để tạo điểm dữ liệu đầu tiên.</p>
      </div>`;
  }

  const scores   = points.map(p => Number(p.score ?? 0));
  const sparkSVG = renderSparkline(scores, trend);

  // Latest breakdown — dùng breakdown của latest point
  const latestBreakdown = latest?.breakdown ?? null;

  // Verdict badges — 5 latest points, newest first
  const badgePoints = [...points].reverse().slice(0, 5);

  return `
    <div class="detail-section" id="convictionTimelineSection">
      <div class="detail-section-header" style="align-items:flex-end;gap:12px;">
        <div>
          <h3>Conviction Timeline</h3>
          <p class="muted" style="font-size:.78rem;margin-top:2px;">
            ${data.total} data-point · ${esc(data.ticker)}
          </p>
        </div>
        <span class="badge ${tm.cls}" style="margin-left:auto;font-size:.8rem;padding:4px 10px;">
          ${tm.icon} ${tm.label}
        </span>
      </div>

      <!-- Sparkline -->
      <div style="margin:10px 0 14px;">
        ${sparkSVG}
        <div style="display:flex;justify-content:space-between;margin-top:4px;">
          <span style="font-size:.72rem;color:var(--muted);">${fmtDate(points[0]?.snapshotted_at)}</span>
          <span style="font-size:.72rem;color:var(--muted);">${fmtDate(latest?.snapshotted_at)}</span>
        </div>
      </div>

      <!-- Verdict badges (5 gần nhất) -->
      <div style="display:flex;flex-wrap:wrap;gap:6px;margin-bottom:14px;">
        ${badgePoints.map(p => {
          const vCls  = VERDICT_CLS[String(p.verdict ?? '').toUpperCase()] ?? 'neutral';
          const confPct = p.confidence != null ? Math.round(p.confidence * 100) : null;
          return `
            <div style="display:flex;flex-direction:column;align-items:center;gap:3px;">
              <span class="badge ${vCls}" style="font-size:.72rem;padding:3px 8px;">
                ${esc(String(p.verdict ?? 'N/A').toUpperCase())}
              </span>
              <span style="font-size:.68rem;color:var(--muted);">
                ${fmtDate(p.snapshotted_at)}
                ${confPct != null ? `· ${confPct}%` : ''}
              </span>
            </div>`;
        }).join('')}
      </div>

      <!-- Latest breakdown bars -->
      <div>
        <p class="suggest-section-title" style="margin-bottom:8px;">Score breakdown — lần review gần nhất</p>
        ${renderBreakdownGrid(latestBreakdown)}
      </div>

      <!-- Score delta -->
      ${data.earliest_score != null && data.latest_score != null ? (() => {
        const delta = Number(data.latest_score) - Number(data.earliest_score);
        const sign  = delta > 0 ? '+' : '';
        const cls   = delta > 0 ? 'score-high' : delta < 0 ? 'score-low' : '';
        return `
          <div style="margin-top:12px;padding-top:10px;border-top:1px solid var(--border, rgba(255,255,255,.08));
                      display:flex;align-items:center;gap:10px;">
            <span style="font-size:.82rem;color:var(--muted);">Thay đổi từ đầu:</span>
            <span class="${cls}" style="font-weight:700;font-size:.9rem;">
              ${sign}${delta.toFixed(1)} điểm
            </span>
            <span style="font-size:.82rem;color:var(--muted);margin-left:auto;">
              ${fmtScore(data.earliest_score)} → ${fmtScore(data.latest_score)}
            </span>
          </div>`;
      })() : ''}
    </div>`;
}

// ---------------------------------------------------------------------------
// Loader — gọi từ thesis-service.js
// ---------------------------------------------------------------------------

/**
 * Fetch conviction timeline và inject vào slot.
 * Thiết kế: fire-and-forget, không block render chính.
 * @param {string|number} thesisId
 */
export async function loadConvictionTimeline(thesisId) {
  const slot = document.getElementById(`convictionTimelineSlot-${thesisId}`);
  if (!slot) return;

  // Skeleton
  slot.innerHTML = `
    <div class="detail-section" aria-busy="true">
      <div class="detail-section-header"><h3>Conviction Timeline</h3></div>
      <div style="margin:12px 0;">
        <div class="skel" style="height:72px;border-radius:6px;"></div>
      </div>
      <div style="display:flex;gap:8px;flex-wrap:wrap;">
        ${[1,2,3].map(() => '<div class="skel skel-badge" style="width:64px;"></div>').join('')}
      </div>
    </div>`;

  try {
    const data = await getJson(`${thesisApiBase()}/${thesisId}/conviction-timeline?limit=20`);
    if (!data) {
      slot.innerHTML = '';
      return;
    }
    slot.innerHTML = renderConvictionTimeline(data);
  } catch (err) {
    // Silent degradation — conviction timeline không phải critical path
    slot.innerHTML = `
      <div class="detail-section">
        <div class="detail-section-header"><h3>Conviction Timeline</h3></div>
        <p class="empty-state" style="font-size:.8rem;">Chưa tải được timeline: ${esc(err.message)}</p>
      </div>`;
  }
}
