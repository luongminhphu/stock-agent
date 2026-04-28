'use strict';


function el(id) { return document.getElementById(id); }
function apiBase() { return '/api/v1/readmodel/dashboard'; }
function thesisApiBase() { return '/api/v1/thesis'; }
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
  if (n == null) return '—';
  return Number(n).toLocaleString('vi-VN', { maximumFractionDigits: decimals });
}

function fmtDate(d) {
  if (!d) return '—';
  return new Date(d).toLocaleDateString('vi-VN', { day: '2-digit', month: '2-digit', year: 'numeric' });
}

function badge(val) {
  const cls = String(val || '').toLowerCase();
  return `<span class="badge ${cls}">${val || '—'}</span>`;
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

// NEW: cache review + selections cho AI apply flow
let latestAiReviews = {};       // key: thesisId -> latest review payload
let aiApplyThesisId = null;     // thesis đang mở modal AI apply
let aiSelectedRecIds = [];      // rec ids user tick trong modal

function scoreClass(s) {
  if (s == null) return '';
  if (s >= 86) return 'score-high';
  if (s >= 71) return 'score-good';
  if (s >= 51) return 'score-mid';
  if (s >= 31) return 'score-warn';
  return 'score-low';
}

function fmtScore(s) {
  return s == null ? '—' : Math.round(Number(s));
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
        <span style="color:var(--muted);font-size:.82rem;">4 thành phần đóng góp vào health score</span>
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

function renderReviewRecommendSection(thesisId) {
  return `
    <div class="detail-section" id="reviewRecommendSection-${thesisId}">
      <div class="detail-section-header" style="align-items:flex-end; gap:12px;">
        <div style="max-width: 65%;">
          <h3>Agent Suggestion</h3>
          <p class="muted" style="font-size: 0.78rem; margin-top: 2px;">
            Nhờ AI rà lại thesis, bạn vẫn là người xác nhận thay đổi.
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
        AI đang phân tích thesis...
      </div>
      <div id="aiReviewResult-${thesisId}" class="suggest-result hidden"></div>
    </div>
  `;
}

function renderReviewRecommendResult(thesisId, d) {
  // cache lại latest review cho thesis này để dùng khi apply
  console.log('[AI Review raw response]', JSON.stringify(d));
  latestAiReviews[thesisId] = d;

  const confPct = Math.round((d.confidence ?? 0) * 100);
  const verdictCls =
    (String(d.verdict ?? "").toLowerCase() || "neutral") || "neutral";

  const risks  = d.risk_signals ?? d.risks ?? [];
  const watches = d.next_watch_items ?? d.nextwatchitems ?? [];

  const riskItems = risks
    .map((r) => `<li>${esc(r)}</li>`)
    .join("");
  const watchItems = watches
    .map((w) => `<li>${esc(w)}</li>`)
    .join("");

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
          <strong>AI check xong — gợi ý của AI:</strong><br/>
          • Verdict: ${esc(String(d.verdict ?? "").toUpperCase()) || "N/A"}, confidence ${confPct}%<br/>
          ${
            risks[0]
              ? `• Rủi ro chính: ${esc(risks[0])}`
              : "• Rủi ro chính: Chưa có rủi ro nổi bật được nêu rõ."
          }
        </div>

        <div style="display:flex;gap:10px;margin-top:10px;align-items:center;flex-wrap:wrap;">
          <span style="
            display:inline-flex;align-items:center;gap:6px;
            background:rgba(109,170,69,.15);color:#6daa45;
            border:1px solid rgba(109,170,69,.3);
            border-radius:999px;padding:4px 12px;font-size:.82rem;font-weight:600;
          ">
            ✓ Đã áp dụng tự động
          </span>
          <button
            class="ghost-btn dismiss-ai-review-btn"
            data-thesis-id="${thesisId}"
            style="min-height:30px;padding:0 10px;font-size:.8rem;"
          >
            Đóng
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
      <textarea class="form-assumption-description" placeholder="Nội dung assumption">${esc(data.description)}</textarea>
    </div>
    <div class="form-field" style="flex:1;">
      <label>Rationale</label>
      <textarea class="form-assumption-rationale" placeholder="Cơ sở / logic">${esc(data.rationale)}</textarea>
    </div>
    <div style="display:flex;align-items:flex-end;">
      <button type="button" class="icon-btn danger remove-form-row-btn" title="Xóa dòng">🗑</button>
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
      <textarea class="form-catalyst-description" placeholder="Mô tả catalyst">${esc(data.description)}</textarea>
    </div>
    <div class="form-field" style="flex:1;">
      <label>Rationale</label>
      <textarea class="form-catalyst-rationale" placeholder="Tác động kỳ vọng">${esc(data.rationale)}</textarea>
    </div>
    <div class="form-field" style="min-width:180px;">
      <label>Timeline</label>
      <input class="form-catalyst-timeline" placeholder="Q3 2025" value="${esc(data.expected_timeline)}" />
    </div>
    <div style="display:flex;align-items:flex-end;">
      <button type="button" class="icon-btn danger remove-form-row-btn" title="Xóa dòng">🗑</button>
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
  showToast('✨ Đã điền thesis form, assumptions và catalysts từ AI suggest');
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
      <span>${c.expected_timeline ? `📅 ${esc(c.expected_timeline)} — ` : ''}${esc(c.rationale ?? '')}</span>
    </div>`).join('');

  return `
    <div class="suggest-result-header">
      <strong>✨ AI gợi ý cho ${esc(d.ticker)}</strong>
      <button class="apply-suggest-btn">↓ Điền vào form</button>
    </div>
    <div class="suggest-body">
      <p style="font-weight:600;margin-bottom:4px;">${esc(d.thesis_title ?? '')}</p>
      <p style="color:var(--muted);font-size:.88rem;line-height:1.6;">${esc(d.thesis_summary ?? '')}</p>
      ${d.entry_price_hint || d.target_price_hint || d.stop_loss_hint ? `
        <div style="display:flex;gap:12px;flex-wrap:wrap;margin-top:4px;">
          ${d.entry_price_hint ? `<span class="badge">Entry: ${fmt(d.entry_price_hint)}₫</span>` : ''}
          ${d.target_price_hint ? `<span class="badge bullish">Target: ${fmt(d.target_price_hint)}₫</span>` : ''}
          ${d.stop_loss_hint ? `<span class="badge bearish">Stop: ${fmt(d.stop_loss_hint)}₫</span>` : ''}
        </div>` : ''}
      ${assumes ? `<div><p class="suggest-section-title">Assumptions gợi ý</p>${assumes}</div>` : ''}
      ${cats ? `<div><p class="suggest-section-title">Catalysts gợi ý</p>${cats}</div>` : ''}
      <div class="suggest-confidence">
        <span>Độ tin cậy AI: ${confPct}%</span>
        <div class="confidence-bar"><div class="confidence-fill" style="width:${confPct}%"></div></div>
      </div>
      ${d.reasoning ? `<p style="color:var(--muted);font-size:.82rem;line-height:1.6;">${esc(d.reasoning)}</p>` : ''}
    </div>`;
}

function renderAssumptionSuggestResult(items) {
  if (!items.length) return '<p class="empty-state">AI không trả về assumption phù hợp.</p>';
  return items.map((a, idx) => `
    <div class="suggest-item">
      <strong>${idx + 1}. ${esc(a.description)}</strong>
      ${a.rationale ? `<span>${esc(a.rationale)}</span>` : ''}
      <button type="button" class="ghost-btn apply-assumption-suggest-btn" data-index="${idx}" style="margin-top:8px;min-height:32px;padding:0 10px;font-size:.8rem;">Điền vào form</button>
    </div>`).join('');
}

function renderCatalystSuggestResult(items) {
  if (!items.length) return '<p class="empty-state">AI không trả về catalyst phù hợp.</p>';
  return items.map((c, idx) => `
    <div class="suggest-item">
      <strong>${idx + 1}. ${esc(c.description)}</strong>
      <span>${c.expected_timeline ? `📅 ${esc(c.expected_timeline)} — ` : ''}${esc(c.rationale ?? '')}</span>
      <button type="button" class="ghost-btn apply-catalyst-suggest-btn" data-index="${idx}" style="margin-top:8px;min-height:32px;padding:0 10px;font-size:.8rem;">Điền vào form</button>
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
      latest_morning_brief_data: latestMorningBrief ?? null,
      latest_eod_brief_at: latestEodBrief?.created_at ?? latestEodBrief?.generated_at ?? null,
      latest_eod_brief_summary: latestEodBrief?.summary ?? latestEodBrief?.headline ?? latestEodBrief?.content ?? null,
      latest_eod_brief_data: latestEodBrief ?? null,
    });
    if (_selectedThesisId) {
      const t = _theses.find(x => x.id === _selectedThesisId);
      if (t) loadThesisDetail(t.id);
      else el('thesisDetail').innerHTML = emptyDetailHTML();
    }
  } catch (err) {
    el('errorBanner').textContent = `Lỗi tải dữ liệu: ${err.message}`;
    el('errorBanner').classList.remove('hidden');
  }
}

