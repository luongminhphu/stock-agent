/**
 * portfolio-loader.js
 * Owner: modules/portfolio
 * Responsibility: fetch /dashboard/portfolio/trades + /dashboard/portfolio (thesis) → render.
 * Rule: KHÔNG chứa business logic. Chỉ fetch → normalize → render.
 *
 * QuickTrade integration (Wave 2):
 *   1. window.__qtRefreshHoldings = loadPortfolio  — set SỚM để tránh race nếu user
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
 * Load portfolio section.
 * Fetch cả 2 view song song:
 *   - /dashboard/portfolio/trades  → PnlService (positions thực tế)
 *   - /dashboard/portfolio         → DashboardService (thesis-based)
 */
export async function loadPortfolio() {
  const wrap = el('portfolioSection');
  if (!wrap) return;

  // ── 1. Đăng ký refresh callback SỚM — trước bất kỳ await nào ───────────────
  // QuickTrade toast gọi window.__qtRefreshHoldings() sau khi trade thành công.
  // Set ở đây để nếu user click B/S trong lúc fetch đang chạy,
  // callback vẫn trỏ đúng vào loadPortfolio().
  window.__qtRefreshHoldings = loadPortfolio;

  // ── 2. Đảm bảo QuickTrade modal tồn tại trong DOM TRƯỚC render ───────────
  // ensureModal() bên trong QuickTrade.init() là idempotent — safe to call nhiều lần.
  if (window.QuickTrade) {
    window.QuickTrade.init();
  }

  wrap.innerHTML = '<p class="muted" style="padding:16px">Đang tải portfolio…</p>';

  const base = apiBase();

  try {
    const [tradesRes, thesisRes] = await Promise.all([
      getJson(`${base}/portfolio/trades`).catch(() => null),
      getJson(`${base}/portfolio`).catch(() => null),
    ]);

    // renderPortfolio() tự gọi QuickTrade.injectTradeButtons() trên tất cả
    // tbody[data-holdings-tbody] (cả Trades và Thesis tab) sau khi set innerHTML.
    renderPortfolio(wrap, {
      trades: tradesRes,
      thesis: thesisRes,
    });
  } catch (err) {
    wrap.innerHTML = `<p class="empty-state">Lỗi tải portfolio: ${err.message}</p>`;
    console.error('[portfolio-loader] loadPortfolio error:', err);
  }
}
