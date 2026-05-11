/**
 * app.js — Entry point (Wave 7 + Wave 2b watchlist + Wave 5 decisions + Wave A leaderboard + Wave D lesson loop + Wave E brief ticker + Wave F brief feedback + Wave G brief generate)
 * Responsibility: import tất cả modules, wire events, khởi động dashboard.
 * Rule: KHÔNG chứa business logic. Chỉ bootstrap + wiring.
 */

import { el, openModal, closeModal } from './utils/dom.js';
import { loadDashboard, loadBacktesting } from './modules/dashboard/dashboard-loader.js';
import { loadThesisDetail }     from './modules/thesis/thesis-service.js';
import { bindLessonPersistedEvent } from './modules/thesis/thesis-service.js';
import {
  openNewThesisModal,
  openEditThesisModal,
  bindThesisFormEvents,
} from './modules/thesis/thesis-form.js';
import { bindSuggestEvents }    from './modules/thesis/thesis-suggest.js';
import { loadPortfolio }        from './modules/portfolio/portfolio-loader.js';
import { loadWatchlist, handleAddTicker } from './modules/watchlist/watchlist-loader.js';
import {
  loadDecisions,
  loadLessons,
  bindDecisionFormEvents,
  openDecisionModal,
} from './modules/decision/decision-loader.js';
import { loadLeaderboard }      from './modules/leaderboard/leaderboard-service.js';
import { bindFeedbackEvents }   from './modules/briefing/brief-feedback.js';
import { bindGenerateBriefButtons } from './modules/briefing/brief-generate.js';
import { state }                from './state/dashboard-state.js';

// ---------------------------------------------------------------------------
// Brief tab switching
// ---------------------------------------------------------------------------
function bindBriefTabs() {
  const tabBar = document.querySelector('.brief-tab-bar');
  if (!tabBar) return;

  tabBar.addEventListener('click', e => {
    const btn = e.target.closest('.brief-tab');
    if (!btn) return;

    const targetId = btn.getAttribute('aria-controls');
    if (!targetId) return;

    tabBar.querySelectorAll('.brief-tab').forEach(t => {
      t.classList.remove('active');
      t.setAttribute('aria-selected', 'false');
    });
    document.querySelectorAll('.brief-tab-pane').forEach(p => {
      p.classList.add('hidden');
    });

    btn.classList.add('active');
    btn.setAttribute('aria-selected', 'true');
    document.getElementById(targetId)?.classList.remove('hidden');
  });
}

// ---------------------------------------------------------------------------
// Wave E: Brief ticker chip click → loadThesisDetail
// Delegates from document (chips are re-rendered on each brief refresh).
// ---------------------------------------------------------------------------
function bindBriefTickerClick() {
  document.addEventListener('click', e => {
    const chip = e.target.closest('[data-brief-ticker]');
    if (!chip) return;
    const ticker = chip.dataset.briefTicker?.toUpperCase();
    if (!ticker) return;
    const thesis = state.theses.find(t => t.ticker?.toUpperCase() === ticker);
    if (!thesis) return;
    loadThesisDetail(thesis.id);
    document.getElementById('thesesTableWrap')
      ?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  });

  document.addEventListener('keydown', e => {
    if (e.key !== 'Enter' && e.key !== ' ') return;
    const chip = e.target.closest('[data-brief-ticker]');
    if (!chip) return;
    e.preventDefault();
    chip.click();
  });
}

// ---------------------------------------------------------------------------
// Watchlist add modal: wire form submit
// ---------------------------------------------------------------------------
function bindWatchlistAddModal() {
  const form = document.getElementById('watchlistAddForm');
  if (!form) return;

  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const ticker = form.querySelector('#watchlistTickerInput')?.value?.trim();
    const note   = form.querySelector('#watchlistNoteInput')?.value?.trim() ?? '';
    if (!ticker) return;
    closeModal('watchlistAddModal');
    form.reset();
    await handleAddTicker(ticker, note);
  });
}