function renderSummary(s) {
  if (!s) return;
  if (el('openTheses'))     el('openTheses').textContent     = s.open_theses ?? s.open_thesis_count ?? '—';
  if (el('riskyTheses'))    el('riskyTheses').textContent    = s.risky_theses ?? s.risky_thesis_count ?? '—';
  if (el('upcoming7d'))     el('upcoming7d').textContent     = s.upcoming_catalysts_7d ?? s.upcoming_7d ?? '—';
  if (el('reviewsToday'))   el('reviewsToday').textContent   = s.reviews_today ?? s.review_count_today ?? '—';
  if (el('totalReviewsHero')) el('totalReviewsHero').textContent = s.total_reviews ?? s.review_count_total ?? '—';
}

function renderThesesTable(list) {
  const wrap = el('thesesTableWrap');
  if (!list.length) {
    wrap.innerHTML = '<p class="empty-state">Chưa có thesis nào. Nhấn <strong>+ Thesis mới</strong> để tạo.</p>';
    return;
  }
  wrap.innerHTML = `
    <table>
      <thead><tr><th>Mã / Hướng</th><th>Tiêu đề</th><th>Score</th><th>Status</th><th>Cập nhật</th><th></th></tr></thead>
      <tbody>
        ${list.map(t => `
          <tr data-id="${t.id}" class="${t.id === _selectedThesisId ? 'is-selected' : ''}">
            <td class="ticker-cell"><strong>${esc(t.ticker)}</strong><span>${badge(t.direction)}</span></td>
            <td>${esc(t.title ?? '—')}</td>
            <td class="${scoreClass(t.score)}">
              <div style="display:flex;flex-direction:column;gap:2px;">
                <strong>${fmtScore(t.score)}</strong>
                ${(t.score_tier || t.score_tier_icon) ? `<span style="font-size:.78rem;color:var(--muted);">${esc(t.score_tier_icon ?? '')} ${esc(t.score_tier ?? '')}</span>` : ''}
              </div>
            </td>
            <td>${badge(t.status)}</td>
            <td style="color:var(--muted);font-size:.82rem;">${fmtDate(t.updated_at)}</td>
            <td><div style="display:flex;gap:6px;"><button class="icon-btn edit-thesis-btn" data-id="${t.id}" title="Sửa thesis">✏️</button><button class="icon-btn danger delete-thesis-btn" data-id="${t.id}" title="Xóa thesis">🗑</button></div></td>
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
  } catch (err) {
    wrap.innerHTML = `<div class="error-banner">Lỗi tải chi tiết: ${err.message}</div>${emptyDetailHTML()}`;
  }
}

