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
// Maps đúng fields từ BriefOutput schema (src/ai/schemas.py)
// ---------------------------------------------------------------------------
export function renderBriefCard(phase, brief, dateStr) {
  const isEod = phase === 'eod';
  const label = isEod ? 'End-of-Day Brief' : 'Morning Brief';
  const icon  = isEod ? '🌙' : '🌅';

  if (!brief) {
    return `<div class="brief-empty">Chưa có brief.</div>`;
  }

  const sentiment       = brief.sentiment ?? null;
  const smeta           = SENTIMENT_META[String(sentiment ?? '').toUpperCase()] ?? null;
  const headline        = brief.headline ?? null;
  const summary         = brief.summary ?? brief.content ?? null;
  const keyMovers       = Array.isArray(brief.key_movers)       ? brief.key_movers       : [];
  const watchlistAlerts = Array.isArray(brief.watchlist_alerts) ? brief.watchlist_alerts : [];
  const actionItems     = Array.isArray(brief.action_items)     ? brief.action_items     : [];
  const tickerSummaries = Array.isArray(brief.ticker_summaries) ? brief.ticker_summaries : [];

  return `
    <div class="brief-card phase-${isEod ? 'eod' : 'morning'}">
      <div class="brief-header">
        <div class="brief-phase-icon">${icon}</div>
        <div>
          <div class="brief-phase-label">${label}</div>
          <div class="brief-date">${dateStr ?? fmtDate(brief.created_at)}</div>
        </div>
        ${smeta
          ? `<span class="sentiment-badge ${smeta.cls}">${smeta.icon} ${smeta.label}</span>`
          : ''}
      </div>

      ${headline
        ? `<div class="brief-headline">${esc(headline)}</div>`
        : ''}

      <div class="brief-body">
        ${summary
          ? `<div class="brief-summary">${esc(summary)}</div>`
          : ''}

        ${keyMovers.length ? `
          <div class="brief-section">
            <div class="brief-section-title">📌 Key Movers</div>
            <div class="brief-movers">
              ${keyMovers.map(s => {
                const isStr = typeof s === 'string';
                const ticker = isStr ? s : (s.ticker ?? s);
                const chg    = isStr ? null : s.change_pct;
                const cls    = chg == null ? '' : chg >= 0 ? 'up' : 'down';
                return `<span class="mover-pill ${cls}">
                  <strong>${esc(String(ticker))}</strong>
                  ${chg != null ? `<span class="chg">${chg >= 0 ? '+' : ''}${Number(chg).toFixed(1)}%</span>` : ''}
                </span>`;
              }).join('')}
            </div>
          </div>` : ''}

        ${watchlistAlerts.length ? `
          <div class="brief-section">
            <div class="brief-section-title">⚠️ Watchlist Alerts</div>
            ${watchlistAlerts.map(a =>
              `<div class="brief-item">${esc(a)}</div>`
            ).join('')}
          </div>` : ''}

        ${actionItems.length ? `
          <div class="brief-section">
            <div class="brief-section-title">✅ Action Items</div>
            ${actionItems.map(a =>
              `<div class="brief-item action">${esc(a)}</div>`
            ).join('')}
          </div>` : ''}

        ${tickerSummaries.length ? `
          <div class="brief-section">
            <div class="brief-section-title">📊 Ticker Summaries</div>
            <table class="brief-ticker-table">
              <thead>
                <tr>
                  <th>Mã</th>
                  <th>Signal</th>
                  <th style="text-align:right;">Chg%</th>
                </tr>
              </thead>
              <tbody>
                ${tickerSummaries.map(t => {
                  const chg = t.change_pct;
                  const cls = chg == null ? '' : chg >= 0 ? 'pos' : 'neg';
                  const sigCls = t.signal ? t.signal.toLowerCase().replace(/\s+/g, '-') : '';
                  return `<tr>
                    <td>
                      <div class="bt-ticker">${esc(t.ticker ?? '')}</div>
                      ${t.one_line
                        ? `<div class="bt-note">${esc(t.one_line)}</div>`
                        : ''}
                      ${t.watch_reason
                        ? `<div class="bt-note" style="font-style:italic;">${esc(t.watch_reason)}</div>`
                        : ''}
                    </td>
                    <td>${t.signal ? `<span class="bt-signal ${sigCls}">${esc(t.signal)}</span>` : '—'}</td>
                    <td class="bt-chg ${cls}">${chg != null ? (chg >= 0 ? '+' : '') + Number(chg).toFixed(2) + '%' : '—'}</td>
                  </tr>`;
                }).join('')}
              </tbody>
            </table>
          </div>` : ''}
      </div>
    </div>`;
}

// ---------------------------------------------------------------------------
// Wire brief tab switching
// ---------------------------------------------------------------------------
function wireBriefTabs() {
  const tabBar = document.querySelector('.brief-tab-bar');
  if (!tabBar) return;

  tabBar.addEventListener('click', e => {
    const btn = e.target.closest('[data-brief-tab]');
    if (!btn) return;

    const target = btn.dataset.briefTab;

    // Update tab buttons
    tabBar.querySelectorAll('.brief-tab').forEach(t => {
      const active = t.dataset.briefTab === target;
      t.classList.toggle('active', active);
      t.setAttribute('aria-selected', String(active));
    });

    // Show / hide panes
    const morningPane = el('morningBriefWrap');
    const eodPane     = el('eodBriefWrap');
    if (morningPane) morningPane.classList.toggle('hidden', target !== 'morning');
    if (eodPane)     eodPane.classList.toggle('hidden',    target !== 'eod');
  });
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

  // Wire tab clicks (idempotent — listener is on parent, safe to re-attach)
  wireBriefTabs();
}

// ---------------------------------------------------------------------------
// Snapshots (alias for renderSnapshots)
// ---------------------------------------------------------------------------
export function renderSnapshots_alias(data) { return renderSnapshots(data); }
