/**
 * animate.js
 * Owner: utils
 * Responsibility: animation helpers dùng chung toàn dashboard.
 */

/**
 * countUp — animate một số từ 0 → target trong `duration`ms.
 * @param {HTMLElement} el   — DOM node để đặt textContent
 * @param {number}      to   — giá trị đích
 * @param {number}      [duration=600]
 */
export function countUp(el, to, duration = 600) {
  if (!el || typeof to !== 'number' || isNaN(to)) return;
  if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
    el.textContent = to;
    return;
  }
  const start = performance.now();
  const from  = 0;
  function step(now) {
    const p = Math.min((now - start) / duration, 1);
    // ease-out cubic
    const eased = 1 - Math.pow(1 - p, 3);
    el.textContent = Math.round(from + (to - from) * eased);
    if (p < 1) requestAnimationFrame(step);
  }
  requestAnimationFrame(step);
}

/**
 * flashValue — bật class `updated` trong 1 beat rồi tắt,
 * dùng để highlight khi giá trị thay đổi.
 * @param {HTMLElement} el
 */
export function flashValue(el) {
  if (!el) return;
  el.classList.remove('updated');
  // force reflow
  void el.offsetWidth;
  el.classList.add('updated');
  setTimeout(() => el.classList.remove('updated'), 800);
}
