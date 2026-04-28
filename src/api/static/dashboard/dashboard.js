'use strict';


function el(id) { return document.getElementById(id); }
function apiBase() { return '/api/v1/readmodel/dashboard'; }
function thesisApiBase() { return '/api/v1/thesis'; }
function readmodelBase() { return '/api/v1/readmodel'; }
function authHeaders() { return { 'Content-Type': 'application/json' }; }

async function getJson(url, options = {}) {
  const r = await fetch(url, { ...options, headers: { ...authHeaders(), ...(options.headers ?? {}) } });
  if (!r.ok) {
    const msg = await r.text().catch(() => r.statusText);
    throw new Error(`${r.status} ${msg}`);
  }
  if (r.status === 204 || r.headers.get('content-length') === '0') return null;
  return r.json();
}

async function sendJson(url, method, body) {
  return getJson(url, { method, body: body != null ? JSON.stringify(body) : undefined });
}

function fmt(n, decimals = 0) {
  if (n == null) return '\u2014';
  return Number(n).toLocaleString('vi-VN', { maximumFractionDigits: decimals });
}

function fmtDate(d) {
  if (!d) return '\u2014';
  return new Date(d).toLocaleDateString('vi-VN', { day: '2-digit', month: '2-digit', year: 'numeric' });
}

