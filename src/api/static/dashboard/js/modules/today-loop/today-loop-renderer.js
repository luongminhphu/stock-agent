/**
 * today-loop-renderer.js — Renders today-loop data into dashboard UI
 * Segment owner: readmodel (display concerns)
 *
 * Targets:
 *   renderThesisDigest(items)  → #thesisDigestStrip (injected after #actionSurface)
 *   updateMarketMoodKpi(mood)  → #latestScanCard (updates before loadDashboard)
 *   updateSignalsBadge(sigs)   → #signalsFeed header badge count
 */

// ---------------------------------------------------------------------------
// 1. Thesis Digest Strip
// ---------------------------------------------------------------------------

const FLAG_META = {
  low_conviction: { icon: '📉', label: 'Conviction thấp', cls: 'tds-flag--conviction' },
  overdue_review: { icon: '📋', label: 'Chưa review',     cls: 'tds-flag--overdue'    },
};

const VERDICT_LABEL = {
  buy:        { text: 'BUY',     cls: 'tds-verdict--buy'     },
  hold:       { text: 'HOLD',    cls: 'tds-verdict--hold'    },
  sell:       { text: 'SELL',    cls: 'tds-verdict--sell'    },
  watch:      { text: 'WATCH',   cls: 'tds-verdict--watch'   },
  no_verdict: { text: '—',       cls: ''                     },
};

function _fmtPnl(pnl) {
  if (pnl == null) return '';
  const sign = pnl >= 0 ? '+' : '';
  return `${sign}${pnl.toFixed(1)}%`;
}

function _buildDigestItem(t) {
  const flags = (t.flags ?? [])
    .map(f => {
      const m = FLAG_META[f] ?? { icon: '⚠️', label: f, cls: '' };
      return `<span class="tds-flag ${m.cls}" title="${m.label}">${m.icon} ${m.label}</span>`;
    })
    .join('');

  const verdict = VERDICT_LABEL[t.last_verdict] ?? { text: t.last_verdict ?? '—', cls: '' };
  const pnl     = _fmtPnl(t.pnl_pct);
  const pnlCls  = t.pnl_pct != null ? (t.pnl_pct >= 0 ? 'tds-pnl--pos' : 'tds-pnl--neg') : '';
  const score   = t.score != null ? `${Math.round(t.score)}` : '—';
  const thesisAttr = t.thesis_id ? `data-thesis-id="${t.thesis_id}"` : '';

  return `
    <div class="tds-item" ${thesisAttr} role="button" tabindex="0"
         aria-label="${t.ticker}: ${(t.flags ?? []).join(', ')}">
      <span class="tds-ticker">${t.ticker ?? '—'}</span>
      <span class="tds-flags">${flags}</span>
      <div class="tds-aside">
        ${pnl ? `<span class="tds-pnl ${pnlCls}">${pnl}</span>` : ''}
        <span class="tds-score" title="Conviction score">${score}</span>
        <span class="tds-verdict ${verdict.cls}">${verdict.text}</span>
      </div>
    </div>`;
}

function _getOrCreateStrip() {
  let strip = document.getElementById('thesisDigestStrip');
  if (strip) return strip;

  strip = document.createElement('div');
  strip.id = 'thesisDigestStrip';
  strip.className = 'thesis-digest-strip hidden';
  strip.setAttribute('aria-label', 'Thesis cần chú ý hôm nay');

  // Inject into #todayDuoRow (alongside #actionSurface in the same row)
  const duoRow = document.getElementById('todayDuoRow');
  if (duoRow) {
    duoRow.appendChild(strip);
  } else {
    // Fallback: inject after #actionSurface
    const anchor = document.getElementById('actionSurface');
    if (anchor?.parentNode) {
      anchor.parentNode.insertBefore(strip, anchor.nextSibling);
    } else {
      const main = document.querySelector('main') ?? document.body;
      main.prepend(strip);
    }
  }
  return strip;
}

export function renderThesisDigest(items, { generatedAt } = {}) {
  const strip = _getOrCreateStrip();

  if (!items || !items.length) {
    strip.classList.add('hidden');
    return;
  }

  const ts = generatedAt
    ? new Date(generatedAt).toLocaleTimeString('vi-VN', { hour: '2-digit', minute: '2-digit' })
    : '';

  strip.classList.remove('hidden');
  strip.innerHTML = `
    <div class="tds-header">
      <span class="tds-title">⚠️ Thesis cần chú ý</span>
      <span class="tds-count">${items.length} mã</span>
      ${ts ? `<span class="tds-ts muted">${ts}</span>` : ''}
    </div>
    <div class="tds-items">
      ${items.map(_buildDigestItem).join('')}
    </div>`;

  // Wire click → navigate:thesis
  strip.querySelectorAll('.tds-item[data-thesis-id]').forEach(el => {
    const thesisId = parseInt(el.dataset.thesisId, 10);
    if (!thesisId) return;
    const handle = () => {
      document.dispatchEvent(
        new CustomEvent('navigate:thesis', { detail: { thesisId } })
      );
      document.getElementById('thesesTableWrap')
        ?.scrollIntoView({ behavior: 'smooth', block: 'start' });
    };
    el.addEventListener('click', handle);
    el.addEventListener('keydown', e => {
      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); handle(); }
    });
  });
}

// ---------------------------------------------------------------------------
// 2. Market Mood KPI
// ---------------------------------------------------------------------------

export function updateMarketMoodKpi(mood, { stale = false } = {}) {
  if (!mood || !mood.bias) return;

  const card    = document.getElementById('latestScanCard');
  const atEl    = document.getElementById('latestScanAt');
  const summEl  = document.getElementById('latestScanSummary');
  if (!card) return;

  const bias     = mood.bias;
  const greenPct = mood.green_pct != null ? `${Math.round(mood.green_pct)}% xanh` : '';
  const scannedAt = mood.scanned_at
    ? new Date(mood.scanned_at).toLocaleTimeString('vi-VN', { hour: '2-digit', minute: '2-digit' })
    : '';

  if (atEl) {
    atEl.textContent = bias.toUpperCase();
    atEl.className   = `signal-value ${bias === 'bullish' ? 'text-green' : bias === 'bearish' ? 'text-red' : ''}`;
  }

  if (summEl) {
    const parts = [greenPct, scannedAt ? `lúc ${scannedAt}` : ''].filter(Boolean);
    summEl.textContent = parts.join(' · ') || (stale ? 'Dữ liệu cũ' : '');
  }

  card.classList.toggle('signal-card--stale', stale);
}

// ---------------------------------------------------------------------------
// 3. Signals Feed Badge
// ---------------------------------------------------------------------------

export function updateSignalsBadge(topSignals, meta = {}) {
  if (!topSignals?.length) return;

  const wrap = document.getElementById('signalsFeed');
  if (!wrap) return;

  const inject = () => {
    const header = wrap.querySelector('.signals-section-header');
    if (!header) return;
    if (header.querySelector('.tl-signal-badge')) return; // already injected

    const badge = document.createElement('span');
    badge.className = 'tl-signal-badge';
    badge.title = `${topSignals.length} tín hiệu mạnh nhất hôm nay từ today-loop`;
    badge.textContent = `⚡ Top ${topSignals.length}`;
    header.appendChild(badge);
  };

  // Try immediately (if signalsFeed already rendered)
  inject();

  // Also observe for when renderSignalsFeed() fires later
  const obs = new MutationObserver(() => { inject(); obs.disconnect(); });
  obs.observe(wrap, { childList: true, subtree: true });
}
