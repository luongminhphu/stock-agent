/**
 * app.js — Entry point (Wave 7 + Wave 2b watchlist + Wave 5 decisions + Wave A leaderboard + Wave D lesson loop + Wave E brief ticker + Wave F brief feedback + Wave G brief generate + Wave 1 UX + Wave 2 memory + AttentionPanel + Wave 1 wire + Wave 2 wire + Wave 3 wire + Wave 4 wire + Wave A gap-wire + market breadth + engine heartbeat + today-loop)
 * Responsibility: import tất cả modules, wire events, khởi động dashboard.
 * Rule: KHÔNG chứa business logic. Chỉ bootstrap + wiring.
 */

import { loadDashboard }         from './modules/dashboard/dashboard-loader.js';
import { loadMarket }            from './modules/market/market-loader.js';
import { loadWatchlist }         from './modules/watchlist/watchlist-loader.js';
import { loadTheses }            from './modules/thesis/thesis-loader.js';
import { loadDecisions,
         loadLessons }           from './modules/decision/decision-loader.js';
import { loadBriefing }          from './modules/briefing/briefing-loader.js';
import { initBriefFeedback,
         loadFeedbackSummary,
         resetFeedbackState }     from './modules/briefing/brief-feedback.js';
import { initBriefGenerate }     from './modules/briefing/brief-generate.js';
import { initBriefTicker }       from './modules/briefing/brief-ticker.js';
import { initThesisActions }     from './modules/thesis/thesis-actions.js';
import { initDecisionActions }   from './modules/decision/decision-actions.js';
import { loadPortfolio }         from './modules/portfolio/portfolio-loader.js';
import { loadLeaderboard }       from './modules/leaderboard/leaderboard-loader.js';
import { loadMemory }            from './modules/memory/memory-loader.js';
import { loadAttentionPanel, startAttentionAutoRefresh } from './modules/attention/attention-loader.js';
import { loadMarketBreadth }    from './modules/market/breadth.js';
import { debounce }             from './utils/debounce.js';
import { loadTodayLoop, startTodayLoopAutoRefresh } from './modules/today-loop/today-loop-loader.js';
import { state }                from './state/dashboard-state.js';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function el(id) { return document.getElementById(id); }

// ---------------------------------------------------------------------------
// Reload orchestration
// ---------------------------------------------------------------------------

function reloadAll() {
  loadDashboard();
  loadMarket();
  loadWatchlist();
  loadTheses();
  loadDecisions();
  loadLessons();
  loadBriefing();
  loadPortfolio();
  loadLeaderboard();
  loadMemory();
  loadAttentionPanel();
  loadMarketBreadth();
  loadTodayLoop();
}

// ---------------------------------------------------------------------------
// KPI card click → scroll to section
// ---------------------------------------------------------------------------

function initKpiClickable() {
  const mapping = [
    { cardId: 'stocksTracked',     targetId: 'watchlistSection' },
    { cardId: 'activeTheses',      targetId: 'thesesTableWrap' },
    { cardId: 'riskyTheses',       targetId: 'thesesTableWrap' },
    { cardId: 'recentDecisions',   targetId: 'decisionsSection' },
  ];

  mapping.forEach(({ cardId, targetId }) => {
    const card   = el(cardId);
    const target = el(targetId);
    if (!card || !target) return;

    card.style.cursor = 'pointer';
    card.setAttribute('role', 'button');
    card.setAttribute('tabindex', '0');
    card.setAttribute('aria-label', `Scroll đến ${targetId}`);

    const handle = () => target.scrollIntoView({ behavior: 'smooth', block: 'start' });
    card.addEventListener('click',   handle);
    card.addEventListener('keydown', e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); handle(); } });
  });
}

// ---------------------------------------------------------------------------
// DOMContentLoaded wire helpers
// ---------------------------------------------------------------------------

function bindSectionToggles() {
  document.querySelectorAll('.section-toggle').forEach(btn => {
    btn.addEventListener('click', () => {
      const targetId = btn.dataset.target;
      const target   = el(targetId);
      if (!target) return;
      target.classList.toggle('collapsed');
      btn.setAttribute('aria-expanded', !target.classList.contains('collapsed'));
    });
  });
}

function bindRefreshBtn() {
  const btn = el('refreshBtn');
  if (!btn) return;
  btn.addEventListener('click', () => {
    btn.disabled = true;
    reloadAll();
    setTimeout(() => { btn.disabled = false; }, 2000);
  });
}

