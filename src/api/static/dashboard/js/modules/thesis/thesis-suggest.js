/**
 * thesis-suggest.js
 * Owner: modules/thesis
 * Responsibility: AI suggest flows cho thesis form, assumption modal, catalyst modal.
 * - renderSuggestResult / renderAssumptionSuggestResult / renderCatalystSuggestResult
 * - applySuggestToThesisForm
 * - bindSuggestEvents — đăng ký tất cả AI suggest buttons, gọi 1 lần từ app.js
 */

import { el, showToast } from '../../utils/dom.js';
import { esc, fmt } from '../../utils/format.js';
import { thesisApiBase, sendJson } from '../../api/client.js';
import { makeAssumptionRow, makeCatalystRow, clearFormRows, seedBlankFormRows } from './thesis-form.js';

// ---------------------------------------------------------------------------
// Render helpers
// ---------------------------------------------------------------------------
export function renderSuggestResult(d) {
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
          ${d.entry_price_hint  ? `<span class="badge">Entry: ${fmt(d.entry_price_hint)}₫</span>`        : ''}
          ${d.target_price_hint ? `<span class="badge bullish">Target: ${fmt(d.target_price_hint)}₫</span>` : ''}
          ${d.stop_loss_hint    ? `<span class="badge bearish">Stop: ${fmt(d.stop_loss_hint)}₫</span>`    : ''}
        </div>` : ''}
      ${assumes ? `<div><p class="suggest-section-title">Assumptions gợi ý</p>${assumes}</div>` : ''}
      ${cats    ? `<div><p class="suggest-section-title">Catalysts gợi ý</p>${cats}</div>`    : ''}
      <div class="suggest-confidence">
        <span>Độ tin cậy AI: ${confPct}%</span>
        <div class="confidence-bar"><div class="confidence-fill" style="width:${confPct}%"></div></div>
      </div>
      ${d.reasoning ? `<p style="color:var(--muted);font-size:.82rem;line-height:1.6;">${esc(d.reasoning)}</p>` : ''}
    </div>`;
}

export function renderAssumptionSuggestResult(items) {
  if (!items.length) return '<p class="empty-state">AI không trả về assumption phù hợp.</p>';
  return items.map((a, idx) => `
    <div class="suggest-item">
      <strong>${idx + 1}. ${esc(a.description)}</strong>
      ${a.rationale ? `<span>${esc(a.rationale)}</span>` : ''}
      <button type="button" class="ghost-btn apply-assumption-suggest-btn" data-index="${idx}"
        style="margin-top:8px;min-height:32px;padding:0 10px;font-size:.8rem;">Điền vào form</button>
    </div>`).join('');
}

export function renderCatalystSuggestResult(items) {
  if (!items.length) return '<p class="empty-state">AI không trả về catalyst phù hợp.</p>';
  return items.map((c, idx) => `
    <div class="suggest-item">
      <strong>${idx + 1}. ${esc(c.description)}</strong>
      <span>${c.expected_timeline ? `📅 ${esc(c.expected_timeline)} — ` : ''}${esc(c.rationale ?? '')}</span>
      <button type="button" class="ghost-btn apply-catalyst-suggest-btn" data-index="${idx}"
        style="margin-top:8px;min-height:32px;padding:0 10px;font-size:.8rem;">Điền vào form</button>
    </div>`).join('');
}

// ---------------------------------------------------------------------------
// applySuggestToThesisForm — điền kết quả AI vào thesis form
// ---------------------------------------------------------------------------
export function applySuggestToThesisForm(data, fallbackTicker) {
  el('thesisTickerField').value  = data.ticker ?? fallbackTicker;
  el('thesisTitleField').value   = data.thesis_title ?? '';
  el('thesisSummaryField').value = data.thesis_summary ?? '';
  el('thesisEntryField').value   = data.entry_price_hint ?? '';
  el('thesisTargetField').value  = data.target_price_hint ?? '';
  el('thesisStopField').value    = data.stop_loss_hint ?? '';

  clearFormRows();
  const aWrap = el('thesisFormAssumptionRows');
  const cWrap = el('thesisFormCatalystRows');
  (data.assumptions ?? []).forEach(item => aWrap?.appendChild(makeAssumptionRow(item)));
  (data.catalysts   ?? []).forEach(item => cWrap?.appendChild(makeCatalystRow(item)));
  seedBlankFormRows();
  showToast('✨ Đã điền thesis form, assumptions và catalysts từ AI suggest');
}

