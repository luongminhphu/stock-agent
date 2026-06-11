/**
 * engine-heartbeat.js
 * Owner: modules/engine (observability concern)
 * Responsibility: poll GET /core/snapshot → hiển thị engine status badge trong topbar.
 *
 * Badge states:
 *   IDLE    — snapshot tồn tại, captured_at <= STALE_THRESHOLD phút trước
 *   STALE   — captured_at quá STALE_THRESHOLD phút (engine có thể down)
 *   ERROR   — fetch thất bại
 *
 * Rule: chỉ cập nhật DOM trong #engineHeartbeat — không side-effect nào khác.
 * Poll interval: 60s. Graceful degradation: badge ẩn nếu element không tồn tại.
 */

import { coreApiBase, getJson } from '../../api/client.js';

const POLL_INTERVAL_MS    = 60_000;  // 1 phút
const STALE_THRESHOLD_MIN = 35;      // > 35 phút không có cycle → STALE

export async function initEngineHeartbeat() {
  const el = document.getElementById('engineHeartbeat');
  if (!el) return;

  await _fetchAndRender(el);
  setInterval(() => _fetchAndRender(el), POLL_INTERVAL_MS);
}

async function _fetchAndRender(el) {
  try {
    const data = await getJson(`${coreApiBase()}/snapshot`);
    const capturedAt = data?.captured_at ?? data?.timestamp ?? null;
    if (!capturedAt) {
      _render(el, 'IDLE', null);
      return;
    }
    const diffMin = (Date.now() - new Date(capturedAt).getTime()) / 60_000;
    const state   = diffMin > STALE_THRESHOLD_MIN ? 'STALE' : 'IDLE';
    _render(el, state, diffMin);
  } catch {
    _render(el, 'ERROR', null);
  }
}

function _render(el, state, diffMin) {
  const label = _label(state, diffMin);
  el.className = `engine-heartbeat ${_cls(state, diffMin)}`;
  el.setAttribute('title', _tooltip(state, diffMin));
  el.setAttribute('aria-label', label);
  el.innerHTML = `<span class="heartbeat-dot"></span><span class="heartbeat-text">${label}</span>`;
}

function _label(state, diffMin) {
  if (state === 'ERROR') return 'Engine ✕';
  if (diffMin === null)  return 'Engine —';
  if (diffMin < 1)       return 'Engine < 1m';
  return `Engine ${Math.round(diffMin)}m`;
}

function _tooltip(state, diffMin) {
  if (state === 'ERROR') return 'Không thể kết nối tới Intelligence Engine';
  if (state === 'STALE') return `Engine chưa chạy ${Math.round(diffMin ?? 0)} phút — kiểm tra scheduler`;
  return `Last cycle: ${Math.round(diffMin ?? 0)} phút trước`;
}

function _cls(state, diffMin) {
  if (state === 'ERROR') return 'heartbeat-error';
  if (state === 'STALE') return 'heartbeat-stale';
  // IDLE + có captured_at hợp lệ → xanh lá (hệ thống đang chạy bình thường)
  if (diffMin !== null)  return 'heartbeat-ok';
  // IDLE nhưng chưa từng có snapshot → giữ muted
  return 'heartbeat-idle';
}
