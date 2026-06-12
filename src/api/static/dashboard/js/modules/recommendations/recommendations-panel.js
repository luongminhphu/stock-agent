/**
 * recommendations-panel.js
 * Owner: modules/recommendations
 * Responsibility: fetch GET /api/v1/readmodel/dashboard/recommendations
 *   → render Khuyến nghị panel (verdict header + priority action cards + risk flags).
 *
 * Data source: IntelligenceSnapshotStore — updated after each engine cycle.
 * Auto-refresh: 5 min (same as engine heartbeat).
 *
 * Render contract:
 *   - Panel container: #recommendationsSection
 *   - Verdict header: #recVerdictHeader
 *   - Action cards list: #recActionList
 *   - Risk flags: #recRiskList
 *   - Watch list: #recWatchList
 *   - Generated-at timestamp: #recGeneratedAt
 *   - Stale badge: #recStaleBadge
 */

import { readmodelApiBase, getJson } from '../../api/client.js';

const _API_URL       = `${readmodelApiBase()}/dashboard/recommendations`;
const _POLL_MS       = 5 * 60 * 1000;  // 5 phút

// ── Verdict config ────────────────────────────────────────────────────────────

const _VERDICT_LABEL = {
  BUY_SIGNAL:    { text: 'MUA',          cls: 'rec-verdict--buy',    icon: '📈' },
  SELL_SIGNAL:   { text: 'BÁN',          cls: 'rec-verdict--sell',   icon: '📉' },
  HOLD:          { text: 'GIỮ',          cls: 'rec-verdict--hold',   icon: '⏸' },
  REVIEW_THESIS: { text: 'XEM LẠI THESIS', cls: 'rec-verdict--review', icon: '🔍' },
  RISK_ALERT:    { text: 'CẢNH BÁO RỦI RO', cls: 'rec-verdict--risk', icon: '⚠️' },
  NO_ACTION:     { text: 'CHƯA CÓ TÍN HIỆU', cls: 'rec-verdict--none', icon: '—' },
};

const _ACTION_TYPE_LABEL = {
  REVIEW_THESIS:  { icon: '🔍', label: 'Review thesis' },
  CHECK_STOP_LOSS:{ icon: '🛑', label: 'Kiểm tra SL'  },
  CONSIDER_EXIT:  { icon: '🚪', label: 'Xem xét thoát'},
  CONSIDER_ENTRY: { icon: '🎯', label: 'Xem xét vào'  },
  MONITOR:        { icon: '👁',  label: 'Theo dõi'     },
  NO_ACTION:      { icon: '—',  label: 'Không cần làm' },
};

const _URGENCY_LABEL = {
  immediate: { text: 'Ngay bây giờ', cls: 'urgency--immediate' },
  today:     { text: 'Hôm nay',      cls: 'urgency--today'     },
  this_week: { text: 'Tuần này',     cls: 'urgency--week'      },
  medium:    { text: 'Theo dõi',     cls: 'urgency--week'      },
  low:       { text: 'Theo dõi',     cls: 'urgency--week'      },
};

const _SEVERITY_CLS = { HIGH: 'risk--high', MEDIUM: 'risk--medium', LOW: 'risk--low' };

// ── Public API ─────────────────────────────────────────────────────────────────

export async function loadRecommendations() {
  const root = document.getElementById('recommendationsPanelBody');
  if (!root) return;

  try {
    const data = await getJson(_API_URL);
    _render(root, data);
  } catch (err) {
    _renderError(root, err);
  }
}

export function startRecommendationsAutoRefresh() {
  setInterval(loadRecommendations, _POLL_MS);
}

// ── Render ─────────────────────────────────────────────────────────────────────

