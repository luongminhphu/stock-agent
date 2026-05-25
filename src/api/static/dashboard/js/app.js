/**
 * app.js — Bootstrap & wiring only. No domain logic.
 * Rule: KHÔNG chứa business logic. Chỉ bootstrap + wiring.
 */

import { el, showToast, openModal, closeModal } from './utils/dom.js';
import { loadDashboard, loadBacktesting } from './modules/dashboard/dashboard-loader.js';
import { loadThesisDetail }     from './modules/thesis/thesis-service.js';
import { loadPortfolio }        from './modules/portfolio/portfolio-loader.js';
import { loadWatchlist }        from './modules/watchlist/watchlist-loader.js';
import {
  bindThesisFormEvents,
  openNewThesisModal,
  openEditThesisModal,
  makeAssumptionRow,
  makeCatalystRow,
} from './modules/thesis/thesis-form.js';
import { bindSuggestEvents }    from './modules/thesis/thesis-suggest.js';
import { loadDecisions }        from './modules/decision/decision-loader.js';
import { bindFeedbackEvents }   from './modules/decision/feedback-handler.js';
import { loadLeaderboard }      from './modules/leaderboard/leaderboard-loader.js';
import { loadMemory }           from './modules/memory/memory-loader.js';
import { loadAttentionPanel, startAttentionAutoRefresh } from './modules/attention/attention-loader.js';
import { bindGenerateBriefButtons } from './modules/brief/brief-loader.js';
import { bindWatchlistThesisNavigate } from './modules/watchlist/watchlist-navigate.js';
import { bindLessonPersistedEvent } from './modules/thesis/thesis-service.js';
import { state } from './state/dashboard-state.js';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------
function debounce(fn, ms) {
  let t;
  return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), ms); };
}

// ---------------------------------------------------------------------------
// initBriefAutoOpen — open morning brief tab if URL hash matches
// ---------------------------------------------------------------------------
function initBriefAutoOpen() {
  const hash = location.hash;
  if (hash === '#morning-brief' || hash === '#brief') {
    const tab = document.querySelector('[data-tab="brief"]');
    if (tab) tab.click();
  }
}

// ---------------------------------------------------------------------------
// KPI cells clickable → scroll to thesis board
// ---------------------------------------------------------------------------
export function initKpiClickable() {
  document.querySelectorAll('[data-kpi-scroll]').forEach(cell => {
    cell.style.cursor = 'pointer';
    cell.addEventListener('click', () => {
      document.querySelector('#thesisBoard')
        ?.scrollIntoView({ behavior: 'smooth', block: 'start' });
    });
  });
}

// ---------------------------------------------------------------------------
// Tab navigation
// ---------------------------------------------------------------------------
function initTabNav() {
  const tabs  = document.querySelectorAll('[data-tab]');
  const panes = document.querySelectorAll('[data-pane]');
  tabs.forEach(tab => {
    tab.addEventListener('click', () => {
      tabs.forEach(t  => t.classList.remove('active'));
      panes.forEach(p => p.classList.add('hidden'));
      tab.classList.add('active');
      const pane = document.querySelector(`[data-pane="${tab.dataset.tab}"]`);
      if (pane) pane.classList.remove('hidden');
    });
  });
}

// ---------------------------------------------------------------------------
// Attention Panel helper
// ---------------------------------------------------------------------------
function bindAttentionActions() {
  document.addEventListener('click', async e => {
    const chip = e.target.closest('.brief-ticker-chip[data-thesis-id]');
    if (chip) {
      const thesisId = Number(chip.dataset.thesisId);
      if (thesisId) await loadThesisDetail(thesisId);
    }
  });
}

// Wave E: Brief ticker chip click → loadThesisDetail
function bindBriefThesisNavigation() {
  document.addEventListener('click', async e => {
    const chip = e.target.closest('[data-navigate-thesis]');
    if (!chip) return;
    const id = Number(chip.dataset.navigateThesis);
    if (!id) return;
    const tab = document.querySelector('[data-tab="thesis"]');
    if (tab) tab.click();
    await loadThesisDetail(id);
    document.querySelector('#thesisBoard')
      ?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  });
}

// ---------------------------------------------------------------------------
// navigate:thesis event (from watchlist badges, brief chips, etc.)
// ---------------------------------------------------------------------------
function bindThesisNavigateEvent() {
  document.addEventListener('navigate:thesis', async e => {
    const thesisId = e.detail?.thesisId;
    if (!thesisId) return;
    // Switch to thesis tab
    const tab = document.querySelector('[data-tab="thesis"]');
    if (tab) tab.click();
    await loadThesisDetail(thesisId);
    document.querySelector('#thesisBoard')
      ?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  });
}

// #3 FIX: Watchlist thesis badge → navigate:thesis → loadThesisDetail
function bindWatchlistNavigate() {
  document.addEventListener('navigate:thesis', async e => {
    const thesisId = e.detail?.thesisId;
    if (!thesisId) return;
    await loadThesisDetail(thesisId);
  });
}