function fmtDateTime(d) {
  if (!d) return '\u2014';
  return new Date(d).toLocaleString('vi-VN', { day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit' });
}

function badge(val) {
  const cls = String(val || '').toLowerCase();
  return `<span class="badge ${cls}">${val || '\u2014'}</span>`;
}

function esc(v) {
  return String(v ?? '').replace(/[&<>'"]/g, s => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' }[s]));
}

function highlightScanText(text) {
  if (!text) return esc(text);
  return esc(text)
    .replace(/\b([A-Z]{2,5})(?=:|\s|,|;)/g,
      '<strong style="color:#7dd3fc;font-weight:800;letter-spacing:.04em;">$1</strong>')
    .replace(/(-\d+(?:\.\d+)?%?)/g,
      '<span style="color:#fb923c;font-weight:600;">$1</span>')
    .replace(/(\+\d+(?:\.\d+)?%?)/g,
      '<span style="color:#4ade80;font-weight:700;">$1</span>');
}

function showToast(msg, type = 'success', ms = 3000) {
  const t = document.createElement('div');
  t.className = `toast ${type}`;
  t.textContent = msg;
  document.body.appendChild(t);
  setTimeout(() => t.remove(), ms);
}

function openModal(id) { const d = el(id); if (d) d.showModal(); }
function closeModal(id) { const d = el(id); if (d) d.close(); }

let _selectedThesisId = null;
let _theses = [];
let _deleteCallback = null;

// Cache review + selections cho AI apply flow
let latestAiReviews = {};
let aiApplyThesisId = null;
let aiSelectedRecIds = [];

function scoreClass(s) {
  if (s == null) return '';
  if (s >= 86) return 'score-high';
  if (s >= 71) return 'score-good';
  if (s >= 51) return 'score-mid';
  if (s >= 31) return 'score-warn';
  return 'score-low';
}

function fmtScore(s) {
  return s == null ? '\u2014' : Math.round(Number(s));
}

function pct(value, max) {
  if (value == null || !max) return 0;
  return Math.max(0, Math.min(100, (Number(value) / Number(max)) * 100));
}

function renderScoreBreakdown(breakdown) {
  if (!breakdown) return '';

  const rows = [
    { key: 'assumption_health', label: 'Assumptions', value: breakdown.assumption_health, max: 40 },
    { key: 'catalyst_progress', label: 'Catalysts', value: breakdown.catalyst_progress, max: 30 },
    { key: 'risk_reward', label: 'Risk / Reward', value: breakdown.risk_reward, max: 20 },
    { key: 'review_confidence', label: 'Review confidence', value: breakdown.review_confidence, max: 10 },
  ];

  return `
    <div class="detail-section">
      <div class="detail-section-header">
        <h3>Score breakdown</h3>
        <span style="color:var(--muted);font-size:.82rem;">4 th\u00e0nh ph\u1ea7n \u0111\u00f3ng g\u00f3p v\u00e0o health score</span>
      </div>
      <div class="detail-list">
        ${rows.map(r => `
          <div class="detail-item">
            <div class="detail-item-row">
              <span style="font-weight:600;font-size:.9rem;">${r.label}</span>
              <span class="${scoreClass((Number(r.value || 0) / r.max) * 100)}" style="font-weight:700;">${fmtScore(r.value)}/${r.max}</span>
            </div>
            <div style="margin-top:8px;height:8px;border-radius:999px;background:rgba(255,255,255,.08);overflow:hidden;">
              <div class="${scoreClass((Number(r.value || 0) / r.max) * 100)}" style="height:100%;width:${pct(r.value, r.max)}%;border-radius:999px;background:currentColor;"></div>
            </div>
          </div>
        `).join('')}
      </div>
    </div>`;
}

// ---------------------------------------------------------------------------
// Review Timeline
// ---------------------------------------------------------------------------

function verdictColor(verdict) {
  const v = String(verdict || '').toLowerCase();
  if (v === 'bullish') return '#4ade80';
  if (v === 'bearish') return '#fb923c';
  return '#94a3b8';
}

function renderReviewTimeline(thesisId) {
  return `
    <div class="detail-section" id="reviewTimelineSection-${thesisId}">
      <div class="detail-section-header">
        <h3>Review Timeline</h3>
        <span style="color:var(--muted);font-size:.82rem;">5 AI review g\u1ea7n nh\u1ea5t</span>
      </div>
      <div id="reviewTimelineContent-${thesisId}">
        <div style="display:flex;align-items:center;gap:8px;color:var(--muted);font-size:.85rem;padding:12px 0;">
          <div class="spinner" style="width:16px;height:16px;"></div>
          \u0110ang t\u1ea3i...
        </div>
      </div>
    </div>
  `;
}

async function loadReviewTimeline(thesisId) {
  const container = el(`reviewTimelineContent-${thesisId}`);
  if (!container) return;
  try {
    const data = await getJson(`${readmodelBase()}/dashboard/theses/${thesisId}/review-timeline?limit=5`);
    const items = data?.items ?? [];
    if (!items.length) {
      container.innerHTML = '<p class="empty-state" style="padding:12px 0;">Ch\u01b0a c\u00f3 AI review n\u00e0o.</p>';
      return;
    }
    container.innerHTML = renderReviewTimelineItems(items);
  } catch (err) {
    container.innerHTML = `<p style="color:var(--muted);font-size:.82rem;padding:12px 0;">Kh\u00f4ng t\u1ea3i \u0111\u01b0\u1ee3c timeline: ${esc(err.message)}</p>`;
  }
}

function renderReviewTimelineItems(items) {
  return `
    <div class="review-timeline" style="position:relative;padding-left:24px;">
      <div style="
        position:absolute;left:7px;top:8px;bottom:8px;width:2px;
        background:rgba(255,255,255,.08);border-radius:2px;
      "></div>
      ${items.map((item, idx) => {
        const color = verdictColor(item.verdict);
        const isFirst = idx === 0;
        return `
          <div class="review-timeline-node" style="position:relative;margin-bottom:${isFirst ? '20px' : '16px'}">
            <!-- dot -->
            <div style="
              position:absolute;left:-20px;top:4px;
              width:10px;height:10px;border-radius:50%;
              background:${color};
              box-shadow:0 0 0 3px rgba(255,255,255,.06);
              flex-shrink:0;
            "></div>

            <div class="detail-item" style="
              border:1px solid rgba(255,255,255,.07);
              border-left:3px solid ${color}20;
              padding:12px 14px;
              border-radius:8px;
              background:rgba(255,255,255,.02);
              ${isFirst ? 'background:rgba(255,255,255,.04);' : ''}
            ">
              <!-- header row -->
              <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:8px;">
                <span class="badge ${esc(item.verdict.toLowerCase())}" style="font-size:.82rem;padding:3px 10px;">
                  ${esc(item.verdict.toUpperCase())}
                </span>
                <div style="display:flex;align-items:center;gap:6px;flex:1;min-width:120px;">
                  <div style="flex:1;height:5px;border-radius:999px;background:rgba(255,255,255,.08);overflow:hidden;">
                    <div style="height:100%;width:${item.confidence_pct}%;background:${color};border-radius:999px;"></div>
                  </div>
                  <span style="font-size:.78rem;color:var(--muted);white-space:nowrap;">${item.confidence_pct}%</span>
                </div>
                <span style="font-size:.75rem;color:var(--muted);margin-left:auto;white-space:nowrap;">
                  ${fmtDateTime(item.reviewed_at)}
                </span>
                ${item.reviewed_price ? `<span style="font-size:.75rem;color:var(--muted);">@ ${fmt(item.reviewed_price)}\u20ab</span>` : ''}
              </div>

              <!-- reasoning -->
              ${item.reasoning ? `
                <p style="font-size:.87rem;line-height:1.65;color:var(--text, #e2e8f0);margin-bottom:8px;">
                  ${esc(item.reasoning)}
                </p>` : ''}

              <!-- risk signals -->
              ${item.risk_signals.length ? `
                <div style="margin-bottom:6px;">
                  <p style="font-size:.75rem;font-weight:600;color:#fb923c;text-transform:uppercase;letter-spacing:.06em;margin-bottom:4px;">\u26a0 Risk signals</p>
                  <ul style="padding-left:1.1em;margin:0;">
                    ${item.risk_signals.map(r => `<li style="font-size:.82rem;color:var(--muted);line-height:1.55;">${esc(r)}</li>`).join('')}
                  </ul>
                </div>` : ''}

              <!-- next watch items -->
              ${item.next_watch_items.length ? `
                <div>
                  <p style="font-size:.75rem;font-weight:600;color:#7dd3fc;text-transform:uppercase;letter-spacing:.06em;margin-bottom:4px;">\ud83d\udc41 Watch</p>
                  <ul style="padding-left:1.1em;margin:0;">
                    ${item.next_watch_items.map(w => `<li style="font-size:.82rem;color:var(--muted);line-height:1.55;">${esc(w)}</li>`).join('')}
                  </ul>
                </div>` : ''}
            </div>
          </div>
        `;
      }).join('')}
    </div>
  `;
}

function renderReviewRecommendSection(thesisId) {
  return `
    <div class="detail-section" id="reviewRecommendSection-${thesisId}">
      <div class="detail-section-header" style="align-items:flex-end; gap:12px;">
        <div style="max-width: 65%;">
          <h3>Agent Suggestion</h3>
          <p class="muted" style="font-size: 0.78rem; margin-top: 2px;">
            Nh\u1edd AI r\u00e0 l\u1ea1i thesis, b\u1ea1n v\u1eabn l\u00e0 ng\u01b0\u1eddi x\u00e1c nh\u1eadn thay \u0111\u1ed5i.
          </p>
        </div>
        <button
          class="suggest-btn"
          id="aiReviewBtn-${thesisId}"
          style="
            min-height:30px;
            padding:0 14px;
            font-size:.8rem;
            margin-left:auto;
          "
        >
          Verify
        </button>
      </div>
      <div id="aiReviewLoading-${thesisId}" class="suggest-loading hidden">
        <div class="spinner"></div>
        AI \u0111ang ph\u00e2n t\u00edch thesis...
      </div>
      <div id="aiReviewResult-${thesisId}" class="suggest-result hidden"></div>
    </div>
  `;
}

function renderReviewRecommendResult(thesisId, d) {
  console.log('[AI Review raw response]', JSON.stringify(d));
  latestAiReviews[thesisId] = d;

  const confPct = Math.round((d.confidence ?? 0) * 100);
  const verdictCls =
    (String(d.verdict ?? "").toLowerCase() || "neutral") || "neutral";

  const risks  = d.risk_signals ?? d.risks ?? [];
  const watches = d.next_watch_items ?? d.nextwatchitems ?? [];

  const riskItems = risks.map((r) => `<li>${esc(r)}</li>`).join("");
  const watchItems = watches.map((w) => `<li>${esc(w)}</li>`).join("");

  return `
    <div class="suggest-body">
      <div style="display:flex;align-items:center;gap:10px;margin-bottom:10px;">
        <span class="badge ${verdictCls}" style="font-size:.95rem;padding:6px 14px;">
          ${esc(String(d.verdict ?? "").toUpperCase())}
        </span>
        <span style="color:var(--muted);font-size:.85rem;">
          Confidence ${confPct}%
        </span>
      </div>

      <div class="confidence-bar" style="margin-bottom:12px;">
        <div class="confidence-fill" style="width:${confPct}%;"></div>
      </div>

      ${
        d.reasoning
          ? `<p style="line-height:1.65;margin-bottom:10px;">${esc(d.reasoning)}</p>`
          : ""
      }

      ${
        riskItems
          ? `
        <div>
          <p class="suggest-section-title">Risk signals</p>
          <ul style="padding-left:1.2em;color:var(--muted);font-size:.88rem;">
            ${riskItems}
          </ul>
        </div>`
          : ""
      }

      ${
        watchItems
          ? `
        <div style="margin-top:10px;">
          <p class="suggest-section-title">Next watch items</p>
          <ul style="padding-left:1.2em;color:var(--muted);font-size:.88rem;">
            ${watchItems}
          </ul>
        </div>`
          : ""
      }

      <div style="display:flex;flex-direction:column;gap:6px;margin-top:14px;">
        <div style="font-size:0.8rem;color:var(--muted);">
          <strong>AI check xong \u2014 g\u1ee3i \u00fd c\u1ee7a AI:</strong><br/>
          \u2022 Verdict: ${esc(String(d.verdict ?? "").toUpperCase()) || "N/A"}, confidence ${confPct}%<br/>
          ${
            risks[0]
              ? `\u2022 R\u1ee7i ro ch\u00ednh: ${esc(risks[0])}`
              : "\u2022 R\u1ee7i ro ch\u00ednh: Ch\u01b0a c\u00f3 r\u1ee7i ro n\u1ed5i b\u1eadt \u0111\u01b0\u1ee3c n\u00eau r\u00f5."
          }
        </div>

        <div style="display:flex;gap:10px;margin-top:10px;align-items:center;flex-wrap:wrap;">
          <span style="
            display:inline-flex;align-items:center;gap:6px;
            background:rgba(109,170,69,.15);color:#6daa45;
            border:1px solid rgba(109,170,69,.3);
            border-radius:999px;padding:4px 12px;font-size:.82rem;font-weight:600;
          ">
            \u2713 \u0110\u00e3 \u00e1p d\u1ee5ng t\u1ef1 \u0111\u1ed9ng
          </span>
          <button
            class="ghost-btn dismiss-ai-review-btn"
            data-thesis-id="${thesisId}"
            style="min-height:30px;padding:0 10px;font-size:.8rem;"
          >
            \u0110\u00f3ng
          </button>
        </div>
      </div>
    </div>
  `;
}

function makeAssumptionRow(data = {}) {
  const wrap = document.createElement('div');
  wrap.className = 'detail-item form-row-item';
  wrap.innerHTML = `
    <div class="form-field" style="flex:1;">
      <label>Assumption</label>
      <textarea class="form-assumption-description" placeholder="N\u1ed9i dung assumption">${esc(data.description)}</textarea>
    </div>
    <div class="form-field" style="flex:1;">
      <label>Rationale</label>
      <textarea class="form-assumption-rationale" placeholder="C\u01a1 s\u1edf / logic">${esc(data.rationale)}</textarea>
    </div>
    <div style="display:flex;align-items:flex-end;">
      <button type="button" class="icon-btn danger remove-form-row-btn" title="X\u00f3a d\u00f2ng">\ud83d\uddd1</button>
    </div>`;
  wrap.querySelector('.remove-form-row-btn').addEventListener('click', () => wrap.remove());
  return wrap;
}

function makeCatalystRow(data = {}) {
  const wrap = document.createElement('div');
  wrap.className = 'detail-item form-row-item';
  wrap.innerHTML = `
    <div class="form-field" style="flex:1;">
      <label>Catalyst</label>
      <textarea class="form-catalyst-description" placeholder="M\u00f4 t\u1ea3 catalyst">${esc(data.description)}</textarea>
    </div>
    <div class="form-field" style="flex:1;">
      <label>Rationale</label>
      <textarea class="form-catalyst-rationale" placeholder="T\u00e1c \u0111\u1ed9ng k\u1ef3 v\u1ecdng">${esc(data.rationale)}</textarea>
    </div>
    <div class="form-field" style="min-width:180px;">
      <label>Timeline</label>
      <input class="form-catalyst-timeline" placeholder="Q3 2025" value="${esc(data.expected_timeline)}" />
    </div>
    <div style="display:flex;align-items:flex-end;">
      <button type="button" class="icon-btn danger remove-form-row-btn" title="X\u00f3a d\u00f2ng">\ud83d\uddd1</button>
    </div>`;
  wrap.querySelector('.remove-form-row-btn').addEventListener('click', () => wrap.remove());
  return wrap;
}

function clearFormRows() {
  const a = el('thesisFormAssumptionRows');
  const c = el('thesisFormCatalystRows');
  if (a) a.innerHTML = '';
  if (c) c.innerHTML = '';
}

function seedBlankFormRows() {
  const a = el('thesisFormAssumptionRows');
  const c = el('thesisFormCatalystRows');
  if (a && !a.children.length) a.appendChild(makeAssumptionRow());
  if (c && !c.children.length) c.appendChild(makeCatalystRow());
}

function collectFormAssumptions() {
  return Array.from(document.querySelectorAll('#thesisFormAssumptionRows .form-row-item'))
    .map(row => ({
      description: row.querySelector('.form-assumption-description')?.value?.trim() || '',
      rationale: row.querySelector('.form-assumption-rationale')?.value?.trim() || null,
    }))
    .filter(x => x.description);
}

function collectFormCatalysts() {
  return Array.from(document.querySelectorAll('#thesisFormCatalystRows .form-row-item'))
    .map(row => ({
      description: row.querySelector('.form-catalyst-description')?.value?.trim() || '',
      rationale: row.querySelector('.form-catalyst-rationale')?.value?.trim() || null,
      expected_timeline: row.querySelector('.form-catalyst-timeline')?.value?.trim() || null,
    }))
    .filter(x => x.description);
}

async function syncNewDetailItems(thesisId, assumptions, catalysts) {
  const [existingAssums, existingCats] = await Promise.all([
    getJson(`${thesisApiBase()}/${thesisId}/assumptions`).catch(() => []),
    getJson(`${thesisApiBase()}/${thesisId}/catalysts`).catch(() => []),
  ]);
  const assumList = Array.isArray(existingAssums) ? existingAssums : (existingAssums?.items ?? []);
  const catList = Array.isArray(existingCats) ? existingCats : (existingCats?.items ?? []);
  const existingAssumDescs = new Set(assumList.map(a => (a.description ?? '').trim()));
  const existingCatDescs = new Set(catList.map(c => (c.description ?? '').trim()));

  for (const a of assumptions) {
    if (!existingAssumDescs.has(a.description)) {
      await sendJson(`${thesisApiBase()}/${thesisId}/assumptions`, 'POST', { ...a, status: 'pending', confidence: null });
    }
  }
  for (const c of catalysts) {
    if (!existingCatDescs.has(c.description)) {
      await sendJson(`${thesisApiBase()}/${thesisId}/catalysts`, 'POST', { ...c, status: 'pending' });
    }
  }
}

function applySuggestToThesisForm(data, fallbackTicker) {
  el('thesisTickerField').value = data.ticker ?? fallbackTicker;
  el('thesisTitleField').value = data.thesis_title ?? '';
  el('thesisSummaryField').value = data.thesis_summary ?? '';
  el('thesisEntryField').value = data.entry_price_hint ?? '';
  el('thesisTargetField').value = data.target_price_hint ?? '';
  el('thesisStopField').value = data.stop_loss_hint ?? '';

  clearFormRows();
  const aWrap = el('thesisFormAssumptionRows');
  const cWrap = el('thesisFormCatalystRows');
  (data.assumptions ?? []).forEach(item => aWrap?.appendChild(makeAssumptionRow(item)));
  (data.catalysts ?? []).forEach(item => cWrap?.appendChild(makeCatalystRow(item)));
  seedBlankFormRows();
  showToast('\u2728 \u0110\u00e3 \u0111i\u1ec1n thesis form, assumptions v\u00e0 catalysts t\u1eeb AI suggest');
}

function renderSuggestResult(d) {
  const confPct = Math.round((d.confidence ?? 0) * 100);
  const assumes = (d.assumptions ?? []).map(a => `
    <div class="suggest-item">
      <strong>${esc(a.description)}</strong>
      ${a.rationale ? `<span>${esc(a.rationale)}</span>` : ''}
    </div>`).join('');
  const cats = (d.catalysts ?? []).map(c => `
    <div class="suggest-item">
      <strong>${esc(c.description)}</strong>
      <span>${c.expected_timeline ? `\ud83d\udcc5 ${esc(c.expected_timeline)} \u2014 ` : ''}${esc(c.rationale ?? '')}</span>
    </div>`).join('');

  return `
    <div class="suggest-result-header">
      <strong>\u2728 AI g\u1ee3i \u00fd cho ${esc(d.ticker)}</strong>
      <button class="apply-suggest-btn">\u2193 \u0110i\u1ec1n v\u00e0o form</button>
    </div>
    <div class="suggest-body">
      <p style="font-weight:600;margin-bottom:4px;">${esc(d.thesis_title ?? '')}</p>
      <p style="color:var(--muted);font-size:.88rem;line-height:1.6;">${esc(d.thesis_summary ?? '')}</p>
      ${d.entry_price_hint || d.target_price_hint || d.stop_loss_hint ? `
        <div style="display:flex;gap:12px;flex-wrap:wrap;margin-top:4px;">
          ${d.entry_price_hint ? `<span class="badge">Entry: ${fmt(d.entry_price_hint)}\u20ab</span>` : ''}
          ${d.target_price_hint ? `<span class="badge bullish">Target: ${fmt(d.target_price_hint)}\u20ab</span>` : ''}
          ${d.stop_loss_hint ? `<span class="badge bearish">Stop: ${fmt(d.stop_loss_hint)}\u20ab</span>` : ''}
        </div>` : ''}
      ${assumes ? `<div><p class="suggest-section-title">Assumptions g\u1ee3i \u00fd</p>${assumes}</div>` : ''}
      ${cats ? `<div><p class="suggest-section-title">Catalysts g\u1ee3i \u00fd</p>${cats}</div>` : ''}
      <div class="suggest-confidence">
        <span>\u0110\u1ed9 tin c\u1eady AI: ${confPct}%</span>
        <div class="confidence-bar"><div class="confidence-fill" style="width:${confPct}%"></div></div>
      </div>
      ${d.reasoning ? `<p style="color:var(--muted);font-size:.82rem;line-height:1.6;">${esc(d.reasoning)}</p>` : ''}
    </div>`;
}

function renderAssumptionSuggestResult(items) {
  if (!items.length) return '<p class="empty-state">AI kh\u00f4ng tr\u1ea3 v\u1ec1 assumption ph\u00f9 h\u1ee3p.</p>';
  return items.map((a, idx) => `
    <div class="suggest-item">
      <strong>${idx + 1}. ${esc(a.description)}</strong>
      ${a.rationale ? `<span>${esc(a.rationale)}</span>` : ''}
      <button type="button" class="ghost-btn apply-assumption-suggest-btn" data-index="${idx}" style="margin-top:8px;min-height:32px;padding:0 10px;font-size:.8rem;">\u0110i\u1ec1n v\u00e0o form</button>
    </div>`).join('');
}

function renderCatalystSuggestResult(items) {
  if (!items.length) return '<p class="empty-state">AI kh\u00f4ng tr\u1ea3 v\u1ec1 catalyst ph\u00f9 h\u1ee3p.</p>';
  return items.map((c, idx) => `
    <div class="suggest-item">
      <strong>${idx + 1}. ${esc(c.description)}</strong>
      <span>${c.expected_timeline ? `\ud83d\udcc5 ${esc(c.expected_timeline)} \u2014 ` : ''}${esc(c.rationale ?? '')}</span>
      <button type="button" class="ghost-btn apply-catalyst-suggest-btn" data-index="${idx}" style="margin-top:8px;min-height:32px;padding:0 10px;font-size:.8rem;">\u0110i\u1ec1n v\u00e0o form</button>
    </div>`).join('');
}

async function loadDashboard() {
  const status = el('statusFilter').value;
  const base = apiBase();
  el('errorBanner').classList.add('hidden');
  try {
    const [stats, theses, verdictAccuracy, catalysts, latestScan, latestMorningBrief, latestEodBrief] = await Promise.all([
      getJson(`${base}/stats`).catch(() => null),
      getJson(`${base}/theses?status=${status}`).catch(() => []),
      getJson(`${base}/backtesting/verdict-accuracy`).catch(() => []),
      getJson(`${base}/catalysts/upcoming?days=30`).catch(() => []),
      getJson(`${base}/scan/latest`).catch(() => null),
      getJson(`${base}/brief/latest?phase=morning`).catch(() => null),
      getJson(`${base}/brief/latest?phase=eod`).catch(() => null),
    ]);
    renderSummary(stats);
    _theses = theses?.items ?? [];
    renderThesesTable(_theses);
    renderVerdicts(verdictAccuracy?.items ?? []);
    renderCatalystList(catalysts?.items ?? []);
    renderSnapshots({
      latest_scan_at: latestScan?.created_at ?? latestScan?.generated_at ?? null,
      latest_scan_summary: latestScan?.summary ?? latestScan?.headline ?? latestScan?.notes ?? null,
      latest_morning_brief_at: latestMorningBrief?.created_at ?? latestMorningBrief?.generated_at ?? null,
      latest_morning_brief_summary: latestMorningBrief?.summary ?? latestMorningBrief?.headline ?? latestMorningBrief?.content ?? null,
      latest_eod_brief_at: latestEodBrief?.created_at ?? latestEodBrief?.generated_at ?? null,
      latest_eod_brief_summary: latestEodBrief?.summary ?? latestEodBrief?.headline ?? latestEodBrief?.content ?? null,
    });
    if (_selectedThesisId) {
      const t = _theses.find(x => x.id === _selectedThesisId);
      if (t) loadThesisDetail(t.id);
      else el('thesisDetail').innerHTML = emptyDetailHTML();
    }
  } catch (err) {
    el('errorBanner').textContent = `L\u1ed7i t\u1ea3i d\u1eef li\u1ec7u: ${err.message}`;
    el('errorBanner').classList.remove('hidden');
  }
}

function renderSummary(s) {
  if (!s) return;
  el('openTheses').textContent = s.open_theses ?? s.open_thesis_count ?? '\u2014';
  el('riskyTheses').textContent = s.risky_theses ?? s.risky_thesis_count ?? '\u2014';
  el('upcoming7d').textContent = s.upcoming_catalysts_7d ?? s.upcoming_7d ?? '\u2014';
  el('reviewsToday').textContent = s.reviews_today ?? s.review_count_today ?? '\u2014';
  el('totalReviewsHero').textContent = s.total_reviews ?? s.review_count_total ?? '\u2014';
}

function renderThesesTable(list) {
  const wrap = el('thesesTableWrap');
  if (!list.length) {
    wrap.innerHTML = '<p class="empty-state">Ch\u01b0a c\u00f3 thesis n\u00e0o. Nh\u1ea5n <strong>+ Thesis m\u1edbi</strong> \u0111\u1ec3 t\u1ea1o.</p>';
    return;
  }
  wrap.innerHTML = `
    <table>
      <thead><tr><th>M\u00e3 / H\u01b0\u1edbng</th><th>Ti\u00eau \u0111\u1ec1</th><th>Score</th><th>Status</th><th>C\u1eadp nh\u1eadt</th><th></th></tr></thead>
      <tbody>
        ${list.map(t => `
          <tr data-id="${t.id}" class="${t.id === _selectedThesisId ? 'is-selected' : ''}">
            <td class="ticker-cell"><strong>${esc(t.ticker)}</strong><span>${badge(t.direction)}</span></td>
            <td>${esc(t.title ?? '\u2014')}</td>
            <td class="${scoreClass(t.score)}">
              <div style="display:flex;flex-direction:column;gap:2px;">
                <strong>${fmtScore(t.score)}</strong>
                ${(t.score_tier || t.score_tier_icon) ? `<span style="font-size:.78rem;color:var(--muted);">${esc(t.score_tier_icon ?? '')} ${esc(t.score_tier ?? '')}</span>` : ''}
              </div>
            </td>
            <td>${badge(t.status)}</td>
            <td style="color:var(--muted);font-size:.82rem;">${fmtDate(t.updated_at)}</td>
            <td><div style="display:flex;gap:6px;"><button class="icon-btn edit-thesis-btn" data-id="${t.id}" title="S\u1eeda thesis">\u270f\ufe0f</button><button class="icon-btn danger delete-thesis-btn" data-id="${t.id}" title="X\u00f3a thesis">\ud83d\uddd1</button></div></td>
          </tr>`).join('')}
      </tbody>
    </table>`;

  wrap.querySelectorAll('tbody tr').forEach(row => {
    row.addEventListener('click', e => {
      if (e.target.closest('.edit-thesis-btn') || e.target.closest('.delete-thesis-btn')) return;
      const id = row.dataset.id;
      _selectedThesisId = id;
      wrap.querySelectorAll('tbody tr').forEach(r => r.classList.toggle('is-selected', r.dataset.id === id));
      loadThesisDetail(id);
    });
  });
  wrap.querySelectorAll('.edit-thesis-btn').forEach(btn => btn.addEventListener('click', e => { e.stopPropagation(); openEditThesisModal(btn.dataset.id); }));
  wrap.querySelectorAll('.delete-thesis-btn').forEach(btn => btn.addEventListener('click', e => { e.stopPropagation(); confirmDeleteThesis(btn.dataset.id); }));
}

async function loadThesisDetail(thesisId) {
  const wrap = el('thesisDetail');
  wrap.innerHTML = '<div class="empty-detail"><div class="spinner"></div></div>';
  try {
    const [thesis, assumptions, catalysts, reviews] = await Promise.all([
      getJson(`${thesisApiBase()}/${thesisId}`),
      getJson(`${thesisApiBase()}/${thesisId}/assumptions`).catch(() => []),
      getJson(`${thesisApiBase()}/${thesisId}/catalysts`).catch(() => []),
      getJson(`${thesisApiBase()}/${thesisId}/reviews`).catch(() => []),
    ]);
    wrap.innerHTML = renderThesisDetailHTML(thesis, assumptions, catalysts, reviews);
    wireDetailActions(thesisId, wrap);
    // Load review timeline sau khi DOM đã sẵn sàng
    loadReviewTimeline(thesisId);
  } catch (err) {
    wrap.innerHTML = `<div class="error-banner">L\u1ed7i t\u1ea3i chi ti\u1ebft: ${err.message}</div>${emptyDetailHTML()}`;
  }
}

function emptyDetailHTML() {
  return `<div class="empty-detail"><div class="empty-detail-copy"><h3>Ch\u1ecdn m\u1ed9t thesis</h3><p>Xem assumptions, catalysts v\u00e0 review history.</p></div></div>`;
}

function renderThesisDetailHTML(t, assumptions, catalysts, reviews) {
  const assumList = Array.isArray(assumptions) ? assumptions : (assumptions?.items ?? []);
  const catList = Array.isArray(catalysts) ? catalysts : (catalysts?.items ?? []);
  const revList = Array.isArray(reviews) ? reviews : (reviews?.items ?? []);
  return `
    <div class="detail-head">
      <div>
        <div class="detail-meta">
          <span class="badge" style="font-size:.9rem;padding:6px 12px;">${esc(t.ticker)}</span>
          ${badge(t.direction)}
          ${badge(t.status)}
          ${t.score_tier ? `<span class="badge ${scoreClass(t.score)}">${esc(t.score_tier_icon ?? '')} ${esc(t.score_tier)}</span>` : ''}
        </div>
        <h2 style="margin-top:10px;">${esc(t.title ?? '\u2014')}</h2>
      </div>
      <div class="detail-head-actions"><button class="ghost-btn" id="detailEditBtn">\u270f\ufe0f S\u1eeda</button><button class="danger-btn" id="detailDeleteBtn">\ud83d\uddd1 X\u00f3a thesis</button></div>
    </div>
    ${t.summary ? `<p class="detail-summary">${esc(t.summary)}</p>` : ''}
    <div class="detail-grid">
      <div class="detail-stat">
        <span>Score</span>
        <strong class="${scoreClass(t.score)}">${fmtScore(t.score)}/100</strong>
        ${t.score_tier ? `<span style="color:var(--muted);font-size:.82rem;">${esc(t.score_tier_icon ?? '')} ${esc(t.score_tier)}</span>` : ''}
      </div>
      <div class="detail-stat"><span>Entry</span><strong>${t.entry_price ? fmt(t.entry_price) + '\u20ab' : '\u2014'}</strong></div>
      <div class="detail-stat"><span>Target</span><strong>${t.target_price ? fmt(t.target_price) + '\u20ab' : '\u2014'}</strong></div>
      <div class="detail-stat"><span>Stop loss</span><strong>${t.stop_loss ? fmt(t.stop_loss) + '\u20ab' : '\u2014'}</strong></div>
      <div class="detail-stat"><span>T\u1ea1o l\u00fac</span><strong style="font-size:.9rem;">${fmtDate(t.created_at)}</strong></div>
      <div class="detail-stat"><span>C\u1eadp nh\u1eadt</span><strong style="font-size:.9rem;">${fmtDate(t.updated_at)}</strong></div>
    </div>
    <div class="detail-columns">
      <div class="detail-section"><div class="detail-section-header"><h3>Assumptions (${assumList.length})</h3><button class="ghost-btn" style="min-height:34px;padding:0 12px;font-size:.82rem;" id="addAssumBtn">+ Th\u00eam</button></div><div class="detail-list" id="assumptionList">${assumList.length ? assumList.map(a => renderAssumItem(a)).join('') : '<p class="empty-state">Ch\u01b0a c\u00f3 assumption.</p>'}</div></div>
      <div class="detail-section"><div class="detail-section-header"><h3>Catalysts (${catList.length})</h3><button class="ghost-btn" style="min-height:34px;padding:0 12px;font-size:.82rem;" id="addCatBtn">+ Th\u00eam</button></div><div class="detail-list" id="catalystDetailList">${catList.length ? catList.map(c => renderCatItem(c)).join('') : '<p class="empty-state">Ch\u01b0a c\u00f3 catalyst.</p>'}</div></div>
    </div>
    ${renderScoreBreakdown(t.score_breakdown)}
    ${renderReviewTimeline(t.id)}
    ${renderReviewRecommendSection(t.id)}
  `;
}

function renderAssumItem(a) {
  return `<div class="detail-item" data-assum-id="${a.id}"><div class="detail-item-row"><span style="font-weight:600;font-size:.9rem;">${esc(a.description)}</span><div class="detail-item-actions">${badge(a.status)}<button class="icon-btn edit-assum-btn" data-id="${a.id}" title="S\u1eeda">\u270f\ufe0f</button><button class="icon-btn danger delete-assum-btn" data-id="${a.id}" title="X\u00f3a">\ud83d\uddd1</button></div></div>${a.rationale ? `<p>${esc(a.rationale)}</p>` : ''}</div>`;
}
function renderCatItem(c) {
  return `<div class="detail-item" data-cat-id="${c.id}"><div class="detail-item-row"><span style="font-weight:600;font-size:.9rem;">${esc(c.description)}</span><div class="detail-item-actions">${badge(c.status)}<button class="icon-btn edit-cat-btn" data-id="${c.id}" title="S\u1eeda">\u270f\ufe0f</button><button class="icon-btn danger delete-cat-btn" data-id="${c.id}" title="X\u00f3a">\ud83d\uddd1</button></div></div>${c.expected_timeline ? `<p>\ud83d\udcc5 ${esc(c.expected_timeline)}</p>` : ''}${c.rationale ? `<p>${esc(c.rationale)}</p>` : ''}</div>`;
}

function wireDetailActions(thesisId, wrap) {
  wrap.querySelector('#detailEditBtn')?.addEventListener('click', () => openEditThesisModal(thesisId));
  wrap.querySelector('#detailDeleteBtn')?.addEventListener('click', () => confirmDeleteThesis(thesisId));
  wrap.querySelector('#addAssumBtn')?.addEventListener('click', () => openAssumptionModal(thesisId, null));
  wrap.querySelectorAll('.edit-assum-btn').forEach(btn => btn.addEventListener('click', () => openAssumptionModal(thesisId, btn.dataset.id)));
  wrap.querySelectorAll('.delete-assum-btn').forEach(btn => btn.addEventListener('click', () => confirmDeleteAssumption(thesisId, btn.dataset.id)));
  wrap.querySelector('#addCatBtn')?.addEventListener('click', () => openCatalystModal(thesisId, null));
  wrap.querySelectorAll('.edit-cat-btn').forEach(btn => btn.addEventListener('click', () => openCatalystModal(thesisId, btn.dataset.id)));
  wrap.querySelectorAll('.delete-cat-btn').forEach(btn => btn.addEventListener('click', () => confirmDeleteCatalyst(thesisId, btn.dataset.id)));
  wrap.querySelector(`#aiReviewBtn-${thesisId}`)?.addEventListener('click', () => triggerAiReview(thesisId));
  wrap.addEventListener('click', async (e) => {
    if (e.target.closest('.apply-ai-review-btn')) {
      const btn = e.target.closest('.apply-ai-review-btn');
      const tid = btn.dataset.thesisId;
      openApplyAiReviewModal(Number(tid));
      return;
    }
    if (e.target.closest('.dismiss-ai-review-btn')) {
      const tid = e.target.closest('.dismiss-ai-review-btn').dataset.thesisId;
      const r = wrap.querySelector(`#aiReviewResult-${tid}`);
      if (r) {
        r.classList.add('hidden');
        r.innerHTML = '';
      }
      return;
    }
  });
}

function openNewThesisModal() {
  el('thesisModalTitle').textContent = 'T\u1ea1o Thesis m\u1edbi';
  el('thesisIdField').value = '';
  el('thesisForm').reset();
  clearFormRows();
  seedBlankFormRows();
  el('suggestResult').classList.add('hidden');
  el('suggestLoading').classList.add('hidden');
  openModal('thesisModal');
}

async function openEditThesisModal(thesisId) {
  el('thesisModalTitle').textContent = 'Ch\u1ec9nh s\u1eeda Thesis';
  el('suggestResult').classList.add('hidden');
  el('suggestLoading').classList.add('hidden');
  try {
    const [t, assumptions, catalysts] = await Promise.all([
      getJson(`${thesisApiBase()}/${thesisId}`),
      getJson(`${thesisApiBase()}/${thesisId}/assumptions`).catch(() => []),
      getJson(`${thesisApiBase()}/${thesisId}/catalysts`).catch(() => []),
    ]);
    el('thesisIdField').value = t.id;
    el('thesisTickerField').value = t.ticker ?? '';
    el('thesisTitleField').value = t.title ?? '';
    el('thesisSummaryField').value = t.summary ?? '';
    el('thesisEntryField').value = t.entry_price ?? '';
    el('thesisTargetField').value = t.target_price ?? '';
    el('thesisStopField').value = t.stop_loss ?? '';
    el('thesisStatusField').value = t.status ?? 'active';
    el('thesisDirectionField').value = t.direction ?? 'bullish';
    el('suggestTicker').value = t.ticker ?? '';
    clearFormRows();
    const aWrap = el('thesisFormAssumptionRows');
    const cWrap = el('thesisFormCatalystRows');
    const assumList = Array.isArray(assumptions) ? assumptions : (assumptions?.items ?? []);
    const catList = Array.isArray(catalysts) ? catalysts : (catalysts?.items ?? []);
    assumList.forEach(a => aWrap?.appendChild(makeAssumptionRow(a)));
    catList.forEach(c => cWrap?.appendChild(makeCatalystRow(c)));
    seedBlankFormRows();
    openModal('thesisModal');
  } catch (err) {
    showToast(`Kh\u00f4ng t\u1ea3i \u0111\u01b0\u1ee3c thesis: ${err.message}`, 'error');
  }
}

el('thesisForm')?.addEventListener('submit', async e => {
  e.preventDefault();
  const btn = el('thesisSubmitBtn');
  btn.classList.add('btn-loading');
  btn.textContent = '\u0110ang l\u01b0u\u2026';
  const id = el('thesisIdField').value;
  const payload = {
    ticker: el('thesisTickerField').value.trim().toUpperCase(),
    title: el('thesisTitleField').value.trim(),
    summary: el('thesisSummaryField').value.trim() || null,
    entry_price: el('thesisEntryField').value ? Number(el('thesisEntryField').value) : null,
    target_price: el('thesisTargetField').value ? Number(el('thesisTargetField').value) : null,
    stop_loss: el('thesisStopField').value ? Number(el('thesisStopField').value) : null,
    status: el('thesisStatusField').value,
    direction: el('thesisDirectionField').value,
  };
  const assumptions = collectFormAssumptions();
  const catalysts = collectFormCatalysts();
  try {
    let thesisId = id;
    if (id) {
      await sendJson(`${thesisApiBase()}/${id}`, 'PATCH', payload);
      await syncNewDetailItems(id, assumptions, catalysts);
      showToast('\u2705 \u0110\u00e3 c\u1eadp nh\u1eadt thesis');
      thesisId = id;
    } else {
      const created = await sendJson(`${thesisApiBase()}`, 'POST', payload);
      thesisId = created?.id ?? null;
      _selectedThesisId = thesisId;
      if (thesisId) {
        for (const a of assumptions) await sendJson(`${thesisApiBase()}/${thesisId}/assumptions`, 'POST', { ...a, status: 'pending', confidence: null });
        for (const c of catalysts) await sendJson(`${thesisApiBase()}/${thesisId}/catalysts`, 'POST', { ...c, status: 'pending' });
      }
      showToast('\u2705 \u0110\u00e3 t\u1ea1o thesis m\u1edbi');
    }
    closeModal('thesisModal');
    await loadDashboard();
    if (thesisId) await loadThesisDetail(thesisId);
  } catch (err) {
    showToast(`L\u1ed7i: ${err.message}`, 'error');
  } finally {
    btn.classList.remove('btn-loading');
    btn.textContent = 'L\u01b0u Thesis';
  }
});

function confirmDeleteThesis(thesisId) {
  const t = _theses.find(x => x.id === thesisId);
  el('deleteModalMsg').textContent = `B\u1ea1n ch\u1eafc ch\u1eafn mu\u1ed1n x\u00f3a thesis "${t?.title ?? thesisId}" (${t?.ticker ?? ''})? Thao t\u00e1c n\u00e0y kh\u00f4ng th\u1ec3 ho\u00e0n t\u00e1c.`;
  _deleteCallback = async () => {
    await sendJson(`${thesisApiBase()}/${thesisId}`, 'DELETE');
    _selectedThesisId = null;
    closeModal('deleteModal');
    showToast('\ud83d\uddd1 \u0110\u00e3 x\u00f3a thesis');
    await loadDashboard();
  };
  openModal('deleteModal');
}

async function openAssumptionModal(thesisId, assumId) {
  const ticker = _theses.find(t => String(t.id) === String(thesisId))?.ticker ?? '';
  el('assumptionThesisId').value = thesisId;
  el('assumptionIdField').value = assumId ?? '';
  el('assumptionModalTitle').textContent = assumId ? 'Ch\u1ec9nh s\u1eeda Assumption' : 'Th\u00eam Assumption';
  el('assumptionForm').reset();
  el('assumptionSuggestTicker').value = ticker;
  if (el('assumptionTickerDisplay')) el('assumptionTickerDisplay').textContent = ticker || '\u2014';
  el('assumptionSuggestResult').classList.add('hidden');
  el('assumptionSuggestLoading').classList.add('hidden');
  if (assumId) {
    try {
      const a = await getJson(`${thesisApiBase()}/${thesisId}/assumptions/${assumId}`);
      el('assumptionDescField').value = a.description ?? '';
      el('assumptionRationaleField').value = a.rationale ?? '';
      el('assumptionStatusField').value = a.status ?? 'valid';
      el('assumptionConfidenceField').value = a.confidence ?? '';
    } catch (err) {
      showToast(`Kh\u00f4ng t\u1ea3i \u0111\u01b0\u1ee3c assumption: ${err.message}`, 'error');
      return;
    }
  }
  openModal('assumptionModal');
}

el('assumptionForm')?.addEventListener('submit', async e => {
  e.preventDefault();
  const thesisId = el('assumptionThesisId').value;
  const assumId = el('assumptionIdField').value;
  const payload = {
    description: el('assumptionDescField').value.trim(),
    rationale: el('assumptionRationaleField').value.trim() || null,
    status: el('assumptionStatusField').value,
    confidence: el('assumptionConfidenceField').value ? Number(el('assumptionConfidenceField').value) : null,
  };
  try {
    if (assumId) {
      await sendJson(`${thesisApiBase()}/${thesisId}/assumptions/${assumId}`, 'PATCH', payload);
      showToast('\u2705 \u0110\u00e3 c\u1eadp nh\u1eadt assumption');
    } else {
      await sendJson(`${thesisApiBase()}/${thesisId}/assumptions`, 'POST', payload);
      showToast('\u2705 \u0110\u00e3 th\u00eam assumption');
    }
    closeModal('assumptionModal');
    await loadThesisDetail(thesisId);
  } catch (err) {
    showToast(`L\u1ed7i: ${err.message}`, 'error');
  }
});

function confirmDeleteAssumption(thesisId, assumId) {
  el('deleteModalMsg').textContent = 'B\u1ea1n ch\u1eafc ch\u1eafn mu\u1ed1n x\u00f3a assumption n\u00e0y?';
  _deleteCallback = async () => {
    await sendJson(`${thesisApiBase()}/${thesisId}/assumptions/${assumId}`, 'DELETE');
    closeModal('deleteModal');
    showToast('\ud83d\uddd1 \u0110\u00e3 x\u00f3a assumption');
    await loadThesisDetail(thesisId);
  };
  openModal('deleteModal');
}

async function openCatalystModal(thesisId, catId) {
  const ticker = _theses.find(t => String(t.id) === String(thesisId))?.ticker ?? '';
  el('catalystThesisId').value = thesisId;
  el('catalystIdField').value = catId ?? '';
  el('catalystModalTitle').textContent = catId ? 'Ch\u1ec9nh s\u1eeda Catalyst' : 'Th\u00eam Catalyst';
  el('catalystForm').reset();
  el('catalystSuggestTicker').value = ticker;
  if (el('catalystTickerDisplay')) el('catalystTickerDisplay').textContent = ticker || '\u2014';
  el('catalystSuggestResult').classList.add('hidden');
  el('catalystSuggestLoading').classList.add('hidden');
  if (catId) {
    try {
      const c = await getJson(`${thesisApiBase()}/${thesisId}/catalysts/${catId}`);
      el('catalystDescField').value = c.description ?? '';
      el('catalystRationaleField').value = c.rationale ?? '';
      el('catalystStatusField').value = c.status ?? 'pending';
      el('catalystTimelineField').value = c.expected_timeline ?? '';
    } catch (err) {
      showToast(`Kh\u00f4ng t\u1ea3i \u0111\u01b0\u1ee3c catalyst: ${err.message}`, 'error');
      return;
    }
  }
  openModal('catalystModal');
}
