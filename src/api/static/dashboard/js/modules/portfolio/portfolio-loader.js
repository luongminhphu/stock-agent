/**
 * portfolio-loader.js
 * Owner: modules/portfolio
 * Responsibility: fetch /dashboard/portfolio/trades + /dashboard/portfolio (thesis) → render.
 * Rule: KHÔNG chứa business logic. Chỉ fetch → normalize → render.
 *
 * QuickTrade integration (Wave 2):
 *   - import trực tiếp từ ./quick-trade.js (ES module)
 *   - window.__qtRefreshHoldings = loadPortfolio — set SỚM để tránh race
 *   - init() gọi TRƯỚC renderPortfolio() để modal có trong DOM
 *     trước khi injectTradeButtons() chạy bên trong renderer
 */

import { el }                          from '../../utils/dom.js';
import { readmodelApiBase, getJson }   from '../../api/client.js';
import { renderPortfolio }             from './portfolio-renderer.js';
import { init as qtInit,
         injectTradeButtons }          from './quick-trade.js';

/**
 * @param {string=} userId
 */
export async function loadPortfolio(userId) {
  const section = el('portfolioSection');   // getElementById — không có '#'
  if (!section) return;

  // Wave 2: register refresh hook sớm để tránh race condition
  window.__qtRefreshHoldings = () => loadPortfolio(userId);

  // Wave 2: expose cho renderer dùng (renderer vẫn guard window.QuickTrade?.)
  if (!window.QuickTrade) {
    window.QuickTrade = { init: qtInit, injectTradeButtons };
  }
  qtInit();

  section.classList.add('loading');

  try {
    const base = readmodelApiBase();   // '/api/v1/readmodel' — không có trailing /dashboard
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
