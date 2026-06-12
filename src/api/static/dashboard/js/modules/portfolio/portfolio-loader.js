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

// Wave 4: Skeleton screen — hiển thị ngay trước khi fetch complete
function portfolioSkeletonHTML() {
  const row = (cols) => `<tr style="pointer-events:none;">${cols.map(w =>
    `<td><div class="skel skel-text" style="width:${w}%;"></div></td>`
  ).join('')}</tr>`;
  return `
    <div class="skel-table-wrap" aria-busy="true" aria-label="Đang tải danh mục…">
      <div style="display:flex;gap:8px;margin-bottom:10px;">
        <div class="skel skel-badge" style="width:72px;"></div>
        <div class="skel skel-badge" style="width:60px;"></div>
      </div>
      <table class="data-table">
        <thead><tr>
          ${['40','30','45','40','50','40','45'].map(w =>
            `<th><div class="skel skel-text" style="width:${w}%;"></div></th>`
          ).join('')}
        </tr></thead>
        <tbody>
          ${[row([55,35,42,38,52,44,40]),row([48,38,50,36,44,48,42]),row([60,30,38,44,56,36,48]),row([45,42,44,40,48,52,36])]}
        </tbody>
      </table>
    </div>`;
}

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

  // Wave 4: show skeleton immediately
  section.innerHTML = portfolioSkeletonHTML();
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