// ---------------------------------------------------------------------------
// Decision tab switching (Decisions | Lessons)
// ---------------------------------------------------------------------------
function bindDecisionTabs() {
  const tabBar = document.querySelector('.dec-tab-bar');
  if (!tabBar) return;

  tabBar.addEventListener('click', async (e) => {
    const btn = e.target.closest('.dec-tab');
    if (!btn) return;

    const target = btn.dataset.tab;

    tabBar.querySelectorAll('.dec-tab').forEach(t => t.classList.remove('active'));
    btn.classList.add('active');

    const decisionsPane = el('decisionsPane');
    const lessonsPane   = el('lessonsPane');

    if (target === 'decisions') {
      decisionsPane?.classList.remove('hidden');
      lessonsPane?.classList.add('hidden');
    } else {
      decisionsPane?.classList.add('hidden');
      lessonsPane?.classList.remove('hidden');
      const wrap = el('lessonsListWrap');
      if (wrap && (wrap.innerHTML.includes('\u0110ang tải') || wrap.children.length === 0)) {
        await loadLessons();
      }
    }
  });
}

// ---------------------------------------------------------------------------
// Leaderboard sort wiring
// ---------------------------------------------------------------------------
function bindLeaderboardSort() {
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

// ---------------------------------------------------------------------------
// Bootstrap
// ---------------------------------------------------------------------------
document.addEventListener('DOMContentLoaded', async () => {

  // ── Error boundary: surface module-load failures visibly ──────────────────
  // ES module import errors are silent by default — button clicks do nothing.
  // This catches top-level bootstrap errors and shows a toast.
  window.addEventListener('error', (e) => {
    if (e.filename?.includes('/static/dashboard/')) {
      const banner = document.getElementById('errorBanner');
      if (banner) {
        banner.textContent = `⚠️ Dashboard lỗi tải module: ${e.message} (${e.filename?.split('/').pop()}:${e.lineno})`;
        banner.classList.remove('hidden');
      }
      console.error('[stock-agent] module error:', e.message, e.filename, e.lineno);
    }
  });
  window.addEventListener('unhandledrejection', (e) => {
    console.error('[stock-agent] unhandled promise rejection:', e.reason);
  });

  // 1. Bind brief tab switcher
  bindBriefTabs();

  // 1b. Wave E: brief ticker chips → thesis detail
  bindBriefTickerClick();

  // 1c. Wave F: brief feedback buttons → POST /briefing/{id}/feedback
  bindFeedbackEvents();

  // 1d. Wave G: generate brief buttons → POST /briefing/{phase}/generate
  bindGenerateBriefButtons();

  // 2. Bind thesis form + delete confirm (submit handlers)
  bindThesisFormEvents({
    onThesisSaved: async (thesisId) => {
      await loadDashboard();
      if (thesisId) await loadThesisDetail(thesisId);
      await loadPortfolio();
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
  });
  el('statusFilter')?.addEventListener('change', loadDashboard);

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

  // 6. Modal close buttons (data-close attribute pattern)
  document.addEventListener('click', e => {
    const btn = e.target.closest('[data-close]');
    if (btn) closeModal(btn.dataset.close);
  });

  // 7. AI Apply confirm
  el('aiApplyConfirmBtn')?.addEventListener('click', async () => {
    const { thesisApiBase, sendJson } = await import('./api/client.js');
    const { showToast } = await import('./utils/dom.js');
    if (!state.aiApplyThesisId || !state.aiSelectedRecIds.length) return;
    const btn = el('aiApplyConfirmBtn');
    btn.disabled = true;
    btn.textContent = '\u0110ang áp dụng…';
    try {
      await sendJson(
        `${thesisApiBase()}/${state.aiApplyThesisId}/recommendations/apply`,
        'POST',
        { recommendation_ids: state.aiSelectedRecIds },
      );
      closeModal('aiApplyModal');
      showToast('\u2705 \u0110ã áp dụng gợi ý AI');
      await loadThesisDetail(state.aiApplyThesisId);
    } catch (err) {
      showToast(`Lỗi áp dụng: ${err.message}`, 'error');
    } finally {
      btn.disabled = false;
      btn.textContent = 'Xác nhận áp dụng';
      state.aiApplyThesisId   = null;
      state.aiSelectedRecIds  = [];
    }
  });

  // 8. Watchlist add modal form
  bindWatchlistAddModal();

  // 9. Decision section wiring
  el('newDecisionBtn')?.addEventListener('click', openDecisionModal);
  bindDecisionFormEvents();
  bindDecisionTabs();
  bindLessonPersistedEvent();

  // 10. Leaderboard wiring
  bindLeaderboardSort();

  // 11. Initial parallel load
  await Promise.all([
    loadDashboard(),
    loadBacktesting(),
    loadPortfolio(),
    loadWatchlist(),
    loadDecisions(),
    loadLeaderboard('score'),
  ]);
});