function emptyDetailHTML() {
  return `<div class="empty-detail"><div class="empty-detail-copy"><h3>Chọn một thesis</h3><p>Xem assumptions, catalysts và review history.</p></div></div>`;
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
        <h2 style="margin-top:10px;">${esc(t.title ?? '—')}</h2>
      </div>
      <div class="detail-head-actions"><button class="ghost-btn" id="detailEditBtn">✏️ Sửa</button><button class="danger-btn" id="detailDeleteBtn">🗑 Xóa thesis</button></div>
    </div>
    ${t.summary ? `<p class="detail-summary">${esc(t.summary)}</p>` : ''}
    <div class="detail-grid">
      <div class="detail-stat">
        <span>Score</span>
        <strong class="${scoreClass(t.score)}">${fmtScore(t.score)}/100</strong>
        ${t.score_tier ? `<span style="color:var(--muted);font-size:.82rem;">${esc(t.score_tier_icon ?? '')} ${esc(t.score_tier)}</span>` : ''}
      </div>
      <div class="detail-stat"><span>Entry</span><strong>${t.entry_price ? fmt(t.entry_price) + '₫' : '—'}</strong></div>
      <div class="detail-stat"><span>Target</span><strong>${t.target_price ? fmt(t.target_price) + '₫' : '—'}</strong></div>
      <div class="detail-stat"><span>Stop loss</span><strong>${t.stop_loss ? fmt(t.stop_loss) + '₫' : '—'}</strong></div>
      <div class="detail-stat"><span>Tạo lúc</span><strong style="font-size:.9rem;">${fmtDate(t.created_at)}</strong></div>
      <div class="detail-stat"><span>Cập nhật</span><strong style="font-size:.9rem;">${fmtDate(t.updated_at)}</strong></div>
    </div>
    <div class="detail-columns">
      <div class="detail-section"><div class="detail-section-header"><h3>Assumptions (${assumList.length})</h3><button class="ghost-btn" style="min-height:34px;padding:0 12px;font-size:.82rem;" id="addAssumBtn">+ Thêm</button></div><div class="detail-list" id="assumptionList">${assumList.length ? assumList.map(a => renderAssumItem(a)).join('') : '<p class="empty-state">Chưa có assumption.</p>'}</div></div>
      <div class="detail-section"><div class="detail-section-header"><h3>Catalysts (${catList.length})</h3><button class="ghost-btn" style="min-height:34px;padding:0 12px;font-size:.82rem;" id="addCatBtn">+ Thêm</button></div><div class="detail-list" id="catalystDetailList">${catList.length ? catList.map(c => renderCatItem(c)).join('') : '<p class="empty-state">Chưa có catalyst.</p>'}</div></div>
    </div>
    ${revList.length ? `<div style="margin-top:18px;"><h3 style="margin-bottom:12px;">Review gần nhất</h3>${revList.slice(0, 3).map(r => `<div class="review-card"><div class="review-head"><span class="review-meta">${fmtDate(r.reviewed_at)}</span>${badge(r.verdict)}<span style="color:var(--muted);font-size:.82rem;">Conf: ${r.confidence ?? '—'}</span></div><p class="review-reasoning">${esc(r.reasoning ?? '')}</p></div>`).join('')}</div>` : ''}
    ${renderScoreBreakdown(t.score_breakdown)}
    ${renderReviewRecommendSection(t.id)}
  `;
}



function renderAssumItem(a) {
  return `<div class="detail-item" data-assum-id="${a.id}"><div class="detail-item-row"><span style="font-weight:600;font-size:.9rem;">${esc(a.description)}</span><div class="detail-item-actions">${badge(a.status)}<button class="icon-btn edit-assum-btn" data-id="${a.id}" title="Sửa">✏️</button><button class="icon-btn danger delete-assum-btn" data-id="${a.id}" title="Xóa">🗑</button></div></div>${a.rationale ? `<p>${esc(a.rationale)}</p>` : ''}</div>`;
}
function renderCatItem(c) {
  return `<div class="detail-item" data-cat-id="${c.id}"><div class="detail-item-row"><span style="font-weight:600;font-size:.9rem;">${esc(c.description)}</span><div class="detail-item-actions">${badge(c.status)}<button class="icon-btn edit-cat-btn" data-id="${c.id}" title="Sửa">✏️</button><button class="icon-btn danger delete-cat-btn" data-id="${c.id}" title="Xóa">🗑</button></div></div>${c.expected_timeline ? `<p>📅 ${esc(c.expected_timeline)}</p>` : ''}${c.rationale ? `<p>${esc(c.rationale)}</p>` : ''}</div>`;
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
  wrap.addEventListener("click", async (e) => {
    // Mở modal "Áp dụng gợi ý"
    if (e.target.closest(".apply-ai-review-btn")) {
      const btn = e.target.closest(".apply-ai-review-btn");
      const tid = btn.dataset.thesisId;
      openApplyAiReviewModal(Number(tid));
      return;
    }
  
    // "Để sau" — chỉ ẩn card AI check
    if (e.target.closest(".dismiss-ai-review-btn")) {
      const tid = e.target.closest(".dismiss-ai-review-btn").dataset.thesisId;
      const r = wrap.querySelector(`#aiReviewResult-${tid}`);
      if (r) {
        r.classList.add("hidden");
        r.innerHTML = "";
      }
      return;
    }
  });
}

