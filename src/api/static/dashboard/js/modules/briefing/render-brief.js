/**
 * render-brief.js
 * Owner: modules/briefing
 * Responsibility: render Catalyst calendar list, Snapshots panel, Brief cards.
 * Được tách từ dashboard.js (renderCatalystList, renderSnapshots, renderBriefCard).
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

const SIGNAL_CLS = {
  breakout: 'breakout', 'risk-on': 'breakout',
  bearish:  'bearish',  'risk-off': 'bearish',
  pullback: 'pullback',  neutral:  'watchlist',
};

// ---------------------------------------------------------------------------
// Brief card (morning / eod)
// ---------------------------------------------------------------------------
export function renderBriefCard(phase, brief, dateStr) {
  const isEod   = phase === 'eod';
  const label   = isEod ? 'End-of-Day Brief' : 'Morning Brief';
  const icon    = isEod ? '🌙' : '🌅';

  if (!brief) {
    return `
      <div class="snapshot-card">
        <span class="snapshot-label">${icon} ${label}</span>
        <strong>—</strong>
        <p class="muted">Chưa có brief.</p>
      </div>`;
  }

  const sentiment = brief.market_sentiment ?? brief.sentiment ?? null;
  const smeta     = SENTIMENT_META[String(sentiment ?? '').toUpperCase()] ?? null;
  const signals   = brief.top_signals ?? brief.signals ?? [];
  const watchlist = brief.watchlist_verdicts ?? brief.watchlist ?? [];
  const summary   = brief.summary ?? brief.headline ?? brief.content ?? null;

  return `
    <div class="snapshot-card brief-card">
      <span class="snapshot-label">${icon} ${label}</span>
      <strong>${dateStr ?? fmtDate(brief.created_at ?? brief.generated_at)}</strong>
      ${smeta
        ? `<span class="badge ${smeta.cls}" style="margin-top:4px;font-size:.78rem;">${smeta.icon} ${smeta.label}</span>`
        : ''}
      ${summary
        ? `<p class="muted" style="font-size:.82rem;margin-top:6px;line-height:1.55;">${esc(String(summary).slice(0, 200))}${String(summary).length > 200 ? '…' : ''}</p>`
        : ''}
      ${signals.length ? `
        <div style="margin-top:8px;">
          <p class="suggest-section-title">Top signals</p>
          ${signals.slice(0, 3).map(s => {
            const cls = SIGNAL_CLS[String(s.signal_type ?? s.type ?? '').toLowerCase()] ?? 'watchlist';
            return `<span class="badge ${cls}" style="font-size:.75rem;margin-right:4px;margin-bottom:4px;display:inline-block;">${esc(s.ticker ?? '')} ${esc(s.signal_type ?? s.type ?? '')}</span>`;
          }).join('')}
        </div>` : ''}
      ${watchlist.length ? `
        <div style="margin-top:6px;">
          <p class="suggest-section-title">Watchlist verdicts</p>
          ${watchlist.slice(0, 4).map(w => `
            <div style="font-size:.8rem;margin-bottom:2px;">
              <strong>${esc(w.ticker ?? '')}</strong>: ${esc(w.verdict ?? '')} — ${esc(w.reasoning ? String(w.reasoning).slice(0, 60) : '')}
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
