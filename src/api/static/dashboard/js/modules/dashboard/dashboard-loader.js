/**
 * dashboard-loader.js
 * Owner: modules/dashboard
 */

import { el }               from '../../utils/dom.js';
import { apiBase, briefingApiBase, getJson } from '../../api/client.js';
import { state }            from '../../state/dashboard-state.js';
import { renderThesesTable, thesisTableSkeletonHTML, emptyDetailHTML } from '../thesis/render-thesis-table.js';
import { loadThesisDetail, confirmDeleteThesis } from '../thesis/thesis-service.js';
import { openEditThesisModal } from '../thesis/thesis-form.js';
import {
  renderVerdicts,
  renderAccuracy,
  renderWorstCalls,
  renderBestCalls,
  initCallsTabs,
} from '../backtesting/render-backtesting.js';
import { renderCatalystCalendar, renderSnapshots } from '../briefing/render-brief.js';
import { loadLeaderboard } from './leaderboard-loader.js';
import { renderHealthHeatmap, refreshHeatmapCell } from './render-heatmap.js';
import { countUp, flashValue } from '../../utils/animate.js';

function wireDeleteThesis(id) {
  const msg = el('deleteModalMsg');
  const btn = el('deleteConfirmBtn');
  if (msg) msg.textContent = 'Bạn có chắc muốn xóa thesis này không? Hành động không thể hoàn tác.';
  if (btn) {
    const fresh = btn.cloneNode(true);
    btn.parentNode.replaceChild(fresh, btn);
    fresh.addEventListener('click', async () => {
      const { thesisApiBase, sendJson } = await import('../../api/client.js');
      const { showToast, closeModal }   = await import('../../utils/dom.js');
      try {
        await sendJson(`${thesisApiBase()}/${id}`, 'DELETE');
        closeModal('deleteModal');
        showToast('🗑 Đã xóa thesis');
        state.selectedThesisId = null;
        await loadDashboard();
      } catch (err) {
        showToast(`Lỗi xóa: ${err.message}`, 'error');
      }
    });
  }
  import('../../utils/dom.js').then(({ openModal }) => openModal('deleteModal'));
}

// ---------------------------------------------------------------------------
// loadDashboard
// ---------------------------------------------------------------------------
export async function loadDashboard() {
  const statusFilter = el('statusFilter')?.value ?? 'active';
  const wrap         = el('thesesTableWrap');
  if (wrap) wrap.innerHTML = thesisTableSkeletonHTML();

  try {
    const [
      thesesRes,
      kpiRes,
      catalystRes,
      verdictRes,
      accuracyRes,
      portfolioRes,
    ] = await Promise.allSettled([
      getJson(`${apiBase()}/theses?status=${statusFilter}`),
      getJson(`${apiBase()}/dashboard/kpis`).catch(() => null),
      getJson(`${apiBase()}/catalysts?upcoming_days=7`).catch(() => []),
      getJson(`${apiBase()}/dashboard/verdicts`).catch(() => null),
      getJson(`${apiBase()}/dashboard/accuracy`).catch(() => null),
      getJson(`${apiBase()}/portfolio`).catch(() => null),
    ]);

    const theses    = thesesRes.status    === 'fulfilled' ? (thesesRes.value?.items    ?? thesesRes.value    ?? []) : [];
    const kpi       = kpiRes.status       === 'fulfilled' ? kpiRes.value       : null;
    const catalysts = catalystRes.status  === 'fulfilled' ? (catalystRes.value?.items ?? catalystRes.value ?? []) : [];
    const verdicts  = verdictRes.status   === 'fulfilled' ? verdictRes.value   : null;
    const accuracy  = accuracyRes.status  === 'fulfilled' ? accuracyRes.value  : null;
    const portfolio = portfolioRes.status === 'fulfilled' ? portfolioRes.value : null;

    state.theses        = theses;
    state.portfolioData = portfolio;

    // KPIs
    if (kpi) {
      const setKpi = (id, val, opts = {}) => {
        const el2 = el(id);
        if (!el2) return;
        if (opts.countUp && typeof val === 'number') {
          countUp(el2, val, { duration: 600, decimals: opts.decimals ?? 0 });
        } else {
          el2.textContent = val ?? '—';
        }
      };
      setKpi('openTheses',      kpi.open_theses,       { countUp: true });
      setKpi('activeTheses',    kpi.active_theses,     { countUp: true });
      setKpi('avgScore',        kpi.avg_score,         { countUp: true, decimals: 1 });
      setKpi('riskyTheses',     kpi.risky_theses,      { countUp: true });
      setKpi('staleReview',     kpi.stale_review,      { countUp: true });
      setKpi('upcoming7d',      kpi.upcoming_catalysts,{ countUp: true });
      setKpi('staleReviewCard', kpi.stale_review,      { countUp: true });
    }

    // Render thesis table
    renderThesesTable(state.theses, {
      onSelect: (id) => loadThesisDetail(id),
      onEdit:   (id) => openEditThesisModal(id, state.theses.find(t => t.id === id)),
      onDelete: (id) => confirmDeleteThesis(id),
    });

    renderHealthHeatmap(state.theses);

    // Catalyst calendar
    renderCatalystCalendar(catalysts);

    // Verdicts + accuracy
    if (verdicts) renderVerdicts(verdicts);
    if (accuracy) renderAccuracy(accuracy);
    if (verdicts) {
      renderWorstCalls(verdicts);
      renderBestCalls(verdicts);
      initCallsTabs();
    }

    // Snapshots
    const snaps = portfolio?.snapshots ?? [];
    if (snaps.length) renderSnapshots(snaps);

    // Brief ticker
    if (typeof window.__briefTickerReady === 'function') window.__briefTickerReady(state.theses);

    // Re-open selected detail
    if (state.selectedThesisId) {
      const still = state.theses.find(t => t.id === state.selectedThesisId);
      if (still) loadThesisDetail(state.selectedThesisId);
      else {
        const wrap2 = el('thesisDetail');
        if (wrap2) { wrap2.classList.add('empty-detail'); wrap2.innerHTML = emptyDetailHTML(); }
        state.selectedThesisId = null;
      }
    }

  } catch (err) {
    console.error('[dashboard] loadDashboard error:', err);
    if (wrap) wrap.innerHTML = `<p class="error-banner">Lỗi tải dashboard: ${err.message}</p>`;
  }
}