function openNewThesisModal() {
  el('thesisModalTitle').textContent = 'Tạo Thesis mới';
  el('thesisIdField').value = '';
  el('thesisForm').reset();
  clearFormRows();
  seedBlankFormRows();
  el('suggestResult').classList.add('hidden');
  el('suggestLoading').classList.add('hidden');
  openModal('thesisModal');
}

async function openEditThesisModal(thesisId) {
  el('thesisModalTitle').textContent = 'Chỉnh sửa Thesis';
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
    showToast(`Không tải được thesis: ${err.message}`, 'error');
  }
}

el('thesisForm')?.addEventListener('submit', async e => {
  e.preventDefault();
  const btn = el('thesisSubmitBtn');
  btn.classList.add('btn-loading');
  btn.textContent = 'Đang lưu…';
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
      showToast('✅ Đã cập nhật thesis');
      thesisId = id;
    } else {
      const created = await sendJson(`${thesisApiBase()}`, 'POST', payload);
      thesisId = created?.id ?? null;
      _selectedThesisId = thesisId;
      if (thesisId) {
        for (const a of assumptions) await sendJson(`${thesisApiBase()}/${thesisId}/assumptions`, 'POST', { ...a, status: 'pending', confidence: null });
        for (const c of catalysts) await sendJson(`${thesisApiBase()}/${thesisId}/catalysts`, 'POST', { ...c, status: 'pending' });
      }
      showToast('✅ Đã tạo thesis mới');
    }
    closeModal('thesisModal');
    await loadDashboard();
    if (thesisId) await loadThesisDetail(thesisId);
  } catch (err) {
    showToast(`Lỗi: ${err.message}`, 'error');
  } finally {
    btn.classList.remove('btn-loading');
    btn.textContent = 'Lưu Thesis';
  }
});

function confirmDeleteThesis(thesisId) {
  const t = _theses.find(x => x.id === thesisId);
  el('deleteModalMsg').textContent = `Bạn chắc chắn muốn xóa thesis "${t?.title ?? thesisId}" (${t?.ticker ?? ''})? Thao tác này không thể hoàn tác.`;
  _deleteCallback = async () => {
    await sendJson(`${thesisApiBase()}/${thesisId}`, 'DELETE');
    _selectedThesisId = null;
    closeModal('deleteModal');
    showToast('🗑 Đã xóa thesis');
    await loadDashboard();
  };
  openModal('deleteModal');
}

async function openAssumptionModal(thesisId, assumId) {
  const ticker = _theses.find(t => String(t.id) === String(thesisId))?.ticker ?? '';
  el('assumptionThesisId').value = thesisId;
  el('assumptionIdField').value = assumId ?? '';
  el('assumptionModalTitle').textContent = assumId ? 'Chỉnh sửa Assumption' : 'Thêm Assumption';
  el('assumptionForm').reset();
  el('assumptionSuggestTicker').value = ticker;
  if (el('assumptionTickerDisplay')) el('assumptionTickerDisplay').textContent = ticker || '—';
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
      showToast(`Không tải được assumption: ${err.message}`, 'error');
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
      showToast('✅ Đã cập nhật assumption');
    } else {
      await sendJson(`${thesisApiBase()}/${thesisId}/assumptions`, 'POST', payload);
      showToast('✅ Đã thêm assumption');
    }
    closeModal('assumptionModal');
    await loadThesisDetail(thesisId);
  } catch (err) {
    showToast(`Lỗi: ${err.message}`, 'error');
  }
});

function confirmDeleteAssumption(thesisId, assumId) {
  el('deleteModalMsg').textContent = 'Bạn chắc chắn muốn xóa assumption này?';
  _deleteCallback = async () => {
    await sendJson(`${thesisApiBase()}/${thesisId}/assumptions/${assumId}`, 'DELETE');
    closeModal('deleteModal');
    showToast('🗑 Đã xóa assumption');
    await loadThesisDetail(thesisId);
  };
  openModal('deleteModal');
}

async function openCatalystModal(thesisId, catId) {
  const ticker = _theses.find(t => String(t.id) === String(thesisId))?.ticker ?? '';
  el('catalystThesisId').value = thesisId;
  el('catalystIdField').value = catId ?? '';
  el('catalystModalTitle').textContent = catId ? 'Chỉnh sửa Catalyst' : 'Thêm Catalyst';
  el('catalystForm').reset();
  el('catalystSuggestTicker').value = ticker;
  if (el('catalystTickerDisplay')) el('catalystTickerDisplay').textContent = ticker || '—';
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
      showToast(`Không tải được catalyst: ${err.message}`, 'error');
      return;
    }
  }
  openModal('catalystModal');
}

el('catalystForm')?.addEventListener('submit', async e => {
  e.preventDefault();
  const thesisId = el('catalystThesisId').value;
  const catId = el('catalystIdField').value;
  const payload = {
    description: el('catalystDescField').value.trim(),
    rationale: el('catalystRationaleField').value.trim() || null,
    status: el('catalystStatusField').value,
    expected_timeline: el('catalystTimelineField').value.trim() || null,
  };
  try {
    if (catId) {
      await sendJson(`${thesisApiBase()}/${thesisId}/catalysts/${catId}`, 'PATCH', payload);
      showToast('✅ Đã cập nhật catalyst');
    } else {
      await sendJson(`${thesisApiBase()}/${thesisId}/catalysts`, 'POST', payload);
      showToast('✅ Đã thêm catalyst');
    }
    closeModal('catalystModal');
    await loadThesisDetail(thesisId);
  } catch (err) {
    showToast(`Lỗi: ${err.message}`, 'error');
  }
});

