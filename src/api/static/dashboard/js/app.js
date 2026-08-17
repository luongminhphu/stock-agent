/**
 * app.js — Entry point
 * Responsibility: import critical modules, wire events, bootstrap dashboard.
 * Rule: NO business logic. Bootstrap + wiring only.
 *
 * Performance waves implemented here:
 *   Wave 1 — Load sequencing: critical → deferred 100ms → lazy IntersectionObserver
 *   Wave 2 — Lazy dynamic import() for 6 heavy modules (loaded on demand)
 *   Wave 3 — CSS consolidated into dashboard.css @import chain (index.html)
 *   Wave 4 — Skeleton screens in portfolio-loader & recommendations-panel
 *
 * NOTE(brief-feedback-summary): loadBriefFeedbackSummary is implemented in
 * modules/briefing/brief-feedback.js but NOT wired here yet.
 * Wire it back once GET /api/v1/dashboard/brief/feedback-summary is deployed.
 */

// ---------------------------------------------------------------------------
// CRITICAL IMPORTS — needed before first paint / event wiring
// (no lazy here — these are tiny or required for immediate interaction)
// ---------------------------------------------------------------------------
import { el, openModal, closeModal } from './utils/dom.js?v=1';
import { loadDashboard, loadBacktesting } from './modules/dashboard/dashboard-loader.js?v=1';
import { loadThesisDetail }              from './modules/thesis/thesis-service.js?v=1';
import { bindLessonPersistedEvent }       from './modules/thesis/thesis-service.js?v=1';
import {
  openNewThesisModal,
  bindThesisFormEvents,
} from './modules/thesis/thesis-form.js?v=1';
import { bindSuggestEvents }             from './modules/thesis/thesis-suggest.js?v=1';
import { loadPortfolio, startPortfolioAutoRefresh } from './modules/portfolio/portfolio-loader.js?v=2';
import { loadWatchlist, handleAddTicker } from './modules/watchlist/watchlist-loader.js?v=1';
import { bindFeedbackEvents }            from './modules/briefing/brief-feedback.js?v=1';
import { bindGenerateBriefButtons }      from './modules/briefing/brief-generate.js?v=1';
import { bindBriefTabs, initBriefAutoOpen } from './modules/briefing/brief-tabs.js?v=1';
import { bindBriefTickerClick }          from './modules/briefing/brief-ticker.js?v=1';
import { loadAttentionPanel, startAttentionAutoRefresh } from './modules/attention/attention-loader.js?v=2';
import { loadMarketBreadth }             from './modules/market/breadth.js?v=1';
import { debounce }                      from './utils/debounce.js?v=1';
import { initMobileNav }                 from './modules/mobile-nav.js?v=1';
import { state }                         from './state/dashboard-state.js?v=1';
import { initEngineHeartbeat }           from './modules/engine/engine-heartbeat.js?v=1';
import { initEngineControls }            from './modules/engine/engine-controls.js?v=1';
import {
  bindWatchlistThesisNavigate,
  bindWatchlistAddModal,
} from './modules/watchlist/watchlist-nav.js?v=1';
import { initKpiClickable }             from './modules/dashboard/kpi-clickable.js?v=1';
import { initTopbarSearch, reapplySearch } from './modules/search/topbar-search.js?v=1';

// ---------------------------------------------------------------------------
// Wave 2 — Lazy module loader helpers
// Modules loaded via dynamic import() on first call; subsequent calls reuse
// the cached promise. Modules are NOT in the initial module graph.
// ---------------------------------------------------------------------------

let _lazyDecisionLoader  = null;
let _lazyLeaderboard     = null;
let _lazyLeaderboardSort = null;
let _lazyDecisionTabs    = null;
let _lazyMemory          = null;
let _lazyTodayLoop       = null;
// _lazyRecommendations removed — recommendations panel consolidated into #intelligencePanel

async function _getDecisionLoader() {
  if (!_lazyDecisionLoader) _lazyDecisionLoader = import('./modules/decision/decision-loader.js?v=1');
  return _lazyDecisionLoader;
}
async function _getLeaderboard() {
  if (!_lazyLeaderboard) _lazyLeaderboard = import('./modules/leaderboard/leaderboard-service.js?v=1');
  return _lazyLeaderboard;
}
async function _getLeaderboardSort() {
  if (!_lazyLeaderboardSort) _lazyLeaderboardSort = import('./modules/leaderboard/leaderboard-sort.js?v=1');
  return _lazyLeaderboardSort;
}
async function _getDecisionTabs() {
  if (!_lazyDecisionTabs) _lazyDecisionTabs = import('./modules/decision/decision-tabs.js?v=1');
  return _lazyDecisionTabs;
}
async function _getMemory() {
  if (!_lazyMemory) _lazyMemory = import('./modules/memory/memory-loader.js?v=1');
  return _lazyMemory;
}
async function _getTodayLoop() {
  if (!_lazyTodayLoop) _lazyTodayLoop = import('./modules/today-loop/today-loop-loader.js?v=1');
  return _lazyTodayLoop;
}
// _getRecommendations() removed — use loadIntelligencePanel() instead

