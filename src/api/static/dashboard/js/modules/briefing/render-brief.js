/**
 * render-brief.js
 * Owner: modules/briefing
 * Responsibility: render Catalyst calendar list, Snapshots panel, Brief cards.
 */

import { el } from '../../utils/dom.js';
import { esc, fmtDate } from '../../utils/format.js';

// ---------------------------------------------------------------------------
// Catalyst calendar list (sidebar)
// ---------------------------------------------------------------------------
export function renderCatalystList(list) {
  const wrap = el('catalystList');
  if (!wrap) return;
  if (!list.length) {
    wrap.innerHTML = '<p class="empty-state">Không có catalyst nào trong 30 ngày tới.</p>';
    return;
  }
  wrap.innerHTML = list.map(item => `
    <div class="catalyst-item">
      <div class="catalyst-item-row">
        ${item.thesis_ticker
          ? `<span class="badge" style="font-size:.78rem;padding:2px 8px;letter-spacing:.04em;">${esc(item.thesis_ticker)}</span>`
          : ''}
        <span style="flex:1;font-weight:600;">— ${esc(item.description)}</span>
      </div>
      <div style="display:flex;gap:8px;align-items:center;margin-top:4px;flex-wrap:wrap;">
        ${item.expected_date
          ? `<span style="font-size:.8rem;color:var(--muted);">📅 ${fmtDate(item.expected_date)}</span>`
          : '<span style="font-size:.8rem;color:var(--muted);">· —</span>'}
        ${item.thesis_title
          ? `<span style="font-size:.78rem;color:var(--muted);font-style:italic;">${esc(item.thesis_title)}</span>`
          : ''}
      </div>
    </div>`).join('<div class="catalyst-divider"></div>');
}

// ---------------------------------------------------------------------------
// Sentiment metadata
// ---------------------------------------------------------------------------
const SENTIMENT_META = {
  RISK_ON:   { cls: 'sent-risk-on',   icon: '🟢', label: 'Risk-On'   },
  RISK_OFF:  { cls: 'sent-risk-off',  icon: '🔴', label: 'Risk-Off'  },
  MIXED:     { cls: 'sent-mixed',     icon: '⚡',  label: 'Mixed'     },
  UNCERTAIN: { cls: 'sent-uncertain', icon: '❓',  label: 'Uncertain' },
};