function confirmDeleteCatalyst(thesisId, catId) {
  el('deleteModalMsg').textContent = 'Bạn chắc chắn muốn xóa catalyst này?';
  _deleteCallback = async () => {
    await sendJson(`${thesisApiBase()}/${thesisId}/catalysts/${catId}`, 'DELETE');
    closeModal('deleteModal');
    showToast('🗑 Đã xóa catalyst');
    await loadThesisDetail(thesisId);
  };
  openModal('deleteModal');
}

el('deleteConfirmBtn')?.addEventListener('click', async () => {
  if (!_deleteCallback) return;
  const btn = el('deleteConfirmBtn');
  btn.classList.add('btn-loading');
  btn.textContent = 'Đang xóa…';
  try { await _deleteCallback(); }
  catch (err) { showToast(`Lỗi xóa: ${err.message}`, 'error'); }
  finally {
    btn.classList.remove('btn-loading');
    btn.textContent = 'Xóa';
    _deleteCallback = null;
  }
});

el('aiSuggestBtn')?.addEventListener('click', async () => {
  const ticker = (el('suggestTicker')?.value ?? el('thesisTickerField')?.value ?? '').trim().toUpperCase();
  if (!ticker) { showToast('Nhập mã cổ phiếu trước', 'error'); return; }
  const btn = el('aiSuggestBtn');
  const loading = el('suggestLoading');
  const result = el('suggestResult');
  btn.disabled = true;
  loading.classList.remove('hidden');
  result.classList.add('hidden');
  try {
    const data = await sendJson(`${thesisApiBase()}/suggest?ticker=${encodeURIComponent(ticker)}`, 'POST', null);
    result.innerHTML = renderSuggestResult(data);
    result.classList.remove('hidden');
    result.querySelector('.apply-suggest-btn')?.addEventListener('click', () => applySuggestToThesisForm(data, ticker));
  } catch (err) {
    result.innerHTML = `<div class="error-banner" style="margin:0;">AI suggest lỗi: ${err.message}</div>`;
    result.classList.remove('hidden');
  } finally {
    btn.disabled = false;
    loading.classList.add('hidden');
  }
});

el('assumptionAiSuggestBtn')?.addEventListener('click', async () => {
  const ticker = el('assumptionSuggestTicker')?.value?.trim().toUpperCase();
  if (!ticker) { showToast('Không xác định được mã cổ phiếu cho assumption này', 'error'); return; }
  const loading = el('assumptionSuggestLoading');
  const result = el('assumptionSuggestResult');
  loading.classList.remove('hidden');
  result.classList.add('hidden');
  try {
    const data = await sendJson(`${thesisApiBase()}/suggest?ticker=${encodeURIComponent(ticker)}`, 'POST', null);
    const items = data.assumptions ?? [];
    result.innerHTML = renderAssumptionSuggestResult(items);
    result.classList.remove('hidden');
    result.querySelectorAll('.apply-assumption-suggest-btn').forEach(btn => btn.addEventListener('click', () => {
      const item = items[Number(btn.dataset.index)];
      el('assumptionDescField').value = item?.description ?? '';
      el('assumptionRationaleField').value = item?.rationale ?? '';
      showToast('✨ Đã điền assumption từ AI');
    }));
  } catch (err) {
    result.innerHTML = `<div class="error-banner" style="margin:0;">AI suggest lỗi: ${err.message}</div>`;
    result.classList.remove('hidden');
  } finally {
    loading.classList.add('hidden');
  }
});

el('catalystAiSuggestBtn')?.addEventListener('click', async () => {
  const ticker = el('catalystSuggestTicker')?.value?.trim().toUpperCase();
  if (!ticker) { showToast('Không xác định được mã cổ phiếu cho catalyst này', 'error'); return; }
  const loading = el('catalystSuggestLoading');
  const result = el('catalystSuggestResult');
  loading.classList.remove('hidden');
  result.classList.add('hidden');
  try {
    const data = await sendJson(`${thesisApiBase()}/suggest?ticker=${encodeURIComponent(ticker)}`, 'POST', null);
    const items = data.catalysts ?? [];
    result.innerHTML = renderCatalystSuggestResult(items);
    result.classList.remove('hidden');
    result.querySelectorAll('.apply-catalyst-suggest-btn').forEach(btn => btn.addEventListener('click', () => {
      const item = items[Number(btn.dataset.index)];
      el('catalystDescField').value = item?.description ?? '';
      el('catalystRationaleField').value = item?.rationale ?? '';
      el('catalystTimelineField').value = item?.expected_timeline ?? '';
      showToast('✨ Đã điền catalyst từ AI');
    }));
  } catch (err) {
    result.innerHTML = `<div class="error-banner" style="margin:0;">AI suggest lỗi: ${err.message}</div>`;
    result.classList.remove('hidden');
  } finally {
    loading.classList.add('hidden');
  }
});

