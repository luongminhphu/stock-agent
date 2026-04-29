/**
 * dashboard-loader.js
 * Owner: modules/dashboard
 * Responsibility: orchestrate data fetching + render calls cho màn hình chính.
 * - loadDashboard()   — fetch stats, theses, backtesting, briefs, scan
 * - renderSummary()   — KPI cards
 * - loadBacktesting() — fetch + render backtesting section
 *
 * NOTE: renderVerdicts, renderCatalystList, renderSnapshots, renderBriefCard
 *       được import từ các render modules tương ứng (Wave 3).
 *       renderThesesTable import từ render-thesis-table.js.
 */

import { el } from '../../utils/dom.js';
import { esc } from '../../utils/format.js';
import { apiBase, getJson } from '../../api/client.js';
import { state } from '../../state/dashboard-state.js';
import { renderThesesTable } from '../thesis/render-thesis-table.js';
import { loadThesisDetail } from '../thesis/thesis-service.js';
import { renderVerdicts }     from '../backtesting/render-backtesting.js';
import { renderCatalystList } from '../briefing/render-brief.js';
import { renderSnapshots }    from '../briefing/render-brief.js';

// ---------------------------------------------------------------------------
// Main loader
// ---------------------------------------------------------------------------
export async function loadDashboard() {
  const status = el('statusFilter').value;
  const base   = apiBase();
  el('errorBanner').classList.add('hidden');

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

    state.theses = theses?.items ?? [];
    renderThesesTable(state.theses);

    renderVerdicts(verdictAccuracy?.items ?? []);
    renderCatalystList(catalysts?.items ?? []);

    renderSnapshots({
      latest_scan_at:              latestScan?.created_at ?? latestScan?.generated_at ?? null,
      latest_scan_summary:         latestScan?.summary ?? latestScan?.headline ?? latestScan?.notes ?? null,
      latest_morning_brief_at:     latestMorningBrief?.created_at ?? latestMorningBrief?.generated_at ?? null,
      latest_morning_brief_summary: latestMorningBrief?.summary ?? latestMorningBrief?.headline ?? latestMorningBrief?.content ?? null,
      latest_morning_brief_data:   latestMorningBrief ?? null,
      latest_eod_brief_at:         latestEodBrief?.created_at ?? latestEodBrief?.generated_at ?? null,
      latest_eod_brief_summary:    latestEodBrief?.summary ?? latestEodBrief?.headline ?? latestEodBrief?.content ?? null,
      latest_eod_brief_data:       latestEodBrief ?? null,
    });

    // Re-render detail panel nếu đang có thesis được chọn
    if (state.selectedThesisId) {
      const t = state.theses.find(x => x.id === state.selectedThesisId);
      if (t) await loadThesisDetail(t.id);
      else {
        const { emptyDetailHTML } = await import('../thesis/render-thesis-table.js');
        el('thesisDetail').innerHTML = emptyDetailHTML();
      }
    }
  } catch (err) {
    el('errorBanner').textContent = `Lỗi tải dữ liệu: ${err.message}`;
    el('errorBanner').classList.remove('hidden');
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
