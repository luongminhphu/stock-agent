/**
 * quick-trade.js
 * Owner: modules/portfolio
 * Responsibility: QuickTrade modal — B/S button injection + form submit.
 *
 * Usage:
 *   window.QuickTrade.init()          — inject modal HTML vào DOM (idempotent)
 *   window.QuickTrade.injectTradeButtons(tbody, opts)  — add B/S cells to rows
 *
 * Pattern: global window.QuickTrade object (not ES module) so dashboard
 * index.html can load it via <script> tag without import map.
 */

(function () {
  'use strict';

  // -------------------------------------------------------------------------
  // Constants
  // -------------------------------------------------------------------------
  const MODAL_ID   = 'qt-modal';
  const API_BASE   = () => window.__STOCK_API_BASE ?? '';

  // -------------------------------------------------------------------------
  // Modal HTML
  // -------------------------------------------------------------------------
  function _modalHTML() {
    return `
<div id="${MODAL_ID}" class="qt-modal" role="dialog" aria-modal="true" aria-labelledby="qt-title" hidden>
  <div class="qt-backdrop"></div>
  <div class="qt-sheet">
    <header class="qt-header">
      <h3 id="qt-title" class="qt-title">Giao dịch</h3>
      <button class="qt-close" aria-label="Đóng">×</button>
    </header>

    <form id="qt-form" class="qt-form" novalidate>
      <div class="qt-field">
        <label for="qt-ticker">Ticker</label>
        <input id="qt-ticker" name="ticker" type="text" placeholder="VD: HPG" autocomplete="off" required>
      </div>

      <div class="qt-field qt-field--thesis" hidden>
        <label for="qt-thesis">Thesis (tùy chọn)</label>
        <select id="qt-thesis" name="thesis_id">
          <option value="">-- Chọn thesis --</option>
        </select>
      </div>

      <div class="qt-field">
        <label>Loại lệnh</label>
        <div class="qt-side-group" role="radiogroup">
          <label class="qt-side-btn qt-side-buy">
            <input type="radio" name="side" value="buy" checked> Mua (B)
          </label>
          <label class="qt-side-btn qt-side-sell">
            <input type="radio" name="side" value="sell"> Bán (S)
          </label>
        </div>
      </div>

      <div class="qt-field">
        <label for="qt-qty">Số lượng (cổ phiếu)</label>
        <input id="qt-qty" name="qty" type="number" min="1" step="100" placeholder="VD: 1000" required>
      </div>

      <div class="qt-field">
        <label for="qt-price">Giá khớp (VNĐ)</label>
        <input id="qt-price" name="price" type="number" min="0" step="50" placeholder="VD: 55000" required>
      </div>

      <div class="qt-field">
        <label for="qt-date">Ngày giao dịch</label>
        <input id="qt-date" name="traded_at" type="date" required>
      </div>

      <div id="qt-error" class="qt-error" hidden></div>

      <div class="qt-actions">
        <button type="button" class="qt-btn qt-btn--ghost qt-cancel">Hủy</button>
        <button type="submit" class="qt-btn qt-btn--primary" id="qt-submit">Xác nhận</button>
      </div>
    </form>
  </div>
</div>`;
  }

  // -------------------------------------------------------------------------
  // init() — inject modal vào DOM (idempotent)
  // -------------------------------------------------------------------------
  function init() {
    if (document.getElementById(MODAL_ID)) return;
    document.body.insertAdjacentHTML('beforeend', _modalHTML());
    _bindModal();
  }

  // -------------------------------------------------------------------------
  // injectTradeButtons(tbody, opts)
  // opts.fromThesisTab: true → thesis_id đã biết từ row → ẩn thesis dropdown
  // -------------------------------------------------------------------------
  function injectTradeButtons(tbody, opts = {}) {
    if (!tbody) return;
    const { fromThesisTab = false } = opts;

    tbody.querySelectorAll('tr[data-ticker]').forEach(tr => {
      const actionCell = tr.querySelector('.col-action');
      if (!actionCell || actionCell.dataset.qtInjected) return;
      actionCell.dataset.qtInjected = '1';

      const ticker   = tr.dataset.ticker   ?? '';
      const thesisId = tr.dataset.thesisId ?? '';

      actionCell.innerHTML = `
        <div class="qt-btn-group">
          <button class="qt-inline-btn qt-inline-buy"  data-ticker="${_esc(ticker)}" data-thesis-id="${_esc(thesisId)}" data-from-thesis="${fromThesisTab}" title="Mua ${_esc(ticker)}">B</button>
          <button class="qt-inline-btn qt-inline-sell" data-ticker="${_esc(ticker)}" data-thesis-id="${_esc(thesisId)}" data-from-thesis="${fromThesisTab}" title="Bán ${_esc(ticker)}">S</button>
        </div>`;

      actionCell.querySelectorAll('.qt-inline-btn').forEach(btn => {
        btn.addEventListener('click', e => {
          e.stopPropagation();
          _openModal({
            ticker:      btn.dataset.ticker,
            thesisId:    btn.dataset.thesisId,
            side:        btn.classList.contains('qt-inline-buy') ? 'buy' : 'sell',
            fromThesis:  btn.dataset.fromThesis === 'true',
          });
        });
      });
    });
  }

  // -------------------------------------------------------------------------
  // Modal open/close
  // -------------------------------------------------------------------------
  function _openModal({ ticker, thesisId, side, fromThesis }) {
    const modal = document.getElementById(MODAL_ID);
    if (!modal) return;

    modal.querySelector('#qt-ticker').value  = ticker ?? '';
    modal.querySelector('#qt-date').value    = new Date().toISOString().slice(0, 10);
    modal.querySelector('#qt-qty').value     = '';
    modal.querySelector('#qt-price').value   = '';
    modal.querySelector('#qt-error').hidden  = true;
    modal.querySelector('#qt-error').textContent = '';

    // Side
    modal.querySelectorAll('[name="side"]').forEach(r => { r.checked = (r.value === side); });

    // Thesis dropdown
    const thesisField = modal.querySelector('.qt-field--thesis');
    if (fromThesis && thesisId) {
      thesisField.hidden = true;
      modal.querySelector('#qt-thesis').value = thesisId;
    } else {
      thesisField.hidden = false;
      _populateThesisDropdown(modal, ticker, thesisId);
    }

    modal.hidden = false;
    modal.querySelector('#qt-qty').focus();
  }

  function _closeModal() {
    const modal = document.getElementById(MODAL_ID);
    if (modal) modal.hidden = true;
  }

  // -------------------------------------------------------------------------
  // Thesis dropdown population
  // -------------------------------------------------------------------------
  async function _populateThesisDropdown(modal, ticker, selectedId) {
    const sel = modal.querySelector('#qt-thesis');
    sel.innerHTML = '<option value="">-- Đang tải... --</option>';
    try {
      const data = await fetch(`${API_BASE()}/theses?status=active&limit=100`).then(r => r.json());
      const theses = data?.items ?? data?.theses ?? [];
      const filtered = ticker
        ? theses.filter(t => t.ticker?.toUpperCase() === ticker.toUpperCase())
        : theses;

      sel.innerHTML = '<option value="">-- Chọn thesis (tùy chọn) --</option>';
      filtered.forEach(t => {
        const opt = document.createElement('option');
        opt.value       = String(t.id);
        opt.textContent = `#${t.id} ${t.ticker} — ${t.title ?? ''}`;
        opt.selected    = String(t.id) === String(selectedId);
        sel.appendChild(opt);
      });
    } catch {
      sel.innerHTML = '<option value="">-- Không tải được thesis --</option>';
    }
  }

  // -------------------------------------------------------------------------
  // Modal event bindings
  // -------------------------------------------------------------------------
  function _bindModal() {
    const modal = document.getElementById(MODAL_ID);
    if (!modal) return;

    modal.querySelector('.qt-close').addEventListener('click', _closeModal);
    modal.querySelector('.qt-cancel').addEventListener('click', _closeModal);
    modal.querySelector('.qt-backdrop').addEventListener('click', _closeModal);

    document.addEventListener('keydown', e => {
      if (e.key === 'Escape' && !modal.hidden) _closeModal();
    });

    modal.querySelector('#qt-form').addEventListener('submit', async e => {
      e.preventDefault();
      await _submitTrade(modal);
    });
  }

  // -------------------------------------------------------------------------
  // Trade submission
  // -------------------------------------------------------------------------
  async function _submitTrade(modal) {
    const submitBtn = modal.querySelector('#qt-submit');
    const errorEl   = modal.querySelector('#qt-error');
    errorEl.hidden  = true;

    const form     = modal.querySelector('#qt-form');
    const ticker   = form.querySelector('#qt-ticker').value.trim().toUpperCase();
    const side     = form.querySelector('[name="side"]:checked')?.value;
    const qty      = Number(form.querySelector('#qt-qty').value);
    const price    = Number(form.querySelector('#qt-price').value);
    const tradedAt = form.querySelector('#qt-date').value;
    const thesisId = form.querySelector('#qt-thesis').value || null;

    if (!ticker || !side || !qty || !price || !tradedAt) {
      errorEl.textContent = 'Vui lòng điền đủ thông tin.';
      errorEl.hidden = false;
      return;
    }

    submitBtn.disabled = true;
    submitBtn.textContent = 'Đang gửi...';

    try {
      const payload = { ticker, side, qty, price, traded_at: tradedAt };
      if (thesisId) payload.thesis_id = Number(thesisId);

      const res = await fetch(`${API_BASE()}/trades`, {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify(payload),
      });

      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err?.detail ?? `HTTP ${res.status}`);
      }

      _closeModal();
      if (typeof window.__qtRefreshHoldings === 'function') {
        window.__qtRefreshHoldings();
      }
    } catch (err) {
      errorEl.textContent = `Lỗi: ${err.message}`;
      errorEl.hidden = false;
    } finally {
      submitBtn.disabled = false;
      submitBtn.textContent = 'Xác nhận';
    }
  }

  // -------------------------------------------------------------------------
  // Escape helper
  // -------------------------------------------------------------------------
  function _esc(s) {
    return String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  }

  // -------------------------------------------------------------------------
  // Export to window
  // -------------------------------------------------------------------------
  window.QuickTrade = { init, injectTradeButtons };
})();