async function openApplyAiReviewModal(thesisId) {
  aiApplyThesisId = thesisId;
  aiSelectedRecIds = [];

  const body = el('aiApplyModalBody');
  const confirmBtn = el('aiApplyConfirmBtn');
  if (!body) return;

  body.innerHTML = '<p class="empty-state">Đang tải gợi ý từ AI...</p>';
  if (confirmBtn) {
    confirmBtn.disabled = true;
    confirmBtn.textContent = 'Đang tải...';
  }

  openModal('aiApplyModal');

  try {
    const res = await getJson(`${thesisApiBase()}/${thesisId}/recommendations`);
    const items = Array.isArray(res) ? res : (res?.items ?? []);

    if (!items.length) {
      body.innerHTML = '<p class="empty-state">Không còn gợi ý nào đang chờ áp dụng.</p>';
      return;
    }

    aiSelectedRecIds = items.map(r => r.id);

    body.innerHTML = `
      <div class="review-columns">
        <div class="review-box">
          <p class="suggest-section-title">Assumptions</p>
          ${items.filter(r => r.target_type === 'assumption').map(r => `
            <label class="suggest-item">
              <div style="display:flex;align-items:flex-start;gap:8px">
                <input type="checkbox" class="ai-rec-checkbox" data-rec-id="${r.id}" checked>
                <div>
                  <strong>${esc(r.target_description ?? '')}</strong>
                  <span> → <b>${esc(r.recommended_status ?? '')}</b>: ${esc(r.reason ?? '')}</span>
                </div>
              </div>
            </label>`).join('') || '<p class="empty-state">Không có assumption nào.</p>'}
        </div>
        <div class="review-box">
          <p class="suggest-section-title">Catalysts</p>
          ${items.filter(r => r.target_type === 'catalyst').map(r => `
            <label class="suggest-item">
              <div style="display:flex;align-items:flex-start;gap:8px">
                <input type="checkbox" class="ai-rec-checkbox" data-rec-id="${r.id}" checked>
                <div>
                  <strong>${esc(r.target_description ?? '')}</strong>
                  <span> → <b>${esc(r.recommended_status ?? '')}</b>: ${esc(r.reason ?? '')}</span>
                </div>
              </div>
            </label>`).join('') || '<p class="empty-state">Không có catalyst nào.</p>'}
        </div>
      </div>`;

    body.querySelectorAll('.ai-rec-checkbox').forEach(cb => {
      cb.addEventListener('change', () => {
        const id = Number(cb.dataset.recId);
        if (cb.checked) {
          if (!aiSelectedRecIds.includes(id)) aiSelectedRecIds.push(id);
        } else {
          aiSelectedRecIds = aiSelectedRecIds.filter(x => x !== id);
        }
      });
    });

  } catch (err) {
    body.innerHTML = `<div class="error-banner" style="margin:0">Không tải được gợi ý: ${esc(err.message)}</div>`;
  } finally {
    if (confirmBtn && aiSelectedRecIds.length > 0) {
      confirmBtn.disabled = false;
      confirmBtn.textContent = 'Xác nhận áp dụng';
    } else if (confirmBtn && !confirmBtn.disabled) {
      confirmBtn.disabled = false;
      confirmBtn.textContent = 'Xác nhận áp dụng';
    }
  }
}

function renderVerdicts(list) {
  const wrap = el('verdictList');
  const rows = list;
  if (!rows.length) { wrap.innerHTML = '<p class="empty-state">Chưa có dữ liệu.</p>'; return; }
  wrap.innerHTML = rows.map(v => `<div class="row-item"><div><div class="row-title">${badge(v.verdict)}</div><div class="row-subtitle">${v.count ?? v.total ?? 0} review · ${v.pct != null ? v.pct + '%' : v.accuracy != null ? (v.accuracy * 100).toFixed(1) + '%' : ''}</div></div></div>`).join('');
}

