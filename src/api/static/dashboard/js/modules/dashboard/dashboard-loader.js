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
        showToast('\ud83d\uddd1 \u0110ã xóa thesis');
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
  if (val == null || isNaN(val)) return '\u2014';
  const abs = Math.abs(val);
  if (abs >= 1_000_000_000) return (val / 1_000_000_000).toFixed(2) + 'B';
  if (abs >= 1_000_000)     return (val / 1_000_000).toFixed(2) + 'M';
  return new Intl.NumberFormat('vi-VN').format(Math.round(val));
}

// ---------------------------------------------------------------------------
// Tier Breakdown pill bar
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
// Alerts Triggered Strip
// ---------------------------------------------------------------------------
export function renderAlertsStrip(alerts) {
  const wrap = el('alertsTriggeredStrip');
  if (!wrap) return;

  const items = Array.isArray(alerts) ? alerts : (alerts?.items ?? []);
  if (!items.length) { wrap.classList.add('hidden'); return; }

  const priorityCls  = { HIGH: 'alert-chip--high', MEDIUM: 'alert-chip--medium', LOW: 'alert-chip--low' };
  const priorityIcon = { HIGH: '\ud83d\udd34', MEDIUM: '\ud83d\udfe1', LOW: '\ud83d\udd35' };

  const shown    = items.slice(0, 5);
  const overflow = items.length - shown.length;

  wrap.classList.remove('hidden');
  wrap.innerHTML = `
    <span class="alerts-strip__label">Alerts</span>
    ${shown.map(a => {
      const p = (a.priority ?? 'MEDIUM').toUpperCase();
      const price = a.triggered_price != null
        ? new Intl.NumberFormat('vi-VN').format(a.triggered_price)
        : null;
      const at = a.triggered_at
        ? new Date(a.triggered_at).toLocaleString('vi-VN', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' })
        : null;
      return `
        <div class="alert-chip ${priorityCls[p] ?? 'alert-chip--medium'}" title="${a.ticker} \u00b7 ${a.label ?? a.condition_type ?? ''} \u00b7 ${at ?? ''}">
          <span class="alert-chip__ticker">${priorityIcon[p] ?? '\ud83d\udfe1'} ${a.ticker ?? '\u2014'}</span>
          <span class="alert-chip__label">${a.label ?? a.condition_type ?? ''}</span>
          ${price ? `<span class="alert-chip__price">@ ${price}</span>` : ''}
        </div>`;
    }).join('')}
    ${overflow > 0 ? `<span class="alerts-strip__more">+${overflow} khác</span>` : ''}
  `;
}

// ---------------------------------------------------------------------------
// Signals feed — grouped ticker cards
// ---------------------------------------------------------------------------
export function renderSignalsFeed(res) {
  const wrap = el('signalsFeed');
  if (!wrap) return;

  const items = Array.isArray(res) ? res : (res?.items ?? []);

  if (!items.length) {
    wrap.classList.add('hidden');
    wrap.innerHTML = '';
    return;
  }

  const isGrouped = items[0] && Array.isArray(items[0].signal_types);

  const cards = isGrouped
    ? items.map(g => _buildSignalCard(g))
    : _groupClientSide(items).map(g => _buildSignalCard(g));

  wrap.classList.remove('hidden');
  wrap.innerHTML = `
    <div class="signals-section-header">
      <span class="signals-section-title">Tín hiệu kỹ thuật</span>
      <span class="signals-section-meta muted">${items.length} mã \u00b7 ${_totalSignals(items, isGrouped)} lần</span>
    </div>
    <div class="signals-cards">${cards.join('')}</div>
  `;
}

function _groupClientSide(rawItems) {
  const groups = {};
  for (const s of rawItems) {
    const key = s.ticker;
    if (!groups[key]) {
      groups[key] = {
        ticker: s.ticker,
        signal_types: new Set(),
        max_strength: 0,
        max_confidence: 0,
        count: 0,
        last_seen: s.occurred_at,
        source: s.source,
      };
    }
    const g = groups[key];
    if (s.signal_type) g.signal_types.add(s.signal_type);
    g.max_strength   = Math.max(g.max_strength, s.strength ?? 0);
    g.max_confidence = Math.max(g.max_confidence, s.confidence ?? 0);
    g.count++;
    if (s.occurred_at > (g.last_seen ?? '')) g.last_seen = s.occurred_at;
  }
  return Object.values(groups)
    .map(g => ({ ...g, signal_types: [...g.signal_types] }))
    .sort((a, b) => b.max_strength - a.max_strength);
}

