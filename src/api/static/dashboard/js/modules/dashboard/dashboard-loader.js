/**
 * dashboard-loader.js
 * Owner: modules/dashboard
 */

import { el }               from '../../utils/dom.js';
import { apiBase, briefingApiBase, getJson } from '../../api/client.js';
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

/** Format số VNĐ compact */
function fmtVnd(val) {
  if (val == null || isNaN(val)) return '—';
  const abs = Math.abs(val);
  if (abs >= 1_000_000_000) return (val / 1_000_000_000).toFixed(2) + 'B';
  if (abs >= 1_000_000)     return (val / 1_000_000).toFixed(2) + 'M';
  return new Intl.NumberFormat('vi-VN').format(Math.round(val));
}

// ---------------------------------------------------------------------------
// [wave-fe2] Tier Breakdown pill bar
// Reads from /theses/aggregate → tier_breakdown: { A, B, C, D }
// Renders inline pills: A: 3 · B: 5 · C: 2 · D: 1
// Hidden when no data or all zero.
// ---------------------------------------------------------------------------
export function renderTierBreakdown(aggregate) {
  const wrap = el('tierBreakdown');
  if (!wrap) return;

  const tb = aggregate?.tier_breakdown ?? aggregate?.tiers ?? null;
  if (!tb) { wrap.classList.add('hidden'); return; }

  const tiers = ['A', 'B', 'C', 'D'];
  const total = tiers.reduce((s, t) => s + (parseInt(tb[t] ?? tb[t.toLowerCase()] ?? 0, 10)), 0);
  if (total === 0) { wrap.classList.add('hidden'); return; }

  const tierMeta = {
    A: { cls: 'tier-pill--a', label: 'A' },
    B: { cls: 'tier-pill--b', label: 'B' },
    C: { cls: 'tier-pill--c', label: 'C' },
    D: { cls: 'tier-pill--d', label: 'D' },
  };

  wrap.classList.remove('hidden');
  wrap.innerHTML = `
    <span class="tier-bar-label">Tier phân bổ</span>
    <div class="tier-pills">
      ${tiers.map(t => {
        const count = parseInt(tb[t] ?? tb[t.toLowerCase()] ?? 0, 10);
        if (count === 0) return '';
        return `<span class="tier-pill ${tierMeta[t].cls}" title="Tier ${t}: ${count} thesis">${tierMeta[t].label} <strong>${count}</strong></span>`;
      }).join('')}
    </div>
    <span class="tier-bar-total muted">${total} theses</span>
  `;
}

// ---------------------------------------------------------------------------
// [wave-fe2] Alerts Triggered Strip
// Reads from /alerts/triggered → items[]
// Each item: { ticker, label, triggered_at, triggered_price, priority }
// Hidden when empty. Max 5 items shown.
// ---------------------------------------------------------------------------
export function renderAlertsStrip(alerts) {
  const wrap = el('alertsTriggeredStrip');
  if (!wrap) return;

  const items = Array.isArray(alerts) ? alerts : (alerts?.items ?? []);
  if (!items.length) { wrap.classList.add('hidden'); return; }

  const priorityCls = { HIGH: 'alert-item--high', MEDIUM: 'alert-item--medium', LOW: 'alert-item--low' };
  const priorityIcon = { HIGH: '🔴', MEDIUM: '🟡', LOW: '🔵' };

  const shown = items.slice(0, 5);
  const overflow = items.length - shown.length;

  wrap.classList.remove('hidden');
  wrap.innerHTML = `
    <div class="alerts-strip-label">🔔 Alerts đã kích hoạt</div>
    <div class="alerts-strip-items">
      ${shown.map(a => {
        const p = (a.priority ?? 'MEDIUM').toUpperCase();
        const triggeredAt = a.triggered_at ? new Date(a.triggered_at).toLocaleString('vi-VN', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' }) : '—';
        const price = a.triggered_price != null ? new Intl.NumberFormat('vi-VN').format(a.triggered_price) : null;
        return `
          <div class="alert-item ${priorityCls[p] ?? 'alert-item--medium'}">
            <span class="alert-priority">${priorityIcon[p] ?? '🟡'}</span>
            <span class="alert-ticker">${a.ticker ?? '—'}</span>
            <span class="alert-label">${a.label ?? a.condition_type ?? ''}</span>
            ${price ? `<span class="alert-price">@ ${price}</span>` : ''}
            <span class="alert-time muted">${triggeredAt}</span>
          </div>`;
      }).join('')}
      ${overflow > 0 ? `<div class="alerts-strip-more muted">+${overflow} alerts khác</div>` : ''}
    </div>
  `;
}

