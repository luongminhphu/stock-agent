/**
 * dashboard-loader.js
 * Owner: modules/dashboard
 */

import { el }               from '../../utils/dom.js';
import { apiBase, getJson } from '../../api/client.js';
import { state }            from '../../state/dashboard-state.js';
import { renderThesesTable, thesisTableSkeletonHTML, emptyDetailHTML } from '../thesis/render-thesis-table.js';
import { loadThesisDetail }    from '../thesis/thesis-service.js';
import { openEditThesisModal } from '../thesis/thesis-form.js';
import {
  renderVerdicts,
  renderAccuracy,
  renderWorstCalls,
  renderBestCalls,
  initCallsTabs,
} from '../backtesting/render-backtesting.js';
import { renderCatalystList, renderSnapshots } from '../briefing/render-brief.js';
import { loadLeaderboard } from './leaderboard-loader.js';
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
  const worst = negative.slice(0, 5);
  worst._positiveCount = positive.length;
  const best = positive.slice(0, 5);
  best._negativeCount = negative.length;
  return { worst, best };
}

function showLoadingSkeletons() {
  const tableWrap = document.getElementById('thesesTableWrap');
  if (tableWrap) tableWrap.innerHTML = thesisTableSkeletonHTML(5);
  if (!state.selectedThesisId) {
    const detail = el('thesisDetail');
    if (detail) detail.innerHTML = emptyDetailHTML();
  }
}

/** Format số VNĐ compact: 1,234,567 → "1.23M" hoặc "1,234,567" */
function fmtVnd(val) {
  if (val == null || isNaN(val)) return '—';
  const abs = Math.abs(val);
  if (abs >= 1_000_000_000) return (val / 1_000_000_000).toFixed(2) + 'B';
  if (abs >= 1_000_000)     return (val / 1_000_000).toFixed(2) + 'M';
  return new Intl.NumberFormat('vi-VN').format(Math.round(val));
}

export async function loadDashboard() {
  const status = el('statusFilter')?.value ?? 'active';
  const base   = apiBase();
  el('errorBanner')?.classList.add('hidden');
  showLoadingSkeletons();

  try {
    const [
      stats, theses, verdictAccuracy, catalysts,
      latestScan, latestMorningBrief, latestEodBrief,
      portfolioTrades,
      briefFeedback,           // Wave A
    ] = await Promise.all([
      getJson(`${base}/stats`).catch(() => null),
      getJson(`${base}/theses?status=${status}`).catch(() => []),
      getJson(`${base}/backtesting/verdict-accuracy`).catch(() => null),
      getJson(`${base}/catalysts/upcoming?days=30`).catch(() => []),
      getJson(`${base}/scan/latest`).catch(() => null),
      getJson(`${base}/brief/latest?phase=morning`).catch(() => null),
      getJson(`${base}/brief/latest?phase=eod`).catch(() => null),
      getJson(`${base}/portfolio/trades`).catch(() => null),
      getJson(`${base}/brief/feedback-summary`).catch(() => null),  // Wave A
    ]);

    renderSummary(stats, portfolioTrades);

    state.theses = theses?.items ?? theses ?? [];
    renderThesesTable(state.theses, {
      onSelect: (id) => loadThesisDetail(id),
      onEdit:   (id) => openEditThesisModal(id, state.theses.find(t => t.id === id)),
      onDelete: (id) => wireDeleteThesis(id),
    });

    const accuracyRows = normalizeAccuracyRes(verdictAccuracy);
    state.cachedVerdictAccuracy = accuracyRows;
    renderVerdicts(accuracyRows);
    renderAccuracy(accuracyRows);

    renderCatalystList(catalysts?.items ?? catalysts ?? []);
    renderSnapshots({
      latest_scan_at:            latestScan?.scanned_at ?? latestScan?.created_at ?? latestScan?.generated_at ?? null,
      latest_scan_summary:       latestScan?.summary ?? latestScan?.headline ?? null,
      latest_morning_brief_at:   latestMorningBrief?.created_at ?? latestMorningBrief?.generated_at ?? null,
      latest_morning_brief_data: latestMorningBrief ?? null,
      latest_eod_brief_at:       latestEodBrief?.created_at ?? latestEodBrief?.generated_at ?? null,
      latest_eod_brief_data:     latestEodBrief ?? null,
      brief_feedback:            briefFeedback ?? null,            // Wave A
    });

    // Wave B: leaderboard — fire-and-forget, không block main render
    loadLeaderboard().catch(() => null);

    if (state.selectedThesisId) {
      const t = state.theses.find(x => x.id === state.selectedThesisId);
      if (t) await loadThesisDetail(t.id);
      else {
        const detail = el('thesisDetail');
        if (detail) detail.innerHTML = emptyDetailHTML();
        state.selectedThesisId = null;
      }
    }
  } catch (err) {
    const banner = el('errorBanner');
    if (banner) {
      banner.textContent = `Lỗi tải dữ liệu: ${err.message}`;
      banner.classList.remove('hidden');
    }
    console.error('[dashboard-loader] loadDashboard error:', err);
  }
}

