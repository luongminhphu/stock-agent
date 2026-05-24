/**
 * app.js — Entry point (Wave 7 + Wave 2b watchlist + Wave 5 decisions + Wave A leaderboard + Wave D lesson loop + Wave E brief ticker + Wave F brief feedback + Wave G brief generate + Wave 1 UX + Wave 2 memory + AttentionPanel + Wave 1 wire + Wave 2 wire + Wave 3 wire)
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
import { loadMemory }           from './modules/memory/memory-loader.js';
import { loadAttentionPanel, startAttentionAutoRefresh } from './modules/attention/attention-loader.js';
import { debounce }             from './utils/debounce.js';
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
// Wave 1 UX: Brief auto-open theo giờ trong ngày (GMT+7)
// ---------------------------------------------------------------------------
function initBriefAutoOpen() {
  const collapsible = document.getElementById('briefCollapsible');
  if (!collapsible) return;

  const now = new Date();
  const vnHour = (now.getUTCHours() + 7) % 24;
  const vnMin  = now.getUTCMinutes();
  const vnTime = vnHour + vnMin / 60;

  const isMorningWindow = vnTime >= 6 && vnTime < 11;
  const isEodWindow     = vnTime >= 14.5 && vnTime <= 18.5;

  if (!isMorningWindow && !isEodWindow) return;

  collapsible.open = true;

  const targetTab = isMorningWindow ? 'morning' : 'eod';
  const tabBar = collapsible.querySelector('.brief-tab-bar');
  if (!tabBar) return;

  tabBar.querySelectorAll('.brief-tab').forEach(t => {
    const isTarget = t.dataset.tab === targetTab;
    t.classList.toggle('active', isTarget);
    t.setAttribute('aria-selected', String(isTarget));
  });

  const morningPane = document.getElementById('morningBriefWrap');
  const eodPane     = document.getElementById('eodBriefWrap');
  if (isMorningWindow) {
    morningPane?.classList.remove('hidden');
    eodPane?.classList.add('hidden');
  } else {
    eodPane?.classList.remove('hidden');
    morningPane?.classList.add('hidden');
  }
}

// ---------------------------------------------------------------------------
// Wave 1 UX: KPI cards clickable
// ---------------------------------------------------------------------------
export function initKpiClickable() {
  const scrollTo = (targetId, offset = 0) => {
    const el = document.getElementById(targetId);
    if (!el) return;
    const y = el.getBoundingClientRect().top + window.scrollY - offset;
    window.scrollTo({ top: y, behavior: 'smooth' });
  };

  const kpiMap = [
    {
      cardId:   'riskyTheses',
      targetId: 'thesisBoardTitle',
      label:    'Xem thesis rủi ro',
      onEnter:  () => {
        const filter = document.getElementById('statusFilter');
        if (filter && filter.value !== 'active') {
          filter.value = 'active';
          filter.dispatchEvent(new Event('change'));
        }
      },
    },
    {
      cardId:   'staleReviewCard',
      targetId: 'thesesTableWrap',
      label:    'Xem thesis cần review',
    },
    {
      cardId:   'upcoming7d',
      targetId: 'catalystList',
      label:    'Xem catalyst calendar',
    },
    {
      cardId:   'openTheses',
      targetId: 'thesisBoardTitle',
      label:    'Xem thesis board',
    },
  ];

  kpiMap.forEach(({ cardId, targetId, label, onEnter }) => {
    const card = document.getElementById(cardId)
      ?? document.querySelector(`[id="${cardId}"]`);
    if (!card) return;

    const article = card.tagName === 'ARTICLE' ? card : card.closest('article') ?? card;

    article.classList.add('kpi--clickable');
    article.setAttribute('role', 'button');
    article.setAttribute('tabindex', '0');
    article.setAttribute('aria-label', label);

    const handle = () => {
      onEnter?.();
      scrollTo(targetId, 72);
    };

    article.addEventListener('click', handle);
    article.addEventListener('keydown', e => {
      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); handle(); }
    });
  });
}

// ---------------------------------------------------------------------------
// Wave E: Brief ticker chip click → loadThesisDetail
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
// #3 FIX: Watchlist thesis badge → navigate:thesis → loadThesisDetail
// ---------------------------------------------------------------------------
function bindWatchlistThesisNavigate() {
  document.addEventListener('navigate:thesis', (e) => {
    const { thesisId } = e.detail ?? {};
    if (!thesisId) return;
    loadThesisDetail(thesisId);
    document.getElementById('thesesTableWrap')
      ?.scrollIntoView({ behavior: 'smooth', block: 'start' });
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
      if (wrap && (wrap.innerHTML.includes('Đang tải') || wrap.children.length === 0)) {
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
// Loop wire: trade action → Cluster C auto-refresh
// ---------------------------------------------------------------------------
function bindQuickTradeDecisionRefresh() {
  document.addEventListener('decision:logged', async () => {
    await loadDecisions();
  });
}

// ---------------------------------------------------------------------------
// Wave 1 wire: watchlist mutations → AttentionPanel refresh
// ---------------------------------------------------------------------------
function bindWatchlistAttentionRefresh() {
  document.addEventListener('watchlist:changed', () => {
    loadAttentionPanel();
  });
  document.addEventListener('watchlist:scan-complete', () => {
    loadAttentionPanel();
  });
}

// ---------------------------------------------------------------------------
// Wave 2 wire: AI Review + Briefing generate → AttentionPanel refresh
// ---------------------------------------------------------------------------
function bindReviewAndBriefAttentionRefresh() {
  document.addEventListener('thesis:review-complete', () => {
    loadAttentionPanel();
  });
  document.addEventListener('briefing:generated', () => {
    loadAttentionPanel();
  });
}

// ---------------------------------------------------------------------------
// Wave 3 wire: decision:changed { thesisId } → loadThesisDetail if selected
//
// decision-loader dispatches decision:changed after:
//   - form submit (thesisId từ select — có thể null)
//   - evaluateDecision (thesisId từ DOM data-thesis-id — có thể null)
//   - replayDecision (thesisId từ result.thesis_id — có thể null)
//
// Guard bắt buộc:
//   1. thesisId phải là number hợp lệ (không null, không NaN)
//   2. phải match state.selectedThesisId đang mở
// Nếu không match → skip (không reload nhầm thesis khác đang mở).
// ---------------------------------------------------------------------------
function bindDecisionThesisRefresh() {
  document.addEventListener('decision:changed', (e) => {
    const { thesisId } = e.detail ?? {};
    if (!thesisId || isNaN(thesisId)) return;               // guard: null / NaN
    if (thesisId !== state.selectedThesisId) return;        // guard: chỉ reload nếu đang mở
    loadThesisDetail(thesisId);
  });
}

// ---------------------------------------------------------------------------
// Bootstrap
// ---------------------------------------------------------------------------
document.addEventListener('DOMContentLoaded', async () => {

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

  // 1a. Wave 1: auto-open brief theo giờ VN
  initBriefAutoOpen();

  // 1b. Wave E: brief ticker chips → thesis detail
  bindBriefTickerClick();

  // 1c. Wave F: brief feedback buttons → POST /briefing/{id}/feedback
  bindFeedbackEvents();

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
      const { showToast: toast } = await import('./utils/dom.js');
      toast('✅ Đã áp dụng gợi ý AI');
      await loadThesisDetail(state.aiApplyThesisId);
    } catch (err) {
      const { showToast: toast } = await import('./utils/dom.js');
      toast(`Lỗi áp dụng: ${err.message}`, 'error');
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

  // 11. Loop wire: B/S trade → decision:logged → Cluster C auto-refresh
  bindQuickTradeDecisionRefresh();

  // 12. Wave 1 wire: watchlist mutations → AttentionPanel refresh
  bindWatchlistAttentionRefresh();

  // 12b. Wave 2 wire: AI Review + Briefing generate → AttentionPanel refresh
  bindReviewAndBriefAttentionRefresh();

  // 12c. Wave 3 wire: decision:changed { thesisId } → loadThesisDetail if selected
  bindDecisionThesisRefresh();

  // 13. Initial parallel load
  await Promise.all([
    loadDashboard(),
    loadBacktesting(),
    loadPortfolio(),
    loadWatchlist(),
    loadDecisions(),
    loadLeaderboard('score'),
    loadMemory(),
    loadAttentionPanel(),
  ]);

  // 14. Wave 1: KPI clickable — wire sau khi DOM đã render đủ values
  initKpiClickable();

  // 15. AttentionPanel auto-refresh mỗi 5 phút
  startAttentionAutoRefresh();
});
