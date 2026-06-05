/**
 * leaderboard-sort.js
 * Owner: modules/leaderboard
 * Responsibility: wire .lb-sort-bar click → call loadLeaderboard(sortKey).
 *
 * Exports:
 *   bindLeaderboardSort(deps) — deps: { loadLeaderboard }
 */

/**
 * @param {{ loadLeaderboard: (sortKey: string) => void }} deps
 */
export function bindLeaderboardSort({ loadLeaderboard }) {
  const sortBar = document.querySelector('.lb-sort-bar');
  if (!sortBar) return;

  sortBar.addEventListener('click', (e) => {
    const btn = e.target.closest('[data-lb-sort]');
    if (!btn) return;

    sortBar.querySelectorAll('[data-lb-sort]').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');

    loadLeaderboard(btn.dataset.lbSort);
  });
}
