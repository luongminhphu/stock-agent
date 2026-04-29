// utils/format.js — Pure formatting & rendering helpers
// Owner: dashboard shell (không phụ thuộc DOM, không có side effects)

/**
 * Format số theo locale vi-VN
 * @param {number|null} n
 * @param {number} decimals
 * @returns {string}
 */
export function fmt(n, decimals = 0) {
  if (n == null) return '\u2014';
  return Number(n).toLocaleString('vi-VN', { maximumFractionDigits: decimals });
}

/**
 * Format date thành dd/mm/yyyy
 * @param {string|Date|null} d
 * @returns {string}
 */
export function fmtDate(d) {
  if (!d) return '\u2014';
  return new Date(d).toLocaleDateString('vi-VN', { day: '2-digit', month: '2-digit', year: 'numeric' });
}

/**
 * Render badge HTML cho một giá trị
 * @param {string|null} val
 * @returns {string} HTML string
 */
export function badge(val) {
  const cls = String(val || '').toLowerCase();
  return `<span class="badge ${cls}">${val || '\u2014'}</span>`;
}

/**
 * Escape HTML entities để tránh XSS
 * @param {*} v
 * @returns {string}
 */
export function esc(v) {
  return String(v ?? '').replace(
    /[&<>'"]/g,
    s => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' }[s])
  );
}

/**
 * Highlight ticker symbols, số âm/dương trong scan text
 * @param {string|null} text
 * @returns {string} HTML string
 */
export function highlightScanText(text) {
  if (!text) return esc(text);
  return esc(text)
    .replace(
      /\b([A-Z][A-Z0-9]{1,4})(?![A-Z0-9])(?=\s*[:;,]|\s+(?:Price|price|-|\+))/g,
      '<strong style="color:#7dd3fc;font-weight:800;letter-spacing:.04em;">$1</strong>'
    )
    .replace(
      /(-\d+(?:\.\d+)?%?)/g,
      '<span style="color:#fb923c;font-weight:600;">$1</span>'
    )
    .replace(
      /(\+\d+(?:\.\d+)?%?)/g,
      '<span style="color:#4ade80;font-weight:700;">$1</span>'
    );
}

/**
 * Trả về CSS class tương ứng với health score
 * @param {number|null} s
 * @returns {string}
 */
export function scoreClass(s) {
  if (s == null) return '';
  if (s >= 86) return 'score-high';
  if (s >= 71) return 'score-good';
  if (s >= 51) return 'score-mid';
  if (s >= 31) return 'score-warn';
  return 'score-low';
}

/**
 * Format score thành số nguyên hoặc '—'
 * @param {number|null} s
 * @returns {string|number}
 */
export function fmtScore(s) {
  return s == null ? '\u2014' : Math.round(Number(s));
}

/**
 * Tính phần trăm clamp 0-100
 * @param {number|null} value
 * @param {number} max
 * @returns {number}
 */
export function pct(value, max) {
  if (value == null || !max) return 0;
  return Math.max(0, Math.min(100, (Number(value) / Number(max)) * 100));
}
