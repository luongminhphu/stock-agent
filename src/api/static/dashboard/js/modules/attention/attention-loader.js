/**
 * attention-loader.js — AttentionPanel module
 * Segment owner: readmodel
 * Endpoint: GET /api/v1/readmodel/dashboard/attention
 *
 * Renders panel "Việc cần làm hôm nay" vào #actionSurface.
 * Items grouped: critical → high → medium.
 * Auto-refresh mỗi 5 phút.
 */

import { apiBase } from '../../api/client.js';

const REFRESH_INTERVAL_MS = 5 * 60 * 1000; // 5 phút
let _refreshTimer = null;

// ---------------------------------------------------------------------------
// API
// ---------------------------------------------------------------------------

async function fetchAttentionPanel(limit = 20) {
  const res = await fetch(`${apiBase()}/attention?limit=${limit}`, {
    headers: { 'Content-Type': 'application/json' },
  });
  if (!res.ok) throw new Error(`attention ${res.status}`);
  return res.json();
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const KIND_META = {
  triggered_alert:    { icon: '🔔', label: 'Alert kích hoạt' },
  stop_loss_proximity:{ icon: '🛑', label: 'Stop-loss gần' },
  overdue_review:     { icon: '📋', label: 'Cần review' },
  upcoming_catalyst:  { icon: '📅', label: 'Catalyst sắp tới' },
};

const URGENCY_LABEL = {
  critical: { text: 'Khẩn',      cls: 'attn-badge--critical' },
  high:     { text: 'Hôm nay',   cls: 'attn-badge--high' },
  medium:   { text: 'Theo dõi',  cls: 'attn-badge--medium' },
};

function fmtRelTime(isoStr) {
  if (!isoStr) return '';
  const diff = Date.now() - new Date(isoStr).getTime();
  const mins = Math.floor(diff / 60_000);
  if (mins < 60)  return `${mins}p trước`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24)   return `${hrs}h trước`;
  return `${Math.floor(hrs / 24)}d trước`;
}

function renderMeta(item) {
  const parts = [];
  if (item.metadata?.days_overdue)    parts.push(`quá hạn ${item.metadata.days_overdue} ngày`);
  if (item.metadata?.distance_pct != null) parts.push(`cách stop ${item.metadata.distance_pct.toFixed(1)}%`);
  if (item.metadata?.hours_until != null)  parts.push(`còn ${item.metadata.hours_until}h`);
  return parts.join(' · ');
}

// ---------------------------------------------------------------------------
// Render
// ---------------------------------------------------------------------------

function renderItem(item) {
  const { icon, label } = KIND_META[item.kind] ?? { icon: '⚡', label: item.kind };
  const urgency = URGENCY_LABEL[item.urgency] ?? { text: item.urgency, cls: 'attn-badge--medium' };
  const meta    = renderMeta(item);
  const relTime = fmtRelTime(item.ts);
  const thesisAttr = item.thesis_id ? `data-thesis-id="${item.thesis_id}"` : '';
  const clickable   = item.thesis_id ? 'attn-item--clickable' : '';

  return `
    <li class="attn-item ${clickable}" data-urgency="${item.urgency}" ${thesisAttr}
        role="${item.thesis_id ? 'button' : 'listitem'}" tabindex="${item.thesis_id ? '0' : '-1'}"
        aria-label="${label}: ${item.ticker} — ${item.message}">
      <span class="attn-item__icon" aria-hidden="true">${icon}</span>
      <div class="attn-item__body">
        <div class="attn-item__top">
          <span class="attn-item__ticker">${item.ticker}</span>
          <span class="attn-item__msg">${item.message}</span>
        </div>
        ${meta ? `<div class="attn-item__meta">${meta}</div>` : ''}
      </div>
      <div class="attn-item__aside">
        <span class="attn-badge ${urgency.cls}">${urgency.text}</span>
        ${relTime ? `<span class="attn-item__time">${relTime}</span>` : ''}
      </div>
    </li>`;
}

function renderGroup(urgency, items) {
  if (!items.length) return '';
  const label = URGENCY_LABEL[urgency]?.text ?? urgency;
  const listId = `attnGroup-${urgency}`;
  return `
    <div class="attn-group" data-urgency="${urgency}">
      <div class="attn-group__header">
        <span class="attn-group__label">${label}</span>
        <span class="attn-group__count">${items.length}</span>
      </div>
      <ul class="attn-group__list" id="${listId}" aria-label="${label} items">
        ${items.map(renderItem).join('')}
      </ul>
    </div>`;
}