// ---------------------------------------------------------------------------
// Wave Dashboard-1: Action Surface
// Derives from stats + catalysts already fetched in loadDashboard().
// Renders actionable nudges above the brief strip.
// Hidden when all signals = 0 (no noise on clean days).
// ---------------------------------------------------------------------------
export function renderActionSurface(stats, catalysts) {
  const wrap = el('actionSurface');
  if (!wrap) return;

  const reviewsToday  = parseInt(stats?.reviews_today         ?? stats?.review_count_today  ?? 0, 10);
  const riskyCount    = parseInt(stats?.risky_theses          ?? stats?.risky_thesis_count  ?? 0, 10);
  const upcoming7d    = parseInt(stats?.upcoming_catalysts_7d ?? stats?.upcoming_7d          ?? 0, 10);
  // [wave-fe1] stale review nudge
  const staleCount    = parseInt(stats?.stale_review_count    ?? 0, 10);
  const staleDays     = stats?.stale_review_days ?? 14;

  const items = [];

  if (reviewsToday > 0) {
    items.push({
      icon: '⚠️',
      cls: 'as-item--warn',
      text: `${reviewsToday} thesis cần review hôm nay`,
      target: 'thesesTableWrap',
      label: 'Review ngay',
    });
  }

  if (staleCount > 0) {
    items.push({
      icon: '🕐',
      cls: 'as-item--warn',
      text: `${staleCount} thesis chưa review trong ${staleDays} ngày`,
      target: 'thesesTableWrap',
      label: 'Review ngay',
    });
  }

  if (riskyCount > 0) {
    items.push({
      icon: '🔴',
      cls: 'as-item--danger',
      text: `${riskyCount} thesis có score thấp (< 40)`,
      target: 'thesesTableWrap',
      label: 'Xem thesis',
    });
  }

  if (upcoming7d > 0) {
    items.push({
      icon: '📅',
      cls: 'as-item--info',
      text: `${upcoming7d} catalyst trong 7 ngày tới`,
      target: 'catalystList',
      label: 'Xem lịch',
    });
  }

  if (!items.length) {
    wrap.classList.add('hidden');
    wrap.innerHTML = '';
    return;
  }

  wrap.classList.remove('hidden');
  wrap.innerHTML = `
    <div class="as-label">🎯 Việc cần làm hôm nay</div>
    <div class="as-items">
      ${items.map(item => `
        <div class="as-item ${item.cls}">
          <span class="as-icon">${item.icon}</span>
          <span class="as-text">${item.text}</span>
          <button class="as-cta" data-scroll-to="${item.target}" type="button">${item.label} →</button>
        </div>
      `).join('')}
    </div>`;

  // Wire scroll-to buttons
  wrap.querySelectorAll('[data-scroll-to]').forEach(btn => {
    btn.addEventListener('click', () => {
      const target = document.getElementById(btn.dataset.scrollTo);
      if (target) target.scrollIntoView({ behavior: 'smooth', block: 'start' });
    });
  });
}

