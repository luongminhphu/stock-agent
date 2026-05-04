/**
 * dashboard-loader.js
 * Owner: modules/dashboard
 * Responsibility: orchestrate data fetching + render calls cho màn hình chính.
 */

import { el }                  from '../../utils/dom.js';
import { apiBase, getJson }    from '../../api/client.js';
import { state }               from '../../state/dashboard-state.js';
import { renderThesesTable, thesisTableSkeletonHTML, emptyDetailHTML } from '../thesis/render-thesis-table.js';
import { loadThesisDetail }    from '../thesis/thesis-service.js';
import { openEditThesisModal } from '../thesis/thesis-form.js';
import { renderVerdicts, renderAccuracy, renderPerformance } from '../backtesting/render-backtesting.js';
import { renderCatalystList, renderSnapshots } from '../briefing/render-brief.js';
import { countUp, flashValue }  from '../../utils/animate.js';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------
function wireDeleteThesis(id) {
  const msg = el('deleteModalMsg');
  const btn = el('deleteConfirmBtn');
  if (msg) msg.textContent = `Bạn có chắc muốn xóa thesis này không? Hành động không thể hoàn tác.`;
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

// Normalize verdict-accuracy response (array hoặc { items: [...] })
function normalizeAccuracyRes(res) {
  if (!res) return [];
  if (Array.isArray(res)) return res;
  return res.items ?? [];
}

// ---------------------------------------------------------------------------
// WAVE 2d — Show skeletons ngay khi bắt đầu load, trước Promise.all
// ---------------------------------------------------------------------------
function showLoadingSkeletons() {
  const tableWrap = document.getElementById('thesesTableWrap');
  if (tableWrap) tableWrap.innerHTML = thesisTableSkeletonHTML(5);

  if (!state.selectedThesisId) {
    const detail = el('thesisDetail');
    if (detail) detail.innerHTML = emptyDetailHTML();
  }
}

// ---------------------------------------------------------------------------
// Main loader
// ---------------------------------------------------------------------------
export async function loadDashboard() {
  const status = el('statusFilter')?.value ?? 'active';
  const base   = apiBase();
  el('errorBanner')?.classList.add('hidden');

  showLoadingSkeletons();

  try {
    const [
      stats,
      theses,
      verdictAccuracy,
      catalysts,
      latestScan,
      latestMorningBrief,
      latestEodBrief,
    ] = await Promise.all([
      getJson(`${base}/stats`).catch(() => null),
      getJson(`${base}/theses?status=${status}`).catch(() => []),
      getJson(`${base}/backtesting/verdict-accuracy`).catch(() => null),
      getJson(`${base}/catalysts/upcoming?days=30`).catch(() => []),
      getJson(`${base}/scan/latest`).catch(() => null),
      getJson(`${base}/brief/latest?phase=morning`).catch(() => null),
      getJson(`${base}/brief/latest?phase=eod`).catch(() => null),
    ]);

    renderSummary(stats);

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
      latest_scan_at:              latestScan?.created_at ?? latestScan?.generated_at ?? null,
      latest_scan_summary:         latestScan?.summary ?? latestScan?.headline ?? null,
      latest_morning_brief_at:     latestMorningBrief?.created_at ?? latestMorningBrief?.generated_at ?? null,
      latest_morning_brief_data:   latestMorningBrief ?? null,
      latest_eod_brief_at:         latestEodBrief?.created_at ?? latestEodBrief?.generated_at ?? null,
      latest_eod_brief_data:       latestEodBrief ?? null,
    });

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

// ---------------------------------------------------------------------------
// Backtesting loader
// ---------------------------------------------------------------------------
export async function loadBacktesting() {
  const base = apiBase();

  const accuracyWrap    = el('accuracyWrap');
  const performanceWrap = el('performanceWrap');

  if (performanceWrap) performanceWrap.innerHTML = '<p class="muted">Đang tải...</p>';

  try {
    if (state.cachedVerdictAccuracy) {
      renderAccuracy(state.cachedVerdictAccuracy);
    } else {
      if (accuracyWrap) accuracyWrap.innerHTML = '<p class="muted">Đang tải...</p>';
      const accuracyRes  = await getJson(`${base}/backtesting/verdict-accuracy`).catch(() => null);
      const accuracyRows = Array.isArray(accuracyRes) ? accuracyRes : (accuracyRes?.items ?? []);
      state.cachedVerdictAccuracy = accuracyRows;
      renderAccuracy(accuracyRows);
    }

    const performanceRes  = await getJson(`${base}/backtesting/thesis-performances`).catch(() => null);
    const performanceRows = Array.isArray(performanceRes) ? performanceRes : (performanceRes?.items ?? []);
    renderPerformance(performanceRows);

  } catch (err) {
    console.error('[dashboard-loader] loadBacktesting error:', err);
    if (accuracyWrap)    accuracyWrap.innerHTML    = '<p class="empty-state">Lỗi tải dữ liệu accuracy.</p>';
    if (performanceWrap) performanceWrap.innerHTML = '<p class="empty-state">Lỗi tải dữ liệu performance.</p>';
  }
}

// ---------------------------------------------------------------------------
// KPI summary cards — countUp animation + conditional colour
// ---------------------------------------------------------------------------

/**
 * Đọc giá trị số hiện tại của element (bỏ dấu chấm, phẩy, ký hiệu).
 * Dùng để so sánh old vs new cho flashValue direction.
 * @param {HTMLElement} node
 * @returns {number}
 */
function parseCurrentValue(node) {
  return parseFloat((node.textContent ?? '').replace(/[^\d.-]/g, '')) || 0;
}

export function renderSummary(s) {
  if (!s) return;

  /** @type {Array<{id: string, raw: any, suffix?: string, decimals?: number}>} */
  const kpis = [
    { id: 'openTheses',       raw: s.open_theses          ?? s.open_thesis_count    },
    { id: 'riskyTheses',      raw: s.risky_theses         ?? s.risky_thesis_count   },
    { id: 'upcoming7d',       raw: s.upcoming_catalysts_7d ?? s.upcoming_7d         },
    { id: 'reviewsToday',     raw: s.reviews_today        ?? s.review_count_today   },
    { id: 'totalReviewsHero', raw: s.total_reviews        ?? s.review_count_total   },
  ];

  for (const { id, raw, suffix = '', decimals = 0 } of kpis) {
    const node = el(id);
    if (!node) continue;
    const num = parseFloat(String(raw ?? '').replace(/[^\d.-]/g, ''));
    if (!isNaN(num)) {
      // FIX: truyền opts object đúng signature của countUp(el, target, opts)
      const oldVal = parseCurrentValue(node);
      countUp(node, num, { duration: 650, decimals, suffix });
      // FIX: flashValue direction dựa trên so sánh old vs new
      flashValue(node, num >= oldVal);
    } else {
      node.textContent = raw ?? '—';
    }
  }

  // Conditional risk colour: đỏ khi risky > 0, reset khi = 0
  const riskyEl  = el('riskyTheses');
  const riskyVal = parseFloat(String(s.risky_theses ?? s.risky_thesis_count ?? '0').replace(/[^\d.-]/g, ''));
  if (riskyEl) {
    // Toggle class trên signal-card cha gần nhất
    const card = riskyEl.closest('.signal-card');
    if (card) card.classList.toggle('signal-card--alert', riskyVal > 0);

    // Thêm cập nhật màu trực tiếp vào số thẳm khi đỏ
    riskyEl.classList.toggle('kpi-risky', riskyVal > 0);
    riskyEl.classList.toggle('kpi-safe',  riskyVal === 0);
  }
}