function renderSkeleton() {
  return Array.from({ length: 3 }, (_, i) => `
    <li class="attn-item attn-item--skel" aria-hidden="true">
      <div class="skel" style="width:20px;height:20px;border-radius:4px;flex-shrink:0"></div>
      <div style="flex:1;display:flex;flex-direction:column;gap:5px">
        <div class="skel" style="width:${60 + i * 20}px;height:.75rem"></div>
        <div class="skel" style="width:${100 + i * 30}px;height:.7rem"></div>
      </div>
      <div class="skel" style="width:48px;height:1.1rem;border-radius:20px"></div>
    </li>`).join('');
}

function renderEmpty() {
  return `
    <table class="attn-empty-table" role="presentation">
      <tbody>
        <tr>
          <td class="attn-empty-table__cell">
            <div class="attn-empty__icon" aria-hidden="true">✅</div>
            <div class="attn-empty__text">Không có việc cần làm hôm nay.</div>
            <div class="attn-empty__hint">Hệ thống sẽ cảnh báo khi có alert, stop-loss gần, thesis overdue hoặc catalyst sắp tới.</div>
          </td>
        </tr>
      </tbody>
    </table>`;
}

// ---------------------------------------------------------------------------
// Mount / update
// ---------------------------------------------------------------------------

function getContainer() {
  return document.getElementById('actionSurface');
}

function showSkeleton(container) {
  container.classList.remove('hidden');
  container.innerHTML = `
    <div class="attn-panel">
      <div class="attn-panel__header">
        <span class="attn-panel__title">⚡ Việc cần làm hôm nay</span>
      </div>
      <ul class="attn-group__list" aria-busy="true" aria-label="Đang tải…">
        ${renderSkeleton()}
      </ul>
    </div>`;
}

function mount(data, container) {
  const items = data?.items ?? [];
  const generatedAt = data?.generated_at
    ? new Date(data.generated_at).toLocaleTimeString('vi-VN', { hour: '2-digit', minute: '2-digit' })
    : '';

  if (!items.length) {
    container.classList.remove('hidden');
    container.innerHTML = `
      <div class="attn-panel">
        <div class="attn-panel__header">
          <span class="attn-panel__title">⚡ Việc cần làm hôm nay</span>
          ${generatedAt ? `<span class="attn-panel__ts">cập nhật ${generatedAt}</span>` : ''}
        </div>
        ${renderEmpty()}
      </div>`;
    return;
  }

  const critical = items.filter(i => i.urgency === 'critical');
  const high     = items.filter(i => i.urgency === 'high');
  const medium   = items.filter(i => i.urgency === 'medium');

  container.classList.remove('hidden');
  container.innerHTML = `
    <div class="attn-panel">
      <div class="attn-panel__header">
        <span class="attn-panel__title">⚡ Việc cần làm hôm nay</span>
        <span class="attn-panel__count">${items.length} mục</span>
        ${generatedAt ? `<span class="attn-panel__ts">cập nhật ${generatedAt}</span>` : ''}
      </div>
      <div class="attn-panel__body">
        ${renderGroup('critical', critical)}
        ${renderGroup('high',     high)}
        ${renderGroup('medium',   medium)}
      </div>
    </div>`;

  // Wire click: clickable items → dispatch navigate:thesis
  container.querySelectorAll('.attn-item--clickable').forEach(el => {
    const thesisId = parseInt(el.dataset.thesisId, 10);
    if (!thesisId) return;

    const handle = () => {
      document.dispatchEvent(new CustomEvent('navigate:thesis', { detail: { thesisId } }));
      document.getElementById('thesesTableWrap')
        ?.scrollIntoView({ behavior: 'smooth', block: 'start' });
    };
    el.addEventListener('click', handle);
    el.addEventListener('keydown', e => {
      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); handle(); }
    });
  });
}

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

export async function loadAttentionPanel() {
  const container = getContainer();
  if (!container) return;

  showSkeleton(container);

  try {
    const data = await fetchAttentionPanel(20);
    mount(data, container);
  } catch (err) {
    console.warn('[attention] fetch failed:', err.message);
    // Fail gracefully — hide panel rather than show error in prime real estate
    container.classList.add('hidden');
  }
}

/**
 * startAttentionAutoRefresh — gọi một lần trong bootstrap.
 * Tự refresh mỗi 5 phút để attention items luôn current.
 */
export function startAttentionAutoRefresh() {
  if (_refreshTimer) clearInterval(_refreshTimer);
  _refreshTimer = setInterval(loadAttentionPanel, REFRESH_INTERVAL_MS);
}