export async function loadDashboard() {
  const status  = el('statusFilter')?.value ?? 'active';
  const base    = apiBase();
  const briefBase = briefingApiBase();
  el('errorBanner')?.classList.add('hidden');
  showLoadingSkeletons();

  try {
    const [
      stats, theses, verdictAccuracy, catalysts,
      latestScan, latestMorningBrief, latestEodBrief,
      portfolioTrades,
      briefFeedback,
      // [wave-fe2] new fetches
      alertsTriggered,
      thesisAggregate,
    ] = await Promise.all([
      getJson(`${base}/stats`).catch(() => null),
      getJson(`${base}/theses?status=${status}`).catch(() => []),
      getJson(`${base}/backtesting/verdict-accuracy`).catch(() => null),
      getJson(`${base}/catalysts/upcoming?days=30`).catch(() => []),
      getJson(`${base}/scan/latest`).catch(() => null),
      getJson(`${briefBase}/latest?phase=morning`).catch(() => null),
      getJson(`${briefBase}/latest?phase=eod`).catch(() => null),
      getJson(`${base}/portfolio/trades`).catch(() => null),
      getJson(`${briefBase}/feedback-summary`).catch(() => null),
      getJson(`${base}/alerts/triggered`).catch(() => null),
      getJson(`${base}/theses/aggregate`).catch(() => null),
    ]);

    renderSummary(stats, portfolioTrades);

    // [wave-fe2] tier breakdown + alerts strip
    renderTierBreakdown(thesisAggregate);
    renderAlertsStrip(alertsTriggered);

    // Wave Dashboard-1: action surface — derive from already-fetched data
    renderActionSurface(stats, catalysts?.items ?? catalysts ?? []);

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
      brief_feedback:            briefFeedback ?? null,
    });

    // Leaderboard — fire-and-forget
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
  ];

  kpis.forEach(({ id, raw }) => {
    const node = el(id);
    if (!node || raw == null) return;
    const next = parseInt(raw, 10);
    const prev = parseCurrentValue(node);
    if (prev !== next) {
      countUp(node, prev, next, 600);
      flashValue(node);
    }
  });

  // reviews today sub-label
  const reviewsTodayRaw = parseInt(s.reviews_today ?? s.review_count_today ?? 0, 10);
  const reviewsTodayEl = el('reviewsToday');
  if (reviewsTodayEl) {
    reviewsTodayEl.textContent = reviewsTodayRaw > 0 ? `${reviewsTodayRaw} cần review hôm nay` : '';
  }

  // [wave-fe1] stale review KPI
  const staleCount = parseInt(s.stale_review_count ?? 0, 10);
  const staleDays  = s.stale_review_days ?? 14;
  const staleEl    = el('staleReview');
  const staleSubEl = el('staleReviewSub');
  const staleCard  = el('staleReviewCard');
  if (staleEl) {
    const prevStale = parseCurrentValue(staleEl);
    if (prevStale !== staleCount) {
      countUp(staleEl, prevStale, staleCount, 600);
      flashValue(staleEl);
    }
  }
  if (staleSubEl) {
    staleSubEl.textContent = `chưa review ${staleDays}d`;
  }
  if (staleCard) {
    // Dim card when count = 0 (no stale theses — green signal)
    staleCard.classList.toggle('signal-card--ok', staleCount === 0);
    staleCard.classList.toggle('signal-card--risk', staleCount > 0);
  }

  // Portfolio KPIs from trades
  const trades = Array.isArray(portfolio) ? portfolio : (portfolio?.items ?? []);
  if (trades.length) {
    const totalValue   = trades.reduce((s, t) => s + (t.market_value ?? 0), 0);
    const totalCost    = trades.reduce((s, t) => s + (t.cost_basis   ?? 0), 0);
    const unrealizedPnl = totalValue - totalCost;
    const pnlPct        = totalCost > 0 ? (unrealizedPnl / totalCost) * 100 : null;

    const pvEl  = el('portfolioValue');
    const pnlEl = el('unrealizedPnl');
    const pnlPctEl = el('unrealizedPnlPct');
    const pvSubEl  = el('portfolioValueSub');

    if (pvEl) {
      const prev = parseCurrentValue(pvEl);
      if (Math.abs(prev - totalValue) > 1) {
        countUp(pvEl, prev, totalValue / 1_000_000, 800);
        pvEl.textContent = fmtVnd(totalValue);
        flashValue(pvEl);
      }
    }
    if (pvSubEl) pvSubEl.textContent = `${trades.length} vị thế`;

    if (pnlEl) {
      pnlEl.textContent = (unrealizedPnl >= 0 ? '+' : '') + fmtVnd(unrealizedPnl);
      pnlEl.className = 'signal-value ' + (unrealizedPnl >= 0 ? 'text-success' : 'text-danger');
      flashValue(pnlEl);
    }
    if (pnlPctEl && pnlPct != null) {
      pnlPctEl.textContent = (pnlPct >= 0 ? '+' : '') + pnlPct.toFixed(2) + '%';
      pnlPctEl.className = pnlPct >= 0 ? 'signal-sub text-success' : 'signal-sub text-danger';
    }
  }
}