function bindReloadBtn() {
  const reloadBtn = el('reloadBtn');
  if (!reloadBtn) return;
  reloadBtn.addEventListener('click', () => {
    loadDashboard();
    loadMarket();
    loadWatchlist();
    loadTheses();
    loadDecisions();
    loadLessons();
    loadBriefing();
    loadPortfolio();
    loadLeaderboard();
    loadMemory();
    loadAttentionPanel();
    loadTodayLoop();
  });
}

function bindWatchlistActions() {
  const addWatchlistBtn = el('addWatchlistBtn');
  if (!addWatchlistBtn) return;

  addWatchlistBtn.addEventListener('click', () => {
    const modal = el('addWatchlistModal');
    if (modal) {
      modal.classList.remove('hidden');
      el('watchlistTickerInput')?.focus();
    }
  });

  el('cancelWatchlistModal')?.addEventListener('click', () => {
    el('addWatchlistModal')?.classList.add('hidden');
  });

  el('addWatchlistModal')?.addEventListener('click', e => {
    if (e.target === el('addWatchlistModal')) el('addWatchlistModal')?.classList.add('hidden');
  });

  const form = el('addWatchlistForm');
  if (!form) return;
  form.addEventListener('submit', async e => {
    e.preventDefault();
    const ticker    = el('watchlistTickerInput')?.value.trim().toUpperCase();
    const priority  = el('watchlistPriorityInput')?.value ?? 'medium';
    const tags      = el('watchlistTagsInput')?.value.trim();
    const notes     = el('watchlistNotesInput')?.value.trim();
    const submitBtn = form.querySelector('button[type="submit"]');

    if (!ticker) return;

    submitBtn.disabled    = true;
    submitBtn.textContent = 'Đang thêm...';

    try {
      const res = await fetch('/api/v1/watchlist/', {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({ ticker, priority, tags: tags ? tags.split(',').map(t => t.trim()) : [], notes }),
      });

      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail ?? `HTTP ${res.status}`);
      }

      el('addWatchlistModal')?.classList.add('hidden');
      form.reset();
      loadWatchlist();
    } catch (err) {
      const errEl = el('addWatchlistError');
      if (errEl) { errEl.textContent = `Lỗi: ${err.message}`; errEl.classList.remove('hidden'); }
    } finally {
      submitBtn.disabled    = false;
      submitBtn.textContent = 'Thêm mã';
    }
  });
}

function bindThesisActions() {
  initThesisActions();
}

function bindDecisionActions() {
  initDecisionActions();
}

function bindBriefActions() {
  initBriefFeedback();
  initBriefGenerate();
  initBriefTicker();
}

// ---------------------------------------------------------------------------
// Cross-module event wire
// ---------------------------------------------------------------------------

function bindCrossModuleEvents() {
  // When a thesis is saved, refresh both thesis list and decisions
  document.addEventListener('thesis:saved', () => {
    loadTheses();
    loadDecisions();
    loadLessons();
  });

  // When a decision is saved
  document.addEventListener('decision:saved', () => {
    loadDecisions();
    loadLessons();
    loadTheses();
  });

  // When a watchlist item changes
  document.addEventListener('watchlist:changed', () => {
    loadWatchlist();
    loadTheses();
  });

  // When a brief is generated or feedback given
  document.addEventListener('brief:generated', () => {
    loadBriefing();
    resetFeedbackState();
    loadFeedbackSummary();
  });

  document.addEventListener('brief:feedback', () => {
    loadFeedbackSummary();
  });

  // Market breadth refresh
  document.addEventListener('market:breadth-refresh', () => {
    loadMarketBreadth();
  });
}

// ---------------------------------------------------------------------------
// Wave 1 wire: watchlist scan → reload sections
// ---------------------------------------------------------------------------

function bindWatchlistScanWire() {
  document.addEventListener('watchlist:scan-requested', () => {
    loadWatchlist();
    loadMarket();
  });

  document.addEventListener('watchlist:scan-complete', () => {
    loadDashboard();
    loadMarket();
    loadWatchlist();
  });
}

// ---------------------------------------------------------------------------
// Wave 2 wire: attention panel auto-refresh + watchlist changed
// ---------------------------------------------------------------------------

function bindWatchlistChangedWire() {
  document.addEventListener('watchlist:changed', () => {
    loadAttentionPanel();
    loadTodayLoop();
  });
  document.addEventListener('watchlist:scan-complete', () => {
    loadAttentionPanel();
  });
}

// ---------------------------------------------------------------------------
// Wave 3 wire: trade confirmed → reload decisions + lessons + attention
// ---------------------------------------------------------------------------