// ---------------------------------------------------------------------------
// loadBacktesting
// ---------------------------------------------------------------------------
export async function loadBacktesting() {
  try {
    const data = await getJson(`${apiBase()}/backtesting/summary`).catch(() => null);
    if (!data) return;
    const { renderVerdicts: rv, renderAccuracy: ra } = await import('../backtesting/render-backtesting.js');
    if (data.verdicts) rv(data.verdicts);
    if (data.accuracy) ra(data.accuracy);
  } catch (err) {
    console.warn('[dashboard] loadBacktesting error:', err);
  }
}

// ---------------------------------------------------------------------------
// loadBriefTicker
// ---------------------------------------------------------------------------
export async function loadBriefTicker() {
  try {
    const [morning, eod] = await Promise.allSettled([
      getJson(`${briefingApiBase()}/morning/latest`).catch(() => null),
      getJson(`${briefingApiBase()}/eod/latest`).catch(() => null),
    ]);
    const morningData = morning.status === 'fulfilled' ? morning.value : null;
    const eodData     = eod.status     === 'fulfilled' ? eod.value     : null;

    const { renderMorningBrief, renderEodBrief, renderBriefTicker } = await import('../briefing/render-brief.js');
    if (morningData) renderMorningBrief(morningData);
    if (eodData)     renderEodBrief(eodData);

    if (morningData || eodData) {
      renderBriefTicker({ morning: morningData, eod: eodData }, state.theses);
      window.__briefTickerReady = (theses) => renderBriefTicker({ morning: morningData, eod: eodData }, theses);
    }
  } catch (err) {
    console.warn('[dashboard] loadBriefTicker error:', err);
  }
}

// ---------------------------------------------------------------------------
// Snapshot heatmap refresh on thesis:snapshot-updated
// ---------------------------------------------------------------------------
document.addEventListener('thesis:snapshot-updated', (e) => {
  const { thesisId, snapshotScore } = e.detail ?? {};
  if (thesisId == null || snapshotScore == null) return;
  const thesis = state.theses.find(t => t.id === thesisId);
  if (!thesis) return;
  thesis.score = snapshotScore;
  refreshHeatmapCell(thesis);
});

