/**
 * dashboard-loader.js
 * Owner: modules/dashboard
 */

import { el, showToast }           from '../../utils/dom.js';
import { getJson, sendJson, thesisApiBase } from '../../api/client.js';
import { state }                   from '../../state/dashboard-state.js';
import { renderThesesTable, thesisTableSkeletonHTML, emptyDetailHTML } from '../thesis/render-thesis-table.js';
import { loadThesisDetail, confirmDeleteThesis } from '../thesis/thesis-service.js';
import { openEditThesisModal } from '../thesis/thesis-form.js';
import {
  renderKpiCards,
  renderCatalystCalendar,
  renderPortfolioBar,
  renderAccuracyTable,
} from './render-dashboard.js';
import { loadLeaderboard } from './leaderboard-loader.js';
import { renderHealthHeatmap, refreshHeatmapCell } from './render-heatmap.js';
import { countUp, flashValue } from '../../utils/animate.js';


function normalizeAccuracyRes(res) {
  if (!res) return [];
  if (Array.isArray(res)) return res;
  return res.items ?? [];
}

function splitPerformance(rows) {
  if (!rows || !rows.length) return { worst: [], best: [] };
  const withPnl  = rows.filter(r => r.avg_pnl_pct != null && r.snapshot_count > 0);
  const negative = withPnl.filter(r => r.avg_pnl_pct < 0).sort((a, b) => a.avg_pnl_pct - b.avg_pnl_pct);
  const positive = withPnl.filter(r => r.avg_pnl_pct >= 0).sort((a, b) => b.avg_pnl_pct - a.avg_pnl_pct);
  return {
    worst: negative.slice(0, 3),
    best:  positive.slice(0, 3),
  };
}

// ---------------------------------------------------------------------------
// Heatmap cell refresh on thesis:snapshot-updated
// ---------------------------------------------------------------------------
document.addEventListener('thesis:snapshot-updated', (e) => {
  const { thesisId, snapshotScore } = e.detail ?? {};
  if (!thesisId || snapshotScore == null) return;
  const thesis = state.theses.find(t => t.id === thesisId);
  if (!thesis) return;
  thesis.score = snapshotScore;
  refreshHeatmapCell(thesis);
});

// ---------------------------------------------------------------------------
// loadDashboard
// ---------------------------------------------------------------------------
export async function loadDashboard() {
  const wrap = el('thesesTableWrap');
  if (wrap) wrap.innerHTML = thesisTableSkeletonHTML();

  const detail = el('thesisDetail');
  if (detail && detail.innerHTML.trim() === '') {
    detail.classList.add('empty-detail');
    detail.innerHTML = emptyDetailHTML();
  }

  const statusFilter = el('statusFilter')?.value || 'active';

  try {
    const [
      theses,
      kpiData,
      catalysts,
      verdictAccuracy,
    ] = await Promise.allSettled([
      getJson(`${thesisApiBase()}?status=${statusFilter}`),
      getJson('/api/dashboard/kpis'),
      getJson('/api/catalysts?upcoming_days=7'),
      getJson('/api/dashboard/verdict-accuracy'),
    ]).then(rs => rs.map(r => r.status === 'fulfilled' ? r.value : null));

    // KPIs
    if (kpiData) renderKpiCards(kpiData);

    // Catalyst calendar
    const catItems = Array.isArray(catalysts)
      ? catalysts
      : (catalysts?.items ?? []);
    renderCatalystCalendar(catItems);

    // Accuracy table
    const accuracyRows = normalizeAccuracyRes(verdictAccuracy);
    renderAccuracyTable(accuracyRows);

    // Thesis table
    state.theses = theses?.items ?? theses ?? [];
    renderThesesTable(state.theses, {
      onSelect: (id) => loadThesisDetail(id),
      onEdit:   (id) => openEditThesisModal(id, state.theses.find(t => t.id === id)),
      onDelete: (id) => confirmDeleteThesis(id),
    });

    renderHealthHeatmap(state.theses);

    // Portfolio bar
    const portfolioSnap = state.portfolioData;
    if (portfolioSnap) renderPortfolioBar(portfolioSnap, state.theses);

    // Re-open selected thesis detail if any
    if (state.selectedThesisId) {
      loadThesisDetail(state.selectedThesisId);
    }

    // Leaderboard
    loadLeaderboard();

  } catch (err) {
    showToast(`Dashboard lỗi: ${err.message}`, 'error');
    if (wrap) wrap.innerHTML = `<div class="error-banner">Lỗi tải dashboard: ${err.message}</div>`;
  }
}

// ---------------------------------------------------------------------------
// loadBacktesting
// ---------------------------------------------------------------------------
export async function loadBacktesting() {
  try {
    const data = await getJson('/api/dashboard/backtesting-summary');
    if (!data) return;

    const { worst, best } = splitPerformance(data.by_thesis ?? []);

    const worstWrap = el('worstPerformers');
    const bestWrap  = el('bestPerformers');

    if (worstWrap) {
      worstWrap.innerHTML = worst.length
        ? worst.map(r => renderPerfRow(r, 'worst')).join('')
        : '<p class="text-muted" style="padding:var(--space-3)">Chưa có dữ liệu</p>';
    }
    if (bestWrap) {
      bestWrap.innerHTML = best.length
        ? best.map(r => renderPerfRow(r, 'best')).join('')
        : '<p class="text-muted" style="padding:var(--space-3)">Chưa có dữ liệu</p>';
    }
  } catch (_) {
    // silent — không block dashboard
  }
}

function renderPerfRow(r, type) {
  const pct   = r.avg_pnl_pct?.toFixed(1) ?? '—';
  const sign  = r.avg_pnl_pct >= 0 ? '+' : '';
  const cls   = r.avg_pnl_pct >= 0 ? 'text-success' : 'text-danger';
  const count = r.snapshot_count ?? 0;
  return `
    <div class="perf-row" style="display:flex;justify-content:space-between;align-items:center;padding:var(--space-2) var(--space-3);border-bottom:1px solid var(--color-border)">
      <span class="ticker-chip" style="font-weight:600">${r.ticker}</span>
      <span class="${cls}" style="font-weight:600">${sign}${pct}%</span>
      <span class="text-muted text-sm">${count} snaps</span>
    </div>`;
}
