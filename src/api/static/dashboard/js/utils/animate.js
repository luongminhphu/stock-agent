/**
 * animate.js
 * Owner: utils
 * Responsibility: animation helpers dùng chung toàn dashboard.
 *
 * Exports:
 *   countUp(el, target, opts?)  — animate số từ current sang target
 *   flashValue(el, up?)         — flash màu xanh/đỏ khi giá trị thay đổi
 */

/**
 * Animate một element từ giá trị hiện tại lên target.
 * Tự parse số từ textContent (bỏ dấu phẩy, ký hiệu).
 *
 * @param {HTMLElement} el
 * @param {number}      target
 * @param {object}      opts
 * @param {number}      opts.duration   ms (default 600)
 * @param {number}      opts.decimals   chữ số thập phân (default 0)
 * @param {string}      opts.suffix     hậu tố (e.g. '%', 'đ')
 * @param {Function}    opts.format     custom formatter (n: number) => string
 */
export function countUp(el, target, opts = {}) {
  if (!el) return;

  // Respect reduced-motion preference
  const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  if (reduced) {
    el.textContent = _defaultFormat(target, opts);
    return;
  }

  const duration = opts.duration ?? 600;
  const format   = opts.format ?? ((n) => _defaultFormat(n, opts));

  // Parse start từ textContent hiện tại
  const rawText = el.textContent.replace(/[^\d.-]/g, '');
  const start   = parseFloat(rawText) || 0;

  if (start === target) return;

  const startTime = performance.now();

  function tick(now) {
    const elapsed  = now - startTime;
    const progress = Math.min(elapsed / duration, 1);
    // Ease-out cubic
    const eased    = 1 - Math.pow(1 - progress, 3);
    const current  = start + (target - start) * eased;
    el.textContent = format(current);
    if (progress < 1) requestAnimationFrame(tick);
    else el.textContent = format(target); // đảm bảo giá trị cuối chính xác
  }

  requestAnimationFrame(tick);
}

/**
 * Flash màu xanh (up=true) hoặc đỏ (up=false) trên element trong 800ms.
 * @param {HTMLElement} el
 * @param {boolean}     up
 */
export function flashValue(el, up = true) {
  if (!el) return;
  const cls = up ? 'flash-up' : 'flash-down';
  el.classList.remove('flash-up', 'flash-down');
  // Force reflow để restart animation
  void el.offsetWidth;
  el.classList.add(cls);
  setTimeout(() => el.classList.remove(cls), 800);
}

// ── private ──────────────────────────────────────────────────────────────────

function _defaultFormat(n, opts) {
  const decimals = opts.decimals ?? 0;
  const suffix   = opts.suffix   ?? '';
  const formatted = n.toLocaleString('vi-VN', {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  });
  return formatted + suffix;
}
