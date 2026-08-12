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

import { el }                          from '../../utils/dom.js?v=1';
import { readmodelApiBase, getJson }   from '../../api/client.js?v=1';
import { renderPortfolio }             from './portfolio-renderer.js?v=1';
import { init as qtInit,
         injectTradeButtons }          from './quick-trade.js?v=1';
import { init as adjInit,
         injectAdjustButtons }         from './adjust-position.js?v=4';
import { RefreshScheduler }              from '../../utils/refresh-scheduler.js?v=1';

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
 * @param {{silent?: boolean}=} opts — silent: refresh chu kỳ, không skeleton
 */
export async function loadPortfolio(userId, opts = {}) {
  const section = el('portfolioSection');   // getElementById — không có '#'
  if (!section) return;
  const silent = opts.silent === true;

  // Wave 2: register refresh hook sớm để tránh race condition
  window.__qtRefreshHoldings = () => loadPortfolio(userId);

  // Wave 2: expose cho renderer dùng (renderer vẫn guard window.QuickTrade?.)
  if (!window.QuickTrade) {
    window.QuickTrade = { init: qtInit, injectTradeButtons };
  }
  if (!window.AdjustPosition) {
    window.AdjustPosition = { init: adjInit, injectAdjustButtons };
  }
  qtInit();
  adjInit();

  // Wave 4: show skeleton immediately (skip khi silent refresh — tránh flicker)
  if (!silent) {
    section.innerHTML = portfolioSkeletonHTML();
    section.classList.add('loading');
  }

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
    _stampUpdatedAt();
  } catch (err) {
    // Silent refresh fail → giữ nguyên data đang hiển thị, chỉ log
    if (silent) {
      console.warn('[portfolio] silent refresh failed:', err.message);
    } else {
      section.innerHTML = `<p class="section-error">Lỗi tải danh mục: ${err.message}</p>`;
    }
  } finally {
    section.classList.remove('loading');
  }
}

// Wave 6: timestamp "Cập nhật HH:MM" trên panel header — user biết data mới/cũ
function _stampUpdatedAt() {
  const stamp = document.getElementById('portfolioUpdatedAt');
  if (!stamp) return;
  const now = new Date();
  stamp.textContent = `Cập nhật ${String(now.getHours()).padStart(2, '0')}:${String(now.getMinutes()).padStart(2, '0')}`;
  stamp.title = now.toLocaleString('vi-VN');
}

// Wave 6: auto-refresh 60s trong giờ giao dịch (9:00–15:00 ICT, T2–T6);
// ngoài giờ refresh 10 phút — đủ để giữ EOD snapshot mới.
const _REFRESH_MARKET_MS = 60 * 1000;
const _REFRESH_OFFHOURS_MS = 10 * 60 * 1000;

function _isMarketHours() {
  // Giờ VN = UTC+7 — server/user đều ở VN nên dùng local time của browser
  const now = new Date();
  const day = now.getDay();                     // 0=CN, 6=T7
  const mins = now.getHours() * 60 + now.getMinutes();
  return day >= 1 && day <= 5 && mins >= 9 * 60 && mins < 15 * 60;
}

let _autoRefreshStarted = false;
export function startPortfolioAutoRefresh() {
  if (_autoRefreshStarted) return;
  _autoRefreshStarted = true;
  const schedule = () => {
    const interval = _isMarketHours() ? _REFRESH_MARKET_MS : _REFRESH_OFFHOURS_MS;
    RefreshScheduler.register('portfolio', () => {
      loadPortfolio(undefined, { silent: true });
      schedule();   // re-register với interval mới theo giờ hiện tại
    }, interval);
  };
  schedule();
}