// ---------------------------------------------------------------------------
// Conviction timeline tab wiring (detail panel tabs)
// ---------------------------------------------------------------------------
function bindDetailTabNav() {
  document.addEventListener('click', e => {
    const tab = e.target.closest('[data-detail-tab]');
    if (!tab) return;
    const group = tab.closest('[data-detail-tab-group]');
    if (!group) return;
    const name = tab.dataset.detailTab;
    group.querySelectorAll('[data-detail-tab]').forEach(t => t.classList.toggle('active', t === tab));
    group.querySelectorAll('[data-detail-pane]').forEach(p => p.classList.toggle('hidden', p.dataset.detailPane !== name));
  });
}

// ---------------------------------------------------------------------------
// Decision replay actions (quick-trade from review panel)
// ---------------------------------------------------------------------------
function bindDecisionQuickTradeEvent() {
  document.addEventListener('decision:quick-trade', async e => {
    const { thesisId, ticker, action, price } = e.detail ?? {};
    if (!thesisId || !ticker) return;
    const tab = document.querySelector('[data-tab="decisions"]');
    if (tab) tab.click();
    // Pre-fill decision form if available
    const tickerField = el('decisionTickerField');
    const actionField = el('decisionActionField');
    const priceField  = el('decisionPriceField');
    if (tickerField) tickerField.value = ticker;
    if (actionField && action) actionField.value = action;
    if (priceField  && price)  priceField.value  = price;
    el('decisionForm')?.scrollIntoView({ behavior: 'smooth' });
  });
}

// ---------------------------------------------------------------------------
// Wave 3 wire: decision:changed { thesisId } → loadThesisDetail if selected
// ---------------------------------------------------------------------------
function bindDecisionChangedEvent() {
  document.addEventListener('decision:changed', async e => {
    const { thesisId } = e.detail ?? {};
    if (!thesisId) return;
    if (state.selectedThesisId === thesisId) {
      await loadThesisDetail(thesisId);
    }
    // Also refresh dashboard to update conviction scores
    await loadDashboard();
  });
}

// ---------------------------------------------------------------------------
// Main bootstrap
// ---------------------------------------------------------------------------
document.addEventListener('DOMContentLoaded', async () => {
  initTabNav();
  initBriefAutoOpen();
  bindDetailTabNav();
  bindBriefThesisNavigation();
  bindThesisNavigateEvent();
  bindDecisionQuickTradeEvent();
  bindDecisionChangedEvent();
  bindAttentionActions();
  bindLessonPersistedEvent();

  // 1d. Wave G: generate brief buttons → POST /briefing/{phase}/generate
  bindGenerateBriefButtons();

  // 1e. #3 FIX: watchlist thesis badge → navigate:thesis → loadThesisDetail
  bindWatchlistThesisNavigate();

  // 2. Bind thesis form + delete confirm
  bindThesisFormEvents({
    onThesisSaved: async (thesisId) => {
      await loadDashboard();
      if (thesisId) await loadThesisDetail(thesisId);
      await loadPortfolio();
      await loadWatchlist();
      loadAttentionPanel();
    },
  });

  // 3. Bind AI suggest buttons
  bindSuggestEvents();

  // 4. Toolbar buttons
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
  el('reloadBtn')?.addEventListener('click', async () => {
    await loadDashboard();
    await loadBacktesting();
    await loadPortfolio();
    await loadWatchlist();
    await loadDecisions();
    loadLeaderboard();
    loadMemory();
    loadAttentionPanel();
  });

  el('statusFilter')?.addEventListener('change', debounce(loadDashboard, 200));

  // 5. Form row add buttons
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

  // 6. Modal close buttons
  document.addEventListener('click', e => {
    const btn = e.target.closest('[data-close]');
    if (btn) closeModal(btn.dataset.close);
  });

  // 7. Delete confirm modal — wire state.deleteCallback
  // confirmDeleteThesis/Assumption/Catalyst (thesis-service) sets state.deleteCallback
  // then opens deleteModal. This handler executes the callback on confirm click.
  el('deleteConfirmBtn')?.addEventListener('click', async () => {
    if (typeof state.deleteCallback !== 'function') return;
    const btn = el('deleteConfirmBtn');
    btn.disabled = true;
    btn.textContent = 'Đang xóa…';
    try {
      await state.deleteCallback();
    } catch (err) {
      showToast(`Xóa thất bại: ${err.message}`, 'error');
    } finally {
      btn.disabled = false;
      btn.textContent = 'Xóa';
      state.deleteCallback = null;
    }
  });

  // 8. AI Apply confirm
  el('aiApplyConfirmBtn')?.addEventListener('click', async () => {
    const { thesisApiBase, sendJson } = await import('./api/client.js');
    const { showToast } = await import('./utils/dom.js');
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
      const { showToast: toast } = await import('./utils/dom.js');
      toast('✅ Đã áp dụng gợi ý AI');
      await loadThesisDetail(state.aiApplyThesisId);
    } catch (err) {
      const { showToast: toast } = await import('./utils/dom.js');
      toast(`Áp dụng thất bại: ${err.message}`, 'error');
    } finally {
      btn.disabled = false;
      btn.textContent = 'Áp dụng';
    }
  });

  // 9. AI Apply modal — checkbox wiring
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

  // 10. Initial data load
  await Promise.allSettled([
    loadDashboard(),
    loadBacktesting(),
    loadPortfolio(),
    loadWatchlist(),
    loadDecisions(),
  ]);
  loadLeaderboard();
  loadMemory();
  loadAttentionPanel();
  initKpiClickable();

  startAttentionAutoRefresh();
});