function _totalSignals(items, isGrouped) {
  if (isGrouped) return items.reduce((s, g) => s + (g.count ?? 1), 0);
  return items.length;
}

function _buildSignalCard(g) {
  const strength  = Math.round((g.max_strength ?? 0) * 100);
  const conf      = Math.round((g.max_confidence ?? 0) * 100);
  const count     = g.count ?? 1;
  const lastSeen  = g.last_seen
    ? new Date(g.last_seen).toLocaleString('vi-VN', { hour: '2-digit', minute: '2-digit', day: 'numeric', month: 'numeric' })
    : '\u2014';

  const types = (g.signal_types ?? []).map(t =>
    `<span class="sig-type-tag">${_fmtSignalType(t)}</span>`
  ).join('');

  const barCls = strength >= 70 ? 'sig-bar--strong'
    : strength >= 40 ? 'sig-bar--medium'
    : 'sig-bar--weak';

  const countBadge = count > 1
    ? `<span class="sig-count-badge" title="${count} lần trong 7 ngày">\u00d7${count}</span>`
    : '';

  return `
    <div class="signal-card">
      <div class="signal-card-top">
        <span class="signal-card-ticker">${g.ticker}</span>
        ${countBadge}
        <span class="signal-card-time muted">${lastSeen}</span>
      </div>
      <div class="signal-card-types">${types || '<span class="muted">\u2014</span>'}</div>
      <div class="signal-card-bar-row">
        <div class="sig-bar-track" title="Strength ${strength}%">
          <div class="sig-bar-fill ${barCls}" style="width:${strength}%"></div>
        </div>
        ${conf > 0 ? `<span class="sig-conf muted">${conf}%</span>` : ''}
      </div>
    </div>`;
}

function _fmtSignalType(type) {
  const MAP = {
    strong_move:    'STRONG MOVE',
    ma_crossover:   'MA Cross',
    volume_spike:   'Vol Spike',
    rsi_oversold:   'RSI\u2193',
    rsi_overbought: 'RSI\u2191',
    breakout:       'Breakout',
    breakdown:      'Breakdown',
    macd_signal:    'MACD',
    price_above:    'Price\u2191',
    price_below:    'Price\u2193',
  };
  return MAP[type] ?? type.replace(/_/g, ' ').toUpperCase();
}

// ---------------------------------------------------------------------------
// Action Surface
// ---------------------------------------------------------------------------
export function renderActionSurface(stats, catalysts) {
  const wrap = el('actionSurface');
  if (!wrap) return;

  const reviewsToday = parseInt(stats?.reviews_today         ?? stats?.review_count_today  ?? 0, 10);
  const riskyCount   = parseInt(stats?.risky_theses          ?? stats?.risky_thesis_count  ?? 0, 10);
  const upcoming7d   = parseInt(stats?.upcoming_catalysts_7d ?? stats?.upcoming_7d          ?? 0, 10);
  const staleCount   = parseInt(stats?.stale_review_count    ?? 0, 10);
  const staleDays    = stats?.stale_review_days ?? 14;

  const items = [];

  if (reviewsToday > 0) {
    items.push({ icon: '\u26a0\ufe0f', cls: 'as-item--warn', text: `${reviewsToday} thesis cần review hôm nay`, target: 'thesesTableWrap', label: 'Review ngay' });
  }
  if (staleCount > 0) {
    items.push({ icon: '\ud83d\udd50', cls: 'as-item--warn', text: `${staleCount} thesis chưa review trong ${staleDays} ngày`, target: 'thesesTableWrap', label: 'Review ngay' });
  }
  if (riskyCount > 0) {
    items.push({ icon: '\ud83d\udd34', cls: 'as-item--danger', text: `${riskyCount} thesis có score thấp (< 40)`, target: 'thesesTableWrap', label: 'Xem thesis' });
  }
  if (upcoming7d > 0) {
    items.push({ icon: '\ud83d\udcc5', cls: 'as-item--info', text: `${upcoming7d} catalyst trong 7 ngày tới`, target: 'catalystList', label: 'Xem lịch' });
  }

  if (!items.length) { wrap.classList.add('hidden'); wrap.innerHTML = ''; return; }

  wrap.classList.remove('hidden');
  wrap.innerHTML = `
    <div class="as-label">\ud83c\udfaf Việc cần làm hôm nay</div>
    <div class="as-items">
      ${items.map(item => `
        <div class="as-item ${item.cls}">
          <span class="as-icon">${item.icon}</span>
          <span class="as-text">${item.text}</span>
          <button class="as-cta" data-scroll-to="${item.target}" type="button">${item.label} \u2192</button>
        </div>`).join('')}
    </div>`;

  wrap.querySelectorAll('[data-scroll-to]').forEach(btn => {
    btn.addEventListener('click', () => {
      const target = document.getElementById(btn.dataset.scrollTo);
      if (target) target.scrollIntoView({ behavior: 'smooth', block: 'start' });
    });
  });
}

