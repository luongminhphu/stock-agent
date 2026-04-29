import { esc, fmt } from '../../utils/format.js';
import { showToast } from '../../utils/dom.js';
import { el } from '../../utils/dom.js';

/**
 * Điền toàn bộ thesis form từ AI suggest response.
 */
export function applySuggestToThesisForm(data, fallbackTicker, { makeAssumptionRow, makeCatalystRow, clearFormRows, seedBlankFormRows }) {
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
  (data.catalysts ?? []).forEach(item => cWrap?.appendChild(makeCatalystRow(item)));
  seedBlankFormRows();
  showToast('✨ Đã điền thesis form, assumptions và catalysts từ AI suggest');
}

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
          ${d.entry_price_hint  ? `<span class="badge">Entry: ${fmt(d.entry_price_hint)}₫</span>` : ''}
          ${d.target_price_hint ? `<span class="badge bullish">Target: ${fmt(d.target_price_hint)}₫</span>` : ''}
          ${d.stop_loss_hint    ? `<span class="badge bearish">Stop: ${fmt(d.stop_loss_hint)}₫</span>` : ''}
        </div>` : ''}
      ${assumes ? `<div><p class="suggest-section-title">Assumptions gợi ý</p>${assumes}</div>` : ''}
      ${cats    ? `<div><p class="suggest-section-title">Catalysts gợi ý</p>${cats}</div>` : ''}
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
      <button type="button" class="ghost-btn apply-assumption-suggest-btn" data-index="${idx}" style="margin-top:8px;min-height:32px;padding:0 10px;font-size:.8rem;">Điền vào form</button>
    </div>`).join('');
}

export function renderCatalystSuggestResult(items) {
  if (!items.length) return '<p class="empty-state">AI không trả về catalyst phù hợp.</p>';
  return items.map((c, idx) => `
    <div class="suggest-item">
      <strong>${idx + 1}. ${esc(c.description)}</strong>
      <span>${c.expected_timeline ? `📅 ${esc(c.expected_timeline)} — ` : ''}${esc(c.rationale ?? '')}</span>
      <button type="button" class="ghost-btn apply-catalyst-suggest-btn" data-index="${idx}" style="margin-top:8px;min-height:32px;padding:0 10px;font-size:.8rem;">Điền vào form</button>
    </div>`).join('');
}
