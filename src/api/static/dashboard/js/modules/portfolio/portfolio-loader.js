/**
 * portfolio-loader.js
 * Owner: modules/portfolio
 * Responsibility: fetch /dashboard/portfolio/trades + /dashboard/portfolio (thesis) → render.
 * Rule: KHÔNG chứa business logic. Chỉ fetch → normalize → render.
 *
 * QuickTrade integration (Wave 2):
 *   1. window.__qtRefreshHoldings = loadPortfolio  — set SỜM để tránh race nếu user
 *      click B/S trong lúc portfolio đang render.
 *   2. QuickTrade.init() — gọi TRƯỚC renderPortfolio() để đảm bảo modal được
 *      inject vào DOM trước khi injectTradeButtons() chạy bên trong renderer.
 *   3. injectTradeButtons() không gọi thủ công ở đây — renderer đã lo toàn bộ
 *      (cả Trades tbody và Thesis tbody) sau khi innerHTML được set.
 */

import { el }               from '../../utils/dom.js';
import { apiBase, getJson } from '../../api/client.js';
import { renderPortfolio }  from './portfolio-renderer.js';

/**
 * @param {string} userId
 */
export async function loadPortfolio(userId) {
  const section = el('#portfolioSection');
  if (!section) return;

  // Wave 2: register refresh hook sớm để tránh race condition
  window.__qtRefreshHoldings = () => loadPortfolio(userId);

  // Wave 2: init QuickTrade modal trước render
  if (window.QuickTrade?.init) window.QuickTrade.init();

  section.classList.add('loading');

  try {
    const base = apiBase();
    const [trades, thesis] = await Promise.allSettled([
      getJson(`${base}/dashboard/portfolio/trades`),
      getJson(`${base}/dashboard/portfolio`),
    ]);

    renderPortfolio(section, {
      trades: trades.status === 'fulfilled' ? trades.value : null,
      thesis: thesis.status === 'fulfilled' ? thesis.value : null,
    });
  } catch (err) {
    section.innerHTML = `<p class="section-error">Lỗi tải danh mục: ${err.message}</p>`;
  } finally {
    section.classList.remove('loading');
  }
}
