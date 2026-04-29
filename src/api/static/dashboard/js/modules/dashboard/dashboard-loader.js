/**
 * dashboard-loader.js
 * Owner: modules/dashboard
 * Responsibility: orchestrate data fetching + render calls cho màn hình chính.
 */

import { el }                  from '../../utils/dom.js';
import { apiBase, getJson }    from '../../api/client.js';
import { state }               from '../../state/dashboard-state.js';
import { renderThesesTable }   from '../thesis/render-thesis-table.js';
import { loadThesisDetail }    from '../thesis/thesis-service.js';
import { openEditThesisModal } from '../thesis/thesis-form.js';
import { renderVerdicts, renderAccuracy, renderPerformance } from '../backtesting/render-backtesting.js';
import { renderCatalystList, renderSnapshots } from '../briefing/render-brief.js';

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

// ---------------------------------------------------------------------------
// Main loader
// ---------------------------------------------------------------------------
export async function loadDashboard() {
  const status = el('statusFilter')?.value ?? 'active';
  const base   = apiBase();
  el('errorBanner')?.classList.add('hidden');

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
      getJson(`${base}/backtesting/verdict-accuracy`).catch(() => []),
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

    renderVerdicts(verdictAccuracy?.items ?? verdictAccuracy ?? []);
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
        const { emptyDetailHTML } = await import('../thesis/render-thesis-table.js');
        const detail = el('thesisDetail');
        if (detail) detail.innerHTML = emptyDetailHTML();
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
// Backtesting loader — verdict accuracy + thesis performances
// ---------------------------------------------------------------------------
export async function loadBacktesting() {
  const base = apiBase();

  const accuracyWrap     = el('accuracyWrap');
  const performanceWrap  = el('performanceWrap');

  if (accuracyWrap)    accuracyWrap.innerHTML    = '<p class="muted">Đang tải...</p>';
  if (performanceWrap) performanceWrap.innerHTML = '<p class="muted">Đang tải...</p>';

  try {
    const [accuracyRes, performanceRes] = await Promise.all([
      getJson(`${base}/backtesting/verdict-accuracy`).catch(() => null),
      getJson(`${base}/backtesting/thesis-performances`).catch(() => null),
    ]);

    // verdict-accuracy trả về { items: [...] } hoặc array trực tiếp
    const accuracyRows = accuracyRes?.items ?? (Array.isArray(accuracyRes) ? accuracyRes : []);
    renderAccuracy(accuracyRows);

    // thesis-performances trả về array trực tiếp
    const performanceRows = Array.isArray(performanceRes) ? performanceRes : (performanceRes?.items ?? []);
    renderPerformance(performanceRows);

  } catch (err) {
    console.error('[dashboard-loader] loadBacktesting error:', err);
    if (accuracyWrap)    accuracyWrap.innerHTML    = '<p class="empty-state">Lỗi tải dữ liệu accuracy.</p>';
    if (performanceWrap) performanceWrap.innerHTML = '<p class="empty-state">Lỗi tải dữ liệu performance.</p>';
  }
}

// ---------------------------------------------------------------------------
// KPI summary cards
// ---------------------------------------------------------------------------
export function renderSummary(s) {
  if (!s) return;
  if (el('openTheses'))       el('openTheses').textContent       = s.open_theses ?? s.open_thesis_count ?? '—';
  if (el('riskyTheses'))      el('riskyTheses').textContent      = s.risky_theses ?? s.risky_thesis_count ?? '—';
  if (el('upcoming7d'))       el('upcoming7d').textContent       = s.upcoming_catalysts_7d ?? s.upcoming_7d ?? '—';
  if (el('reviewsToday'))     el('reviewsToday').textContent     = s.reviews_today ?? s.review_count_today ?? '—';
  if (el('totalReviewsHero')) el('totalReviewsHero').textContent = s.total_reviews ?? s.review_count_total ?? '—';
}