// ---------------------------------------------------------------------------
// flashValue helper — wire thesis score changes in table
// ---------------------------------------------------------------------------
export function flashThesisScore(thesisId, newScore) {
  const scoreEl = document.querySelector(`[data-thesis-id="${thesisId}"] .thesis-score`);
  if (scoreEl) flashValue(scoreEl, newScore);
}

// ---------------------------------------------------------------------------
// Leaderboard helpers
// ---------------------------------------------------------------------------
export { loadLeaderboard };

// ---------------------------------------------------------------------------
// Snapshot list loader (Wave C detail timeline)
// ---------------------------------------------------------------------------
export async function loadSnapshotList(thesisId) {
  try {
    const data = await getJson(`${apiBase()}/theses/${thesisId}/snapshots`);
    return data?.items ?? data ?? [];
  } catch {
    return [];
  }
}

// ---------------------------------------------------------------------------
// Thesis health breakdown loader
// ---------------------------------------------------------------------------
export async function loadHealthBreakdown() {
  try {
    const data = await getJson(`${apiBase()}/dashboard/health`).catch(() => null);
    if (!data) return;
    const { renderHealthBreakdown } = await import('./render-heatmap.js');
    renderHealthBreakdown(data);
  } catch (err) {
    console.warn('[dashboard] loadHealthBreakdown:', err);
  }
}

// ---------------------------------------------------------------------------
// Catalyst calendar loader (standalone, called from app.js if needed)
// ---------------------------------------------------------------------------
export async function loadCatalystCalendar() {
  try {
    const data = await getJson(`${apiBase()}/catalysts?upcoming_days=7`).catch(() => []);
    const items = Array.isArray(data) ? data : (data?.items ?? []);
    renderCatalystCalendar(items);
  } catch (err) {
    console.warn('[dashboard] loadCatalystCalendar:', err);
  }
}

// ---------------------------------------------------------------------------
// Attention panel data loader
// ---------------------------------------------------------------------------
export async function loadAttentionData() {
  try {
    const data = await getJson(`${apiBase()}/dashboard/attention`).catch(() => null);
    return data;
  } catch {
    return null;
  }
}

// ---------------------------------------------------------------------------
// Portfolio summary loader
// ---------------------------------------------------------------------------
export async function loadPortfolioSummary() {
  try {
    const data = await getJson(`${apiBase()}/portfolio`).catch(() => null);
    state.portfolioData = data;
    return data;
  } catch {
    return null;
  }
}

// ---------------------------------------------------------------------------
// Watchlist badge counts — refresh thesis badge after watchlist change
// ---------------------------------------------------------------------------
export function refreshThesisBadge(thesisId) {
  const row = document.querySelector(`[data-thesis-id="${thesisId}"]`);
  if (!row) return;
  const badge = row.querySelector('.watchlist-badge');
  if (!badge) return;
  getJson(`${apiBase()}/theses/${thesisId}/watchlist-count`)
    .then(d => { badge.textContent = d?.count ?? 0; })
    .catch(() => {});
}

// ---------------------------------------------------------------------------
// Wave A: Leaderboard refresh on decision:logged
// ---------------------------------------------------------------------------
document.addEventListener('decision:logged', () => loadLeaderboard());

// ---------------------------------------------------------------------------
// Wire: thesis:created / thesis:updated → reload dashboard
// ---------------------------------------------------------------------------
document.addEventListener('thesis:created', () => loadDashboard());
document.addEventListener('thesis:updated', (e) => {
  const { thesisId } = e.detail ?? {};
  loadDashboard();
  if (thesisId) loadThesisDetail(thesisId);
});

// ---------------------------------------------------------------------------
// Wire: thesis:deleted → reload dashboard + clear detail
// ---------------------------------------------------------------------------
document.addEventListener('thesis:deleted', () => {
  state.selectedThesisId = null;
  const wrap = el('thesisDetail');
  if (wrap) { wrap.classList.add('empty-detail'); wrap.innerHTML = emptyDetailHTML(); }
  loadDashboard();
});

// ---------------------------------------------------------------------------
// bindReviewDoneListener — heatmap refresh after snapshot review
// ---------------------------------------------------------------------------
function bindReviewDoneListener() {
  document.addEventListener('breakdown:review-done', (e) => {
    const { thesisId } = e.detail ?? {};
    if (thesisId == null) return;
    refreshHeatmapCell(thesisId);
  });
}

bindReviewDoneListener();