// Public wrappers — called from event wires below
async function loadLeaderboard(...args) {
  const m = await _getLeaderboard();
  return m.loadLeaderboard(...args);
}
async function loadDecisions(...args) {
  const m = await _getDecisionLoader();
  return m.loadDecisions(...args);
}
async function loadLessons(...args) {
  const m = await _getDecisionLoader();
  return m.loadLessons(...args);
}
async function openDecisionModal(...args) {
  const m = await _getDecisionLoader();
  return m.openDecisionModal(...args);
}
async function loadMemory() {
  const m = await _getMemory();
  return m.loadMemory();
}
async function loadTodayLoop(...args) {
  const m = await _getTodayLoop();
  return m.loadTodayLoop(...args);
}
// loadRecommendations() removed — consolidated into loadIntelligencePanel()

// ---------------------------------------------------------------------------
// Event wires — decision loop
// ---------------------------------------------------------------------------
function bindDecisionLoopWire() {
  document.addEventListener('decision:created', async (e) => {
    const { thesisId } = e.detail ?? {};
    if (!thesisId) return;
    await loadThesisDetail(thesisId);
    await loadDecisions();
  });
}

function bindDecisionChangedEvent() {
  document.addEventListener('decision:changed', async (e) => {
    const { thesisId } = e.detail ?? {};
    if (!thesisId) return;
    if (state.selectedThesisId === thesisId) await loadThesisDetail(thesisId);
    await loadDashboard();
  });
}

function bindDecisionQuickTradeEvent() {
  document.addEventListener('decision:quick-trade', async (e) => {
    const { thesisId, ticker, action, price } = e.detail ?? {};
    if (!thesisId || !ticker) return;
    openDecisionModal({ thesisId, ticker, action, price });
  });
}

// ---------------------------------------------------------------------------
// Event wires — watchlist / attention
// ---------------------------------------------------------------------------
function bindWatchlistChangedWire() {
  document.addEventListener('watchlist:changed',      () => loadAttentionPanel());
  document.addEventListener('watchlist:scan-complete', () => loadAttentionPanel());
}

function bindTradeConfirmedWire() {
  document.addEventListener('trade:confirmed', () => {
    loadWatchlist();
    loadAttentionPanel();
  });
}

// ---------------------------------------------------------------------------
// Event wires — leaderboard
// ---------------------------------------------------------------------------
function bindDecisionLeaderboardWire() {
  document.addEventListener('decision:changed',  () => loadLeaderboard());
  document.addEventListener('lesson:persisted',  () => loadLeaderboard());
}

// ---------------------------------------------------------------------------
// Wave 1 — IntersectionObserver: lazy-load heavy sections below the fold
// Observer fires once when section enters viewport; then disconnects.
// ---------------------------------------------------------------------------
function _observeLazy(selector, loader) {
  const target = typeof selector === 'string'
    ? document.querySelector(selector)
    : selector;
  if (!target) { loader(); return; }  // fallback: load immediately if no target

  const obs = new IntersectionObserver((entries, o) => {
    if (entries[0].isIntersecting) {
      o.disconnect();
      loader();
    }
  }, { rootMargin: '200px' });        // 200px pre-load before element enters viewport
  obs.observe(target);
}

// Wave 6: timestamp "Cập nhật HH:MM" cho KPI strip
function _stampKpiUpdatedAt() {
  const stamp = document.getElementById('kpiUpdatedAt');
  if (!stamp) return;
  const now = new Date();
  stamp.textContent = `Cập nhật ${String(now.getHours()).padStart(2, '0')}:${String(now.getMinutes()).padStart(2, '0')}`;
  stamp.title = now.toLocaleString('vi-VN');
}