// ---------------------------------------------------------------------------
// bindSuggestEvents — gọi 1 lần từ app.js
// ---------------------------------------------------------------------------
export function bindSuggestEvents() {
  // Thesis AI suggest
  el('aiSuggestBtn')?.addEventListener('click', async () => {
    const ticker = (el('suggestTicker')?.value ?? el('thesisTickerField')?.value ?? '').trim().toUpperCase();
    if (!ticker) { showToast('Nhập mã cổ phiếu trước', 'error'); return; }
    const btn     = el('aiSuggestBtn');
    const loading = el('suggestLoading');
    const result  = el('suggestResult');
    btn.disabled = true;
    loading.classList.remove('hidden');
    result.classList.add('hidden');
    try {
      const data = await sendJson(`${thesisApiBase()}/suggest?ticker=${encodeURIComponent(ticker)}`, 'POST', null);
      result.innerHTML = renderSuggestResult(data);
      result.classList.remove('hidden');
      result.querySelector('.apply-suggest-btn')?.addEventListener('click', () =>
        applySuggestToThesisForm(data, ticker));
    } catch (err) {
      result.innerHTML = `<div class="error-banner" style="margin:0;">AI suggest lỗi: ${err.message}</div>`;
      result.classList.remove('hidden');
    } finally {
      btn.disabled = false;
      loading.classList.add('hidden');
    }
  });

  // Assumption AI suggest
  el('assumptionAiSuggestBtn')?.addEventListener('click', async () => {
    const ticker = el('assumptionSuggestTicker')?.value?.trim().toUpperCase();
    if (!ticker) { showToast('Không xác định được mã cổ phiếu cho assumption này', 'error'); return; }
    const loading = el('assumptionSuggestLoading');
    const result  = el('assumptionSuggestResult');
    loading.classList.remove('hidden');
    result.classList.add('hidden');
    try {
      const data  = await sendJson(`${thesisApiBase()}/suggest?ticker=${encodeURIComponent(ticker)}`, 'POST', null);
      const items = data.assumptions ?? [];
      result.innerHTML = renderAssumptionSuggestResult(items);
      result.classList.remove('hidden');
      result.querySelectorAll('.apply-assumption-suggest-btn').forEach(btn =>
        btn.addEventListener('click', () => {
          const item = items[Number(btn.dataset.index)];
          el('assumptionDescField').value      = item?.description ?? '';
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

  // Catalyst AI suggest
  el('catalystAiSuggestBtn')?.addEventListener('click', async () => {
    const ticker = el('catalystSuggestTicker')?.value?.trim().toUpperCase();
    if (!ticker) { showToast('Không xác định được mã cổ phiếu cho catalyst này', 'error'); return; }
    const loading = el('catalystSuggestLoading');
    const result  = el('catalystSuggestResult');
    loading.classList.remove('hidden');
    result.classList.add('hidden');
    try {
      const data  = await sendJson(`${thesisApiBase()}/suggest?ticker=${encodeURIComponent(ticker)}`, 'POST', null);
      const items = data.catalysts ?? [];
      result.innerHTML = renderCatalystSuggestResult(items);
      result.classList.remove('hidden');
      result.querySelectorAll('.apply-catalyst-suggest-btn').forEach(btn =>
        btn.addEventListener('click', () => {
          const item = items[Number(btn.dataset.index)];
          el('catalystDescField').value      = item?.description ?? '';
          el('catalystRationaleField').value = item?.rationale ?? '';
          el('catalystTimelineField').value  = item?.expected_timeline ?? '';
          showToast('✨ Đã điền catalyst từ AI');
        }));
    } catch (err) {
      result.innerHTML = `<div class="error-banner" style="margin:0;">AI suggest lỗi: ${err.message}</div>`;
      result.classList.remove('hidden');
    } finally {
      loading.classList.add('hidden');
    }
  });
}
