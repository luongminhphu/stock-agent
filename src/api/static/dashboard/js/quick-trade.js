/**
 * quick-trade.js — B/S quick-trade buttons for Holdings table (Trades tab)
 *
 * Injects a [B] and [S] button into the .col-action cell of every holdings row.
 * Clicking opens a modal → user enters qty + price → POST /api/v1/portfolio/buy|sell
 * On success: dismisses modal, refreshes holdings data, shows inline toast.
 *
 * Dependencies: none (vanilla JS, no framework).
 * Called from: portfolio-loader.js after holdings table is rendered.
 *
 * Public API:
 *   initQuickTrade()             — call once after DOM is ready
 *   injectTradeButtons(tbodyEl)  — call after each holdings table re-render
 */

(function (global) {
  'use strict';

  // ─── Modal HTML (injected once into <body>) ────────────────────────────────
  const MODAL_ID = 'qt-modal';

  function ensureModal() {
    if (document.getElementById(MODAL_ID)) return;
    const el = document.createElement('div');
    el.innerHTML = `
      <div id="${MODAL_ID}" class="qt-backdrop" role="dialog" aria-modal="true" aria-labelledby="qt-title" hidden>
        <div class="qt-modal">
          <div class="qt-modal-header">
            <span id="qt-title" class="qt-modal-title">Lệnh giao dịch</span>
            <button class="qt-close" id="qt-close-btn" aria-label="Đóng">✕</button>
          </div>
          <div class="qt-modal-body">
            <div class="qt-ticker-row">
              <span class="qt-badge" id="qt-type-badge">MUA</span>
              <span class="qt-ticker-label" id="qt-ticker-display"></span>
            </div>
            <label class="qt-label" for="qt-qty">Số lượng (cp)</label>
            <input class="qt-input" id="qt-qty" type="number" min="1" step="100" placeholder="VD: 1000" />
            <label class="qt-label" for="qt-price">Giá (VND/cp)</label>
            <input class="qt-input" id="qt-price" type="number" min="100" step="100" placeholder="VD: 48500" />
            <label class="qt-label" for="qt-note">Ghi chú (tuỳ chọn)</label>
            <input class="qt-input" id="qt-note" type="text" maxlength="200" placeholder="" />
            <div class="qt-summary" id="qt-summary"></div>
            <div class="qt-error" id="qt-error" hidden></div>
          </div>
          <div class="qt-modal-footer">
            <button class="qt-btn qt-btn-secondary" id="qt-cancel-btn">Huỷ</button>
            <button class="qt-btn qt-btn-primary" id="qt-confirm-btn">Xác nhận</button>
          </div>
        </div>
      </div>
    `;
    document.body.appendChild(el.firstElementChild);
    _bindModalEvents();
  }

  // ─── State ─────────────────────────────────────────────────────────────────
  let _currentTicker = '';
  let _currentType   = 'buy'; // 'buy' | 'sell'

  // ─── Modal lifecycle ───────────────────────────────────────────────────────
  function openModal(ticker, type) {
    _currentTicker = ticker.toUpperCase();
    _currentType   = type;

    const badge = document.getElementById('qt-type-badge');
    badge.textContent  = type === 'buy' ? 'MUA' : 'BÁN';
    badge.className    = 'qt-badge ' + (type === 'buy' ? 'qt-badge-buy' : 'qt-badge-sell');

    document.getElementById('qt-ticker-display').textContent = _currentTicker;
    document.getElementById('qt-title').textContent =
      (type === 'buy' ? 'Lệnh MUA — ' : 'Lệnh BÁN — ') + _currentTicker;
    document.getElementById('qt-qty').value   = '';
    document.getElementById('qt-price').value = '';
    document.getElementById('qt-note').value  = '';
    document.getElementById('qt-summary').textContent = '';
    _hideError();

    document.getElementById(MODAL_ID).removeAttribute('hidden');
    document.getElementById('qt-qty').focus();
  }

  function closeModal() {
    document.getElementById(MODAL_ID).setAttribute('hidden', '');
  }

  function _bindModalEvents() {
    document.getElementById('qt-close-btn').addEventListener('click', closeModal);
    document.getElementById('qt-cancel-btn').addEventListener('click', closeModal);
    document.getElementById(MODAL_ID).addEventListener('click', function (e) {
      if (e.target === this) closeModal();
    });
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape') closeModal();
    });
    // Live summary
    ['qt-qty', 'qt-price'].forEach(function (id) {
      document.getElementById(id).addEventListener('input', _updateSummary);
    });
    document.getElementById('qt-confirm-btn').addEventListener('click', _handleConfirm);
  }

  function _updateSummary() {
    const qty   = parseFloat(document.getElementById('qt-qty').value)   || 0;
    const price = parseFloat(document.getElementById('qt-price').value) || 0;
    const el    = document.getElementById('qt-summary');
    if (qty > 0 && price > 0) {
      const total = qty * price;
      el.textContent =
        'Tổng giá trị: ' + total.toLocaleString('vi-VN') + ' ₫  (' +
        qty.toLocaleString('vi-VN') + ' cp × ' +
        price.toLocaleString('vi-VN') + ' ₫)';
    } else {
      el.textContent = '';
    }
  }

  function _showError(msg) {
    const el = document.getElementById('qt-error');
    el.textContent = msg;
    el.removeAttribute('hidden');
  }

  function _hideError() {
    document.getElementById('qt-error').setAttribute('hidden', '');
  }

  // ─── API call ──────────────────────────────────────────────────────────────
  async function _handleConfirm() {
    _hideError();
    const qty   = parseFloat(document.getElementById('qt-qty').value);
    const price = parseFloat(document.getElementById('qt-price').value);
    const note  = document.getElementById('qt-note').value.trim() || null;

    if (!qty || qty <= 0)     { _showError('Số lượng phải lớn hơn 0.'); return; }
    if (!price || price <= 0) { _showError('Giá phải lớn hơn 0.'); return; }

    const btn = document.getElementById('qt-confirm-btn');
    btn.disabled    = true;
    btn.textContent = 'Đang xử lý…';

    const endpoint = '/api/v1/portfolio/' + _currentType;
    const body = { ticker: _currentTicker, qty, price };
    if (note) body.note = note;

    try {
      const res = await fetch(endpoint, {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify(body),
      });

      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        const msg  = (data && data.detail) ? data.detail : `Lỗi ${res.status}`;
        _showError(msg);
        return;
      }

      const result = await res.json();
      closeModal();
      _showToast(_buildSuccessMsg(result));

      // Refresh holdings table if callback registered
      if (typeof global.__qtRefreshHoldings === 'function') {
        global.__qtRefreshHoldings();
      }
    } catch (err) {
      _showError('Không thể kết nối server. Kiểm tra lại kết nối.');
    } finally {
      btn.disabled    = false;
      btn.textContent = 'Xác nhận';
    }
  }

  function _buildSuccessMsg(result) {
    const sign = result.realized_pnl != null
      ? (result.realized_pnl >= 0 ? '+' : '') + result.realized_pnl.toLocaleString('vi-VN') + ' ₫'
      : null;
    let msg = (result.trade_type === 'buy' ? '✅ Đã mua ' : '✅ Đã bán ') +
      result.qty.toLocaleString('vi-VN') + ' cp ' + result.ticker +
      ' @ ' + result.price.toLocaleString('vi-VN') + ' ₫';
    if (sign) msg += ' | Realized P&L: ' + sign;
    if (result.position_closed) msg += ' | Vị thế đã đóng';
    return msg;
  }

  // ─── Toast ─────────────────────────────────────────────────────────────────
  function _showToast(msg) {
    let container = document.getElementById('qt-toast-container');
    if (!container) {
      container = document.createElement('div');
      container.id = 'qt-toast-container';
      document.body.appendChild(container);
    }
    const toast = document.createElement('div');
    toast.className   = 'qt-toast';
    toast.textContent = msg;
    container.appendChild(toast);
    requestAnimationFrame(() => { toast.classList.add('qt-toast-visible'); });
    setTimeout(() => {
      toast.classList.remove('qt-toast-visible');
      setTimeout(() => toast.remove(), 300);
    }, 4000);
  }

  // ─── Button injection ──────────────────────────────────────────────────────
  /**
   * Inject [B] [S] buttons vào ô .col-action của mỗi row trong tbody.
   * - Ưu tiên td.col-action (layout mới với cột Hành động riêng).
   * - Fallback về td:first-child nếu chưa có col-action (backward compat).
   * - Safe to call nhiều lần — skip row đã inject rồi.
   */
  function injectTradeButtons(tbodyEl) {
    if (!tbodyEl) return;
    tbodyEl.querySelectorAll('tr').forEach(function (row) {
      // Ưu tiên cột Action riêng
      const actionCell = row.querySelector('td.col-action');
      const targetCell = actionCell || row.querySelector('td:first-child');
      if (!targetCell) return;

      // Skip nếu đã inject
      if (targetCell.querySelector('.qt-btn-inline')) return;

      // Lấy ticker từ td.col-ticker hoặc td:first-child
      const tickerCell = row.querySelector('td.col-ticker') || row.querySelector('td:first-child');
      const ticker = tickerCell ? tickerCell.textContent.trim() : '';
      if (!ticker) return;

      // Nếu là cột action riêng: xóa nội dung placeholder (nút trắng từ renderer)
      if (actionCell) actionCell.innerHTML = '';

      const wrap = document.createElement('div');
      wrap.className = 'action-btns';

      const bBtn = document.createElement('button');
      bBtn.className   = 'qt-btn-inline qt-btn-buy';
      bBtn.textContent = 'B';
      bBtn.title       = 'Mua thêm ' + ticker;
      bBtn.setAttribute('aria-label', 'Mua ' + ticker);
      bBtn.addEventListener('click', function (e) {
        e.stopPropagation();
        openModal(ticker, 'buy');
      });

      const sBtn = document.createElement('button');
      sBtn.className   = 'qt-btn-inline qt-btn-sell';
      sBtn.textContent = 'S';
      sBtn.title       = 'Bán ' + ticker;
      sBtn.setAttribute('aria-label', 'Bán ' + ticker);
      sBtn.addEventListener('click', function (e) {
        e.stopPropagation();
        openModal(ticker, 'sell');
      });

      wrap.appendChild(bBtn);
      wrap.appendChild(sBtn);
      targetCell.appendChild(wrap);
    });
  }

  // ─── Init ──────────────────────────────────────────────────────────────────
  function initQuickTrade() {
    ensureModal();
    document.querySelectorAll('[data-holdings-tbody]').forEach(injectTradeButtons);
  }

  // ─── Public API ────────────────────────────────────────────────────────────
  global.QuickTrade = {
    init:               initQuickTrade,
    injectTradeButtons: injectTradeButtons,
    openModal:          openModal,
  };

}(window));
