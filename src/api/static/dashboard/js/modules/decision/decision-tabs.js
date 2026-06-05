/**
 * decision-tabs.js
 * Owner: modules/decision
 * Responsibility: wire .dec-tab-bar click → show/hide decisionsPane / lessonsPane.
 * Lazy-loads lessons on first switch to 'lessons' tab.
 *
 * Exports:
 *   bindDecisionTabs(deps) — deps: { el, loadLessons }
 */

/**
 * @param {{ el: (id: string) => HTMLElement|null, loadLessons: () => Promise<void> }} deps
 */
export function bindDecisionTabs({ el, loadLessons }) {
  const tabBar = document.querySelector('.dec-tab-bar');
  if (!tabBar) return;

  tabBar.addEventListener('click', async (e) => {
    const btn = e.target.closest('.dec-tab');
    if (!btn) return;

    const target = btn.dataset.tab;

    tabBar.querySelectorAll('.dec-tab').forEach(t => t.classList.remove('active'));
    btn.classList.add('active');

    const decisionsPane = el('decisionsPane');
    const lessonsPane   = el('lessonsPane');

    if (target === 'decisions') {
      decisionsPane?.classList.remove('hidden');
      lessonsPane?.classList.add('hidden');
    } else {
      decisionsPane?.classList.add('hidden');
      lessonsPane?.classList.remove('hidden');
      const wrap = el('lessonsListWrap');
      if (wrap && (wrap.innerHTML.includes('Đang tải') || wrap.children.length === 0)) {
        await loadLessons();
      }
    }
  });
}