export async function loadBacktesting() {
  const base      = apiBase();
  const worstWrap = el('worstCallsWrap');
  const bestWrap  = el('bestCallsWrap');
  const accWrap   = el('accuracyWrap');

  if (worstWrap) worstWrap.innerHTML = '<p class="muted">Đang tải...</p>';
  if (bestWrap)  bestWrap.innerHTML  = '<p class="muted">Đang tải...</p>';

  try {
    if (state.cachedVerdictAccuracy) {
      renderAccuracy(state.cachedVerdictAccuracy);
    } else {
      if (accWrap) accWrap.innerHTML = '<p class="muted">Đang tải...</p>';
      const res  = await getJson(`${base}/backtesting/verdict-accuracy`).catch(() => null);
      const rows = Array.isArray(res) ? res : (res?.items ?? []);
      state.cachedVerdictAccuracy = rows;
      renderAccuracy(rows);
    }

    const perfRes  = await getJson(`${base}/backtesting/thesis-performances`).catch(() => null);
    const perfRows = Array.isArray(perfRes) ? perfRes : (perfRes?.items ?? []);
    const { worst, best } = splitPerformance(perfRows);
    renderWorstCalls(worst);
    renderBestCalls(best);
    initCallsTabs();

  } catch (err) {
    console.error('[dashboard-loader] loadBacktesting error:', err);
    if (accWrap)   accWrap.innerHTML   = '<p class="empty-state">Lỗi tải dữ liệu.</p>';
    if (worstWrap) worstWrap.innerHTML = '<p class="empty-state">Lỗi tải dữ liệu.</p>';
    if (bestWrap)  bestWrap.innerHTML  = '<p class="empty-state">Lỗi tải dữ liệu.</p>';
  }
}

function parseCurrentValue(node) {
  return parseFloat((node.textContent ?? '').replace(/[^\d.-]/g, '')) || 0;
}

export function renderSummary(s, portfolio) {
  if (!s) return;
  const kpis = [
    { id: 'openTheses',       raw: s.open_theses           ?? s.open_thesis_count   },
    { id: 'riskyTheses',      raw: s.risky_theses          ?? s.risky_thesis_count  },
    { id: 'upcoming7d',       raw: s.upcoming_catalysts_7d ?? s.upcoming_7d         },
    { id: 'reviewsToday',     raw: s.reviews_today         ?? s.review_count_today  },
    { id: 'totalReviewsHero', raw: s.total_reviews         ?? s.review_count_total  },
  ];
  for (const { id, raw, suffix = '', decimals = 0 } of kpis) {
    const node = el(id);
    if (!node) continue;
    const num = parseFloat(String(raw ?? '').replace(/[^\d.-]/g, ''));
    if (!isNaN(num)) {
      const oldVal = parseCurrentValue(node);
      countUp(node, num, { duration: 650, decimals, suffix });
      flashValue(node, num >= oldVal);
    } else {
      node.textContent = raw ?? '—';
    }
  }
  const riskyEl  = el('riskyTheses');
  const riskyVal = parseFloat(String(s.risky_theses ?? s.risky_thesis_count ?? '0').replace(/[^\d.-]/g, ''));
  if (riskyEl) {
    const card = riskyEl.closest('.signal-card');
    if (card) card.classList.toggle('signal-card--alert', riskyVal > 0);
    riskyEl.classList.toggle('kpi-risky', riskyVal > 0);
    riskyEl.classList.toggle('kpi-safe',  riskyVal === 0);
  }

  // Portfolio KPIs
  const pvNode      = el('portfolioValue');
  const pvSubNode   = el('portfolioValueSub');
  const pnlNode     = el('unrealizedPnl');
  const pnlPctNode  = el('unrealizedPnlPct');

  if (portfolio) {
    const mv  = portfolio.total_market_value   ?? null;
    const pnl = portfolio.total_unrealized_pnl ?? null;
    const pct = portfolio.total_unrealized_pct ?? null;

    if (pvNode) {
      if (mv != null) {
        pvNode.textContent = fmtVnd(mv);
        flashValue(pvNode, true);
      } else {
        pvNode.textContent = '—';
      }
    }
    if (pvSubNode) {
      const n = portfolio.positions?.length ?? 0;
      pvSubNode.textContent = n > 0 ? `${n} vị thế` : '';
    }

    if (pnlNode) {
      if (pnl != null) {
        const sign = pnl >= 0 ? '+' : '';
        pnlNode.textContent = sign + fmtVnd(pnl);
        pnlNode.className = pnl >= 0 ? 'kpi-safe' : 'kpi-risky';
        flashValue(pnlNode, pnl >= 0);
      } else {
        pnlNode.textContent = '—';
      }
    }

    if (pnlPctNode) {
      if (pct != null) {
        const sign = pct >= 0 ? '+' : '';
        pnlPctNode.textContent = `${sign}${Number(pct).toFixed(2)}%`;
        pnlPctNode.className = pct >= 0 ? 'kpi-safe' : 'kpi-risky';
      } else {
        pnlPctNode.textContent = '';
      }
    }
  }
}
