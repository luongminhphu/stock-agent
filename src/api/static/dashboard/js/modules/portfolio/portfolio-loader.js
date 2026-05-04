/**
 * portfolio-loader.js
 * Owner: modules/portfolio
 * Responsibility: fetch /dashboard/portfolio/trades + /dashboard/portfolio (thesis) → render.
 * Rule: KHÔNG chứa business logic. Chỉ fetch → normalize → render.
 */

import { el }             from '../../utils/dom.js';
import { apiBase, getJson } from '../../api/client.js';
import { renderPortfolio } from './portfolio-renderer.js';

/**
 * Load portfolio section.
 * Fetch cả 2 view song song:
 *   - /dashboard/portfolio/trades  → PnlService (positions thực tế)
 *   - /dashboard/portfolio         → DashboardService (thesis-based)
 */
export async function loadPortfolio() {
  const wrap = el('portfolioSection');
  if (!wrap) return;

  wrap.innerHTML = '<p class="muted" style="padding:16px">Đang tải portfolio…</p>';

  const base = apiBase();

  try {
    const [tradesRes, thesisRes] = await Promise.all([
      getJson(`${base}/portfolio/trades`).catch(() => null),
      getJson(`${base}/portfolio`).catch(() => null),
    ]);

    renderPortfolio(wrap, {
      trades: tradesRes,
      thesis: thesisRes,
    });
  } catch (err) {
    wrap.innerHTML = `<p class="empty-state">Lỗi tải portfolio: ${err.message}</p>`;
    console.error('[portfolio-loader] loadPortfolio error:', err);
  }
}
