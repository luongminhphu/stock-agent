/**
 * debounce.js
 * Owner: utils
 * Responsibility: debounce + throttle helpers for UI event handlers.
 */

/**
 * Returns a debounced version of `fn` that delays invocation by `ms`
 * milliseconds after the last call. The trailing call always fires.
 *
 * @param {Function} fn
 * @param {number}   ms  — delay in milliseconds (default 200)
 * @returns {Function}
 *
 * @example
 * el('statusFilter')?.addEventListener('change', debounce(loadDashboard, 200));
 */
export function debounce(fn, ms = 200) {
  let timer;
  return function (...args) {
    clearTimeout(timer);
    timer = setTimeout(() => fn.apply(this, args), ms);
  };
}

/**
 * Returns a throttled version of `fn` that fires at most once per `ms`
 * milliseconds (leading-edge). Useful for scroll/resize handlers.
 *
 * @param {Function} fn
 * @param {number}   ms — minimum interval in milliseconds (default 200)
 * @returns {Function}
 */
export function throttle(fn, ms = 200) {
  let last = 0;
  return function (...args) {
    const now = Date.now();
    if (now - last >= ms) {
      last = now;
      fn.apply(this, args);
    }
  };
}