// ---------------------------------------------------------------------------
// Main bootstrap
// ---------------------------------------------------------------------------
document.addEventListener('DOMContentLoaded', async () => {
  // ── Mobile nav: bottom tab bar (no-op on desktop) ─────────────────────────
  initMobileNav();

  // ── UI / tab init (critical — needed before user interaction) ──────────────
  initBriefAutoOpen();
  bindBriefTabs();
  bindBriefTickerClick({ state, loadThesisDetail });
  bindWatchlistThesisNavigate({ loadThesisDetail });
  bindWatchlistAddModal({ closeModal, handleAddTicker });

  // Wave 2: bind decision tabs after lazy load
  _getDecisionTabs().then(m =>
    m.bindDecisionTabs({ el, loadLessons })
  );
  // Wave 2: bind leaderboard sort after lazy load
  _getLeaderboardSort().then(m =>
    m.bindLeaderboardSort({ loadLeaderboard })
  );

  // ── Event wires ────────────────────────────────────────────────────────────
  bindDecisionLoopWire();
  bindDecisionChangedEvent();
  bindDecisionQuickTradeEvent();
  bindLessonPersistedEvent();
  bindWatchlistChangedWire();
  bindTradeConfirmedWire();
  bindDecisionLeaderboardWire();

  // Thesis lifecycle (close / invalidate) — reload dashboard so status badges update
  document.addEventListener('thesis:lifecycle-changed', () => {
    loadDashboard().catch(() => null);
  });

  // ── Module init ─────────────────────────────────────────────────────────────
  initEngineHeartbeat();
  initEngineControls();
  // Engine run-complete → reload dashboard + intelligence panel
  document.addEventListener('engine:run-complete', () => {
    loadDashboard().catch(() => null);
  });
  bindFeedbackEvents();
  bindGenerateBriefButtons();

  // Wave 2: decision form events (lazy)
  _getDecisionLoader().then(m => {
    m.bindDecisionFormEvents({
      onDecisionSaved: async (thesisId) => {
        await loadDecisions();
        if (thesisId) await loadThesisDetail(thesisId);
      },
    });
  });

  bindThesisFormEvents({
    onThesisSaved: async (thesisId) => {
      await loadDashboard();
      if (thesisId) await loadThesisDetail(thesisId);
      await loadPortfolio();
      await loadWatchlist();
      loadAttentionPanel();
    },
  });

  bindSuggestEvents();

  // ── Toolbar ─────────────────────────────────────────────────────────────────
  el('newThesisBtn')?.addEventListener('click', () => {
    try {
      openNewThesisModal();
    } catch (err) {
      console.error('[stock-agent] openNewThesisModal failed:', err);
      const banner = document.getElementById('errorBanner');
      if (banner) {
        banner.textContent = `⚠️ Không thể mở modal Thesis mới: ${err.message}`;
        banner.classList.remove('hidden');
      }
    }
  });
  // Wave 2: open decision modal — lazy
  el('newDecisionBtn')?.addEventListener('click', () => openDecisionModal({}));

  el('reloadBtn')?.addEventListener('click', async () => {
    await Promise.allSettled([
      loadDashboard(),
      loadPortfolio(),
      loadWatchlist(),
      loadDecisions(),
      loadMarketBreadth(),
    ]);
    loadLeaderboard();
    loadAttentionPanel();
    // Wave 6: backtesting/memory chỉ reload nếu panel đang mở
    if (document.getElementById('backtestDetails')?.open) loadBacktesting().catch(() => null);
    if (document.getElementById('memoryDetails')?.open) loadMemory().catch(() => null);
  });

  el('addWatchlistBtn')?.addEventListener('click', () => openModal('watchlistAddModal'));
  el('statusFilter')?.addEventListener('change', debounce(loadDashboard, 200));

  // ── Form row add buttons ─────────────────────────────────────────────────────
  el('addFormAssumptionBtn')?.addEventListener('click', () => {
    import('./modules/thesis/thesis-form.js?v=1').then(({ makeAssumptionRow }) => {
      el('thesisFormAssumptionRows')?.appendChild(makeAssumptionRow());
    });
  });
  el('addFormCatalystBtn')?.addEventListener('click', () => {
    import('./modules/thesis/thesis-form.js?v=1').then(({ makeCatalystRow }) => {
      el('thesisFormCatalystRows')?.appendChild(makeCatalystRow());
    });
  });

  // ── Modal infrastructure ─────────────────────────────────────────────────────
  document.addEventListener('click', e => {
    const btn = e.target.closest('[data-close]');
    if (btn) closeModal(btn.dataset.close);
  });

  el('deleteConfirmBtn')?.addEventListener('click', async () => {
    if (typeof state.deleteCallback !== 'function') return;
    const btn = el('deleteConfirmBtn');
    btn.disabled = true;
    btn.textContent = 'Đang xóa…';
    try {
      await state.deleteCallback();
    } catch (err) {
      const { showToast } = await import('./utils/dom.js?v=1');
      showToast(`Xóa thất bại: ${err.message}`, 'error');
    } finally {
      btn.disabled = false;
      btn.textContent = 'Xóa';
      state.deleteCallback = null;
    }
  });

  el('aiApplyConfirmBtn')?.addEventListener('click', async () => {
    const { thesisApiBase, sendJson } = await import('./api/client.js?v=1');
    if (!state.aiApplyThesisId || !state.aiSelectedRecIds.length) return;
    const btn = el('aiApplyConfirmBtn');
    btn.disabled = true;
    btn.textContent = 'Đang áp dụng…';
    try {
      await sendJson(
        `${thesisApiBase()}/${state.aiApplyThesisId}/recommendations/apply`,
        'POST',
        { recommendation_ids: state.aiSelectedRecIds },
      );
      closeModal('aiApplyModal');
      const { showToast } = await import('./utils/dom.js?v=1');
      showToast('✅ Đã áp dụng gợi ý AI');
      await loadThesisDetail(state.aiApplyThesisId);
    } catch (err) {
      const { showToast } = await import('./utils/dom.js?v=1');
      showToast(`Áp dụng thất bại: ${err.message}`, 'error');
    } finally {
      btn.disabled = false;
      btn.textContent = 'Áp dụng';
    }
  });

  el('aiApplyModal')?.addEventListener('change', e => {
    const cb = e.target.closest('.ai-rec-checkbox');
    if (!cb) return;
    const id = Number(cb.dataset.recId);
    if (cb.checked) {
      if (!state.aiSelectedRecIds.includes(id)) state.aiSelectedRecIds.push(id);
    } else {
      state.aiSelectedRecIds = state.aiSelectedRecIds.filter(x => x !== id);
    }
  });

  // ── Wave 1: Initial data load — 3 tiers ─────────────────────────────────────
  //
  // Tier A — CRITICAL: blocks first meaningful paint. Run immediately.
  //   loadDashboard() — thesis table, stats, KPIs, scan, brief snapshots
  //   loadPortfolio() — holdings & P&L (has its own skeleton)
  //   loadWatchlist() — attention panel data source
  //   loadMarketBreadth() — market context strip
  //
  // Tier B — DEFERRED: useful but not critical. Delay 100ms so browser can
  //   paint Tier A content before starting these fetches.
  //   loadBacktesting()   — verdict accuracy + worst/best calls
  //   loadAttentionPanel() — derived from watchlist (watchlist loads first)
  //   loadDecisions()     — decision history (below-fold panel)
  //
  // Tier C — LAZY: heavy, below-fold. Load when section enters viewport
  //   (IntersectionObserver with 200px rootMargin).
  //   loadLeaderboard()      → #leaderboardStrip
  //   loadMemory()           → #memoryKpiStrip
  //   loadTodayLoop()        → #todayDuoRow
  //   (recommendations consolidated into #intelligencePanel)

  // Tier A — critical, run now
  await Promise.allSettled([
    loadDashboard().then(_stampKpiUpdatedAt),
    loadPortfolio(),
    loadWatchlist(),
    loadMarketBreadth(),
  ]);

  // Wave 6: portfolio auto-refresh (60s giờ giao dịch / 10 phút ngoài giờ)
  startPortfolioAutoRefresh();

  // Tier B — deferred 100ms
  setTimeout(() => {
    loadAttentionPanel();
    loadDecisions().catch(() => null);
  }, 100);

  // Tier C — lazy IntersectionObserver (below-fold, heavy)
  _observeLazy('#leaderboardStrip',       () => loadLeaderboard().catch(() => null));

  // Wave 6: memory + RRG collapsed mặc định — chỉ fetch khi user mở lần đầu
  const _lazyOnOpen = (detailsId, loader) => {
    const det = document.getElementById(detailsId);
    if (!det) return;
    let loaded = false;
    det.addEventListener('toggle', () => {
      if (det.open && !loaded) {
        loaded = true;
        loader();
      }
    });
    // Nếu mặc định open (pref/localStorage sau này) thì load ngay
    if (det.open) { loaded = true; loader(); }
  };
  _lazyOnOpen('memoryDetails',   () => loadMemory().catch(() => null));
  _lazyOnOpen('rrgPanel',        () => import('./modules/leaderboard/rrg-chart.js?v=1').then(m => m.loadRRG()).catch(() => null));
  _lazyOnOpen('backtestDetails', () => loadBacktesting().catch(() => null));
  _observeLazy('#todayDuoRow',            () => {
    loadTodayLoop().catch(() => null);
    // start auto-refresh after first load
    _getTodayLoop().then(m => m.startTodayLoopAutoRefresh()).catch(() => null);
  });
  // #recommendationsSection removed — intelligence panel already loaded via dashboard-loader

  // ── Misc post-load init ──────────────────────────────────────────────────────
  initKpiClickable();
  initTopbarSearch();

  // Re-apply search query after each data reload
  document.addEventListener('dashboard:rendered',  reapplySearch);
  // Wave 6: KPI strip timestamp mỗi lần dashboard render xong
  document.addEventListener('dashboard:rendered',  _stampKpiUpdatedAt);
  document.addEventListener('watchlist:rendered',  reapplySearch);

  startAttentionAutoRefresh();
  // Note: startTodayLoopAutoRefresh and startRecommendationsAutoRefresh
  // are called inside _observeLazy handlers above (after first load completes)
});
