/**
 * app.js — Entry point (Wave 7 + Wave 2b watchlist + Wave 5 decisions + Wave A leaderboard)
 * Responsibility: import tất cả modules, wire events, khởi động dashboard.
 * Rule: KHÔNG chứa business logic. Chỉ bootstrap + wiring.
 */

import { el, openModal, closeModal } from './utils/dom.js';
import { loadDashboard, loadBacktesting } from './modules/dashboard/dashboard-loader.js';
import { loadThesisDetail }     from './modules/thesis/thesis-service.js';
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

    // Deactivate all tabs + hide all panes
    tabBar.querySelectorAll('.brief-tab').forEach(t => {
      t.classList.remove('active');
      t.setAttribute('aria-selected', 'false');
    });
    document.querySelectorAll('.brief-tab-pane').forEach(p => {
      p.classList.add('hidden');
    });

    // Activate clicked tab + show target pane
    btn.classList.add('active');
    btn.setAttribute('aria-selected', 'true');
    document.getElementById(targetId)?.classList.remove('hidden');
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
    const ticker = form.querySelector('#wlTickerInput')?.value?.trim();
    const note   = form.querySelector('#wlNoteInput')?.value?.trim() ?? '';
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
      // Lazy-load lessons on first switch
      const wrap = el('lessonsListWrap');
      if (wrap && wrap.innerHTML.includes('Đang tải') || wrap?.children.length === 0) {
        await loadLessons();
      }
    }
  });
}

// ---------------------------------------------------------------------------
// Bootstrap
// ---------------------------------------------------------------------------
document.addEventListener('DOMContentLoaded', async () => {

  // 1. Bind brief tab switcher
  bindBriefTabs();

  // 2. Bind thesis form + delete confirm (submit handlers)
  bindThesisFormEvents({
    onThesisSaved: async (thesisId) => {
      await loadDashboard();
      if (thesisId) await loadThesisDetail(thesisId);
      // Reload portfolio khi thesis thay đổi (có thể ảnh hưởng view=thesis)
      await loadPortfolio();
      // Reload leaderboard khi thesis thay đổi
      await loadLeaderboard();
    },
  });

  // 3. Bind AI suggest buttons (thesis / assumption / catalyst modals)
  bindSuggestEvents();

  // 4. Toolbar buttons
  el('newThesisBtn')?.addEventListener('click', openNewThesisModal);
  el('reloadBtn')?.addEventListener('click', async () => {
    await loadDashboard();
    await loadBacktesting();
    await loadPortfolio();
    await loadWatchlist();
    await loadDecisions();
    await loadLeaderboard();
  });
  el('statusFilter')?.addEventListener('change', loadDashboard);

  // 5. Form row add buttons (inline trong modal)
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
    btn.textContent = 'Đang áp dụng…';
    try {
      await sendJson(
        `${thesisApiBase()}/${state.aiApplyThesisId}/recommendations/apply`,
        'POST',
        { recommendation_ids: state.aiSelectedRecIds },
      );
      closeModal('aiApplyModal');
      showToast('✅ Đã áp dụng gợi ý AI');
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
  // openDecisionModal() fetch thesis list trước khi show modal —
  // không dùng openModal() generic vì sẽ bỏ trống dropdown thesis.
  el('newDecisionBtn')?.addEventListener('click', openDecisionModal);
  bindDecisionFormEvents();
  bindDecisionTabs();

  // 10. Initial load (tất cả song song)
  await Promise.all([
    loadDashboard(),
    loadBacktesting(),
    loadPortfolio(),
    loadWatchlist(),
    loadDecisions(),
    loadLeaderboard(),
  ]);
});
