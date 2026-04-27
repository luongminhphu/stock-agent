'use strict';


function el(id) { return document.getElementById(id); }
function apiBase() { return '/api/v1/readmodel/dashboard'; }
function thesisApiBase() { return '/api/v1/thesis/'; }
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
            Nhờ AI rà lại thesis, bản văn là người xác nhận thay đổi.
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
  latestAiReviews[thesisId] = d;

  const confPct = Math.round((d.confidence ?? 0) * 100);
  const verdictCls =
    (String(d.verdict ?? "").toLowerCase() || "neutral") || "neutral";

  const risks =
    d.risk_signals ??
    d.risks ??
    d.risksignals ??
    [];
  const watches =
    d.next_watch_items ??
    d.nextwatchitems ??
    [];

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
          • Verdict: ${esc(String(d.verdict ?? "").toUpperCase())}, confidence ${confPct}%<br/>
          ${
            risks[0]
              ? `• Rủi ro chính: ${esc(risks[0])}`
              : "• Rủi ro chính: Chưa có rủi ro nổi bật được nêu rõ."
          }
        </div>

        <div style="display:flex;gap:10px;margin-top:6px;flex-wrap:wrap;">
          <button
            class="suggest-btn"
            style="background:var(--accent);color:#fff;min-height:32px;padding:0 14px;font-size:.82rem;"
            onclick="openApplyAiModal('${esc(String(thesisId))}')"
          >
            Apply gợi ý
          </button>
          <button
            class="suggest-btn outline"
            style="min-height:32px;padding:0 14px;font-size:.82rem;"
            onclick="dismissAiReview('${esc(String(thesisId))}')"
          >
            Bỏ qua
          </button>
        </div>
      </div>
    </div>
  `;
}

function dismissAiReview(thesisId) {
  const result = el(`aiReviewResult-${thesisId}`);
  if (result) {
    result.classList.add('hidden');
    result.innerHTML = '';
  }
}

function openApplyAiModal(thesisId) {
  const review = latestAiReviews[thesisId];
  if (!review) return showToast('Chưa có kết quả AI để apply.', 'error');

  aiApplyThesisId  = thesisId;
  aiSelectedRecIds = [];

  const recs = review.recommendations ?? [];
  const body  = el('aiApplyModalBody');
  if (!body) return;

  if (!recs.length) {
    body.innerHTML = '<p style="color:var(--muted);font-size:.9rem;">AI không có đề xuất thay đổi cụ thể nào.</p>';
  } else {
    body.innerHTML = recs.map((rec, i) => `
      <label class="rec-item" style="display:flex;align-items:flex-start;gap:10px;padding:10px 0;border-bottom:1px solid var(--border);cursor:pointer;">
        <input type="checkbox" data-rec-id="${esc(String(rec.id ?? i))}" style="margin-top:3px;accent-color:var(--accent);">
        <div>
          <div style="font-weight:600;font-size:.9rem;">${esc(rec.field ?? rec.type ?? 'Đề xuất')}</div>
          <div style="color:var(--muted);font-size:.85rem;margin-top:2px;">${esc(rec.suggestion ?? rec.content ?? '')}</div>
          ${rec.reason ? `<div style="font-size:.8rem;margin-top:4px;color:var(--accent);">${esc(rec.reason)}</div>` : ''}
        </div>
      </label>
    `).join('');
  }

  openModal('aiApplyModal');
}

async function confirmApplyAi() {
  if (!aiApplyThesisId) return;
  const checks = el('aiApplyModalBody')?.querySelectorAll('input[type=checkbox]:checked');
  aiSelectedRecIds = Array.from(checks ?? []).map(c => c.dataset.recId).filter(Boolean);

  if (!aiSelectedRecIds.length) {
    return showToast('Chưa chọn đề xuất nào.', 'error');
  }

  const review = latestAiReviews[aiApplyThesisId];
  if (!review) return;

  try {
    await sendJson(`${thesisApiBase()}${aiApplyThesisId}/review/apply`, 'POST', {
      review_id: review.id ?? null,
      rec_ids: aiSelectedRecIds,
    });
    showToast('Đã apply đề xuất AI thành công!');
    closeModal('aiApplyModal');
    await loadThesisDetail(aiApplyThesisId);
  } catch (err) {
    showToast(`Apply lỗi: ${err.message}`, 'error');
  }
}

async function approveReview(thesisId, reviewId) {
  try {
    await sendJson(`${thesisApiBase()}${thesisId}/reviews/${reviewId}/approve`, 'POST', null);
    showToast('Đã xác nhận review!');
    await loadThesisDetail(thesisId);
  } catch (err) {
    showToast(`Lỗi approve: ${err.message}`, 'error');
  }
}

// ─── THESIS LIST ───────────────────────────────────────────────────────────────

async function loadDashboard() {
  const wrap = el('thesisList');
  if (!wrap) return;
  wrap.innerHTML = '<div class="loading"><div class="spinner"></div> Đang tải...</div>';
  try {
    const data = await getJson(apiBase());
    _theses = (data?.theses ?? data ?? []);
    renderThesisList(_theses, wrap);
  } catch (err) {
    wrap.innerHTML = `<div class="error-banner">Lỗi tải thesis: ${esc(err.message)}</div>`;
  }
}

function renderThesisList(theses, wrap) {
  if (!theses.length) {
    wrap.innerHTML = `
      <div class="empty-state">
        <div style="font-size:2.5rem;margin-bottom:12px;">📋</div>
        <h3>Chưa có thesis nào</h3>
        <p>Tạo thesis đầu tiên để bắt đầu theo dõi mã cổ phiếu.</p>
        <button class="suggest-btn" style="margin-top:12px;" onclick="openModal('createThesisModal')">+ Tạo thesis</button>
      </div>`;
    return;
  }
  wrap.innerHTML = theses.map(t => renderThesisCard(t)).join('');
  theses.forEach(t => {
    el(`viewDetailBtn-${t.id}`)?.addEventListener('click', () => loadThesisDetail(t.id));
    el(`editThesisBtn-${t.id}`)?.addEventListener('click', () => openEditModal(t));
    el(`deleteThesisBtn-${t.id}`)?.addEventListener('click', () => openDeleteModal(t.id, t.ticker));
  });
}

function renderThesisCard(t) {
  const score  = t.score ?? null;
  const cls    = scoreClass(score);
  const ticker = esc(t.ticker ?? '—');
  const title  = esc(t.title ?? '—');

  return `
    <div class="thesis-card" id="thesisCard-${t.id}">
      <div class="thesis-card-header">
        <div class="thesis-card-meta">
          <span class="thesis-ticker">${ticker}</span>
          ${badge(t.status)}
        </div>
        <div class="thesis-score ${cls}" title="Health score">
          ${fmtScore(score)}
        </div>
      </div>
      <div class="thesis-title">${title}</div>
      <div class="thesis-card-footer">
        <span class="thesis-date">Tạo: ${fmtDate(t.created_at)}</span>
        <div class="thesis-actions">
          <button class="action-btn" id="viewDetailBtn-${t.id}" title="Xem chi tiết">📊 Chi tiết</button>
          <button class="action-btn" id="editThesisBtn-${t.id}" title="Chỉnh sửa">✏️</button>
          <button class="action-btn danger" id="deleteThesisBtn-${t.id}" title="Xóa">🗑</button>
        </div>
      </div>
    </div>
  `;
}

// ─── THESIS DETAIL ────────────────────────────────────────────────────────────

async function loadThesisDetail(thesisId) {
  const wrap = el('thesisDetail');
  if (!wrap) return;
  wrap.innerHTML = '<div class="loading"><div class="spinner"></div> Đang tải chi tiết...</div>';
  try {
    const data = await getJson(`${thesisApiBase()}${thesisId}`);
    wrap.innerHTML = renderThesisDetail(data);
    wireDetailActions(thesisId, data);
    el('thesisDetailSection')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  } catch (err) {
    wrap.innerHTML = `<div class="error-banner">Lỗi tải chi tiết: ${esc(err.message)}</div>`;
  }
}

function renderThesisDetail(t) {
  const score = t.score ?? null;
  const cls   = scoreClass(score);

  const assumptions = t.assumptions ?? [];
  const catalysts   = t.catalysts   ?? [];
  const reviews     = t.reviews     ?? [];
  const breakdown   = t.score_breakdown ?? null;

  return `
    <div class="detail-header">
      <div class="detail-header-left">
        <span class="thesis-ticker" style="font-size:1.3rem;">${esc(t.ticker ?? '—')}</span>
        ${badge(t.status)}
      </div>
      <div class="detail-score ${cls}" title="Health score">${fmtScore(score)}</div>
    </div>
    <h2 style="font-size:1.1rem;font-weight:700;margin-bottom:4px;">${esc(t.title ?? '—')}</h2>
    <p style="color:var(--muted);font-size:.88rem;margin-bottom:16px;">Tạo: ${fmtDate(t.created_at)} · Cập nhật: ${fmtDate(t.updated_at)}</p>

    ${t.summary ? `<div class="detail-section"><p>${esc(t.summary)}</p></div>` : ''}

    ${renderScoreBreakdown(breakdown)}

    ${renderAssumptionsSection(t.id, assumptions)}
    ${renderCatalystsSection(t.id, catalysts)}
    ${renderReviewsSection(t.id, reviews)}
    ${renderReviewRecommendSection(t.id)}
  `;
}

function wireDetailActions(thesisId, data) {
  el(`aiReviewBtn-${thesisId}`)?.addEventListener('click', () => triggerAiReview(thesisId));

  const addAsmBtn = el(`addAssumptionBtn-${thesisId}`);
  addAsmBtn?.addEventListener('click', () => openAddAssumptionModal(thesisId));

  const addCatBtn = el(`addCatalystBtn-${thesisId}`);
  addCatBtn?.addEventListener('click', () => openAddCatalystModal(thesisId));

  // Wire assumption status toggles
  (data.assumptions ?? []).forEach(a => {
    el(`toggleAsmBtn-${a.id}`)?.addEventListener('click', () => toggleAssumptionStatus(thesisId, a.id, a.status));
    el(`deleteAsmBtn-${a.id}`)?.addEventListener('click', () => deleteAssumption(thesisId, a.id));
  });

  // Wire catalyst status toggles
  (data.catalysts ?? []).forEach(c => {
    el(`toggleCatBtn-${c.id}`)?.addEventListener('click', () => toggleCatalystStatus(thesisId, c.id, c.status));
    el(`deleteCatBtn-${c.id}`)?.addEventListener('click', () => deleteCatalyst(thesisId, c.id));
  });
}

function renderAssumptionsSection(thesisId, assumptions) {
  const items = assumptions.map(a => `
    <div class="detail-item" id="assumptionItem-${a.id}">
      <div class="detail-item-row">
        <span style="font-weight:600;font-size:.9rem;">${esc(a.content ?? a.description ?? '—')}</span>
        ${badge(a.status)}
      </div>
      ${a.note ? `<p style="color:var(--muted);font-size:.83rem;margin-top:4px;">${esc(a.note)}</p>` : ''}
      <div style="display:flex;gap:8px;margin-top:8px;">
        <button class="action-btn" id="toggleAsmBtn-${a.id}" style="font-size:.78rem;">⟳ Toggle</button>
        <button class="action-btn danger" id="deleteAsmBtn-${a.id}" style="font-size:.78rem;">🗑</button>
      </div>
    </div>
  `).join('');

  return `
    <div class="detail-section">
      <div class="detail-section-header">
        <h3>Assumptions (${assumptions.length})</h3>
        <button class="suggest-btn" id="addAssumptionBtn-${thesisId}" style="min-height:28px;padding:0 12px;font-size:.8rem;">+ Thêm</button>
      </div>
      <div class="detail-list">
        ${items || '<p class="muted" style="font-size:.85rem;">Chưa có assumption nào.</p>'}
      </div>
    </div>
  `;
}

function renderCatalystsSection(thesisId, catalysts) {
  const items = catalysts.map(c => `
    <div class="detail-item" id="catalystItem-${c.id}">
      <div class="detail-item-row">
        <span style="font-weight:600;font-size:.9rem;">${esc(c.content ?? c.description ?? '—')}</span>
        ${badge(c.status)}
      </div>
      ${c.note ? `<p style="color:var(--muted);font-size:.83rem;margin-top:4px;">${esc(c.note)}</p>` : ''}
      ${c.expected_date ? `<p style="color:var(--muted);font-size:.8rem;">📅 ${fmtDate(c.expected_date)}</p>` : ''}
      <div style="display:flex;gap:8px;margin-top:8px;">
        <button class="action-btn" id="toggleCatBtn-${c.id}" style="font-size:.78rem;">⟳ Toggle</button>
        <button class="action-btn danger" id="deleteCatBtn-${c.id}" style="font-size:.78rem;">🗑</button>
      </div>
    </div>
  `).join('');

  return `
    <div class="detail-section">
      <div class="detail-section-header">
        <h3>Catalysts (${catalysts.length})</h3>
        <button class="suggest-btn" id="addCatalystBtn-${thesisId}" style="min-height:28px;padding:0 12px;font-size:.8rem;">+ Thêm</button>
      </div>
      <div class="detail-list">
        ${items || '<p class="muted" style="font-size:.85rem;">Chưa có catalyst nào.</p>'}
      </div>
    </div>
  `;
}

function renderReviewsSection(thesisId, reviews) {
  const items = reviews.map(r => `
    <div class="detail-item">
      <div class="detail-item-row">
        ${badge(r.verdict)}
        <span style="color:var(--muted);font-size:.8rem;">${fmtDate(r.created_at)}</span>
      </div>
      ${r.reasoning ? `<p style="color:var(--muted);font-size:.85rem;margin-top:6px;line-height:1.5;">${esc(r.reasoning)}</p>` : ''}
      ${r.confidence != null ? `
        <div style="margin-top:8px;">
          <span style="font-size:.8rem;color:var(--muted);">Confidence: ${Math.round(r.confidence * 100)}%</span>
          <div class="confidence-bar" style="margin-top:4px;">
            <div class="confidence-fill" style="width:${Math.round(r.confidence * 100)}%;"></div>
          </div>
        </div>
      ` : ''}
    </div>
  `).join('');

  return `
    <div class="detail-section">
      <div class="detail-section-header">
        <h3>Review history (${reviews.length})</h3>
      </div>
      <div class="detail-list">
        ${items || '<p class="muted" style="font-size:.85rem;">Chưa có review nào.</p>'}
      </div>
    </div>
  `;
}

// ─── TRIGGER AI REVIEW ────────────────────────────────────────────────────────

async function triggerAiReview(thesisId) {
  const loading = el(`aiReviewLoading-${thesisId}`);
  const result  = el(`aiReviewResult-${thesisId}`);
  const btn     = el(`aiReviewBtn-${thesisId}`);
  if (!loading || !result) return;
  btn && (btn.disabled = true);
  loading.classList.remove('hidden');
  result.classList.add('hidden');
  result.innerHTML = '';
  try {
    // ✅ Gọi đúng endpoint: POST /v1/thesis/{id}/review
    const data = await sendJson(
      `${thesisApiBase()}${thesisId}/review`, 'POST', null
    );
    // Cache lại để Apply flow dùng
    latestAiReviews[thesisId] = data;
    result.innerHTML = renderReviewRecommendResult(thesisId, data);
    result.classList.remove('hidden');
    // ✅ Reload thesis detail để cập nhật health score sau khi backend recompute
    await loadThesisDetail(thesisId);
  } catch (err) {
    result.innerHTML = `<div class="error-banner" style="margin:0;">AI review lỗi: ${esc(err.message)}</div>`;
    result.classList.remove('hidden');
  } finally {
    loading.classList.add('hidden');
    btn && (btn.disabled = false);
  }
}

// ─── ASSUMPTION ACTIONS ───────────────────────────────────────────────────────

async function toggleAssumptionStatus(thesisId, assumptionId, currentStatus) {
  const nextStatus = currentStatus === 'valid' ? 'invalid' : 'valid';
  try {
    await sendJson(`${thesisApiBase()}${thesisId}/assumptions/${assumptionId}`, 'PATCH', { status: nextStatus });
    await loadThesisDetail(thesisId);
  } catch (err) {
    showToast(`Lỗi cập nhật assumption: ${err.message}`, 'error');
  }
}

async function deleteAssumption(thesisId, assumptionId) {
  if (!confirm('Xóa assumption này?')) return;
  try {
    await sendJson(`${thesisApiBase()}${thesisId}/assumptions/${assumptionId}`, 'DELETE', null);
    showToast('Đã xóa assumption.');
    await loadThesisDetail(thesisId);
  } catch (err) {
    showToast(`Lỗi xóa: ${err.message}`, 'error');
  }
}

async function openAddAssumptionModal(thesisId) {
  _selectedThesisId = thesisId;
  el('addAssumptionThesisId').value = thesisId;
  el('addAssumptionContent').value  = '';
  el('addAssumptionNote').value     = '';
  openModal('addAssumptionModal');
}

async function submitAddAssumption() {
  const thesisId = el('addAssumptionThesisId').value;
  const content  = el('addAssumptionContent').value.trim();
  if (!content) return showToast('Nội dung assumption không được trống.', 'error');

  try {
    await sendJson(`${thesisApiBase()}${thesisId}/assumptions`, 'POST', { content, status: 'valid' });
    showToast('Đã thêm assumption!');
    closeModal('addAssumptionModal');
    await loadThesisDetail(thesisId);
  } catch (err) {
    showToast(`Lỗi thêm assumption: ${err.message}`, 'error');
  }
}

// ─── CATALYST ACTIONS ────────────────────────────────────────────────────────

async function toggleCatalystStatus(thesisId, catalystId, currentStatus) {
  const nextStatus = currentStatus === 'pending' ? 'triggered' : 'pending';
  try {
    await sendJson(`${thesisApiBase()}${thesisId}/catalysts/${catalystId}`, 'PATCH', { status: nextStatus });
    await loadThesisDetail(thesisId);
  } catch (err) {
    showToast(`Lỗi cập nhật catalyst: ${err.message}`, 'error');
  }
}

async function deleteCatalyst(thesisId, catalystId) {
  if (!confirm('Xóa catalyst này?')) return;
  try {
    await sendJson(`${thesisApiBase()}${thesisId}/catalysts/${catalystId}`, 'DELETE', null);
    showToast('Đã xóa catalyst.');
    await loadThesisDetail(thesisId);
  } catch (err) {
    showToast(`Lỗi xóa: ${err.message}`, 'error');
  }
}

async function openAddCatalystModal(thesisId) {
  _selectedThesisId = thesisId;
  el('addCatalystThesisId').value  = thesisId;
  el('addCatalystContent').value   = '';
  el('addCatalystNote').value      = '';
  el('addCatalystDate').value      = '';
  openModal('addCatalystModal');
}

async function submitAddCatalyst() {
  const thesisId    = el('addCatalystThesisId').value;
  const content     = el('addCatalystContent').value.trim();
  const note        = el('addCatalystNote').value.trim();
  const expectedDate = el('addCatalystDate').value;
  if (!content) return showToast('Nội dung catalyst không được trống.', 'error');

  try {
    await sendJson(`${thesisApiBase()}${thesisId}/catalysts`, 'POST', {
      content, note: note || null, status: 'pending',
      expected_date: expectedDate || null,
    });
    showToast('Đã thêm catalyst!');
    closeModal('addCatalystModal');
    await loadThesisDetail(thesisId);
  } catch (err) {
    showToast(`Lỗi thêm catalyst: ${err.message}`, 'error');
  }
}

// ─── CREATE / EDIT / DELETE THESIS ───────────────────────────────────────────

function openEditModal(t) {
  el('editThesisId').value      = t.id;
  el('editThesisTicker').value  = t.ticker ?? '';
  el('editThesisTitle').value   = t.title  ?? '';
  el('editThesisSummary').value = t.summary ?? '';
  el('editThesisStatus').value  = t.status  ?? 'active';
  openModal('editThesisModal');
}

async function submitEditThesis() {
  const id      = el('editThesisId').value;
  const ticker  = el('editThesisTicker').value.trim().toUpperCase();
  const title   = el('editThesisTitle').value.trim();
  const summary = el('editThesisSummary').value.trim();
  const status  = el('editThesisStatus').value;
  if (!ticker || !title) return showToast('Ticker và tiêu đề không được trống.', 'error');

  try {
    await sendJson(`${thesisApiBase()}${id}`, 'PUT', { ticker, title, summary: summary || null, status });
    showToast('Đã cập nhật thesis!');
    closeModal('editThesisModal');
    await loadDashboard();
  } catch (err) {
    showToast(`Lỗi cập nhật: ${err.message}`, 'error');
  }
}

function openDeleteModal(thesisId, ticker) {
  el('deleteThesisLabel').textContent = ticker ?? thesisId;
  _deleteCallback = async () => {
    try {
      await sendJson(`${thesisApiBase()}${thesisId}`, 'DELETE', null);
      showToast('Đã xóa thesis.');
      closeModal('deleteConfirmModal');
      el('thesisDetail').innerHTML = '';
      await loadDashboard();
    } catch (err) {
      showToast(`Lỗi xóa: ${err.message}`, 'error');
    }
  };
  openModal('deleteConfirmModal');
}

async function submitCreateThesis() {
  const ticker  = el('newThesisTicker').value.trim().toUpperCase();
  const title   = el('newThesisTitle').value.trim();
  const summary = el('newThesisSummary').value.trim();

  if (!ticker || !title) return showToast('Ticker và tiêu đề không được trống.', 'error');

  const assumptions = Array.from(
    el('thesisFormAssumptionRows')?.querySelectorAll('.assumption-row') ?? []
  ).map(row => ({
    content: row.querySelector('.asm-content')?.value.trim(),
    status:  'valid',
  })).filter(a => a.content);

  const catalysts = Array.from(
    el('thesisFormCatalystRows')?.querySelectorAll('.catalyst-row') ?? []
  ).map(row => ({
    content:       row.querySelector('.cat-content')?.value.trim(),
    expected_date: row.querySelector('.cat-date')?.value || null,
    status:        'pending',
  })).filter(c => c.content);

  try {
    await sendJson(thesisApiBase(), 'POST', {
      ticker, title, summary: summary || null,
      assumptions, catalysts,
    });
    showToast('Đã tạo thesis mới!');
    closeModal('createThesisModal');
    el('newThesisTicker').value  = '';
    el('newThesisTitle').value   = '';
    el('newThesisSummary').value = '';
    el('thesisFormAssumptionRows').innerHTML = '';
    el('thesisFormCatalystRows').innerHTML   = '';
    await loadDashboard();
  } catch (err) {
    showToast(`Lỗi tạo thesis: ${err.message}`, 'error');
  }
}

function makeAssumptionRow() {
  const div = document.createElement('div');
  div.className = 'assumption-row';
  div.style.cssText = 'display:flex;gap:8px;align-items:center;margin-bottom:6px;';
  div.innerHTML = `
    <input class="asm-content form-input" type="text" placeholder="Nội dung assumption" style="flex:1;">
    <button type="button" class="action-btn danger" onclick="this.closest('.assumption-row').remove()" style="font-size:.8rem;">✕</button>
  `;
  return div;
}

function makeCatalystRow() {
  const div = document.createElement('div');
  div.className = 'catalyst-row';
  div.style.cssText = 'display:flex;gap:8px;align-items:center;margin-bottom:6px;flex-wrap:wrap;';
  div.innerHTML = `
    <input class="cat-content form-input" type="text" placeholder="Nội dung catalyst" style="flex:2;min-width:160px;">
    <input class="cat-date form-input" type="date" style="flex:1;min-width:120px;">
    <button type="button" class="action-btn danger" onclick="this.closest('.catalyst-row').remove()" style="font-size:.8rem;">✕</button>
  `;
  return div;
}

// ─── BACKTESTING ─────────────────────────────────────────────────────────────

async function loadBacktesting() {
  const wrap = el('backtestingList');
  if (!wrap) return;
  try {
    const data = await getJson('/api/v1/backtesting/');
    const items = data?.results ?? data ?? [];
    if (!items.length) {
      wrap.innerHTML = '<p class="muted" style="font-size:.85rem;">Chưa có backtesting nào.</p>';
      return;
    }
    wrap.innerHTML = items.map(b => `
      <div class="detail-item">
        <div class="detail-item-row">
          <span style="font-weight:600;">${esc(b.ticker ?? b.strategy ?? '—')}</span>
          <span class="${b.pnl >= 0 ? 'score-good' : 'score-low'}" style="font-weight:700;">
            ${b.pnl >= 0 ? '+' : ''}${fmt(b.pnl, 1)}%
          </span>
        </div>
        <p style="color:var(--muted);font-size:.83rem;margin-top:4px;">
          ${fmtDate(b.start_date)} → ${fmtDate(b.end_date)}
          ${b.sharpe != null ? ` · Sharpe: ${fmt(b.sharpe, 2)}` : ''}
        </p>
      </div>
    `).join('');
  } catch (err) {
    wrap.innerHTML = `<p style="color:var(--muted);font-size:.85rem;">Chưa có dữ liệu backtesting.</p>`;
  }
}

// ─── INIT ────────────────────────────────────────────────────────────────────

document.addEventListener('DOMContentLoaded', () => {
  // Thesis list
  el('refreshBtn')?.addEventListener('click', loadDashboard);
  el('createThesisBtn')?.addEventListener('click', () => openModal('createThesisModal'));

  // Create modal
  el('submitCreateThesisBtn')?.addEventListener('click', submitCreateThesis);
  el('cancelCreateThesisBtn')?.addEventListener('click', () => closeModal('createThesisModal'));

  // Edit modal
  el('submitEditThesisBtn')?.addEventListener('click', submitEditThesis);
  el('cancelEditThesisBtn')?.addEventListener('click',  () => closeModal('editThesisModal'));

  // Delete modal
  el('confirmDeleteBtn')?.addEventListener('click', () => _deleteCallback?.());
  el('cancelDeleteBtn')?.addEventListener('click',  () => closeModal('deleteConfirmModal'));

  // Add assumption modal
  el('submitAddAssumptionBtn')?.addEventListener('click', submitAddAssumption);
  el('cancelAddAssumptionBtn')?.addEventListener('click', () => closeModal('addAssumptionModal'));

  // Add catalyst modal
  el('submitAddCatalystBtn')?.addEventListener('click', submitAddCatalyst);
  el('cancelAddCatalystBtn')?.addEventListener('click',  () => closeModal('addCatalystModal'));

  // AI apply modal
  el('confirmAiApplyBtn')?.addEventListener('click', confirmApplyAi);
  el('cancelAiApplyBtn')?.addEventListener('click',  () => closeModal('aiApplyModal'));

  // Form row builders
  el('addFormAssumptionBtn')?.addEventListener('click', () => el('thesisFormAssumptionRows')?.appendChild(makeAssumptionRow()));
  el('addFormCatalystBtn')?.addEventListener('click', () => el('thesisFormCatalystRows')?.appendChild(makeCatalystRow()));

  seedBlankFormRows();
  loadDashboard();
  loadBacktesting();
});


function seedBlankFormRows() {
  const asmContainer = el('thesisFormAssumptionRows');
  const catContainer = el('thesisFormCatalystRows');
  if (asmContainer && !asmContainer.children.length) asmContainer.appendChild(makeAssumptionRow());
  if (catContainer && !catContainer.children.length)  catContainer.appendChild(makeCatalystRow());
}
