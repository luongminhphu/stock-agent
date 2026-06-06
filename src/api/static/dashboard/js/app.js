/**
 * app.js — Entry point
 * Responsibility: import all modules, wire events, bootstrap dashboard.
 * Rule: NO business logic. Bootstrap + wiring only.
 *
 * NOTE(brief-feedback-summary): loadBriefFeedbackSummary is implemented in
 * modules/briefing/brief-feedback.js but NOT wired here yet.
 * Wire it back once GET /api/v1/dashboard/brief/feedback-summary is deployed.
 */

import { el, openModal, closeModal } from './utils/dom.js';
import { loadDashboard, loadBacktesting } from './modules/dashboard/dashboard-loader.js';
import { loadThesisDetail }              from './modules/thesis/thesis-service.js';
import { bindLessonPersistedEvent }       from './modules/thesis/thesis-service.js';
import {
  openNewThesisModal,
  bindThesisFormEvents,
} from './modules/thesis/thesis-form.js';
import { bindSuggestEvents }             from './modules/thesis/thesis-suggest.js';
import { loadPortfolio }                 from './modules/portfolio/portfolio-loader.js';
import { loadWatchlist, handleAddTicker } from './modules/watchlist/watchlist-loader.js';
import {
  loadDecisions,
  loadLessons,
  bindDecisionFormEvents,
  openDecisionModal,
} from './modules/decision/decision-loader.js';
import { loadLeaderboard }               from './modules/leaderboard/leaderboard-service.js';
import { bindFeedbackEvents }            from './modules/briefing/brief-feedback.js';
import { bindGenerateBriefButtons }      from './modules/briefing/brief-generate.js';
import { bindBriefTabs, initBriefAutoOpen } from './modules/briefing/brief-tabs.js';
import { bindBriefTickerClick }          from './modules/briefing/brief-ticker.js';
import { loadMemory }                    from './modules/memory/memory-loader.js';
import { loadAttentionPanel, startAttentionAutoRefresh } from './modules/attention/attention-loader.js';
import { loadMarketBreadth }             from './modules/market/breadth.js';
import { debounce }                      from './utils/debounce.js';
import { state }                         from './state/dashboard-state.js';
import { loadTodayLoop, startTodayLoopAutoRefresh } from './modules/today-loop/today-loop-loader.js';
import { initEngineHeartbeat }           from './modules/engine/engine-heartbeat.js';
import { bindDecisionTabs }              from './modules/decision/decision-tabs.js';
import { bindLeaderboardSort }           from './modules/leaderboard/leaderboard-sort.js';
import {
  bindWatchlistThesisNavigate,
  bindWatchlistAddModal,
} from './modules/watchlist/watchlist-nav.js';
import { initKpiClickable }             from './modules/dashboard/kpi-clickable.js';
import { initTopbarSearch, reapplySearch } from './modules/search/topbar-search.js';

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
// Main bootstrap
// ---------------------------------------------------------------------------
document.addEventListener('DOMContentLoaded', async () => {
  // — UI / tab init —
  initBriefAutoOpen();
  bindBriefTabs();
  bindBriefTickerClick({ state, loadThesisDetail });
  bindWatchlistThesisNavigate({ loadThesisDetail });
  bindWatchlistAddModal({ closeModal, handleAddTicker });
  bindDecisionTabs({ el, loadLessons });
  bindLeaderboardSort({ loadLeaderboard });

  // — Event wires —
  bindDecisionLoopWire();
  bindDecisionChangedEvent();
  bindDecisionQuickTradeEvent();
  bindLessonPersistedEvent();
  bindWatchlistChangedWire();
  bindTradeConfirmedWire();
  bindDecisionLeaderboardWire();

  // — Module init —
  initEngineHeartbeat();
  bindFeedbackEvents();
  bindGenerateBriefButtons();

  bindDecisionFormEvents({
    onDecisionSaved: async (thesisId) => {
      await loadDecisions();
      if (thesisId) await loadThesisDetail(thesisId);
    },
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

  // — Toolbar —
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
  el('newDecisionBtn')?.addEventListener('click', () => openDecisionModal({}));

  el('reloadBtn')?.addEventListener('click', async () => {
    await Promise.allSettled([
      loadDashboard(),
      loadBacktesting(),
      loadPortfolio(),
      loadWatchlist(),
      loadDecisions(),
      loadMarketBreadth(),
    ]);
    loadLeaderboard();
    loadMemory();
    loadAttentionPanel();
  });

  el('addWatchlistBtn')?.addEventListener('click', () => openModal('watchlistAddModal'));
  el('statusFilter')?.addEventListener('change', debounce(loadDashboard, 200));

  // — Form row add buttons —
  el('addFormAssumptionBtn')?.addEventListener('click', () => {
    import('./modules/thesis/thesis-form.js').then(({ makeAssumptionRow }) => {
      el('thesisFormAssumptionRows')?.appendChild(makeAssumptionRow());
    });
  });
  el('addFormCatalystBtn')?.addEventListener('click', () => {
    import('./modules/thesis/thesis-form.js').then(({ makeCatalystRow }) => {
      el('thesisFormCatalystRows')?.appendChild(makeCatalystRow());
    });
  });

  // — Modal infrastructure —
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
      const { showToast } = await import('./utils/dom.js');
      showToast(`Xóa thất bại: ${err.message}`, 'error');
    } finally {
      btn.disabled = false;
      btn.textContent = 'Xóa';
      state.deleteCallback = null;
    }
  });

  el('aiApplyConfirmBtn')?.addEventListener('click', async () => {
    const { thesisApiBase, sendJson } = await import('./api/client.js');
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
      const { showToast } = await import('./utils/dom.js');
      showToast('✅ Đã áp dụng gợi ý AI');
      await loadThesisDetail(state.aiApplyThesisId);
    } catch (err) {
      const { showToast } = await import('./utils/dom.js');
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

  // — Initial data load —
  await Promise.allSettled([
    loadDashboard(),
    loadBacktesting(),
    loadPortfolio(),
    loadWatchlist(),
    loadDecisions(),
    loadMarketBreadth(),
  ]);
  loadLeaderboard();
  loadMemory();
  loadAttentionPanel();
  loadTodayLoop();
  initKpiClickable();
  initTopbarSearch();

  // Re-apply search query sau mỗi lần data reload
  document.addEventListener('dashboard:rendered',  reapplySearch);
  document.addEventListener('watchlist:rendered',  reapplySearch);

  startAttentionAutoRefresh();
  startTodayLoopAutoRefresh();
});
