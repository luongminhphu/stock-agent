/**
 * app.js — Entry point (Wave 7 + Wave 2b watchlist + Wave 5 decisions + Wave A leaderboard)
 * Owner: dashboard (static adapter)
 */

import { loadBriefing }          from './modules/briefing/briefing-loader.js';
import { loadPortfolio }         from './modules/portfolio/portfolio-loader.js';
import { loadWatchlist }         from './modules/watchlist/watchlist-loader.js';
import { loadTheses }            from './modules/thesis/thesis-loader.js';
import { loadBacktesting }       from './modules/backtesting/backtesting-loader.js';
import { loadDashboardSummary }  from './modules/dashboard/dashboard-loader.js';
import { loadLeaderboard }       from './modules/leaderboard/leaderboard-loader.js';
import {
  loadDecisions,
  loadLessons,
  bindDecisionFormEvents,
  openDecisionModal,
} from './modules/decision/decision-loader.js';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const el = id => document.getElementById(id);

// ---------------------------------------------------------------------------
// Brief tab switcher
// ---------------------------------------------------------------------------

function bindBriefTabs() {
  const tabBar = document.querySelector('.brief-tab-bar');
  if (!tabBar) return;

  tabBar.addEventListener('click', async (e) => {
    const btn = e.target.closest('.brief-tab');
    if (!btn) return;

    const target = btn.dataset.tab;

    tabBar.querySelectorAll('.brief-tab').forEach(t => t.classList.remove('active'));
    btn.classList.add('active');

    const morningWrap = el('morningBriefWrap');
    const eodWrap     = el('eodBriefWrap');

    if (target === 'morning') {
      morningWrap?.classList.remove('hidden');
      eodWrap?.classList.add('hidden');
    } else {
      morningWrap?.classList.add('hidden');
      eodWrap?.classList.remove('hidden');
      // Lazy-load EOD on first switch
      const wrap = el('eodBriefWrap');
      if (wrap && (wrap.innerHTML.includes('Đang tải') || wrap.children.length === 0)) {
        await loadBriefing('eod');
      }
    }
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

    const sortBy = btn.dataset.lbSort;
    loadLeaderboard(sortBy);
  });
}

// ---------------------------------------------------------------------------
// Bootstrap
// ---------------------------------------------------------------------------
document.addEventListener('DOMContentLoaded', async () => {

  // 1. Bind brief tab switcher
  bindBriefTabs();

  // 2. Load morning briefing immediately (default tab)
  await loadBriefing('morning');

  // 3. Portfolio
  loadPortfolio();

  // 4. Watchlist
  loadWatchlist();

  // 5. Theses
  loadTheses();

  // 6. Backtesting
  loadBacktesting();

  // 7. Dashboard summary / KPIs
  loadDashboardSummary();

  // 8. Leaderboard
  bindLeaderboardSort();
  loadLeaderboard('score');

  // 9. Decision section wiring
  // openDecisionModal() fetch thesis list trước khi show modal —
  // bindDecisionFormEvents() wire form submit
  el('newDecisionBtn')?.addEventListener('click', openDecisionModal);
  bindDecisionFormEvents();
  bindDecisionTabs();

  // 10. Initial parallel loads
  await Promise.all([
    loadDecisions(),
  ]);
});
