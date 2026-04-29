// utils/dom.js — DOM helpers & UI primitives
// Owner: dashboard shell (không chứa business logic)

/**
 * Shorthand cho document.getElementById
 * @param {string} id
 * @returns {HTMLElement|null}
 */
export function el(id) {
  return document.getElementById(id);
}

/**
 * Hiển thị toast notification
 * @param {string} msg
 * @param {'success'|'error'|'info'} type
 * @param {number} ms
 */
export function showToast(msg, type = 'success', ms = 3000) {
  const t = document.createElement('div');
  t.className = `toast ${type}`;
  t.textContent = msg;
  document.body.appendChild(t);
  setTimeout(() => t.remove(), ms);
}

/**
 * Mở <dialog> theo id
 * @param {string} id
 */
export function openModal(id) {
  const d = el(id);
  if (d) d.showModal();
}

/**
 * Đóng <dialog> theo id
 * @param {string} id
 */
export function closeModal(id) {
  const d = el(id);
  if (d) d.close();
}