function _render(root, data) {
  const isFresh   = data?.is_fresh ?? false;
  const isStale   = data?.is_stale ?? true;
  const verdict   = (data?.overall_verdict ?? 'NO_ACTION').toUpperCase();
  const conf      = data?.confidence ?? null;
  const context   = data?.market_context ?? null;
  const actions   = data?.priority_actions ?? [];
  const risks     = data?.risk_flags ?? [];
  const watchList = data?.watch_list ?? [];
  const genAt     = data?.generated_at ?? null;

  const vCfg = _VERDICT_LABEL[verdict] ?? _VERDICT_LABEL['NO_ACTION'];

  // format timestamp
  const timeStr = genAt
    ? new Date(genAt).toLocaleString('vi-VN', { dateStyle: 'short', timeStyle: 'short' })
    : null;

  root.innerHTML = `
    <!-- ── Verdict header ── -->
    <div class="rec-verdict-header ${vCfg.cls}">
      <div class="rec-verdict-left">
        <span class="rec-verdict-icon" aria-hidden="true">${vCfg.icon}</span>
        <div>
          <span class="rec-verdict-label">${vCfg.text}</span>
          ${conf !== null ? `<span class="rec-confidence">Độ tin cậy: ${Math.round(conf * 100)}%</span>` : ''}
        </div>
      </div>
      <div class="rec-verdict-right">
        ${isStale ? '<span class="rec-stale-badge">Dữ liệu cũ</span>' : ''}
        ${!isFresh ? '<span class="rec-stale-badge">Engine chưa chạy</span>' : ''}
        ${timeStr ? `<span class="rec-generated-at">Cập nhật: ${timeStr}</span>` : ''}
      </div>
    </div>

    ${context ? `<p class="rec-market-context">${_esc(context)}</p>` : ''}

    <!-- ── Priority actions ── -->
    <div class="rec-section-label">
      <span>Hành động ưu tiên</span>
      <span class="rec-count">${actions.length}</span>
    </div>
    ${actions.length === 0
      ? `<div class="rec-empty-state">
           ${isFresh
             ? 'Engine đã chạy — không có hành động ưu tiên lúc này.'
             : 'Engine chưa chạy hôm nay. Hành động ưu tiên sẽ xuất hiện sau khi engine hoàn thành chu kỳ đầu tiên.'}
         </div>`
      : `<ol class="rec-action-list">${actions.map(_buildActionCard).join('')}</ol>`
    }

    <!-- ── Risk flags ── -->
    ${risks.length > 0 ? `
      <div class="rec-section-label">
        <span>Tín hiệu rủi ro</span>
        <span class="rec-count">${risks.length}</span>
      </div>
      <ul class="rec-risk-list">
        ${risks.map(r => `
          <li class="rec-risk-item ${_SEVERITY_CLS[r.severity?.toUpperCase()] ?? 'risk--low'}">
            <span class="rec-risk-dot"></span>
            <span>${_esc(r.description ?? '')}</span>
          </li>`).join('')}
      </ul>` : ''}

    <!-- ── Watch list ── -->
    ${watchList.length > 0 ? `
      <div class="rec-section-label">
        <span>Cần theo dõi tiếp</span>
      </div>
      <div class="rec-watch-chips">
        ${watchList.map(t => `<span class="rec-watch-chip">${_esc(t)}</span>`).join('')}
      </div>` : ''}
  `;
}

function _buildActionCard(action, idx) {
  const ticker    = action.ticker ?? null;
  const typeKey   = (action.action_type ?? action.action_text ?? 'MONITOR').toUpperCase();
  const urgKey    = (action.urgency ?? 'this_week').toLowerCase();
  const urgCfg    = _URGENCY_LABEL[urgKey] ?? _URGENCY_LABEL['this_week'];
  const typeCfg   = _ACTION_TYPE_LABEL[typeKey] ?? { icon: '•', label: typeKey };
  const instruction = action.instruction ?? action.action_text ?? '';
  const reasoning   = action.reasoning ?? '';

  return `
    <li class="rec-action-card">
      <div class="rec-action-rank">${idx + 1}</div>
      <div class="rec-action-body">
        <div class="rec-action-header">
          ${ticker ? `<span class="rec-ticker-badge">${_esc(ticker)}</span>` : ''}
          <span class="rec-action-type">${typeCfg.icon} ${typeCfg.label}</span>
          <span class="rec-urgency-chip ${urgCfg.cls}">${urgCfg.text}</span>
        </div>
        ${instruction ? `<p class="rec-action-instruction">${_esc(instruction)}</p>` : ''}
        ${reasoning   ? `<p class="rec-action-reasoning">${_esc(reasoning)}</p>` : ''}
      </div>
    </li>`;
}

function _renderError(root, err) {
  root.innerHTML = `
    <div class="rec-empty-state rec-empty-state--error">
      Không thể tải khuyến nghị — <span class="cell-error">${_esc(String(err?.message ?? err))}</span>
    </div>`;
}

function _esc(str) {
  return String(str ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}