// ---------------------------------------------------------------------------
// Brief card (morning / eod)
// Maps đúng fields từ BriefOutput schema (src/ai/schemas.py):
//   headline, sentiment, summary, key_movers[], watchlist_alerts[],
//   action_items[], ticker_summaries[]
// ---------------------------------------------------------------------------
export function renderBriefCard(phase, brief, dateStr) {
  const isEod = phase === 'eod';
  const label = isEod ? 'End-of-Day Brief' : 'Morning Brief';
  const icon  = isEod ? '🌙' : '🌅';

  if (!brief) {
    return `
      <div class="snapshot-card">
        <span class="snapshot-label">${icon} ${label}</span>
        <strong>—</strong>
        <p class="muted">Chưa có brief.</p>
      </div>`;
  }

  // Map đúng BriefOutput fields
  const sentiment       = brief.sentiment ?? null;
  const smeta           = SENTIMENT_META[String(sentiment ?? '').toUpperCase()] ?? null;
  const headline        = brief.headline ?? null;
  const summary         = brief.summary ?? brief.content ?? null;
  const keyMovers       = Array.isArray(brief.key_movers)       ? brief.key_movers       : [];
  const watchlistAlerts = Array.isArray(brief.watchlist_alerts) ? brief.watchlist_alerts : [];
  const actionItems     = Array.isArray(brief.action_items)     ? brief.action_items     : [];
  const tickerSummaries = Array.isArray(brief.ticker_summaries) ? brief.ticker_summaries : [];

  return `
    <div class="snapshot-card brief-card">
      <span class="snapshot-label">${icon} ${label}</span>
      <strong>${dateStr ?? fmtDate(brief.created_at)}</strong>

      ${smeta
        ? `<span class="badge ${smeta.cls}" style="margin-top:4px;font-size:.78rem;">${smeta.icon} ${smeta.label}</span>`
        : ''}

      ${headline
        ? `<p style="font-weight:600;font-size:.9rem;margin-top:8px;">${esc(headline)}</p>`
        : ''}

      ${summary
        ? `<p class="muted" style="font-size:.82rem;margin-top:4px;line-height:1.55;">${esc(summary)}</p>`
        : ''}

      ${keyMovers.length ? `
        <div style="margin-top:8px;">
          <p class="suggest-section-title">📌 Key Movers</p>
          ${keyMovers.map(s =>
            `<span class="badge watchlist" style="font-size:.75rem;margin-right:4px;margin-bottom:4px;display:inline-block;">${esc(s)}</span>`
          ).join('')}
        </div>` : ''}

      ${watchlistAlerts.length ? `
        <div style="margin-top:8px;">
          <p class="suggest-section-title">⚠️ Watchlist Alerts</p>
          ${watchlistAlerts.map(a =>
            `<div style="font-size:.8rem;margin-bottom:4px;padding-left:8px;border-left:2px solid var(--accent);">
               ${esc(a)}
             </div>`
          ).join('')}
        </div>` : ''}

      ${actionItems.length ? `
        <div style="margin-top:8px;">
          <p class="suggest-section-title">✅ Action Items</p>
          <ul style="margin:0;padding-left:16px;">
            ${actionItems.map(a =>
              `<li style="font-size:.8rem;margin-bottom:2px;">${esc(a)}</li>`
            ).join('')}
          </ul>
        </div>` : ''}

      ${tickerSummaries.length ? `
        <div style="margin-top:8px;">
          <p class="suggest-section-title">📊 Ticker Summaries</p>
          ${tickerSummaries.map(t => `
            <div style="font-size:.8rem;margin-bottom:6px;padding:4px 8px;background:var(--surface-alt,rgba(0,0,0,.04));border-radius:6px;">
              <div style="display:flex;align-items:center;gap:6px;flex-wrap:wrap;">
                <strong>${esc(t.ticker ?? '')}</strong>
                ${t.change_pct !== undefined && t.change_pct !== null
                  ? `<span style="color:${t.change_pct >= 0 ? 'var(--green,#22c55e)' : 'var(--red,#ef4444)'};">
                       ${t.change_pct >= 0 ? '+' : ''}${Number(t.change_pct).toFixed(2)}%
                     </span>`
                  : ''}
                ${t.signal
                  ? `<span class="badge" style="font-size:.7rem;">${esc(t.signal)}</span>`
                  : ''}
              </div>
              ${t.one_line
                ? `<div style="color:var(--muted);margin-top:2px;">${esc(t.one_line)}</div>`
                : ''}
              ${t.watch_reason
                ? `<div style="color:var(--muted);font-style:italic;font-size:.75rem;margin-top:1px;">${esc(t.watch_reason)}</div>`
                : ''}
            </div>`).join('')}
        </div>` : ''}
    </div>`;
}

// ---------------------------------------------------------------------------
// Snapshots panel (scan + morning brief + eod brief)
// ---------------------------------------------------------------------------
export function renderSnapshots(data) {
  // Scan
  const scanAt  = el('latestScanAt');
  const scanSum = el('latestScanSummary');
  if (scanAt)  scanAt.textContent  = data.latest_scan_at  ? fmtDate(data.latest_scan_at)  : '—';
  if (scanSum) scanSum.textContent = data.latest_scan_summary ?? 'Chưa có scan snapshot.';

  // Morning brief
  const morningWrap = el('morningBriefWrap');
  if (morningWrap) {
    morningWrap.innerHTML = renderBriefCard(
      'morning',
      data.latest_morning_brief_data,
      data.latest_morning_brief_at ? fmtDate(data.latest_morning_brief_at) : null,
    );
  }

  // EOD brief
  const eodWrap = el('eodBriefWrap');
  if (eodWrap) {
    eodWrap.innerHTML = renderBriefCard(
      'eod',
      data.latest_eod_brief_data,
      data.latest_eod_brief_at ? fmtDate(data.latest_eod_brief_at) : null,
    );
  }
}