export async function loadDashboard() {
  const status    = el('statusFilter')?.value ?? 'active';
  const base      = apiBase();
  const briefBase = briefingApiBase();
  el('errorBanner')?.classList.add('hidden');
  showLoadingSkeletons();

  try {
    const [
      stats, theses, verdictAccuracy, catalysts,
      latestScan, latestMorningBrief, latestEodBrief,
      portfolioTrades,
      briefFeedback,
      alertsTriggered,
      thesisAggregate,
      recentSignals,
    ] = await Promise.all([
      getJson(`${base}/stats`).catch(() => null),
      getJson(`${base}/theses?status=${status}`).catch(() => []),
      getJson(`${base}/backtesting/verdict-accuracy`).catch(() => null),
      // days=30: catalyst window aligned with scheduler _CATALYST_LOOKAHEAD_DAYS
      getJson(`${base}/catalysts/upcoming?days=30`).catch(() => []),
      getJson(`${base}/scan/latest`).catch(() => null),
      getJson(`${briefBase}/latest?phase=morning`).catch(() => null),
      getJson(`${briefBase}/latest?phase=eod`).catch(() => null),
      getJson(`${base}/portfolio/trades`).catch(() => null),
      getJson(`${briefBase}/feedback-summary`).catch(() => null),
      getJson(`${base}/alerts/triggered`).catch(() => null),
      getJson(`${base}/theses/aggregate`).catch(() => null),
      getJson(`${base}/signals/recent?days=7&limit=30`).catch(() => null),
    ]);

    renderSummary(stats, portfolioTrades);
    renderTierBreakdown(thesisAggregate);
    renderAlertsStrip(alertsTriggered);
    renderSignalsFeed(recentSignals);
    renderActionSurface(stats, catalysts?.items ?? catalysts ?? []);

    state.theses = theses?.items ?? theses ?? [];
    renderThesesTable(state.theses, {
      onSelect: (id) => loadThesisDetail(id),
      onEdit:   (id) => openEditThesisModal(id, state.theses.find(t => t.id === id)),
      onDelete: (id) => wireDeleteThesis(id),
    });

    renderHealthHeatmap(state.theses);

    const accuracyRows = normalizeAccuracyRes(verdictAccuracy);
    state.cachedVerdictAccuracy = accuracyRows;
    renderVerdicts(accuracyRows);
    renderAccuracy(accuracyRows);

    // ←— Catalyst Calendar (timeline view, replaces flat list)
    renderCatalystCalendar(catalysts?.items ?? catalysts ?? []);

    renderSnapshots({
      latest_scan:               latestScan ?? null,
      latest_scan_at:            latestScan?.scanned_at ?? latestScan?.created_at ?? null,
      latest_scan_summary:       latestScan?.summary ?? latestScan?.headline ?? null,
      latest_morning_brief_at:   latestMorningBrief?.created_at ?? latestMorningBrief?.generated_at ?? null,
      latest_morning_brief_data: latestMorningBrief ?? null,
      latest_eod_brief_at:       latestEodBrief?.created_at ?? latestEodBrief?.generated_at ?? null,
      latest_eod_brief_data:     latestEodBrief ?? null,
      brief_feedback:            briefFeedback ?? null,
    });

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

  if (worstWrap) worstWrap.innerHTML = '<p class="muted">\u0110ang tải...</p>';
  if (bestWrap)  bestWrap.innerHTML  = '<p class="muted">\u0110ang tải...</p>';

  try {
    if (state.cachedVerdictAccuracy) {
      renderAccuracy(state.cachedVerdictAccuracy);
    } else {
      if (accWrap) accWrap.innerHTML = '<p class="muted">\u0110ang tải...</p>';
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

/**
 * renderSummary — populate KPI strip từ stats API response.
 */
export function renderSummary(s, portfolio) {
  if (!s) return;

  const kpis = [
    { id: 'openTheses',   raw: s.open_theses           ?? s.open_thesis_count  },
    { id: 'pausedTheses', raw: s.paused_theses          ?? 0                   },
    { id: 'riskyTheses',  raw: s.risky_theses          ?? s.risky_thesis_count },
    { id: 'upcoming7d',   raw: s.upcoming_catalysts_7d ?? s.upcoming_7d        },
  ];

  kpis.forEach(({ id, raw }) => {
    const node = el(id);
    if (!node || raw == null) return;
    const next = parseInt(raw, 10);
    countUp(node, next, { duration: 600 });
    flashValue(node);
  });

  const reviewsTodayRaw = parseInt(s.reviews_today ?? s.review_count_today ?? 0, 10);
  const reviewsTodayEl  = el('reviewsToday');
  if (reviewsTodayEl) {
    reviewsTodayEl.textContent = reviewsTodayRaw > 0 ? `${reviewsTodayRaw} cần review hôm nay` : '';
  }

  const staleCount = parseInt(s.stale_review_count ?? 0, 10);
  const staleDays  = s.stale_review_days ?? 14;
  const staleEl    = el('staleReview');
  const staleSubEl = el('staleReviewSub');
  const staleCard  = el('staleReviewCard');
  if (staleEl) {
    countUp(staleEl, staleCount, { duration: 600 });
    flashValue(staleEl);
  }
  if (staleSubEl) staleSubEl.textContent = `chưa review ${staleDays}d`;
  if (staleCard) {
    staleCard.classList.toggle('signal-card--ok',   staleCount === 0);
    staleCard.classList.toggle('signal-card--risk', staleCount > 0);
  }

  const positions     = portfolio?.positions ?? (Array.isArray(portfolio) ? portfolio : []);
  const totalValue    = portfolio?.total_market_value   ?? positions.reduce((acc, t) => acc + (t.market_value ?? 0), 0);
  const unrealizedPnl = portfolio?.total_unrealized_pnl ?? positions.reduce((acc, t) => acc + (t.unrealized_pnl ?? 0), 0);
  const pnlPct        = portfolio?.total_unrealized_pct ?? (
    portfolio?.total_cost_basis > 0
      ? (unrealizedPnl / portfolio.total_cost_basis) * 100
      : null
  );
  const posCount = positions.length;

  const pvEl     = el('portfolioValue');
  const pnlEl    = el('unrealizedPnl');
  const pnlPctEl = el('unrealizedPnlPct');
  const pvSubEl  = el('portfolioValueSub');

  if (pvEl && totalValue != null) { pvEl.textContent = fmtVnd(totalValue); flashValue(pvEl); }
  if (pvSubEl && posCount > 0)    { pvSubEl.textContent = `${posCount} vị thế`; }
  if (pnlEl && unrealizedPnl != null) {
    pnlEl.textContent = (unrealizedPnl >= 0 ? '+' : '') + fmtVnd(unrealizedPnl);
    pnlEl.className = 'signal-value ' + (unrealizedPnl >= 0 ? 'text-success' : 'text-danger');
    flashValue(pnlEl);
  }
  if (pnlPctEl && pnlPct != null) {
    pnlPctEl.textContent = (pnlPct >= 0 ? '+' : '') + pnlPct.toFixed(2) + '%';
    pnlPctEl.className = pnlPct >= 0 ? 'signal-sub text-success' : 'signal-sub text-danger';
  }
}

// ---------------------------------------------------------------------------
// W3/W4 consumer: refresh heatmap cell after AI review completes in panel
// ---------------------------------------------------------------------------
function bindReviewDoneListener() {
  document.addEventListener('breakdown:review-done', (e) => {
    const { thesisId } = e.detail ?? {};
    if (thesisId == null) return;
    refreshHeatmapCell(thesisId);
  });
}

bindReviewDoneListener();