function renderCatalystList(list) {
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

const SENTIMENT_META = {
  RISK_ON:   { cls: 'sent-risk-on',  icon: '🟢', label: 'Risk-On'  },
  RISK_OFF:  { cls: 'sent-risk-off', icon: '🔴', label: 'Risk-Off' },
  MIXED:     { cls: 'sent-mixed',    icon: '⚡', label: 'Mixed'    },
  UNCERTAIN: { cls: 'sent-uncertain',icon: '❓', label: 'Uncertain' },
};

const SIGNAL_CLS = {
  breakout: 'breakout', 'risk-on': 'breakout',
  bearish:  'bearish',  'risk-off': 'bearish',
  pullback: 'pullback', neutral: 'watchlist',
};

function renderBriefCard(phase, brief, dateStr) {
  // brief = full BriefOutput object hoặc null
  const isEod    = phase === 'eod';
  const phaseIcon = isEod ? '🌆' : '🌅';
  const phaseLabel = isEod ? 'EOD brief' : 'Morning brief';
  const phaseCls   = isEod ? 'phase-eod' : 'phase-morning';

  if (!brief) {
    return `
      <div class="brief-card ${phaseCls}">
        <div class="brief-header">
          <div class="brief-phase-icon">${phaseIcon}</div>
          <div>
            <div class="brief-phase-label">${phaseLabel}</div>
            <div class="brief-date">${dateStr || '—'}</div>
          </div>
        </div>
        <div class="brief-empty">Chưa có ${phaseLabel.toLowerCase()}.</div>
      </div>`;
  }

  // brief tồn tại nhưng thiếu structured fields (legacy data)
  if (!brief.headline && !brief.ticker_summaries?.length && brief.content) {
    return `<div class="brief-card ${phaseCls}">
      <div class="brief-header">
        <div class="brief-phase-icon">${phaseIcon}</div>
        <div>
          <div class="brief-phase-label">${phaseLabel}</div>
          <div class="brief-date">${dateStr || '—'}</div>
        </div>
      </div>
      <div class="brief-summary">${esc(brief.content)}</div>
    </div>`;
  }

  const sent  = SENTIMENT_META[brief.sentiment] ?? SENTIMENT_META.UNCERTAIN;
  const movers = (brief.key_movers ?? []).slice(0, 8);
  const tickers = brief.ticker_summaries ?? [];
  const alerts  = brief.watchlist_alerts ?? [];
  const actions = brief.action_items ?? [];

  const moverPills = movers.map(m => {
    // m có thể là "NVL +5.4%" hoặc plain ticker
    const upMatch   = m.match(/\+[\d.]+%/);
    const downMatch = m.match(/-[\d.]+%/);
    const cls = upMatch ? 'up' : downMatch ? 'down' : '';
    const chgPart = (upMatch || downMatch)
      ? `<span class="chg">${(upMatch || downMatch)[0]}</span>` : '';
    const ticker = m.replace(/[+-][\d.]+%/g, '').trim();
    return `<span class="mover-pill ${cls}">${esc(ticker)} ${chgPart}</span>`;
  }).join('');

  const tickerRows = tickers.map(t => {
    const chgCls   = t.change_pct > 0 ? 'pos' : t.change_pct < 0 ? 'neg' : '';
    const chgFmt   = t.change_pct != null
      ? (t.change_pct > 0 ? '+' : '') + Number(t.change_pct).toFixed(2) + '%'
      : '—';
    const sigKey   = (t.signal ?? '').toLowerCase();
    const sigCls   = SIGNAL_CLS[sigKey] ?? '';
    return `
      <tr>
        <td class="t-ticker">${esc(t.ticker)}</td>
        <td><span class="t-signal ${sigCls}">${esc(t.signal ?? '—')}</span></td>
        <td class="t-chg ${chgCls}">${chgFmt}</td>
        <td class="t-note">${esc(t.one_line ?? t.watch_reason ?? '')}</td>
      </tr>`;
  }).join('');

  const alertItems  = alerts.map(a =>
    `<div class="brief-item">  ${esc(a)}</div>`).join('');
  const actionItems = actions.map(a =>
    `<div class="brief-item action">  ${esc(a)}</div>`).join('');

  return `
    <div class="brief-card ${phaseCls}">
      <div class="brief-header">
        <div class="brief-phase-icon">${phaseIcon}</div>
        <div>
          <div class="brief-phase-label">${phaseLabel}</div>
          <div class="brief-date">${dateStr || '—'}</div>
        </div>
        <span class="sentiment-badge ${sent.cls}">${sent.icon} ${sent.label}</span>
      </div>

      ${brief.headline ? `
      <div class="brief-headline">${esc(brief.headline)}</div>` : ''}

      <div class="brief-body">

        ${moverPills ? `
        <div class="brief-movers">
          <span class="mover-label">Movers</span>
          ${moverPills}
        </div>` : ''}

        ${tickerRows ? `
        <div>
          <div class="brief-section-title">Watchlist</div>
          <table class="ticker-table">
            <thead><tr>
              <th>Mã</th><th>Tín hiệu</th>
              <th style="text-align:right">±%</th><th>Ghi chú</th>
            </tr></thead>
            <tbody>${tickerRows}</tbody>
          </table>
        </div>` : ''}

        ${alertItems ? `
        <div class="brief-section">
          <div class="brief-section-title">Cảnh báo watchlist</div>
          ${alertItems}
        </div>` : ''}

        ${actionItems ? `
        <div class="brief-section">
          <div class="brief-section-title">Hành động đề xuất</div>
          ${actionItems}
        </div>` : ''}

        ${brief.summary ? `
        <div class="brief-summary">${esc(brief.summary)}</div>` : ''}

      </div>
    </div>`;
}

function renderSnapshots(s) {
  if (!s) return;
  if (el('latestScanAt'))      el('latestScanAt').textContent = fmtDate(s.latest_scan_at);
  const scanSummaryEl = el('latestScanSummary');
  if (scanSummaryEl) scanSummaryEl.innerHTML = s.latest_scan_summary
    ? highlightScanText(s.latest_scan_summary)
    : '<span style="color:var(--muted)">Chưa có scan snapshot.</span>';

  // Morning brief — render structured card
  const morningWrap = el('morningBriefWrap');
  if (morningWrap) {
    morningWrap.innerHTML = renderBriefCard(
      'morning',
      s.latest_morning_brief_data ?? null,
      fmtDate(s.latest_morning_brief_at),
    );
  }

  // EOD brief — render structured card
  const eodWrap = el('eodBriefWrap');
  if (eodWrap) {
    eodWrap.innerHTML = renderBriefCard(
      'eod',
      s.latest_eod_brief_data ?? null,
      fmtDate(s.latest_eod_brief_at),
    );
  }
}

async function loadBacktesting() {
  const base = apiBase();
  try {
    const [acc, perf] = await Promise.all([
      getJson(`${base}/backtesting/verdict-accuracy`).catch(() => []),
      getJson(`${base}/backtesting/thesis-performances`).catch(() => []),
    ]);
    renderAccuracy(acc);
    renderPerformance(perf);
  } catch {}
}

function renderAccuracy(list) {
  const wrap = el('accuracyWrap');
  const rows = Array.isArray(list) ? list : (list?.items ?? []);
  if (!rows.length) { wrap.innerHTML = '<p class="empty-state">Chưa có dữ liệu.</p>'; return; }
  wrap.innerHTML = `<table><thead><tr><th>Verdict</th><th>Đúng</th><th>Sai</th><th>Accuracy</th></tr></thead><tbody>${rows.map(r => `<tr><td>${badge(r.verdict)}</td><td class="score-high">${r.correct ?? 0}</td><td class="score-low">${r.wrong ?? 0}</td><td>${r.accuracy != null ? (r.accuracy * 100).toFixed(1) + '%' : '—'}</td></tr>`).join('')}</tbody></table>`;
}

function renderPerformance(list) {
  const wrap = el('performanceWrap');
  const rows = Array.isArray(list) ? list : (list?.items ?? []);
  if (!rows.length) { wrap.innerHTML = '<p class="empty-state">Chưa có dữ liệu.</p>'; return; }
  wrap.innerHTML = `<table><thead><tr><th>Mã</th><th>PnL%</th><th>Điểm</th><th>Status</th></tr></thead><tbody>${rows.map(r => `<tr><td class="ticker-cell"><strong>${esc(r.ticker)}</strong></td><td class="${r.pnl_pct >= 0 ? 'pnl-pos' : 'pnl-neg'}">${r.pnl_pct != null ? (r.pnl_pct > 0 ? '+' : '') + r.pnl_pct.toFixed(2) + '%' : '—'}</td><td class="${scoreClass(r.score)}">${r.score ?? '—'}</td><td>${badge(r.status)}</td></tr>`).join('')}</tbody></table>`;
}

async function triggerAiReview(thesisId) {
  const loading = el(`aiReviewLoading-${thesisId}`);
  const result  = el(`aiReviewResult-${thesisId}`);
  const btn     = el(`aiReviewBtn-${thesisId}`);
  if (!loading || !result) return;
  if (btn) btn.disabled = true;
  loading.classList.remove('hidden');
  result.classList.add('hidden');
  result.innerHTML = '';

  try {
    const data = await sendJson(`${thesisApiBase()}/${thesisId}/review`, 'POST', null);
    latestAiReviews[thesisId] = data;
    const reviewHTML = renderReviewRecommendResult(thesisId, data);
    await loadThesisDetail(thesisId);
    const freshResult = el(`aiReviewResult-${thesisId}`);
    if (freshResult) {
      freshResult.innerHTML = reviewHTML;
      freshResult.classList.remove('hidden');
    }
    showToast('✅ AI đã review & áp dụng gợi ý. Score đã được cập nhật.', 'success', 4000);
  } catch (err) {
    const freshResult = el(`aiReviewResult-${thesisId}`) ?? result;
    freshResult.innerHTML = `<div class="error-banner" style="margin:0">AI review lỗi: ${esc(err.message)}</div>`;
    freshResult.classList.remove('hidden');
  } finally {
    const freshLoading = el(`aiReviewLoading-${thesisId}`);
    const freshBtn     = el(`aiReviewBtn-${thesisId}`);
    if (freshLoading) freshLoading.classList.add('hidden');
    if (freshBtn) freshBtn.disabled = false;
  }
}

async function approveReview(thesisId, verdict, reasoning, confidence) {
  try {
    await sendJson(`${thesisApiBase()}/${thesisId}/reviews`, 'POST', {
      verdict,
      reasoning: reasoning || null,
      confidence: confidence || null,
    });
    showToast(`✅ Đã lưu review: ${verdict.toUpperCase()}`);
    // Ẩn recommendation section sau khi approve
    const result = el(`aiReviewResult-${thesisId}`);
    if (result) { result.classList.add('hidden'); result.innerHTML = ''; }
    // Reload detail để thấy review mới trong history
    await loadThesisDetail(thesisId);
    await loadDashboard();
  } catch (err) {
    showToast(`Lỗi lưu review: ${err.message}`, 'error');
  }
}

const elAiApplyConfirmBtn = el('aiApplyConfirmBtn');
if (elAiApplyConfirmBtn) {
  elAiApplyConfirmBtn.addEventListener('click', async () => {
    if (!aiApplyThesisId) {
      showToast('Không xác định được thesis.', 'error');
      return;
    }
    if (!aiSelectedRecIds.length) {
      showToast('Chọn ít nhất 1 gợi ý để áp dụng.', 'error');
      return;
    }

    const btn = elAiApplyConfirmBtn;
    btn.classList.add('btn-loading');
    btn.textContent = 'Đang áp dụng...';

    try {
      const latest = latestAiReviews[aiApplyThesisId] ?? {};
      await sendJson(
        `${thesisApiBase()}/${aiApplyThesisId}/ai-review/apply`,
        'POST',
        {
          applied_recommendation_ids: aiSelectedRecIds,
          verdict: latest.verdict ?? null,
          ai_confidence: latest.confidence ?? null,
        }
      );

      showToast('Đã áp dụng gợi ý từ AI.', 'success');
      closeModal('aiApplyModal');
      await loadThesisDetail(aiApplyThesisId);
    } catch (err) {
      showToast(`Lỗi khi áp dụng gợi ý: ${err.message}`, 'error');
    } finally {
      btn.classList.remove('btn-loading');
      btn.textContent = 'Xác nhận áp dụng';
      aiApplyThesisId = null;
      aiSelectedRecIds = [];
    }
  });
}

document.addEventListener('DOMContentLoaded', async () => {
  document.querySelectorAll('[data-close]').forEach(btn => btn.addEventListener('click', () => closeModal(btn.dataset.close)));
  document.querySelectorAll('dialog').forEach(dlg => dlg.addEventListener('click', e => { if (e.target === dlg) dlg.close(); }));
  el('reloadBtn')?.addEventListener('click', () => { loadDashboard(); loadBacktesting(); });
  el('newThesisBtn')?.addEventListener('click', openNewThesisModal);
  el('addFormAssumptionBtn')?.addEventListener('click', () => el('thesisFormAssumptionRows')?.appendChild(makeAssumptionRow()));
  el('addFormCatalystBtn')?.addEventListener('click', () => el('thesisFormCatalystRows')?.appendChild(makeCatalystRow()));
  seedBlankFormRows();
  loadDashboard();
  loadBacktesting();
});