function bindTradeConfirmedWire() {
  document.addEventListener('trade:confirmed', () => {
    loadDecisions();
    loadLessons();
    loadTheses();
    loadAttentionPanel();
  });
}

// ---------------------------------------------------------------------------
// Wave 4 wire: thesis review submitted → reload theses + attention
// ---------------------------------------------------------------------------

function bindThesisReviewWire() {
  document.addEventListener('thesis:review-submitted', () => {
    loadTheses();
    loadAttentionPanel();
  });

  document.addEventListener('thesis:invalidated', () => {
    loadTheses();
    loadDecisions();
    loadAttentionPanel();
  });
}

// ---------------------------------------------------------------------------
// Wave A gap-wire: navigate:thesis + navigate:decision → scroll + highlight
// ---------------------------------------------------------------------------

function bindNavigationWires() {
  document.addEventListener('navigate:thesis', e => {
    const { thesisId } = e.detail ?? {};
    if (!thesisId) return;
    const wrap = el('thesesTableWrap');
    if (wrap) wrap.scrollIntoView({ behavior: 'smooth', block: 'start' });
    // Highlight row after table renders
    setTimeout(() => {
      const row = document.querySelector(`[data-thesis-id="${thesisId}"]`);
      if (!row) return;
      row.classList.add('row--highlight');
      row.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
      setTimeout(() => row.classList.remove('row--highlight'), 2500);
    }, 400);
  });

  document.addEventListener('navigate:decision', e => {
    const { decisionId } = e.detail ?? {};
    if (!decisionId) return;
    const wrap = el('decisionsSection');
    if (wrap) wrap.scrollIntoView({ behavior: 'smooth', block: 'start' });
    setTimeout(() => {
      const row = document.querySelector(`[data-decision-id="${decisionId}"]`);
      if (!row) return;
      row.classList.add('row--highlight');
      row.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
      setTimeout(() => row.classList.remove('row--highlight'), 2500);
    }, 400);
  });
}

// ---------------------------------------------------------------------------
// Wave B (future): portfolio position changed
// ---------------------------------------------------------------------------
// document.addEventListener('portfolio:position-changed', () => { loadPortfolio(); loadTheses(); });

// ---------------------------------------------------------------------------
// Engine heartbeat — polling watchdog
// ---------------------------------------------------------------------------

const HEARTBEAT_INTERVAL_MS = 60_000; // 1 min

function startEngineHeartbeat() {
  const badge = el('engineHeartbeatBadge');
  if (!badge) return;

  const ping = async () => {
    try {
      const res  = await fetch('/api/v1/engine/status', { cache: 'no-store' });
      const data = await res.json();
      const alive = data?.status === 'running' || data?.status === 'ok';
      badge.textContent = alive ? '• Engine' : '⚠ Engine';
      badge.classList.toggle('heartbeat--alive', alive);
      badge.classList.toggle('heartbeat--dead',  !alive);
      badge.title = alive
        ? `Engine running · ${new Date().toLocaleTimeString('vi-VN')}`
        : `Engine offline · ${data?.detail ?? ''}`;
    } catch {
      badge.textContent = '⚠ Engine';
      badge.classList.remove('heartbeat--alive');
      badge.classList.add('heartbeat--dead');
      badge.title = 'Engine unreachable';
    }
  };

  ping();
  setInterval(ping, HEARTBEAT_INTERVAL_MS);
}

// ---------------------------------------------------------------------------
// Boot
// ---------------------------------------------------------------------------

document.addEventListener('DOMContentLoaded', () => {
  // Initial data load
  loadDashboard();
  loadMarket();
  loadWatchlist();
  loadTheses();
  loadDecisions();
  loadLessons();
  loadBriefing();
  loadPortfolio();
  loadFeedbackSummary();
  loadMarketBreadth();

  // UI wire-ups
  bindSectionToggles();
  bindRefreshBtn();
  bindReloadBtn();
  bindWatchlistActions();
  bindThesisActions();
  bindDecisionActions();
  bindBriefActions();
  bindCrossModuleEvents();
  bindWatchlistScanWire();
  bindWatchlistChangedWire();
  bindTradeConfirmedWire();
  bindThesisReviewWire();
  bindNavigationWires();
  startEngineHeartbeat();

  loadLeaderboard();
  loadMemory();
  loadAttentionPanel();
  loadTodayLoop();          // today-loop: thesis digest + market mood + signal badge
  initKpiClickable();

  // Wave 2 wire: attention panel auto-refresh mỗi 5 phút
  startAttentionAutoRefresh();
  startTodayLoopAutoRefresh(); // today-loop auto-refresh mỗi 10 phút
});
