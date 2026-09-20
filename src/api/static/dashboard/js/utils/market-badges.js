/**
 * market-badges.js — Wave U2b
 * Owner: utils (pure HTML helpers, không fetch, không state)
 *
 * Một chỗ duy nhất áp quy tắc màu biến động HSC (tăng / giảm / tham chiếu /
 * trần / sàn) và các tag bối cảnh thị trường (chất lượng dữ liệu, xu hướng,
 * gần stop) cho mọi surface: bảng thesis, quote strip, watchlist, digest.
 *
 * Input là các field readmodel/API đã chiếu từ market.TickerContext (U2a):
 *   change_pct, is_ceiling, is_floor, source_quality, market_as_of,
 *   trend_state, stop_distance_atr, near_stop, stop_breached.
 */
import { esc, fmt } from './format.js?v=1';

// ─── Movement (quy tắc màu giá HSC) ────────────────────────────────────────

/**
 * @returns {'up'|'down'|'ref'|'ceil'|'floor'|'none'}
 */
export function movementOf({ change_pct = null, is_ceiling = false, is_floor = false } = {}) {
  if (is_ceiling) return 'ceil';
  if (is_floor) return 'floor';
  if (change_pct == null || Number.isNaN(Number(change_pct))) return 'none';
  const v = Number(change_pct);
  if (v > 0) return 'up';
  if (v < 0) return 'down';
  return 'ref';
}

/** Class CSS `.mv-*` — dùng cho bất kỳ số nào phải mang màu biến động. */
export function movementClass(row) {
  return `mv-${movementOf(row)}`;
}

export function fmtSignedPct(v, decimals = 2) {
  if (v == null || Number.isNaN(Number(v))) return '\u2014';
  const n = Number(v);
  return (n > 0 ? '+' : '') + n.toFixed(decimals) + '%';
}

/**
 * Ô giá: giá (màu biến động, tabular) + % thay đổi + tag chất lượng dữ liệu.
 * Dùng cho bảng thesis / watchlist. Trả "—" khi chưa có giá.
 */
export function priceCellHTML(row = {}) {
  const price = row.current_price ?? row.price ?? null;
  if (price == null) {
    return `<div class="price-cell price-cell--empty" title="Chưa có giá thị trường">\u2014</div>`;
  }
  const mv = movementClass(row);
  const limit = movementOf(row);
  const limitTag = limit === 'ceil'
    ? `<span class="hsc-tag hsc-tag-ceil">Trần</span>`
    : limit === 'floor'
      ? `<span class="hsc-tag hsc-tag-floor">Sàn</span>`
      : '';
  return `
    <div class="price-cell">
      <span class="price-cell-value ${mv}">${fmt(price)}</span>
      <span class="price-cell-sub">
        <span class="price-cell-change ${mv}">${esc(fmtSignedPct(row.change_pct))}</span>
        ${limitTag}
        ${dataQualityTagHTML(row.source_quality, row.market_as_of, { compact: true })}
      </span>
    </div>`;
}

// ─── Chất lượng dữ liệu (TickerContext.source_quality) ─────────────────────

const QUALITY_LABEL = {
  live: 'Trực tiếp',
  stale: 'Dữ liệu cũ',
  fallback: 'Thiếu OHLCV',
  quote: 'Chỉ có giá',
};

/**
 * Tag trung tính (không màu — màu chỉ dành cho giá và CTA chính).
 * `live` không hiện tag ở chế độ compact để bảng không bị nhiễu.
 */
export function dataQualityTagHTML(quality, asOf = null, { compact = false } = {}) {
  if (!quality) return '';
  const q = String(quality).toLowerCase();
  if (compact && q === 'live') return '';
  const label = QUALITY_LABEL[q] ?? q;
  const when = asOf ? ` \u00b7 ${fmtTimeShort(asOf)}` : '';
  const title = q === 'live'
    ? `Dữ liệu trực tiếp${when}`
    : q === 'stale'
      ? `Giá/chỉ báo lấy từ phiên trước${when}`
      : 'Không đủ lịch sử giá để tính chỉ báo (MA / RSI / ATR)';
  return `<span class="hsc-tag hsc-tag-neutral dq-tag dq-tag--${esc(q)}" title="${esc(title)}">${esc(label)}</span>`;
}

// ─── Xu hướng (TickerContext.trend_state) ──────────────────────────────────

const TREND_LABEL = {
  UPTREND: 'Xu hướng tăng',
  DOWNTREND: 'Xu hướng giảm',
  SIDEWAYS: 'Đi ngang',
  UNKNOWN: 'Chưa rõ xu hướng',
};

export function trendTagHTML(trend) {
  if (!trend) return '';
  const t = String(trend).toUpperCase();
  const label = TREND_LABEL[t] ?? t;
  return `<span class="hsc-tag hsc-tag-neutral trend-tag trend-tag--${esc(t.toLowerCase())}" title="Giá so với MA20 / MA50 (D1)">${esc(label)}</span>`;
}

// ─── Stop-loss (thesis rule: near_stop = 0 < dist < 1 ATR14) ───────────────

/**
 * Tag "Gần stop" — chỉ khi chưa xuyên stop. Xuyên stop đã có badge riêng.
 */
export function nearStopTagHTML(row = {}) {
  if (row.stop_breached === true || row.near_stop !== true) return '';
  const d = row.stop_distance_atr;
  const dist = d != null ? `${Number(d).toFixed(2)} ATR` : '< 1 ATR';
  return `<span class="hsc-tag hsc-tag-warning near-stop-tag" title="Giá còn cách stop-loss ${esc(dist)} (ATR14) — theo dõi sát, chưa xuyên stop">Gần stop \u00b7 <span class="hsc-num">${esc(dist)}</span></span>`;
}

// ─── Helpers ───────────────────────────────────────────────────────────────

export function fmtTimeShort(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';
  return d.toLocaleTimeString('vi-VN', { hour: '2-digit', minute: '2-digit' });
}
