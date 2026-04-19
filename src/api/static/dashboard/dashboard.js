'use strict';

/* =========================================================
   HELPERS
   ========================================================= */

function el(id) { return document.getElementById(id); }

function apiBase(userId) {
  return `/api/v1/readmodel/dashboard/${encodeURIComponent(userId)}`;
}

function thesisApiBase() { return '/api/v1/thesis'; }

/** Current user id from the input field — used as Wave-1 auth header. */
function currentUserId() {
  return (el('userId')?.value?.trim()) || 'demo-user';
}

/** Base headers injected into every request. */
function authHeaders() {
  return {
    'Content-Type': 'application/json',
    'X-User-Id': currentUserId(),
  };
}

async function getJson(url, options = {}) {
  const r = await fetch(url, {
    ...options,
    headers: { ...authHeaders(), ...(options.headers ?? {}) },
  });
  if (!r.ok) {
    const msg = await r.text().catch(() => r.statusText);
    throw new Error(`${r.status} ${msg}`);
  }
  return r.json();
}

async function sendJson(url, method, body) {
  return getJson(url, {
    method,
    body: body != null ? JSON.stringify(body) : undefined,
  });
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

/* =========================================================
   TOAST
   ========================================================= */

function showToast(msg, type = 'success', ms = 3000) {
  const t = document.createElement('div');
  t.className = `toast ${type}`;
  t.textContent = msg;
  document.body.appendChild(t);
  setTimeout(() => t.remove(), ms);
}

/* =========================================================
   MODAL HELPERS
   ========================================================= */

function openModal(id) {
  const d = el(id);
  if (d) d.showModal();
}

function closeModal(id) {
  const d = el(id);
  if (d) d.close();
}

// Wire close buttons & backdrop click
document.addEventListener('DOMContentLoaded', () => {
  document.querySelectorAll('[data-close]').forEach(btn => {
    btn.addEventListener('click', () => closeModal(btn.dataset.close));
  });
  document.querySelectorAll('dialog').forEach(dlg => {
    dlg.addEventListener('click', e => {
      if (e.target === dlg) dlg.close();
    });
  });
});

/* =========================================================
   STATE
   ========================================================= */

let _selectedThesisId = null;
let _theses = [];
let _deleteCallback = null;

/* =========================================================
   LOAD DASHBOARD (read model)
   ========================================================= */

async function loadDashboard() {
  const userId = currentUserId();
  const status = el('statusFilter').value;
  const base = apiBase(userId);

  el('errorBanner').classList.add('hidden');

  try {
    const [summary, theses, verdicts, catalysts, snapshots] = await Promise.all([
      getJson(`${base}/summary`).catch(() => null),
      getJson(`${base}/theses?status=${status}`).catch(() => []),
      getJson(`${base}/verdicts`).catch(() => []),
      getJson(`${base}/catalysts?horizon_days=30`).catch(() => []),
      getJson(`${base}/snapshots`).catch(() => null),
    ]);

    renderSummary(summary);
    _theses = Array.isArray(theses) ? theses : (theses?.items ?? []);
    renderThesesTable(_theses);
    renderVerdicts(verdicts);
    renderCatalystList(catalysts);
    renderSnapshots(snapshots);

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

/* =========================================================
   RENDER SUMMARY
   ========================================================= */

function renderSummary(s) {
  if (!s) return;
  el('openTheses').textContent = s.open_theses ?? '—';
  el('riskyTheses').textContent = s.risky_theses ?? '—';
  el('upcoming7d').textContent = s.upcoming_catalysts_7d ?? '—';
  el('reviewsToday').textContent = s.reviews_today ?? '—';
  el('totalReviewsHero').textContent = s.total_reviews ?? '—';
  el('upcoming7dHero').textContent = s.upcoming_catalysts_7d ?? '—';
}

/* =========================================================
   RENDER THESES TABLE
   ========================================================= */

function renderThesesTable(list) {
  const wrap = el('thesesTableWrap');
  if (!list.length) {
    wrap.innerHTML = '<p class="empty-state">Chưa có thesis nào. Nhấn <strong>+ Thesis mới</strong> để tạo.</p>';
    return;
  }
  wrap.innerHTML = `
    <table>
      <thead>
        <tr>
          <th>Mã / Hướng</th>
          <th>Tiêu đề</th>
          <th>Score</th>
          <th>Status</th>
          <th>Cập nhật</th>
          <th></th>
        </tr>
      </thead>
      <tbody>
        ${list.map(t => `
          <tr data-id="${t.id}" class="${t.id === _selectedThesisId ? 'is-selected' : ''}">
            <td class="ticker-cell">
              <strong>${t.ticker}</strong>
              <span>${badge(t.direction)}</span>
            </td>
            <td>${t.title ?? '—'}</td>
            <td class="${scoreClass(t.score)}">${t.score != null ? t.score : '—'}</td>
            <td>${badge(t.status)}</td>
            <td style="color:var(--muted);font-size:.82rem;">${fmtDate(t.updated_at)}</td>
            <td>
              <div style="display:flex;gap:6px;">
                <button class="icon-btn edit-thesis-btn" data-id="${t.id}" title="Sửa thesis">✏️</button>
                <button class="icon-btn danger delete-thesis-btn" data-id="${t.id}" title="Xóa thesis">🗑</button>
              </div>
            </td>
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

  wrap.querySelectorAll('.edit-thesis-btn').forEach(btn => {
    btn.addEventListener('click', e => { e.stopPropagation(); openEditThesisModal(btn.dataset.id); });
  });

  wrap.querySelectorAll('.delete-thesis-btn').forEach(btn => {
    btn.addEventListener('click', e => { e.stopPropagation(); confirmDeleteThesis(btn.dataset.id); });
  });
}

function scoreClass(s) {
  if (s == null) return '';
  if (s >= 7) return 'score-high';
  if (s >= 4) return 'score-mid';
  return 'score-low';
}

/* =========================================================
   THESIS DETAIL PANEL
   ========================================================= */

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
          <span class="badge" style="font-size:.9rem;padding:6px 12px;">${t.ticker}</span>
          ${badge(t.direction)} ${badge(t.status)}
        </div>
        <h2 style="margin-top:10px;">${t.title ?? '—'}</h2>
      </div>
      <div class="detail-head-actions">
        <button class="ghost-btn" id="detailEditBtn">✏️ Sửa</button>
        <button class="danger-btn" id="detailDeleteBtn">🗑 Xóa thesis</button>
      </div>
    </div>

    ${t.summary ? `<p class="detail-summary">${t.summary}</p>` : ''}

    <div class="detail-grid">
      <div class="detail-stat"><span>Score</span><strong class="${scoreClass(t.score)}">${t.score ?? '—'}</strong></div>
      <div class="detail-stat"><span>Entry</span><strong>${t.entry_price ? fmt(t.entry_price) + '₫' : '—'}</strong></div>
      <div class="detail-stat"><span>Target</span><strong>${t.target_price ? fmt(t.target_price) + '₫' : '—'}</strong></div>
      <div class="detail-stat"><span>Stop loss</span><strong>${t.stop_loss ? fmt(t.stop_loss) + '₫' : '—'}</strong></div>
      <div class="detail-stat"><span>Tạo lúc</span><strong style="font-size:.9rem;">${fmtDate(t.created_at)}</strong></div>
      <div class="detail-stat"><span>Cập nhật</span><strong style="font-size:.9rem;">${fmtDate(t.updated_at)}</strong></div>
    </div>

    <div class="detail-columns">
      <div class="detail-section">
        <div class="detail-section-header">
          <h3>Assumptions (${assumList.length})</h3>
          <button class="ghost-btn" style="min-height:34px;padding:0 12px;font-size:.82rem;" id="addAssumBtn">+ Thêm</button>
        </div>
        <div class="detail-list" id="assumptionList">
          ${assumList.length ? assumList.map(a => renderAssumItem(a)).join('') : '<p class="empty-state">Chưa có assumption.</p>'}
        </div>
      </div>

      <div class="detail-section">
        <div class="detail-section-header">
          <h3>Catalysts (${catList.length})</h3>
          <button class="ghost-btn" style="min-height:34px;padding:0 12px;font-size:.82rem;" id="addCatBtn">+ Thêm</button>
        </div>
        <div class="detail-list" id="catalystDetailList">
          ${catList.length ? catList.map(c => renderCatItem(c)).join('') : '<p class="empty-state">Chưa có catalyst.</p>'}
        </div>
      </div>
    </div>

    ${revList.length ? `
      <div style="margin-top:18px;">
        <h3 style="margin-bottom:12px;">Review gần nhất</h3>
        ${revList.slice(0, 3).map(r => `
          <div class="review-card">
            <div class="review-head">
              <span class="review-meta">${fmtDate(r.reviewed_at)}</span>
              ${badge(r.verdict)}
              <span style="color:var(--muted);font-size:.82rem;">Conf: ${r.confidence ?? '—'}</span>
            </div>
            <p class="review-reasoning">${r.reasoning ?? ''}</p>
          </div>`).join('')}
      </div>` : ''}
  `;
}

function renderAssumItem(a) {
  return `
    <div class="detail-item" data-assum-id="${a.id}">
      <div class="detail-item-row">
        <span style="font-weight:600;font-size:.9rem;">${a.description}</span>
        <div class="detail-item-actions">
          ${badge(a.status)}
          <button class="icon-btn edit-assum-btn" data-id="${a.id}" title="Sửa">✏️</button>
          <button class="icon-btn danger delete-assum-btn" data-id="${a.id}" title="Xóa">🗑</button>
        </div>
      </div>
      ${a.rationale ? `<p>${a.rationale}</p>` : ''}
    </div>`;
}

function renderCatItem(c) {
  return `
    <div class="detail-item" data-cat-id="${c.id}">
      <div class="detail-item-row">
        <span style="font-weight:600;font-size:.9rem;">${c.description}</span>
        <div class="detail-item-actions">
          ${badge(c.status)}
          <button class="icon-btn edit-cat-btn" data-id="${c.id}" title="Sửa">✏️</button>
          <button class="icon-btn danger delete-cat-btn" data-id="${c.id}" title="Xóa">🗑</button>
        </div>
      </div>
      ${c.expected_timeline ? `<p>📅 ${c.expected_timeline}</p>` : ''}
      ${c.rationale ? `<p>${c.rationale}</p>` : ''}
    </div>`;
}

function wireDetailActions(thesisId, wrap) {
  wrap.querySelector('#detailEditBtn')?.addEventListener('click', () => openEditThesisModal(thesisId));
  wrap.querySelector('#detailDeleteBtn')?.addEventListener('click', () => confirmDeleteThesis(thesisId));

  wrap.querySelector('#addAssumBtn')?.addEventListener('click', () => openAssumptionModal(thesisId, null));
  wrap.querySelectorAll('.edit-assum-btn').forEach(btn =>
    btn.addEventListener('click', () => openAssumptionModal(thesisId, btn.dataset.id)));
  wrap.querySelectorAll('.delete-assum-btn').forEach(btn =>
    btn.addEventListener('click', () => confirmDeleteAssumption(thesisId, btn.dataset.id)));

  wrap.querySelector('#addCatBtn')?.addEventListener('click', () => openCatalystModal(thesisId, null));
  wrap.querySelectorAll('.edit-cat-btn').forEach(btn =>
    btn.addEventListener('click', () => openCatalystModal(thesisId, btn.dataset.id)));
  wrap.querySelectorAll('.delete-cat-btn').forEach(btn =>
    btn.addEventListener('click', () => confirmDeleteCatalyst(thesisId, btn.dataset.id)));
}

/* =========================================================
   THESIS MODAL — CREATE / EDIT
   ========================================================= */

function openNewThesisModal() {
  el('thesisModalTitle').textContent = 'Tạo Thesis mới';
  el('thesisIdField').value = '';
  el('thesisForm').reset();
  el('suggestResult').classList.add('hidden');
  el('suggestLoading').classList.add('hidden');
  openModal('thesisModal');
}

async function openEditThesisModal(thesisId) {
  el('thesisModalTitle').textContent = 'Chỉnh sửa Thesis';
  el('suggestResult').classList.add('hidden');
  el('suggestLoading').classList.add('hidden');
  try {
    const t = await getJson(`${thesisApiBase()}/${thesisId}`);
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
    user_id: currentUserId(),
  };

  try {
    if (id) {
      await sendJson(`${thesisApiBase()}/${id}`, 'PUT', payload);
      showToast('✅ Đã cập nhật thesis');
    } else {
      const created = await sendJson(`${thesisApiBase()}/`, 'POST', payload);
      _selectedThesisId = created?.id ?? null;
      showToast('✅ Đã tạo thesis mới');
    }
    closeModal('thesisModal');
    await loadDashboard();
  } catch (err) {
    showToast(`Lỗi: ${err.message}`, 'error');
  } finally {
    btn.classList.remove('btn-loading');
    btn.textContent = 'Lưu Thesis';
  }
});

/* =========================================================
   THESIS DELETE
   ========================================================= */

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

/* =========================================================
   ASSUMPTION MODAL — CREATE / EDIT
   ========================================================= */

async function openAssumptionModal(thesisId, assumId) {
  el('assumptionThesisId').value = thesisId;
  el('assumptionIdField').value = assumId ?? '';
  el('assumptionModalTitle').textContent = assumId ? 'Chỉnh sửa Assumption' : 'Thêm Assumption';
  el('assumptionForm').reset();

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
      await sendJson(`${thesisApiBase()}/${thesisId}/assumptions/${assumId}`, 'PUT', payload);
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

/* =========================================================
   CATALYST MODAL — CREATE / EDIT
   ========================================================= */

async function openCatalystModal(thesisId, catId) {
  el('catalystThesisId').value = thesisId;
  el('catalystIdField').value = catId ?? '';
  el('catalystModalTitle').textContent = catId ? 'Chỉnh sửa Catalyst' : 'Thêm Catalyst';
  el('catalystForm').reset();

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
      await sendJson(`${thesisApiBase()}/${thesisId}/catalysts/${catId}`, 'PUT', payload);
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

/* =========================================================
   DELETE CONFIRM
   ========================================================= */

el('deleteConfirmBtn')?.addEventListener('click', async () => {
  if (!_deleteCallback) return;
  const btn = el('deleteConfirmBtn');
  btn.classList.add('btn-loading');
  btn.textContent = 'Đang xóa…';
  try {
    await _deleteCallback();
  } catch (err) {
    showToast(`Lỗi xóa: ${err.message}`, 'error');
  } finally {
    btn.classList.remove('btn-loading');
    btn.textContent = 'Xóa';
    _deleteCallback = null;
  }
});

/* =========================================================
   AI SUGGEST
   ========================================================= */

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
    const data = await getJson(`${thesisApiBase()}/suggest?ticker=${encodeURIComponent(ticker)}`);
    result.innerHTML = renderSuggestResult(data);
    result.classList.remove('hidden');

    result.querySelector('.apply-suggest-btn')?.addEventListener('click', () => {
      el('thesisTickerField').value = data.ticker ?? ticker;
      el('thesisTitleField').value = data.thesis_title ?? '';
      el('thesisSummaryField').value = data.thesis_summary ?? '';
      if (data.entry_price_hint) el('thesisEntryField').value = data.entry_price_hint;
      if (data.target_price_hint) el('thesisTargetField').value = data.target_price_hint;
      if (data.stop_loss_hint) el('thesisStopField').value = data.stop_loss_hint;
      showToast('✨ Đã điền thông tin từ AI gợi ý');
    });
  } catch (err) {
    result.innerHTML = `<div class="error-banner" style="margin:0;">AI suggest lỗi: ${err.message}</div>`;
    result.classList.remove('hidden');
  } finally {
    btn.disabled = false;
    loading.classList.add('hidden');
  }
});

function renderSuggestResult(d) {
  const confPct = Math.round((d.confidence ?? 0) * 100);
  const assumes = (d.assumptions ?? []).map(a => `
    <div class="suggest-item">
      <strong>${a.description}</strong>
      ${a.rationale ? `<span>${a.rationale}</span>` : ''}
    </div>`).join('');
  const cats = (d.catalysts ?? []).map(c => `
    <div class="suggest-item">
      <strong>${c.description}</strong>
      <span>${c.expected_timeline ? `📅 ${c.expected_timeline} — ` : ''}${c.rationale ?? ''}</span>
    </div>`).join('');

  return `
    <div class="suggest-result-header">
      <strong>✨ AI gợi ý cho ${d.ticker}</strong>
      <button class="apply-suggest-btn">↓ Áp dụng vào form</button>
    </div>
    <div class="suggest-body">
      <p style="font-weight:600;margin-bottom:4px;">${d.thesis_title ?? ''}</p>
      <p style="color:var(--muted);font-size:.88rem;line-height:1.6;">${d.thesis_summary ?? ''}</p>

      ${d.entry_price_hint || d.target_price_hint ? `
        <div style="display:flex;gap:12px;flex-wrap:wrap;margin-top:4px;">
          ${d.entry_price_hint ? `<span class="badge">Entry: ${fmt(d.entry_price_hint)}₫</span>` : ''}
          ${d.target_price_hint ? `<span class="badge bullish">Target: ${fmt(d.target_price_hint)}₫</span>` : ''}
          ${d.stop_loss_hint ? `<span class="badge bearish">Stop: ${fmt(d.stop_loss_hint)}₫</span>` : ''}
        </div>` : ''}

      ${assumes ? `<div><p class="suggest-section-title">Assumptions</p>${assumes}</div>` : ''}
      ${cats ? `<div><p class="suggest-section-title">Catalysts</p>${cats}</div>` : ''}

      <div class="suggest-confidence">
        <span>Độ tin cậy AI: ${confPct}%</span>
        <div class="confidence-bar"><div class="confidence-fill" style="width:${confPct}%"></div></div>
      </div>
      ${d.reasoning ? `<p style="color:var(--muted);font-size:.82rem;line-height:1.6;">${d.reasoning}</p>` : ''}
    </div>`;
}

/* =========================================================
   SIDEBAR — VERDICTS
   ========================================================= */

function renderVerdicts(list) {
  const wrap = el('verdictList');
  if (!list?.length) { wrap.innerHTML = '<p class="empty-state">Chưa có dữ liệu.</p>'; return; }
  wrap.innerHTML = list.map(v => `
    <div class="row-item">
      <div>
        <div class="row-title">${badge(v.verdict)}</div>
        <div class="row-subtitle">${v.count ?? 0} review · ${v.pct != null ? v.pct + '%' : ''}</div>
      </div>
    </div>`).join('');
}

/* =========================================================
   SIDEBAR — CATALYSTS
   ========================================================= */

function renderCatalystList(list) {
  const wrap = el('catalystList');
  const items = Array.isArray(list) ? list : (list?.items ?? []);
  if (!items.length) { wrap.innerHTML = '<p class="empty-state">Không có catalyst sắp tới.</p>'; return; }
  wrap.innerHTML = items.slice(0, 8).map(c => `
    <div class="row-item">
      <div>
        <div class="row-title">${c.ticker ?? ''} — ${c.description ?? ''}</div>
        <div class="row-subtitle">${c.expected_timeline ?? ''} · ${badge(c.status)}</div>
      </div>
    </div>`).join('');
}

/* =========================================================
   SNAPSHOTS
   ========================================================= */

function renderSnapshots(s) {
  if (!s) return;
  el('latestScanAt').textContent = fmtDate(s.latest_scan_at);
  el('latestScanSummary').textContent = s.latest_scan_summary ?? 'Chưa có scan.';
  el('latestMorningBriefAt').textContent = fmtDate(s.latest_morning_brief_at);
  el('latestMorningBriefSummary').textContent = s.latest_morning_brief_summary ?? 'Chưa có morning brief.';
  el('latestEodBriefAt').textContent = fmtDate(s.latest_eod_brief_at);
  el('latestEodBriefSummary').textContent = s.latest_eod_brief_summary ?? 'Chưa có EOD brief.';
}

/* =========================================================
   BACKTESTING
   ========================================================= */

async function loadBacktesting() {
  const userId = currentUserId();
  const base = apiBase(userId);
  try {
    const [acc, perf] = await Promise.all([
      getJson(`${base}/accuracy`).catch(() => []),
      getJson(`${base}/performance`).catch(() => []),
    ]);
    renderAccuracy(acc);
    renderPerformance(perf);
  } catch { /* silent */ }
}

function renderAccuracy(list) {
  const wrap = el('accuracyWrap');
  const rows = Array.isArray(list) ? list : (list?.items ?? []);
  if (!rows.length) { wrap.innerHTML = '<p class="empty-state">Chưa có dữ liệu.</p>'; return; }
  wrap.innerHTML = `<table><thead><tr><th>Verdict</th><th>Đúng</th><th>Sai</th><th>Accuracy</th></tr></thead><tbody>
    ${rows.map(r => `<tr>
      <td>${badge(r.verdict)}</td>
      <td class="score-high">${r.correct ?? 0}</td>
      <td class="score-low">${r.wrong ?? 0}</td>
      <td>${r.accuracy != null ? (r.accuracy * 100).toFixed(1) + '%' : '—'}</td>
    </tr>`).join('')}
  </tbody></table>`;
}

function renderPerformance(list) {
  const wrap = el('performanceWrap');
  const rows = Array.isArray(list) ? list : (list?.items ?? []);
  if (!rows.length) { wrap.innerHTML = '<p class="empty-state">Chưa có dữ liệu.</p>'; return; }
  wrap.innerHTML = `<table><thead><tr><th>Mã</th><th>PnL%</th><th>Điểm</th><th>Status</th></tr></thead><tbody>
    ${rows.map(r => `<tr>
      <td class="ticker-cell"><strong>${r.ticker}</strong></td>
      <td class="${r.pnl_pct >= 0 ? 'pnl-pos' : 'pnl-neg'}">${r.pnl_pct != null ? (r.pnl_pct > 0 ? '+' : '') + r.pnl_pct.toFixed(2) + '%' : '—'}</td>
      <td class="${scoreClass(r.score)}">${r.score ?? '—'}</td>
      <td>${badge(r.status)}</td>
    </tr>`).join('')}
  </tbody></table>`;
}

/* =========================================================
   BOOT
   ========================================================= */

document.addEventListener('DOMContentLoaded', () => {
  el('reloadBtn')?.addEventListener('click', () => { loadDashboard(); loadBacktesting(); });
  el('newThesisBtn')?.addEventListener('click', openNewThesisModal);

  loadDashboard();
  loadBacktesting();
});
